"""Portal scanner.

For each entry in `config/targets.yml`, fetch open postings via the
portal-type strategy declared in `config/portals.yml`:

  - json_api           → httpx GET of the public board endpoint
  - playwright_html    → Playwright-python on the HTML portal

Each discovered posting is passed through `ingest.ingest()` so we get
the same parsed `Job` row as a single-URL ingest.

This module is intentionally defensive: a failing portal logs and
continues so one broken site doesn't kill a whole scan.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable

import httpx
import structlog

from .config import load_portals, load_targets
from . import ingest


log = structlog.get_logger(__name__)


@dataclass
class ScanResult:
    company: str
    new_job_ids: list[int]
    skipped_duplicates: int
    errors: list[str]


# ── Public API ───────────────────────────────────────────────────────

def scan_company(company_name: str) -> ScanResult:
    targets = {t["name"]: t for t in load_targets().get("companies", [])}
    target = targets.get(company_name)
    if not target:
        raise KeyError(f"'{company_name}' not in config/targets.yml")
    return _scan_one(target)


def scan_all(limit_per_company: int = 20) -> list[ScanResult]:
    results: list[ScanResult] = []
    for target in load_targets().get("companies", []):
        try:
            results.append(_scan_one(target, limit=limit_per_company))
        except Exception as e:
            log.error("scan.failed", company=target.get("name"), err=str(e))
            results.append(ScanResult(company=target.get("name", "?"), new_job_ids=[], skipped_duplicates=0, errors=[str(e)]))
    return results


# ── Strategy dispatch ────────────────────────────────────────────────

def _scan_one(target: dict, limit: int = 20) -> ScanResult:
    portal_type = target.get("portal_type", "custom")
    portal_conf = dict(load_portals().get("portals", {}).get(portal_type, {}))
    # Apply per-company overrides if present.
    overrides = target.get("portal_overrides") or {}
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(portal_conf.get(k), dict):
            portal_conf[k] = {**portal_conf[k], **v}
        else:
            portal_conf[k] = v
    strategy = portal_conf.get("strategy", "playwright_html")

    if strategy == "json_api":
        urls = _scan_json_api(target, portal_conf, limit=limit)
    elif strategy == "playwright_html":
        urls = _scan_playwright(target, portal_conf, limit=limit)
    else:
        return ScanResult(company=target["name"], new_job_ids=[], skipped_duplicates=0,
                          errors=[f"unknown strategy: {strategy}"])

    # de-dupe + ingest
    new_ids, dups = _ingest_many(urls, company=target["name"])
    return ScanResult(company=target["name"], new_job_ids=new_ids, skipped_duplicates=dups, errors=[])


# ── JSON API scanners ────────────────────────────────────────────────

def _slug_from_portal_url(portal_url: str) -> str:
    """Extract a board slug from a careers URL.

    This is heuristic and per-portal. Kept here so the targets.yml file
    can stay human-readable (URLs instead of raw slugs).
    """
    if not portal_url:
        return ""
    m = re.search(r"greenhouse\.io/([^/?#]+)", portal_url)
    if m:
        return m.group(1)
    m = re.search(r"lever\.co/([^/?#]+)", portal_url)
    if m:
        return m.group(1)
    m = re.search(r"ashbyhq\.com/([^/?#]+)", portal_url) or re.search(r"ashby\.hq/([^/?#]+)", portal_url)
    if m:
        return m.group(1)
    # fallback: last path component
    return portal_url.rstrip("/").split("/")[-1]


def _scan_json_api(target: dict, portal_conf: dict, *, limit: int) -> list[str]:
    slug = _slug_from_portal_url(target.get("portal", ""))
    api_url = portal_conf["api_pattern"].format(slug=slug)
    with httpx.Client(timeout=20.0, follow_redirects=True,
                      headers={"User-Agent": "career-ops/0.1"}) as c:
        r = c.get(api_url)
        r.raise_for_status()
        data = r.json()

    # Greenhouse: {"jobs": [{"absolute_url": ...}]}
    # Lever: [ {"hostedUrl": ...} ]
    # Ashby: {"jobs": [...]}  or {"results": [...]}
    jobs: Iterable[dict]
    if isinstance(data, dict):
        jobs = data.get("jobs") or data.get("results") or []
    elif isinstance(data, list):
        jobs = data
    else:
        jobs = []

    extract = portal_conf.get("extract", {})
    url_key = extract.get("url", "absolute_url")

    urls: list[str] = []
    for j in jobs:
        url = _get_dotted(j, url_key)
        if url:
            urls.append(url)
            if len(urls) >= limit:
                break
    return urls


def _get_dotted(d: dict, dotted_key: str):
    cur = d
    for part in dotted_key.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


# ── Playwright scanner ───────────────────────────────────────────────

def _scan_playwright(target: dict, portal_conf: dict, *, limit: int) -> list[str]:
    """Playwright HTML scanner.

    Driven entirely by `portal_conf.selectors`. Works for Workday-style
    tenants and any `custom` entry where selectors are filled in.

    Selectors consumed:
      - job_list          (required) CSS selector for job-link anchors
      - pagination_next   (optional) CSS selector for a "next page" button
      - detail_container  (optional, reserved — not used during scan;
                           ingest re-fetches the detail page)

    Kept resilient: selector timeouts don't crash the scan, they just
    stop pagination.
    """
    selectors = portal_conf.get("selectors") or {}
    job_list_sel = selectors.get("job_list")
    if not job_list_sel:
        raise ValueError(
            f"Portal type '{target.get('portal_type')}' has no 'selectors.job_list' "
            f"in config/portals.yml. Add one or use a json_api portal."
        )

    pagination_sel: str | None = selectors.get("pagination_next")
    start_url: str = target["portal"]

    # Import lazily so `career-ops` stays importable on machines without
    # playwright installed (e.g. CI fast-lane).
    try:
        from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "playwright is not installed. Run `pip install playwright && "
            "python -m playwright install chromium`."
        ) from e

    user_agent = (
        load_portals()
        .get("rate_limits", {})
        .get("user_agent", "career-ops/0.1")
    )

    urls: list[str] = []
    seen: set[str] = set()
    max_pages = 10  # belt-and-braces guard so we never loop forever

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(user_agent=user_agent)
        page = context.new_page()
        try:
            page.goto(start_url, wait_until="domcontentloaded", timeout=30_000)
        except PWTimeout:
            log.warning("portals.playwright.goto_timeout", url=start_url)
            browser.close()
            return []

        for page_idx in range(max_pages):
            # Let dynamic JS hydrate the list.
            try:
                page.wait_for_selector(job_list_sel, timeout=10_000)
            except PWTimeout:
                log.info(
                    "portals.playwright.no_jobs_on_page",
                    company=target["name"],
                    page=page_idx,
                )
                break

            anchors = page.query_selector_all(job_list_sel)
            for a in anchors:
                href = a.get_attribute("href")
                if not href:
                    continue
                url = _absolute_url(start_url, href)
                if url in seen:
                    continue
                seen.add(url)
                urls.append(url)
                if len(urls) >= limit:
                    break

            if len(urls) >= limit:
                break

            # Pagination
            if not pagination_sel:
                break
            next_btn = page.query_selector(pagination_sel)
            if not next_btn:
                break
            try:
                with page.expect_navigation(
                    wait_until="domcontentloaded", timeout=15_000
                ):
                    next_btn.click()
            except PWTimeout:
                # Workday often re-renders without a full navigation.
                page.wait_for_timeout(1500)

        browser.close()

    return urls[:limit]


def _absolute_url(base: str, href: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, href)


# ── De-dupe + ingest ─────────────────────────────────────────────────

def _ingest_many(urls: list[str], *, company: str) -> tuple[list[int], int]:
    from . import storage
    storage.init_db()

    new_ids: list[int] = []
    dups = 0
    for url in urls:
        for s in storage.session():
            existing = s.query(storage.Job).filter_by(url=url).one_or_none()
            if existing:
                dups += 1
                break
        else:
            try:
                job_id = ingest.ingest(url, source_kind="url")
                new_ids.append(job_id)
            except Exception as e:
                log.warning("ingest.failed", url=url, err=str(e))
    return new_ids, dups
