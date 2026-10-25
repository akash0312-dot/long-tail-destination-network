from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app import __version__
from app.api import health, routes
from app.config import get_settings
from app.connectors import router as connector_router
from app.connectors.registry import all_connectors
from app.db import init_engine
from app.errors import install_error_handlers
from app.logging_setup import setup_logging
from app.middleware import PROTECTED_PREFIXES, PUBLIC_PREFIXES, IdempotencyMiddleware, RequestContextMiddleware

BEARER = "bearerAuth"
IDEMPOTENCY_KEY = "IdempotencyKey"


def openapi_with_auth(app: FastAPI):
    """The token check and the Idempotency-Key check live in middleware, so declare both in the spec: /docs gets an
    Authorize button, every protected operation says it needs the bearer token, and every POST under /api/v1/ has
    an Idempotency-Key field (so "Try it out" works there too)."""
    def build():
        if app.openapi_schema:
            return app.openapi_schema
        spec = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
        components = spec.setdefault("components", {})
        components["securitySchemes"] = {BEARER: {
            "type": "http", "scheme": "bearer",
            "description": "The API token (`API_TOKEN`). Required when the service runs with a token, as the live "
                           "demo does; not needed locally."}}
        components["parameters"] = {IDEMPOTENCY_KEY: {
            "name": "Idempotency-Key", "in": "header", "required": True,
            "schema": {"type": "string", "minLength": 1, "maxLength": 200},
            "description": "Any unique value, for example a UUID. Sending the same request again with the same key "
                           "returns the first response instead of doing the work twice; the same key with a "
                           "different body is refused (409 IDEMPOTENCY_KEY_CONFLICT)."}}
        for path, operations in spec["paths"].items():
            protected = path.startswith(PROTECTED_PREFIXES) and not path.startswith(PUBLIC_PREFIXES)
            for method, operation in operations.items():
                if protected:
                    operation["security"] = [{BEARER: []}]
                if method == "post" and path.startswith("/api/v1/"):  # the rule IdempotencyMiddleware enforces
                    operation.setdefault("parameters", []).append(
                        {"$ref": f"#/components/parameters/{IDEMPOTENCY_KEY}"})
        app.openapi_schema = spec
        return spec
    return build


def create_app() -> FastAPI:
    s = get_settings()
    setup_logging(s.log_level)
    init_engine()
    import app.services  # noqa: F401  (register job handlers)
    all_connectors()  # fail fast on a bad manifest

    app = FastAPI(title="Long-Tail Destination Network", version=__version__,
                  description="Connector service for long-tail destinations: contract connectors, posting rules, "
                              "certification, catalogue and tailoring.",
                  swagger_ui_parameters={"persistAuthorization": True})
    install_error_handlers(app)
    app.add_middleware(IdempotencyMiddleware)  # inner
    app.add_middleware(RequestContextMiddleware)  # outer: correlation id, auth, rate limit
    app.include_router(health.router)
    app.include_router(routes.router)
    app.include_router(connector_router.router)
    app.openapi = openapi_with_auth(app)
    return app


app = create_app()
