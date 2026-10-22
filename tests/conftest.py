"""Test setup: SQLite + local storage + the mock platform server on a real port (so timeouts are real)."""
import os
import socket
import tempfile
import threading
import time

import httpx
import pytest

_tmp = tempfile.mkdtemp(prefix="ltn-test-")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


MOCK_PORT = _free_port()
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"
API_PORT = _free_port()  # the API also runs on a real port so certification can fetch pages we host (feeds)
API_URL = f"http://127.0.0.1:{API_PORT}"

os.environ.update({
    "DATABASE_URL": f"sqlite:///{_tmp}/test.db",
    "STORAGE_BACKEND": "local",
    "STORAGE_LOCAL_DIR": f"{_tmp}/storage",
    "PUBLIC_BASE_URL": "http://testserver",
    "INTERNAL_BASE_URL": API_URL,
    "SIMULATION_MODE": "true",
    "MOCK_PLATFORM_URL": MOCK_URL,
    "JOB_EXECUTOR": "sync",
    "CONNECTOR_HTTP_TIMEOUT_S": "1",
    "MAX_INLINE_BACKOFF_S": "3",
    "RATE_LIMIT_PER_MINUTE": "0",
    "API_TOKEN": "",
    "CALLBACK_SIGNING_SECRET": "test-callback-secret",
    "REQUIRE_CERTIFICATION": "false",  # certification gating has its own tests
    "OWN_DOMAINS": "creatorstudio.example",
    "MOCK_WEBHOOK_SECRET": "partner-test-secret",
    "ALLOW_INTERNAL_HOSTS": "true",  # the mock platforms live on 127.0.0.1
    "LOG_LEVEL": "INFO",
})


@pytest.fixture(scope="session", autouse=True)
def mock_server():
    import uvicorn
    from mock_platforms.app import app as mock_app

    from app.main import app as api_app

    servers = []
    for asgi, port, url in ((mock_app, MOCK_PORT, MOCK_URL), (api_app, API_PORT, API_URL)):
        server = uvicorn.Server(uvicorn.Config(asgi, host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        servers.append(server)
        for _ in range(100):
            try:
                if httpx.get(f"{url}/health").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
    yield MOCK_URL
    for server in servers:
        server.should_exit = True


class Mock:
    url = MOCK_URL

    def arm(self, **fault) -> None:
        httpx.post(f"{MOCK_URL}/__admin/faults", json=fault).raise_for_status()

    def posts(self, platform: str, instance: str) -> list[dict]:
        return httpx.get(f"{MOCK_URL}/__admin/posts", params={"platform": platform, "instance": instance}).json()["posts"]

    def calls(self) -> int:
        return httpx.get(f"{MOCK_URL}/__admin/state").json()["calls"]

    def callbacks(self) -> list[dict]:
        return httpx.get(f"{MOCK_URL}/__admin/callback-sink").json()["callbacks"]


@pytest.fixture
def mock(mock_server) -> Mock:
    return Mock()


@pytest.fixture(autouse=True)
def _clean_state(mock_server):
    from app.connectors import http as chttp
    from app.connectors.lemmy import connector as lemmy_conn
    from app.connectors.peertube import connector as peertube_conn

    httpx.post(f"{MOCK_URL}/__admin/reset")
    chttp.breaker.reset()
    chttp.limiter.reset()
    lemmy_conn._jwt_cache.clear()
    peertube_conn._tokens.clear()
    yield


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        yield c
