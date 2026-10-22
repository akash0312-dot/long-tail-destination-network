"""Discourse connector: one topic per post. One connector, every Discourse forum:
destination.params = {"base_url": "https://forum.example", "category_id": 12}.

Idempotency: an HTML comment `<!-- ltn:<marker> -->` is kept in the raw post (not shown to readers). Before posting
we look through the account's recent topics with the same title for that marker. Discourse also refuses duplicate
titles by default, a second line of defence.
"""
from app.connectors.base import BaseConnector, key_marker, metrics, require
from app.connectors.schemas import ConnectionRef, DestinationRef, Rendition
from app.errors import AppError, ErrorCode

RECONCILE_TOPICS = 30


class Connector(BaseConnector):
    def _client(self, destination: DestinationRef | None, connection: ConnectionRef):
        creds = connection.resolved()
        username = creds.get("api_username") or connection.params.get("api_username") or "system"
        headers = {"Api-Key": creds.get("api_key", ""), "Api-Username": username, "Accept": "application/json"}
        return self.http(self.base_url(destination, connection), connection, headers=headers), username

    @staticmethod
    def _live_url(base: str, slug: str, topic_id) -> str:
        return f"{base}/t/{slug}/{topic_id}"

    def _find_existing(self, http, username: str, title: str, marker: str) -> dict | None:
        resp = http.request("GET", f"/topics/created-by/{username}.json", op="find_existing", allow_status=(404,))
        if resp.status_code == 404:
            return None
        topics = require(resp.json(), "topic_list.topics", "find_existing")
        for t in topics[:RECONCILE_TOPICS]:
            if str(t.get("title", "")).strip().lower() != title.strip().lower():
                continue
            topic = http.request("GET", f"/t/{t['id']}.json", op="find_existing").json()
            first_post_id = require(topic, "post_stream.posts", "find_existing")[0]["id"]
            post = http.request("GET", f"/posts/{first_post_id}.json", op="find_existing").json()
            if marker in (post.get("raw") or ""):
                return {"platform_post_id": str(t["id"]), "status": "published",
                        "live_url": self._live_url(http.base_url, t.get("slug", "topic"), t["id"])}
        return None

    def publish(self, rendition: Rendition, destination: DestinationRef, connection: ConnectionRef,
                idempotency_key: str) -> dict:
        self.ensure_valid(rendition, destination, connection)
        marker = f"ltn:{key_marker(idempotency_key)}"
        http, username = self._client(destination, connection)
        try:
            existing = self._find_existing(http, username, rendition.title, marker)
            if existing:
                existing["warnings"] = ["IDEMPOTENT_REPLAY: a post for this idempotency key already exists; "
                                        "returned it instead of publishing again"]
                return existing
            raw = rendition.body
            images = [m for m in rendition.media if m.type == "image"]
            if images:
                raw += "\n\n" + "\n".join(f"![{m.alt or ''}]({m.url})" for m in images)
            if rendition.link_url and rendition.link_url not in raw:
                raw += f"\n\n{rendition.link_url}"
            payload = {"title": rendition.title, "raw": f"{raw}\n\n<!-- {marker} -->"}
            if destination.params.get("category_id") is not None:
                payload["category"] = int(destination.params["category_id"])
            if rendition.tags:
                payload["tags"] = [t.lstrip("#").lower() for t in rendition.tags]
            data = http.request("POST", "/posts.json", op="publish", json=payload).json()
            topic_id = require(data, "topic_id", "publish")
            slug = require(data, "topic_slug", "publish")
            return {"platform_post_id": str(topic_id), "live_url": self._live_url(http.base_url, slug, topic_id),
                    "status": "published", "warnings": []}
        finally:
            http.close()

    def get_status(self, platform_post_id: str, connection: ConnectionRef,
                   destination: DestinationRef | None = None) -> dict:
        http, _ = self._client(destination, connection)
        try:
            resp = http.request("GET", f"/t/{platform_post_id}.json", op="get_status", allow_status=(404, 410))
            if resp.status_code in (404, 410):
                return {"status": "deleted", "live_url": None}
            t = resp.json()
            if t.get("deleted_at"):
                return {"status": "deleted", "live_url": None}
            slug = require(t, "slug", "get_status")
            status = "published" if t.get("visible", True) else "removed"
            return {"status": status, "live_url": self._live_url(http.base_url, slug, platform_post_id)}
        finally:
            http.close()

    def fetch_metrics(self, platform_post_id: str, connection: ConnectionRef, since: str | None,
                      destination: DestinationRef | None = None) -> dict:
        """Topic views, likes, replies, and clicks on links inside the topic (lifetime totals)."""
        http, _ = self._client(destination, connection)
        try:
            t = http.request("GET", f"/t/{platform_post_id}.json", op="metrics").json()
            link_clicks = sum(int(link.get("clicks") or 0) for link in (t.get("details") or {}).get("links", []))
            return metrics(views=require(t, "views", "metrics"), likes=require(t, "like_count", "metrics"),
                           comments=max(0, int(require(t, "posts_count", "metrics")) - 1), clicks=link_clicks)
        finally:
            http.close()

    def delete(self, platform_post_id: str, connection: ConnectionRef,
               destination: DestinationRef | None = None) -> dict:
        http, _ = self._client(destination, connection)
        try:
            http.request("DELETE", f"/t/{platform_post_id}.json", op="delete", allow_status=(404,))
            return {"deleted": True}
        finally:
            http.close()
