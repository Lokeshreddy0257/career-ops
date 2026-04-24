"""Thin abstraction over Anthropic / OpenAI chat completions with
structured-output (JSON) support.
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel

from .config import settings


T = TypeVar("T", bound=BaseModel)


def complete_json(
    prompt: str,
    *,
    system: str | None = None,
    schema: type[T],
    temperature: float = 0.0,
) -> T:
    """Call the configured LLM and parse the response into `schema`.

    Both providers are instructed to emit JSON only. A lenient parser
    tolerates leading/trailing prose by extracting the first top-level
    JSON object / array.
    """
    s = settings()
    raw = _call_provider(prompt, system=system, temperature=temperature, provider=s.llm_provider)
    obj = _extract_json(raw)
    return schema.model_validate(obj)


def complete_text(
    prompt: str,
    *,
    system: str | None = None,
    temperature: float = 0.3,
) -> str:
    s = settings()
    return _call_provider(prompt, system=system, temperature=temperature, provider=s.llm_provider)


# ── Providers ────────────────────────────────────────────────────────

def _call_provider(prompt: str, *, system: str | None, temperature: float, provider: str) -> str:
    s = settings()
    resolved = provider
    if provider == "auto":
        if s.gemini_api_key:
            resolved = "gemini"
        elif s.anthropic_api_key:
            resolved = "anthropic"
        elif s.openai_api_key:
            resolved = "openai"
        else:
            raise ValueError("No API key found. Set GEMINI_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY.")
    if resolved == "gemini":
        return _call_gemini(prompt, system=system, temperature=temperature)
    if resolved == "anthropic":
        return _call_anthropic(prompt, system=system, temperature=temperature)
    if resolved == "openai":
        return _call_openai(prompt, system=system, temperature=temperature)
    raise ValueError(f"Unknown llm_provider: {resolved}")


def _call_gemini(prompt: str, *, system: str | None, temperature: float) -> str:
    import google.generativeai as genai

    s = settings()
    genai.configure(api_key=s.gemini_api_key)
    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    model = genai.GenerativeModel(s.gemini_model)
    response = model.generate_content(
        full_prompt,
        generation_config=genai.GenerationConfig(temperature=temperature),
    )
    return response.text


def _call_anthropic(prompt: str, *, system: str | None, temperature: float) -> str:
    from anthropic import Anthropic

    s = settings()
    client = Anthropic(api_key=s.anthropic_api_key) if s.anthropic_api_key else Anthropic()
    msg = client.messages.create(
        model=s.anthropic_model,
        max_tokens=4096,
        temperature=temperature,
        system=system or "You are a precise assistant. Respond with JSON only when a schema is implied.",
        messages=[{"role": "user", "content": prompt}],
    )
    # Anthropic returns a list of content blocks
    return "".join(block.text for block in msg.content if block.type == "text")


def _call_openai(prompt: str, *, system: str | None, temperature: float) -> str:
    from openai import OpenAI

    s = settings()
    client = OpenAI(api_key=s.openai_api_key) if s.openai_api_key else OpenAI()
    resp = client.chat.completions.create(
        model=s.openai_model,
        temperature=temperature,
        messages=[
            *([{"role": "system", "content": system}] if system else []),
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or ""


# ── JSON extraction ──────────────────────────────────────────────────

def _extract_json(raw: str) -> Any:
    raw = raw.strip()
    # fast path: pure JSON
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # find the first balanced { … } block
    start = raw.find("{")
    if start < 0:
        raise ValueError(f"No JSON object in LLM response: {raw[:400]!r}")
    depth = 0
    for i, ch in enumerate(raw[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(raw[start:i + 1])
    raise ValueError(f"Unbalanced JSON in LLM response: {raw[:400]!r}")
