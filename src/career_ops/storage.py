"""SQLAlchemy models + session helpers. Default DB is SQLite at data/career.db."""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Iterator

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    h1b_history: Mapped[str] = mapped_column(String(32), default="unknown")
    stage: Mapped[str | None] = mapped_column(String(64), default=None)
    portal_url: Mapped[str | None] = mapped_column(String(512), default=None)
    portal_type: Mapped[str | None] = mapped_column(String(64), default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    jobs: Mapped[list["Job"]] = relationship(back_populates="company")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    location: Mapped[str | None] = mapped_column(String(200), default=None)
    url: Mapped[str | None] = mapped_column(String(1024), default=None, unique=True)
    jd_text: Mapped[str] = mapped_column(Text)
    parsed_requirements: Mapped[list | dict | None] = mapped_column(JSON, default=None)
    source_kind: Mapped[str] = mapped_column(String(32), default="text")
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    company: Mapped[Company] = relationship(back_populates="jobs")
    evaluations: Mapped[list["EvaluationRow"]] = relationship(back_populates="job")


class EvaluationRow(Base):
    __tablename__ = "evaluations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    rubric_version: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    scores_json: Mapped[list | dict] = mapped_column(JSON)
    weighted_total: Mapped[float] = mapped_column(Float)
    percent: Mapped[float] = mapped_column(Float)
    grade: Mapped[str] = mapped_column(String(4))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    job: Mapped[Job] = relationship(back_populates="evaluations")


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    status: Mapped[str] = mapped_column(
        String(32), default="interested"
    )  # interested/applied/phone/onsite/offer/rejected
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    cv_artifact_path: Mapped[str | None] = mapped_column(String(512), default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)


# ── Session plumbing ─────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _engine():
    return create_engine(settings().database_url, future=True)


@lru_cache(maxsize=1)
def _session_factory():
    return sessionmaker(bind=_engine(), expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(_engine())


def session() -> Iterator[Session]:
    """Generator yielding a session; caller handles context with `with`."""
    Session_ = _session_factory()
    with Session_() as s:
        yield s


def get_or_create_company(s: Session, name: str, **fields) -> Company:
    company = s.query(Company).filter_by(name=name).one_or_none()
    if company:
        return company
    company = Company(name=name, **fields)
    s.add(company)
    s.flush()
    return company
