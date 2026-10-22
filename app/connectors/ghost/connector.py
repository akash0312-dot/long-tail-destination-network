"""Ghost Admin API connector. One connector, any Ghost site: the site URL is destination.params.base_url.

Idempotency: the post slug is derived from the idempotency key, so a retry after a lost response finds the
existing post by slug instead of creating a second one.
"""
import time

import jwt
import markdown as md

from app.config import get_settings
from app.connectors.base import BaseConnector, key_marker, require
from app.connectors.schemas import ConnectionRef, DestinationRef, Rendition
from app.errors import AppError, ErrorCode

API = "/ghost/api/admin"
TOKEN_TTL_S = 300
SIM_ADMIN_KEY = "0" * 24 + ":" + "0" * 64  # only used in simulation mode
STATUS_MAP = {"published": "published", "draft": "draft", "scheduled": "scheduled", "sent": "published"}


class Connector(BaseConnector):
    def _admin_key(self, connection: ConnectionRef) -> str:
        key = connection.resolved().get("admin_api_key", "")
        if ":" not in key and get_settings().simulation_mode:
            return SIM_ADMIN_KEY
        return key

    def _token(self, connection: ConnectionRef) -> str:
        key = self._admin_key(connection)
        try:
            key_id, secret = key.split(":", 1)
            secret_bytes = bytes.fromhex(secret)
        except ValueError:
            raise AppError(ErrorCode.CONNECTION_AUTH_EXPIRED, "Ghost Admin API key is missing or malformed",
                           {"expected_format": "<id>:<hex secret>"})
        now = int(time.time())
        return jwt.encode({"iat": now, "exp": now + TOKEN_TTL_S, "aud": "/admin/"}, secret_bytes,
                          algorithm="HS256", headers={"kid": key_id})

    def _client(self, destination: DestinationRef | None, connection: ConnectionRef):
        base = self.base_url(destination, connection)
        headers = {"Authorization": f"Ghost {self._token(connection)}", "Accept-Version": "v5.0"}
        return self.http(base, connection, headers=headers)

    @staticmethod
    def _post_result(data: dict, op: str) -> dict:
        posts = require(data, "posts", op)
        if not isinstance(posts, list) or not posts:
            raise AppError(ErrorCode.PLATFORM_SCHEMA_CHANGED, f"Ghost {op} returned no posts", {"op": op})
        post = posts[0]
        return {"platform_post_id": str(require(post, "id", op)), "live_url": require(post, "url", op),
                "status": STATUS_MAP.get(require(post, "status", op), "pending")}

    def publish(self, rendition: Rendition, destination: DestinationRef, connection: ConnectionRef,
                idempotency_key: str) -> dict:
        self.ensure_valid(rendition, destination, connection)
        slug = f"ltn-{key_marker(idempotency_key)}"
        http = self._client(destination, connection)
        try:
            existing = http.request("GET", f"{API}/posts/slug/{slug}/", op="find_existing", allow_status=(404,))
            if existing.status_code == 200:
                result = self._post_result(existing.json(), "find_existing")
                result["warnings"] = ["IDEMPOTENT_REPLAY: a post for this idempotency key already exists; "
                                      "returned it instead of publishing again"]
                return result
            html = rendition.body_html or md.markdown(rendition.body, extensions=["extra"])
            post = {"title": rendition.title, "html": html, "status": "published", "slug": slug,
                    "tags": [{"name": t.lstrip("#")} for t in rendition.tags]}
            if rendition.canonical_url:
                post["canonical_url"] = rendition.canonical_url
            images = [m for m in rendition.media if m.type == "image"]
            if images:
                post["feature_image"] = images[0].url
                if images[0].alt:
                    post["feature_image_alt"] = images[0].alt
            resp = http.request("POST", f"{API}/posts/", op="publish", params={"source": "html"},
                                json={"posts": [post]})
            return {**self._post_result(resp.json(), "publish"), "warnings": []}
        finally:
            http.close()

    def get_status(self, platform_post_id: str, connection: ConnectionRef,
                   destination: DestinationRef | None = None) -> dict:
        http = self._client(destination, connection)
        try:
            resp = http.request("GET", f"{API}/posts/{platform_post_id}/", op="get_status", allow_status=(404,))
            if resp.status_code == 404:
                return {"status": "deleted", "live_url": None}
            r = self._post_result(resp.json(), "get_status")
            return {"status": r["status"], "live_url": r["live_url"]}
        finally:
            http.close()

    def delete(self, platform_post_id: str, connection: ConnectionRef,
               destination: DestinationRef | None = None) -> dict:
        http = self._client(destination, connection)
        try:
            http.request("DELETE", f"{API}/posts/{platform_post_id}/", op="delete", allow_status=(404,))
            return {"deleted": True}
        finally:
            http.close()

    def refresh_auth(self, connection: ConnectionRef, destination: DestinationRef | None = None) -> dict:
        # Ghost admin tokens are short-lived JWTs minted locally from the key; validate the key by minting one.
        self._token(connection)
        return {"expires_at": int(time.time()) + TOKEN_TTL_S}
