"""Recertification scheduler: certified destinations are re-tested before their certification is 30 days old
(RECERTIFY_AFTER_DAYS, default 25). The worker calls recertify_due() every SCHEDULER_INTERVAL_S; it can also be
triggered with POST /api/v1/recertifications/run. A destination that fails is demoted, excluded from publishing and
recommendations, and gets an alert plus an open ticket (app/services/certification.py)."""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.jobs import create_job
from app.models import Destination, Job, utcnow

log = logging.getLogger("app.scheduler")


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=utcnow().tzinfo)


def recertify_due(now: datetime | None = None) -> dict:
    now = now or utcnow()
    cutoff = now - timedelta(days=get_settings().recertify_after_days)
    with session_scope() as db:
        pending = {j.payload.get("destination_id") for j in db.execute(
            select(Job).where(Job.kind == "certification", Job.status.in_(("queued", "running")))).scalars()}
        due = [(d.id, d.name, d.last_certified_at) for d in db.execute(
            select(Destination).where(Destination.certification_status == "certified",
                                      Destination.enabled.is_(True))).scalars()
               if d.last_certified_at is None or _aware(d.last_certified_at) <= cutoff]
    queued, skipped = [], []
    for dest_id, name, last in due:
        if dest_id in pending:
            skipped.append({"destination_id": dest_id, "reason": "certification already queued"})
            continue
        job = create_job("certification", {"destination_id": dest_id, "trigger": "scheduled_recertification"})
        queued.append({"destination_id": dest_id, "destination": name, "job_id": job.id,
                       "last_certified_at": last.isoformat() if last else None})
    summary = {"checked_at": now.isoformat(), "cutoff": cutoff.isoformat(), "due": len(due), "queued": queued,
               "skipped": skipped}
    if due:
        log.info("recertification run", extra={"due": len(due), "queued": len(queued)})
    return summary
