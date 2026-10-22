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


def openapi_with_auth(app: FastAPI):
    """The token check lives in middleware, so declare it in the spec: /docs gets an Authorize button and every
    protected operation says it needs the bearer token."""
    def build():
        if app.openapi_schema:
            return app.openapi_schema
        spec = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
        spec.setdefault("components", {})["securitySchemes"] = {BEARER: {
            "type": "http", "scheme": "bearer",
            "description": "The API token (`API_TOKEN`). Required when the service runs with a token, as the live "
                           "demo does; not needed locally."}}
        for path, operations in spec["paths"].items():
            if path.startswith(PROTECTED_PREFIXES) and not path.startswith(PUBLIC_PREFIXES):
                for operation in operations.values():
                    operation["security"] = [{BEARER: []}]
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
