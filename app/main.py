from fastapi import FastAPI

from app import __version__
from app.api import health, routes
from app.config import get_settings
from app.connectors import router as connector_router
from app.connectors.registry import all_connectors
from app.db import init_engine
from app.errors import install_error_handlers
from app.logging_setup import setup_logging
from app.middleware import IdempotencyMiddleware, RequestContextMiddleware


def create_app() -> FastAPI:
    s = get_settings()
    setup_logging(s.log_level)
    init_engine()
    import app.services  # noqa: F401  (register job handlers)
    all_connectors()  # fail fast on a bad manifest

    app = FastAPI(title="Long-Tail Destination Network", version=__version__,
                  description="Connector service for long-tail destinations: contract connectors, posting rules, "
                              "certification, catalogue and tailoring.")
    install_error_handlers(app)
    app.add_middleware(IdempotencyMiddleware)  # inner
    app.add_middleware(RequestContextMiddleware)  # outer: correlation id, auth, rate limit
    app.include_router(health.router)
    app.include_router(routes.router)
    app.include_router(connector_router.router)
    return app


app = create_app()
