"""Worker process: python -m app.worker"""
import logging
import time

from app.config import get_settings
from app.db import init_engine
from app.jobs import claim_next_job, run_job
from app.logging_setup import setup_logging


def main() -> None:
    s = get_settings()
    setup_logging(s.log_level)
    init_engine()
    import app.services  # noqa: F401  (register job handlers)
    from app.services.scheduler import recertify_due
    log = logging.getLogger("app.worker")
    log.info("worker started")
    next_tick = 0.0
    while True:
        if time.monotonic() >= next_tick:  # recertification scheduler runs inside the worker loop
            try:
                recertify_due()
            except Exception:
                log.exception("recertification scheduler failed")
            next_tick = time.monotonic() + s.scheduler_interval_s
        job_id = claim_next_job()
        if job_id is None:
            time.sleep(s.worker_poll_interval_s)
            continue
        run_job(job_id)


if __name__ == "__main__":
    main()
