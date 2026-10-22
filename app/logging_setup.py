"""Structured JSON logs with correlation id, job id and secret redaction."""
import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)

_REDACT_PATTERNS = [
    re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+"),  # JWTs
    re.compile(r"(?i)\b(bearer|ghost)\s+[\w\-.:=]{16,}"),  # Authorization header values (token-shaped only)
    re.compile(r"(?i)(password|secret|token|api_key|apikey)([\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"),
]
_known_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Any secret resolved at runtime is registered so it can never appear in a log line."""
    if value and len(value) >= 6:
        _known_secrets.add(value)


def redact(text: str) -> str:
    for s in _known_secrets:
        if s in text:
            text = text.replace(s, "[REDACTED]")
    for pat in _REDACT_PATTERNS:
        if pat.groups >= 2:
            text = pat.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
        elif pat.groups == 1:
            text = pat.sub(lambda m: f"{m.group(1)} [REDACTED]", text)
        else:
            text = pat.sub("[REDACTED]", text)
    return text


class JsonFormatter(logging.Formatter):
    _std = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id_var.get(),
            "job_id": job_id_var.get(),
        }
        for k, v in record.__dict__.items():
            if k not in self._std and not k.startswith("_"):
                payload[f"extra_{k}" if k in payload else k] = v  # never overwrite the reserved fields
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str))


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "botocore", "boto3", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # uvicorn access logs go through our formatter too
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
