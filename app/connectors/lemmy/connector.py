"""Lemmy API v3 connector (Lemmy 0.19). One connector, any instance + community: destination.params =
{"base_url": "https://lemmy.example", "community": "community_name"}.

Idempotency: a markdown comment `[//]: # (ltn:<marker>)` (not rendered) is appended to the body. Before posting we
scan the community's newest posts for that marker, so a retry after a lost response returns the existing post.
"""
import threading

import httpx

from app.connectors.base import BaseConnector, key_marker, require
from app.connectors.schemas import ConnectionRef, DestinationRef, Rendition
from app.errors import AppError, ErrorCode

API = "/api/v3"
AUTH_ERRORS = {"not_logged_in", "incorrect_login", "password_incorrect", "email_not_verified",
               "registration_application_pending", "site_ban", "deleted"}
RATE_ERRORS = {"rate_limit_error"}
NOT_FOUND_ERRORS = {"couldnt_find_post", "not_found", "couldnt_find_object"}

_jwt_cache: dict[str, str] = {}
_jwt_lock = threading.Lock()


def lemmy_error(resp: httpx.Response) -> AppError | None:
    try:
        err = str(resp.json().get("error", ""))
    except Exception:
        return None
    if not err:
        return None
    if err in AUTH_ERRORS:
        return AppError(ErrorCode.CONNECTION_AUTH_EXPIRED, f"Lemmy rejected the login/session: {err}",
                        {"platform_message": err})
    if err in RATE_ERRORS:
        return AppError(ErrorCode.PLATFORM_RATE_LIMIT, "Lemmy rate limit reached",
                        {"platform_message": err, "retry_after_s": 60})
    return AppError(ErrorCode.PLATFORM_CONTENT_REJECTED, f"Lemmy refused the request: {err}",
                    {"platform_message": err})


class Connector(BaseConnector):
    def _http(self, base: str, connection: ConnectionRef, jwt: str | None = None):
        headers = {"Authorization": f"Bearer {jwt}"} if jwt else {}
        return self.http(base, connection, map_error=lemmy_error, headers=headers)

    def _login(self, base: str, connection: ConnectionRef, force: bool = False) -> str:
        creds = connection.resolved()
        if creds.get("jwt"):
            return creds["jwt"]
        username, password = creds.get("username") or connection.params.get("username"), creds.get("password")
        if not username or not password:
            raise AppError(ErrorCode.CONNECTION_AUTH_EXPIRED, "Lemmy connection needs username and password (or jwt)")
        cache_key = f"{base}|{username}"
        with _jwt_lock:
            if not force and cache_key in _jwt_cache:
                return _jwt_cache[cache_key]
        http = self._http(base, connection)
        try:
            resp = http.request("POST", f"{API}/user/login", op="login",
                                json={"username_or_email": username, "password": password})
            token = require(resp.json(), "jwt", "login")
        finally:
            http.close()
        with _jwt_lock:
            _jwt_cache[cache_key] = token
        return token

    def _authed(self, base: str, connection: ConnectionRef, fn):
        """Run fn(http) with a session; on an expired session log in again once."""
        for attempt in (0, 1):
            http = self._http(base, connection, self._login(base, connection, force=attempt == 1))
            try:
                return fn(http)
            except AppError as e:
                if e.code != ErrorCode.CONNECTION_AUTH_EXPIRED or attempt == 1:
                    raise
            finally:
                http.close()

    def _community_id(self, http, destination: DestinationRef) -> int:
        if destination.params.get("community_id"):
            return int(destination.params["community_id"])
        name = destination.params.get("community")
        if not name:
            raise AppError(ErrorCode.VALIDATION_ERROR, "Lemmy destination needs params.community")
        resp = http.request("GET", f"{API}/community", op="resolve_community", params={"name": name})
        return int(require(resp.json(), "community_view.community.id", "resolve_community"))

    @staticmethod
    def _result(base: str, data: dict, op: str) -> dict:
        post = require(data, "post_view.post", op)
        post_id = require(post, "id", op)
        status = "deleted" if post.get("deleted") else "removed" if post.get("removed") else "published"
        return {"platform_post_id": str(post_id), "live_url": f"{base}/post/{post_id}", "status": status}

    def publish(self, rendition: Rendition, destination: DestinationRef, connection: ConnectionRef,
                idempotency_key: str) -> dict:
        self.ensure_valid(rendition, destination, connection)
        base = self.base_url(destination, connection)
        marker = f"ltn:{key_marker(idempotency_key)}"

        def run(http):
            community_id = self._community_id(http, destination)
            listing = http.request("GET", f"{API}/post/list", op="find_existing",
                                   params={"community_id": community_id, "sort": "New", "limit": 50})
            for pv in require(listing.json(), "posts", "find_existing"):
                post = require(pv, "post", "find_existing")
                if marker in (post.get("body") or "") and not post.get("deleted"):
                    result = self._result(base, {"post_view": pv}, "find_existing")
                    result["warnings"] = ["IDEMPOTENT_REPLAY: a post for this idempotency key already exists; "
                                          "returned it instead of publishing again"]
                    return result
            body = f"{rendition.body}\n\n[//]: # ({marker})"
            payload = {"name": rendition.title, "community_id": community_id, "body": body}
            link = rendition.link_url or (rendition.media[0].url if rendition.media else None)
            if link:
                payload["url"] = link
            resp = http.request("POST", f"{API}/post", op="publish", json=payload)
            return {**self._result(base, resp.json(), "publish"), "warnings": []}

        return self._authed(base, connection, run)

    def get_status(self, platform_post_id: str, connection: ConnectionRef,
                   destination: DestinationRef | None = None) -> dict:
        base = self.base_url(destination, connection)

        def run(http):
            resp = http.request("GET", f"{API}/post", op="get_status", params={"id": platform_post_id},
                                allow_status=(400, 404))
            if resp.status_code in (400, 404):
                err = lemmy_error(resp)
                if resp.status_code == 404 or (err and err.details.get("platform_message") in NOT_FOUND_ERRORS):
                    return {"status": "deleted", "live_url": None}
                raise err or AppError(ErrorCode.PLATFORM_CONTENT_REJECTED, "Lemmy get_status failed")
            r = self._result(base, resp.json(), "get_status")
            return {"status": r["status"], "live_url": r["live_url"]}

        return self._authed(base, connection, run)

    def delete(self, platform_post_id: str, connection: ConnectionRef,
               destination: DestinationRef | None = None) -> dict:
        base = self.base_url(destination, connection)

        def run(http):
            resp = http.request("POST", f"{API}/post/delete", op="delete",
                                json={"post_id": int(platform_post_id), "deleted": True})
            return {"deleted": bool(require(resp.json(), "post_view.post.deleted", "delete"))}

        return self._authed(base, connection, run)

    def refresh_auth(self, connection: ConnectionRef, destination: DestinationRef | None = None) -> dict:
        base = self.base_url(destination, connection)
        self._login(base, connection, force=True)
        return {"expires_at": None}  # Lemmy 0.19 session JWTs have no fixed expiry
