"""Error codes and the JSON error envelope: {"error": {"code", "message", "retryable", "details"}}."""
from enum import StrEnum
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ErrorCode(StrEnum):
    # Shared connector codes (Appendix A)
    DEST_SPEC_VIOLATION = "DEST_SPEC_VIOLATION"
    DEST_RULE_FREQUENCY_CAP = "DEST_RULE_FREQUENCY_CAP"
    CONNECTION_AUTH_EXPIRED = "CONNECTION_AUTH_EXPIRED"
    PLATFORM_RATE_LIMIT = "PLATFORM_RATE_LIMIT"
    PLATFORM_QUOTA_EXHAUSTED = "PLATFORM_QUOTA_EXHAUSTED"
    PLATFORM_CONTENT_REJECTED = "PLATFORM_CONTENT_REJECTED"
    PLATFORM_UNAVAILABLE = "PLATFORM_UNAVAILABLE"
    CONNECTOR_DISABLED = "CONNECTOR_DISABLED"
    # extensions (documented in README)
    DEST_RULE_SELF_PROMO = "DEST_RULE_SELF_PROMO"
    DEST_RULE_LINK_POLICY = "DEST_RULE_LINK_POLICY"
    DEST_RULE_DISCLOSURE_REQUIRED = "DEST_RULE_DISCLOSURE_REQUIRED"
    DEST_RULE_LANGUAGE_MISMATCH = "DEST_RULE_LANGUAGE_MISMATCH"
    DEST_RULE_AUTOMATION_NOT_ALLOWED = "DEST_RULE_AUTOMATION_NOT_ALLOWED"
    DEST_NOT_CERTIFIED = "DEST_NOT_CERTIFIED"
    CERTIFICATION_FAILED = "CERTIFICATION_FAILED"
    AI_PROVIDER_ERROR = "AI_PROVIDER_ERROR"
    AI_REFUSED = "AI_REFUSED"
    RULES_PAGE_UNAVAILABLE = "RULES_PAGE_UNAVAILABLE"
    CATALOGUE_INPUT_INVALID = "CATALOGUE_INPUT_INVALID"
    PLATFORM_SCHEMA_CHANGED = "PLATFORM_SCHEMA_CHANGED"
    CONNECTOR_NOT_FOUND = "CONNECTOR_NOT_FOUND"
    METHOD_NOT_SUPPORTED = "METHOD_NOT_SUPPORTED"
    # API codes
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    RATE_LIMITED = "RATE_LIMITED"
    IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
    IDEMPOTENCY_KEY_CONFLICT = "IDEMPOTENCY_KEY_CONFLICT"
    IDEMPOTENCY_REQUEST_IN_PROGRESS = "IDEMPOTENCY_REQUEST_IN_PROGRESS"
    SECRET_UNAVAILABLE = "SECRET_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# code -> (http status, retryable)
ERROR_SPEC: dict[ErrorCode, tuple[int, bool]] = {
    ErrorCode.DEST_SPEC_VIOLATION: (422, False),
    ErrorCode.DEST_RULE_FREQUENCY_CAP: (409, True),
    ErrorCode.CONNECTION_AUTH_EXPIRED: (401, False),
    ErrorCode.PLATFORM_RATE_LIMIT: (429, True),
    ErrorCode.PLATFORM_QUOTA_EXHAUSTED: (429, True),
    ErrorCode.PLATFORM_CONTENT_REJECTED: (422, False),
    ErrorCode.PLATFORM_UNAVAILABLE: (503, True),
    ErrorCode.CONNECTOR_DISABLED: (503, False),
    ErrorCode.DEST_RULE_SELF_PROMO: (422, False),
    ErrorCode.DEST_RULE_LINK_POLICY: (422, False),
    ErrorCode.DEST_RULE_DISCLOSURE_REQUIRED: (422, False),
    ErrorCode.DEST_RULE_LANGUAGE_MISMATCH: (422, False),
    ErrorCode.DEST_RULE_AUTOMATION_NOT_ALLOWED: (422, False),
    ErrorCode.DEST_NOT_CERTIFIED: (409, False),
    ErrorCode.CERTIFICATION_FAILED: (422, False),
    ErrorCode.AI_PROVIDER_ERROR: (502, True),
    ErrorCode.AI_REFUSED: (422, False),
    ErrorCode.RULES_PAGE_UNAVAILABLE: (422, False),
    ErrorCode.CATALOGUE_INPUT_INVALID: (400, False),
    ErrorCode.PLATFORM_SCHEMA_CHANGED: (502, False),
    ErrorCode.CONNECTOR_NOT_FOUND: (404, False),
    ErrorCode.METHOD_NOT_SUPPORTED: (405, False),
    ErrorCode.VALIDATION_ERROR: (400, False),
    ErrorCode.NOT_FOUND: (404, False),
    ErrorCode.UNAUTHORIZED: (401, False),
    ErrorCode.RATE_LIMITED: (429, True),
    ErrorCode.IDEMPOTENCY_KEY_REQUIRED: (400, False),
    ErrorCode.IDEMPOTENCY_KEY_CONFLICT: (409, False),
    ErrorCode.IDEMPOTENCY_REQUEST_IN_PROGRESS: (409, True),
    ErrorCode.SECRET_UNAVAILABLE: (500, False),
    ErrorCode.INTERNAL_ERROR: (500, True),
}


class AppError(Exception):
    def __init__(self, code: ErrorCode, message: str, details: dict[str, Any] | None = None,
                 retryable: bool | None = None):
        super().__init__(message)
        self.code = ErrorCode(code)
        self.message = message
        self.details = details or {}
        status, default_retry = ERROR_SPEC[self.code]
        self.status_code = status
        self.retryable = default_retry if retryable is None else retryable

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code.value, "message": self.message,
                          "retryable": self.retryable, "details": self.details}}


def error_body(code: ErrorCode, message: str, details: dict | None = None) -> dict:
    return AppError(code, message, details).to_dict()


def install_error_handlers(app) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        headers = {}
        if "retry_after_s" in exc.details:
            headers["Retry-After"] = str(int(max(1, exc.details["retry_after_s"])))
        return JSONResponse(exc.to_dict(), status_code=exc.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        errs = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(error_body(ErrorCode.VALIDATION_ERROR, "Request validation failed", {"errors": errs}),
                            status_code=400)

    @app.exception_handler(404)
    async def _not_found(_: Request, __):
        return JSONResponse(error_body(ErrorCode.NOT_FOUND, "Route not found"), status_code=404)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        import logging
        logging.getLogger("app.errors").exception("unhandled error")
        return JSONResponse(error_body(ErrorCode.INTERNAL_ERROR, "Internal error", {"type": type(exc).__name__}),
                            status_code=500)
