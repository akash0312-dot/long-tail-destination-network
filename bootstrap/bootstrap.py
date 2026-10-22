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


MOCK = os.environ.get("MOCK_PLATFORM_URL", "http://mock:9000")

# (key, connection, destination) for every Core connector type. Ghost and Lemmy are real local instances; the rest
# are served by the mock platform server. Each destination records its posting rules, which certification requires.
SEEDS = [
    ("ghost",
     {"name": "Local Ghost admin integration", "secret_refs": {"admin_api_key": f"file:{SECRETS_DIR}/ghost_admin_api_key"}},
     {"name": "Long-Tail Test Blog (local Ghost)", "url": GHOST, "category": "blog",
      "audience_tags": ["creators", "ai"], "self_promo_allowed": True,
      "frequency_cap": {"posts": 10, "per_days": 1}, "link_policy": {"allowed": True, "max_links": 10},
      "rules_url": f"{GHOST}/"}),
    ("lemmy",
     {"name": "Local Lemmy test account", "secret_refs": {"password": "env:LEMMY_ADMIN_PASSWORD"},
      "params": {"username": LEMMY_USER}},
     {"name": f"!{LEMMY_COMMUNITY} (local Lemmy)", "url": f"{LEMMY}/c/{LEMMY_COMMUNITY}",
      "params": {"base_url": LEMMY, "community": LEMMY_COMMUNITY}, "category": "forum",
      "audience_tags": ["ai", "founders"], "self_promo_allowed": False,
      "frequency_cap": {"posts": 1, "per_days": 7}, "link_policy": {"allowed": True, "max_links": 2},
      "rules_url": f"{LEMMY}/c/{LEMMY_COMMUNITY}"}),
    ("discourse",
     {"name": "Mock Discourse API key", "secret_refs": {"api_key": "env:MOCK_API_KEY"},
      "params": {"api_username": "longtail_bot"}},
     {"name": "Creator Ops Forum (local mock Discourse)", "url": f"{MOCK}/local/discourse-demo",
      "params": {"category_id": 4}, "category": "forum", "audience_tags": ["creators", "video"],
      "self_promo_allowed": False, "frequency_cap": {"posts": 1, "per_days": 3},
      "link_policy": {"allowed": True, "max_links": 1}, "rules_url": f"{MOCK}/local/discourse-demo/guidelines"}),
    ("devto",
     {"name": "Mock Forem API key", "secret_refs": {"api_key": "env:MOCK_API_KEY"}},
     {"name": "Dev community (local mock Forem)", "url": f"{MOCK}/local/forem-demo", "category": "developer_blog",
      "audience_tags": ["saas", "ai"], "self_promo_allowed": True, "frequency_cap": {"posts": 2, "per_days": 7},
      "link_policy": {"allowed": True}, "rules_url": f"{MOCK}/local/forem-demo/code-of-conduct"}),
    ("webhook",
     {"name": "Demo partner signing secret", "secret_refs": {"signing_secret": "env:PARTNER_WEBHOOK_SECRET"}},
     {"name": "Partner site demo (local mock partner)", "url": f"{MOCK}/partner/partner-demo", "category": "partner",
      "audience_tags": ["marketing"], "self_promo_allowed": True, "frequency_cap": {"posts": 5, "per_days": 1},
      "link_policy": {"allowed": True}, "rules_url": f"{MOCK}/partner/partner-demo/terms"}),
    ("assisted",
     None,
     {"name": "Show-and-tell board (assisted, a person posts)", "url": "https://news.example/submit",
      "params": {"submit_url_template": "https://news.example/submit?url={url}&title={title}"},
      "category": "aggregator", "audience_tags": ["founders"], "self_promo_allowed": False,
      "automation_allowed": False, "frequency_cap": {"posts": 1, "per_days": 30},
      "link_policy": {"allowed": True, "max_links": 1}, "rules_url": "https://news.example/guidelines"}),
    # Advanced connectors: our own hosted MRSS feed, and Hashnode / PeerTube on the mock platform server
    ("mrss",
     None,
     {"name": "Creator video feed (hosted MRSS)", "url": "https://feeds.longtail.test/creator-video",
      "params": {"feed_key": "creator-video"}, "category": "feed", "audience_tags": ["video", "creators"],
      "self_promo_allowed": True, "frequency_cap": {"posts": 10, "per_days": 1}, "link_policy": {"allowed": True},
      "rules_url": f"{API}/api/v1/feeds/creator-video.xml"}),
    ("hashnode",
     {"name": "Mock Hashnode token", "secret_refs": {"api_key": "env:MOCK_API_KEY"}},
     {"name": "Builders blog (local mock Hashnode)", "url": f"{MOCK}/local/hashnode-demo",
      "params": {"publication_id": "pub-demo"}, "category": "developer_blog", "audience_tags": ["ai", "saas"],
      "self_promo_allowed": True, "frequency_cap": {"posts": 2, "per_days": 7}, "link_policy": {"allowed": True},
      "rules_url": f"{MOCK}/local/hashnode-demo/guidelines"}),
    ("peertube",
     {"name": "Mock PeerTube account", "secret_refs": {"password": "env:MOCK_API_KEY"},
      "params": {"username": "longtail_bot"}},
     {"name": "Creator channel (local mock PeerTube)", "url": f"{MOCK}/local/peertube-demo",
      "params": {"channel_id": 1}, "category": "video_platform", "audience_tags": ["video", "creators"],
      "self_promo_allowed": True, "frequency_cap": {"posts": 3, "per_days": 7}, "link_policy": {"allowed": True},
      "rules_url": f"{MOCK}/local/peertube-demo/about/instance"}),
]


def seed_catalogue(c: httpx.Client) -> list[str]:
    """Register connections (secret refs only) and destinations; returns the seeded destination ids."""
    for key, conn, dest in SEEDS:
        conn_id = None
        if conn:
            created = _post(c, "/api/v1/connections", f"seed-conn-{key}",
                            {"connector_key": key, "auth_type": "api_key", **conn})
            conn_id = created["id"] if created else None
        _post(c, "/api/v1/destinations", f"seed-dest-{key}",
              {"connector_key": key, **dest, **({"connection_id": conn_id} if conn_id else {})})
    names = {d["name"] for _, _, d in SEEDS}
    items = c.get(f"{API}/api/v1/destinations").json()["items"]
    return [d["id"] for d in items if d["name"] in names]


def certify_all(c: httpx.Client, destination_ids: list[str]) -> bool:
    """Certify every seeded destination (real test post, live check, delete) and wait for the results."""
    jobs = {}
    for dest_id in destination_ids:
        r = c.post(f"{API}/api/v1/certifications", json={"destination_id": dest_id},
                   headers={"Idempotency-Key": f"bootstrap-cert-{dest_id}-{int(time.time())}"})
        jobs[dest_id] = r.json()["job_id"]
    ok = True
    deadline = time.time() + 300
    for dest_id, job_id in jobs.items():
        while time.time() < deadline:
            job = c.get(f"{API}/api/v1/jobs/{job_id}").json()
            if job["status"] in ("succeeded", "failed"):
                break
            time.sleep(1)
        passed = job["status"] == "succeeded"
        ok &= passed
        log.info("certification", extra={"destination_id": dest_id, "passed": passed,
                                         "destination_level": (job.get("result") or {}).get("level"),
                                         "error": (job.get("error") or {}).get("message")})
    return ok


def main() -> int:
    setup_logging("INFO")
    wait_for(f"{GHOST}/ghost/api/admin/site/", "ghost")
    wait_for(f"{LEMMY}/api/v3/site", "lemmy")
    provision_ghost()
    provision_lemmy()
    headers = {"Authorization": f"Bearer {os.environ['API_TOKEN']}"} if os.environ.get("API_TOKEN") else {}
    with httpx.Client(timeout=30, headers=headers) as c:
        ids = seed_catalogue(c)
        certified = certify_all(c, ids)
    log.info("bootstrap complete", extra={"destinations": len(ids), "all_certified": certified})
    return 0


if __name__ == "__main__":
    sys.exit(main())
