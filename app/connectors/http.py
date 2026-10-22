"""Shared HTTP client for every connector.

- client-side rate limit from the manifest (per connection)
- 429: wait for the platform's reset time (Retry-After / X-RateLimit-Reset); long waits are returned as
  PLATFORM_RATE_LIMIT with retry_after_s so the runtime reschedules instead of hammering
- circuit breaker: after N consecutive failures (timeouts, 5xx, connection errors) stop calling for a cooldown
- non-2xx responses mapped to shared error codes (connector-specific mapping first, then manifest error_map)
"""
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from email.utils import parsedate_to_datetime

import httpx

from app.config import get_settings
from app.errors import AppError, ErrorCode

log = logging.getLogger("app.connectors.http")

DEFAULT_RATE_LIMIT_WAIT_S = 30.0


class CircuitBreaker:
    def __init__(self):
        self._lock = threading.Lock()
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def check(self, key: str) -> None:
        with self._lock:
            until = self._open_until.get(key)
            if until and time.monotonic() < until:
                raise AppError(ErrorCode.PLATFORM_UNAVAILABLE,
                               "Circuit open: platform failed repeatedly; calls paused",
                               {"circuit_open": True, "retry_after_s": round(until - time.monotonic(), 1)})

    def success(self, key: str) -> None:
        with self._lock:
            self._failures[key] = 0
            self._open_until.pop(key, None)

    def failure(self, key: str) -> int:
        s = get_settings()
        with self._lock:
            n = self._failures.get(key, 0) + 1
            self._failures[key] = n
            if n >= s.circuit_breaker_threshold:
                self._open_until[key] = time.monotonic() + s.circuit_breaker_cooldown_s
                log.warning("circuit opened", extra={"breaker_key": key, "failures": n})
            return n

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._open_until.clear()


class RateLimiter:
    """Sliding window per connection, configured from the manifest's rate_limits.per_connection."""

    def __init__(self):
        self._lock = threading.Lock()
        self._windows: dict[str, deque] = {}

    def acquire(self, key: str, requests: int, per_seconds: int) -> None:
        max_wait = get_settings().max_inline_backoff_s
        while True:
            with self._lock:
                now = time.monotonic()
                win = self._windows.setdefault(key, deque())
                while win and now - win[0] >= per_seconds:
                    win.popleft()
                if len(win) < requests:
                    win.append(now)
                    return
                wait = per_seconds - (now - win[0])
            if wait > max_wait:
                raise AppError(ErrorCode.PLATFORM_RATE_LIMIT, "Client-side rate limit for this connection reached",
                               {"source": "client_limit", "retry_after_s": round(wait, 1)})
            time.sleep(wait)

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()


breaker = CircuitBreaker()
limiter = RateLimiter()


def parse_reset_seconds(resp: httpx.Response) -> tuple[float, str]:
    """How long the platform asks us to wait, and which header said so."""
    h = resp.headers
    if "retry-after" in h:
        v = h["retry-after"].strip()
        try:
            return max(0.0, float(v)), "Retry-After"
        except ValueError:
            try:
                return max(0.0, parsedate_to_datetime(v).timestamp() - time.time()), "Retry-After"
            except Exception:
                pass
    for name in ("x-ratelimit-reset", "ratelimit-reset", "x-rate-limit-reset"):
        if name in h:
            try:
                v = float(h[name])
            except ValueError:
                continue
            # large values are epoch timestamps, small ones are deltas
            return (max(0.0, v - time.time()) if v > 1_000_000_000 else max(0.0, v)), name
    return DEFAULT_RATE_LIMIT_WAIT_S, "default"


class PlatformHttp:
    def __init__(self, *, connector_key: str, base_url: str, rate_key: str, rate_limit,
                 error_map: dict[str, ErrorCode], map_error: Callable[[httpx.Response], AppError | None] | None = None,
                 headers: dict | None = None):
        s = get_settings()
        self.connector_key = connector_key
        self.base_url = base_url.rstrip("/")
        self.breaker_key = f"{connector_key}|{self.base_url}"
        self.rate_key = rate_key
        self.rate_limit = rate_limit
        self.error_map = error_map
        self.map_error = map_error
        self.client = httpx.Client(timeout=s.connector_http_timeout_s, headers=headers or {}, follow_redirects=False)

    def close(self):
        self.client.close()

    def request(self, method: str, path: str, *, op: str, allow_status: tuple[int, ...] = (),
                **kwargs) -> httpx.Response:
        s = get_settings()
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        attempt = 0
        while True:
            breaker.check(self.breaker_key)
            limiter.acquire(self.rate_key, self.rate_limit.requests, self.rate_limit.per_seconds)
            started = time.monotonic()
            try:
                resp = self.client.request(method, url, **kwargs)
            except httpx.TimeoutException:
                n = breaker.failure(self.breaker_key)
                log.warning("platform timeout", extra={"connector": self.connector_key, "op": op, "failures": n})
                raise AppError(ErrorCode.PLATFORM_UNAVAILABLE, f"Platform did not answer in time during {op}",
                               {"op": op, "timeout": True, "consecutive_failures": n})
            except httpx.TransportError as e:
                n = breaker.failure(self.breaker_key)
                raise AppError(ErrorCode.PLATFORM_UNAVAILABLE, f"Could not reach platform during {op}",
                               {"op": op, "transport_error": type(e).__name__, "consecutive_failures": n})
            elapsed_ms = round((time.monotonic() - started) * 1000)
            log.info("platform call", extra={"connector": self.connector_key, "op": op, "method": method,
                                              "status": resp.status_code, "elapsed_ms": elapsed_ms})

            if resp.status_code == 429:
                wait, source = parse_reset_seconds(resp)
                if wait <= s.max_inline_backoff_s and attempt < s.max_rate_limit_retries:
                    attempt += 1
                    log.warning("rate limited, backing off", extra={"connector": self.connector_key, "op": op,
                                                                    "wait_s": wait, "reset_source": source})
                    time.sleep(wait)
                    continue
                raise AppError(ErrorCode.PLATFORM_RATE_LIMIT, "Platform rate limit reached; retry after reset time",
                               {"op": op, "retry_after_s": round(wait, 1), "reset_source": source,
                                "retry_at_epoch": round(time.time() + wait)})
            if resp.status_code >= 500 and resp.status_code not in allow_status:
                n = breaker.failure(self.breaker_key)
                raise AppError(ErrorCode.PLATFORM_UNAVAILABLE, f"Platform error {resp.status_code} during {op}",
                               {"op": op, "status": resp.status_code, "consecutive_failures": n})
            breaker.success(self.breaker_key)
            if resp.is_success or resp.status_code in allow_status:
                return resp
            raise self._map(resp, op)

    def _map(self, resp: httpx.Response, op: str) -> AppError:
        if self.map_error:
            err = self.map_error(resp)
            if err:
                err.details.setdefault("op", op)
                err.details.setdefault("status", resp.status_code)
                return err
        message = _platform_message(resp)
        code = self.error_map.get(str(resp.status_code))
        if code is None:
            code = (ErrorCode.CONNECTION_AUTH_EXPIRED if resp.status_code in (401, 403)
                    else ErrorCode.PLATFORM_CONTENT_REJECTED)
        return AppError(code, message or f"Platform returned {resp.status_code}",
                        {"op": op, "status": resp.status_code, "platform_message": message})


def _platform_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:
        return resp.text[:300]
    if isinstance(data, dict):
        if isinstance(data.get("errors"), list) and data["errors"]:
            e = data["errors"][0]
            if isinstance(e, dict):
                return " ".join(str(x) for x in (e.get("message"), e.get("context")) if x)
            return str(e)
        for k in ("error", "message", "detail"):
            if k in data:
                return str(data[k])
    return str(data)[:300]
