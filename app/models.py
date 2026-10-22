"""Database tables. The destination row is the unit of configuration: a new forum = a new row."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Connection(Base):
    """Credentials for one account on one platform. Only secret *references* are stored (env:NAME / file:/path)."""
    __tablename__ = "connections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    connector_key: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200))
    auth_type: Mapped[str] = mapped_column(String(32))
    secret_refs: Mapped[dict] = mapped_column(JSON, default=dict)
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # non-secret: usernames, owner contact
    status: Mapped[str] = mapped_column(String(32), default="active")  # active | expired | revoked
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Destination(Base):
    """Appendix A destination record plus the instance params a connector needs."""
    __tablename__ = "destinations"
    __table_args__ = (UniqueConstraint("normalized_url", "platform_key", name="uq_destination_url_platform"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(1000))
    normalized_url: Mapped[str] = mapped_column(String(1000))
    platform_key: Mapped[str] = mapped_column(String(64))
    connector_key: Mapped[str] = mapped_column(String(64), index=True)
    surface: Mapped[str] = mapped_column(String(64), default="post")
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. {"base_url": ..., "community": ...}
    connection_id: Mapped[str | None] = mapped_column(ForeignKey("connections.id"), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    audience_tags: Mapped[list] = mapped_column(JSON, default=list)
    language: Mapped[str] = mapped_column(String(16), default="en")
    geo: Mapped[str | None] = mapped_column(String(64), nullable=True)
    est_audience: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rules_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    frequency_cap: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {"posts": 1, "per_days": 7}
    self_promo_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    link_policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {"allowed": true, "max_links": 2, ...}
    disclosure_required: Mapped[bool] = mapped_column(Boolean, default=False)
    automation_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    relevance_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    relevance_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    checks: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # reachability + enrichment from the import
    level: Mapped[str] = mapped_column(String(4), default="L0")  # L0..L4
    certification_status: Mapped[str] = mapped_column(String(32), default="uncertified")
    last_certified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Publication(Base):
    """Ledger of every attempt. The rules engine reads it for frequency caps."""
    __tablename__ = "publications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    destination_id: Mapped[str] = mapped_column(ForeignKey("destinations.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), index=True)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32))  # published | blocked | rescheduled | failed | deleted
    platform_post_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    live_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    rendition: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # latest normalised snapshot
    metrics_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    callback_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    scope: Mapped[str] = mapped_column(String(300), primary_key=True)  # "POST /api/v1/publications"
    request_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="in_progress")  # in_progress | done
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RuleProposal(Base):
    """Rules an AI step read from a community's rules page. Never applied until a person confirms them."""
    __tablename__ = "rule_proposals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    destination_id: Mapped[str] = mapped_column(ForeignKey("destinations.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed | confirmed | rejected
    source_url: Mapped[str] = mapped_column(String(1000))
    proposed: Mapped[dict] = mapped_column(JSON)
    applied: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    provider: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CertificationRun(Base):
    __tablename__ = "certification_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    destination_id: Mapped[str] = mapped_column(ForeignKey("destinations.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    evidence_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
