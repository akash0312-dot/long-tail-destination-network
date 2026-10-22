"""PeerTube connector. One connector, every PeerTube instance: destination.params = {base_url, channel_id}.

Videos are imported from a URL (POST /api/v1/videos/imports), so we never upload large files ourselves.
Auth: the instance's local OAuth client + password grant; tokens are cached and refreshed once on 401.
Idempotency: the description ends with a hidden marker; before importing we look at our newest videos with the same
name and read their full description.
"""
import threading
import time

from app.connectors.base import BaseConnector, key_marker, metrics, require
from app.connectors.schemas import ConnectionRef, DestinationRef, Rendition
from app.errors import AppError, ErrorCode

API = "/api/v1"
STATE_PUBLISHED = 1
_tokens: dict[str, tuple[str, float]] = {}
_tokens_lock = threading.Lock()


class Connector(BaseConnector):
    def _token(self, base: str, connection: ConnectionRef, force: bool = False) -> tuple[str, float]:
        creds = connection.resolved()
        username = creds.get("username") or connection.params.get("username")
        if not username or not creds.get("password"):
            raise AppError(ErrorCode.CONNECTION_AUTH_EXPIRED, "PeerTube connection needs username and password")
        cache_key = f"{base}|{username}"
        with _tokens_lock:
            cached = _tokens.get(cache_key)
            if cached and not force and cached[1] > time.time() + 30:
                return cached
        http = self.http(base, connection)
        try:
            client = http.request("GET", f"{API}/oauth-clients/local", op="login").json()
            data = http.request("POST", f"{API}/users/token", op="login", data={
                "client_id": require(client, "client_id", "login"), "client_secret": client.get("client_secret", ""),
                "grant_type": "password", "response_type": "code",
                "username": username, "password": creds["password"]}).json()
            token = (require(data, "access_token", "login"), time.time() + float(data.get("expires_in", 3600)))
        finally:
            http.close()
        with _tokens_lock:
            _tokens[cache_key] = token
        return token

    def _authed(self, base: str, connection: ConnectionRef, fn):
        for attempt in (0, 1):
            token, _ = self._token(base, connection, force=attempt == 1)
            http = self.http(base, connection, headers={"Authorization": f"Bearer {token}"})
            try:
                return fn(http)
            except AppError as e:
                if e.code != ErrorCode.CONNECTION_AUTH_EXPIRED or attempt == 1:
                    raise
            finally:
                http.close()

    @staticmethod
    def _result(base: str, video: dict, op: str) -> dict:
        state = (video.get("state") or {}).get("id", STATE_PUBLISHED)
        return {"platform_post_id": str(require(video, "id", op)),
                "live_url": f"{base}/w/{require(video, 'shortUUID', op)}",
                "status": "published" if state == STATE_PUBLISHED else "pending"}

    def publish(self, rendition: Rendition, destination: DestinationRef, connection: ConnectionRef,
                idempotency_key: str) -> dict:
        self.ensure_valid(rendition, destination, connection)
        channel = destination.params.get("channel_id")
        if not channel:
            raise AppError(ErrorCode.VALIDATION_ERROR, "PeerTube destination needs params.channel_id")
        base = self.base_url(destination, connection)
        marker = f"ltn:{key_marker(idempotency_key)}"
        video_url = next(m.url for m in rendition.media if m.type == "video")

        def run(http):
            mine = http.request("GET", f"{API}/users/me/videos", op="find_existing",
                                params={"count": 50, "sort": "-createdAt"}).json()
            for v in require(mine, "data", "find_existing"):
                if v.get("name") != rendition.title:
                    continue
                desc = http.request("GET", f"{API}/videos/{v['id']}/description", op="find_existing").json()
                if marker in (desc.get("description") or ""):
                    result = self._result(base, v, "find_existing")
                    result["warnings"] = ["IDEMPOTENT_REPLAY: a video for this idempotency key already exists; "
                                          "returned it instead of importing again"]
                    return result
            form = {"channelId": str(channel), "name": rendition.title, "targetUrl": video_url, "privacy": "1",
                    "description": f"{rendition.body}\n\n[//]: # ({marker})", "language": rendition.language}
            form.update({f"tags[{i}]": t.lstrip("#")[:30] for i, t in enumerate(rendition.tags[:5])})
            data = http.request("POST", f"{API}/videos/imports", op="publish", data=form).json()
            return {**self._result(base, require(data, "video", "publish"), "publish"), "warnings": []}

        return self._authed(base, connection, run)

    def get_status(self, platform_post_id: str, connection: ConnectionRef,
                   destination: DestinationRef | None = None) -> dict:
        base = self.base_url(destination, connection)

        def run(http):
            resp = http.request("GET", f"{API}/videos/{platform_post_id}", op="get_status", allow_status=(404,))
            if resp.status_code == 404:
                return {"status": "deleted", "live_url": None}
            r = self._result(base, resp.json(), "get_status")
            return {"status": r["status"], "live_url": r["live_url"]}

        return self._authed(base, connection, run)

    def delete(self, platform_post_id: str, connection: ConnectionRef,
               destination: DestinationRef | None = None) -> dict:
        base = self.base_url(destination, connection)

        def run(http):
            http.request("DELETE", f"{API}/videos/{platform_post_id}", op="delete", allow_status=(404,))
            return {"deleted": True}

        return self._authed(base, connection, run)

    def fetch_metrics(self, platform_post_id: str, connection: ConnectionRef, since: str | None,
                      destination: DestinationRef | None = None) -> dict:
        """Views, likes and comments from the video; total watch time from the owner's stats (best effort)."""
        base = self.base_url(destination, connection)

        def run(http):
            v = http.request("GET", f"{API}/videos/{platform_post_id}", op="metrics").json()
            stats = http.request("GET", f"{API}/videos/{platform_post_id}/stats/overall", op="metrics",
                                 allow_status=(403, 404))
            watch = stats.json().get("totalWatchTime") if stats.status_code == 200 else None
            return metrics(views=require(v, "views", "metrics"), likes=require(v, "likes", "metrics"),
                           comments=v.get("comments"), watch_time_s=watch)

        return self._authed(base, connection, run)

    def refresh_auth(self, connection: ConnectionRef, destination: DestinationRef | None = None) -> dict:
        _, expires_at = self._token(self.base_url(destination, connection), connection, force=True)
        return {"expires_at": int(expires_at)}
