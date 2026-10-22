"""Dev.to (Forem API v1) articles. One connector, any Forem community: destination.params.base_url
(https://dev.to by default).

Idempotency: a markdown comment `[//]: # (ltn:<marker>)` (not rendered) ends the article body; before posting we
search the account's own articles (published and unpublished) for it.

Delete: the public Forem API cannot delete an article, so delete() unpublishes it (it disappears from the site and
feeds) and says so in `note`.
"""
import re

from app.connectors.base import BaseConnector, key_marker, metrics, require, schema_error
from app.connectors.schemas import ConnectionRef, DestinationRef, Rendition
from app.errors import AppError, ErrorCode

HEADERS = {"Accept": "application/vnd.forem.api-v1+json"}


def forem_tag(tag: str) -> str:
    return re.sub(r"[^a-z0-9]", "", tag.lower())


class Connector(BaseConnector):
    def base_url(self, destination, connection) -> str:
        if destination is not None and not (destination.params.get("base_url") or destination.url):
            destination = destination.model_copy(update={"params": {**destination.params, "base_url": "https://dev.to"}})
        return super().base_url(destination, connection)

    def _client(self, destination: DestinationRef | None, connection: ConnectionRef):
        headers = {**HEADERS, "api-key": connection.resolved().get("api_key", "")}
        return self.http(self.base_url(destination, connection), connection, headers=headers)

    @staticmethod
    def _result(article: dict, op: str) -> dict:
        return {"platform_post_id": str(require(article, "id", op)), "live_url": require(article, "url", op),
                "status": "published" if require(article, "published", op) else "draft"}

    def publish(self, rendition: Rendition, destination: DestinationRef, connection: ConnectionRef,
                idempotency_key: str) -> dict:
        self.ensure_valid(rendition, destination, connection)
        marker = f"ltn:{key_marker(idempotency_key)}"
        http = self._client(destination, connection)
        try:
            mine = http.request("GET", "/api/articles/me/all", op="find_existing", params={"per_page": 30}).json()
            if not isinstance(mine, list):
                raise schema_error("find_existing", "[articles]", mine)
            for a in mine:
                if marker in (a.get("body_markdown") or ""):
                    result = self._result(a, "find_existing")
                    result["warnings"] = ["IDEMPOTENT_REPLAY: a post for this idempotency key already exists; "
                                          "returned it instead of publishing again"]
                    return result
            warnings = []
            tags = [forem_tag(t) for t in rendition.tags if forem_tag(t)]
            if tags != [t.lstrip("#") for t in rendition.tags]:
                warnings.append("TAGS_NORMALISED: Forem tags are lowercase letters and digits only")
            article = {"title": rendition.title, "published": True, "tags": tags,
                       "body_markdown": f"{rendition.body}\n\n[//]: # ({marker})"}
            if rendition.canonical_url:
                article["canonical_url"] = rendition.canonical_url
            if rendition.media:
                article["main_image"] = rendition.media[0].url
            data = http.request("POST", "/api/articles", op="publish", json={"article": article}).json()
            return {**self._result(data, "publish"), "warnings": warnings}
        finally:
            http.close()

    def get_status(self, platform_post_id: str, connection: ConnectionRef,
                   destination: DestinationRef | None = None) -> dict:
        http = self._client(destination, connection)
        try:
            resp = http.request("GET", f"/api/articles/{platform_post_id}", op="get_status", allow_status=(404,))
            if resp.status_code == 404:  # unpublished or removed: no longer public
                return {"status": "deleted", "live_url": None}
            r = self._result(resp.json(), "get_status")
            return {"status": r["status"], "live_url": r["live_url"]}
        finally:
            http.close()

    def fetch_metrics(self, platform_post_id: str, connection: ConnectionRef, since: str | None,
                      destination: DestinationRef | None = None) -> dict:
        """Page views are only visible to the author, so they come from the author's own article list."""
        http = self._client(destination, connection)
        try:
            mine = http.request("GET", "/api/articles/me/all", op="metrics", params={"per_page": 100}).json()
            if not isinstance(mine, list):
                raise schema_error("metrics", "[articles]", mine)
            a = next((x for x in mine if str(x.get("id")) == str(platform_post_id)), None)
            if a is None:
                raise AppError(ErrorCode.PLATFORM_CONTENT_REJECTED, "Article not found among the account's articles",
                               {"platform_post_id": platform_post_id})
            return metrics(views=a.get("page_views_count"), likes=require(a, "public_reactions_count", "metrics"),
                           comments=require(a, "comments_count", "metrics"))
        finally:
            http.close()

    def delete(self, platform_post_id: str, connection: ConnectionRef,
               destination: DestinationRef | None = None) -> dict:
        http = self._client(destination, connection)
        try:
            resp = http.request("PUT", f"/api/articles/{platform_post_id}", op="delete", allow_status=(404,),
                                json={"article": {"published": False}})
            if resp.status_code != 404:
                require(resp.json(), "published", "delete")
            return {"deleted": True, "note": "Forem API cannot delete articles; the article was unpublished"}
        finally:
            http.close()
