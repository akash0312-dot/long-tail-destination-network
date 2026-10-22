"""Provision the local test instances and seed the catalogue. Safe to run repeatedly.

Ghost: create owner (first run) -> staff session -> 'Long-Tail Network' integration -> admin API key written to
       /run/app-secrets/ghost_admin_api_key (never printed).
Lemmy: admin is created by lemmy.hjson setup -> log in -> ensure test community exists.
Then register connections (secret refs only) and destinations through our own API with fixed Idempotency-Keys.
"""
import logging
import os
import secrets
import sys
import time
from pathlib import Path

import httpx

from app.logging_setup import register_secret, setup_logging

log = logging.getLogger("bootstrap")
SECRETS_DIR = Path(os.environ.get("SECRETS_DIR", "/run/app-secrets"))
API = os.environ.get("API_URL", "http://api:8000")
GHOST = os.environ.get("GHOST_URL", "http://ghost:2368")
LEMMY = os.environ.get("LEMMY_URL", "http://lemmy:8536")
GHOST_EMAIL = os.environ.get("GHOST_ADMIN_EMAIL", "bot@longtail.test")
LEMMY_USER = os.environ.get("LEMMY_ADMIN_USERNAME", "longtail_admin")
LEMMY_PASSWORD = os.environ.get("LEMMY_ADMIN_PASSWORD", "local-only-lemmy-pass-123")
LEMMY_COMMUNITY = os.environ.get("LEMMY_COMMUNITY", "longtail_test")


def ghost_password() -> str:
    """Use GHOST_ADMIN_PASSWORD if set; otherwise generate one on first run and keep it in the secrets volume."""
    if os.environ.get("GHOST_ADMIN_PASSWORD"):
        return os.environ["GHOST_ADMIN_PASSWORD"]
    p = SECRETS_DIR / "ghost_admin_password"
    if not p.exists():
        SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        p.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        p.chmod(0o600)
    return p.read_text(encoding="utf-8").strip()


GHOST_PASSWORD = ghost_password()
for s in (GHOST_PASSWORD, LEMMY_PASSWORD):
    register_secret(s)


def wait_for(url: str, name: str, timeout_s: int = 300) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=5).status_code < 500:
                log.info(f"{name} is up")
                return
        except httpx.HTTPError:
            pass
        time.sleep(3)
    raise SystemExit(f"{name} did not start within {timeout_s}s ({url})")


def _check(r: httpx.Response, what: str) -> httpx.Response:
    """Fail with the platform's own error message (never the request body, which may hold passwords)."""
    if r.is_success:
        return r
    try:
        errs = r.json().get("errors") or [r.json()]
        msg = "; ".join(str(e.get("message") or e.get("error") or e) for e in errs)
    except Exception:
        msg = r.text[:300]
    raise SystemExit(f"{what} failed: HTTP {r.status_code}: {msg}")


def provision_ghost() -> None:
    api = f"{GHOST}/ghost/api/admin"
    headers = {"Origin": GHOST, "Accept-Version": "v5.0"}
    with httpx.Client(headers=headers, timeout=30) as c:
        setup = c.get(f"{api}/authentication/setup/").json()
        if not setup["setup"][0]["status"]:
            r = c.post(f"{api}/authentication/setup/", json={"setup": [{
                "name": "Long-Tail Bot", "email": GHOST_EMAIL, "password": GHOST_PASSWORD,
                "blogTitle": "Long-Tail Test Blog"}]})
            _check(r, "request")
            log.info("ghost owner created")
        r = c.post(f"{api}/session/", json={"username": GHOST_EMAIL, "password": GHOST_PASSWORD})
        _check(r, "request")
        integrations = c.get(f"{api}/integrations/", params={"include": "api_keys", "limit": "all"}).json()
        integ = next((i for i in integrations.get("integrations", []) if i["name"] == "Long-Tail Network"), None)
        if integ is None:
            r = c.post(f"{api}/integrations/", params={"include": "api_keys"},
                       json={"integrations": [{"name": "Long-Tail Network"}]})
            _check(r, "request")
            integ = r.json()["integrations"][0]
            log.info("ghost integration created")
        key = next(k for k in integ["api_keys"] if k["type"] == "admin")
        # some Ghost 5 releases return the full "<id>:<hex>" in `secret`, older ones only the hex part
        full_key = key["secret"] if ":" in key["secret"] else f"{key['id']}:{key['secret']}"
        register_secret(full_key)
        _write_secret("ghost_admin_api_key", full_key)


def provision_lemmy() -> None:
    with httpx.Client(timeout=30) as c:
        r = c.post(f"{LEMMY}/api/v3/user/login", json={"username_or_email": LEMMY_USER, "password": LEMMY_PASSWORD})
        _check(r, "request")
        token = r.json()["jwt"]
        register_secret(token)
        c.headers["Authorization"] = f"Bearer {token}"
        r = c.get(f"{LEMMY}/api/v3/community", params={"name": LEMMY_COMMUNITY})
        if r.status_code != 200:
            r = c.post(f"{LEMMY}/api/v3/community", json={"name": LEMMY_COMMUNITY,
                                                         "title": "Long-tail connector test community"})
            _check(r, "request")
            log.info("lemmy community created")


def _write_secret(name: str, value: str) -> None:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    p = SECRETS_DIR / name
    p.write_text(value, encoding="utf-8")
    p.chmod(0o644)
    log.info("secret written", extra={"secret_name": name})


def _post(c: httpx.Client, path: str, key: str, body: dict) -> dict | None:
    r = c.post(f"{API}{path}", json=body, headers={"Idempotency-Key": key})
    if r.status_code in (200, 201):
        return r.json()
    log.info("seed skipped", extra={"path": path, "status": r.status_code,
                                    "code": r.json().get("error", {}).get("code")})
    return None


def seed_catalogue() -> None:
    headers = {}
    if os.environ.get("API_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['API_TOKEN']}"
    with httpx.Client(timeout=30, headers=headers) as c:
        ghost_conn = _post(c, "/api/v1/connections", "seed-conn-ghost-local", {
            "connector_key": "ghost", "name": "Local Ghost admin integration", "auth_type": "api_key",
            "secret_refs": {"admin_api_key": f"file:{SECRETS_DIR}/ghost_admin_api_key"}})
        lemmy_conn = _post(c, "/api/v1/connections", "seed-conn-lemmy-local", {
            "connector_key": "lemmy", "name": "Local Lemmy test account", "auth_type": "api_key",
            "secret_refs": {"password": "env:LEMMY_ADMIN_PASSWORD"}, "params": {"username": LEMMY_USER}})
        if ghost_conn:
            _post(c, "/api/v1/destinations", "seed-dest-ghost-local", {
                "name": "Long-Tail Test Blog (local Ghost)", "url": GHOST, "connector_key": "ghost",
                "connection_id": ghost_conn["id"], "category": "blog", "audience_tags": ["creators", "ai"],
                "self_promo_allowed": True, "automation_allowed": True})
        if lemmy_conn:
            _post(c, "/api/v1/destinations", "seed-dest-lemmy-local", {
                "name": f"!{LEMMY_COMMUNITY} (local Lemmy)", "url": f"{LEMMY}/c/{LEMMY_COMMUNITY}",
                "connector_key": "lemmy", "params": {"base_url": LEMMY, "community": LEMMY_COMMUNITY},
                "connection_id": lemmy_conn["id"], "category": "forum", "audience_tags": ["ai", "founders"],
                "frequency_cap": {"posts": 1, "per_days": 7}, "self_promo_allowed": False,
                "automation_allowed": True})


def main() -> int:
    setup_logging("INFO")
    wait_for(f"{GHOST}/ghost/api/admin/site/", "ghost")
    wait_for(f"{LEMMY}/api/v3/site", "lemmy")
    provision_ghost()
    provision_lemmy()
    seed_catalogue()
    log.info("bootstrap complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
