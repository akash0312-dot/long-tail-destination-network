"""Hashnode connector (GraphQL). One connector, every Hashnode blog: destination.params.publication_id.

GraphQL answers HTTP 200 even for errors, so `errors[].extensions.code` is mapped to the shared codes here.
Idempotency: a hidden markdown comment `[//]: # (ltn:<marker>)` ends the post; before publishing we look through
the publication's newest posts for it.
"""
import re

from app.connectors.base import BaseConnector, key_marker, require
from app.connectors.schemas import ConnectionRef, DestinationRef, Rendition
from app.errors import AppError, ErrorCode

FIND = """query Find($id: ObjectId!) { publication(id: $id) { posts(first: 20) {
  edges { node { id url content { markdown } } } } } }"""
PUBLISH = """mutation Publish($input: PublishPostInput!) { publishPost(input: $input) { post { id url } } }"""
STATUS = """query Status($id: ID!) { post(id: $id) { id url } }"""
REMOVE = """mutation Remove($input: RemovePostInput!) { removePost(input: $input) { post { id } } }"""
CODE_MAP = {"UNAUTHENTICATED": ErrorCode.CONNECTION_AUTH_EXPIRED, "FORBIDDEN": ErrorCode.CONNECTION_AUTH_EXPIRED,
            "TOO_MANY_REQUESTS": ErrorCode.PLATFORM_RATE_LIMIT, "RATE_LIMITED": ErrorCode.PLATFORM_RATE_LIMIT}


def _slug(tag: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", tag.lstrip("#").lower()).strip("-")


class Connector(BaseConnector):
    def base_url(self, destination, connection) -> str:
        if destination is not None and not (destination.params.get("base_url") or destination.url):
            destination = destination.model_copy(
                update={"params": {**destination.params, "base_url": "https://gql.hashnode.com"}})
        return super().base_url(destination, connection)

    def _gql(self, destination, connection: ConnectionRef, query: str, variables: dict, op: str) -> dict:
        headers = {"Authorization": connection.resolved().get("api_key", ""), "Content-Type": "application/json"}
        http = self.http(self.base_url(destination, connection), connection, headers=headers)
        try:
            body = http.request("POST", "/", op=op, json={"query": query, "variables": variables}).json()
        finally:
            http.close()
        if body.get("errors"):
            err = body["errors"][0]
            code = (err.get("extensions") or {}).get("code", "")
            raise AppError(CODE_MAP.get(code, ErrorCode.PLATFORM_CONTENT_REJECTED),
                           f"Hashnode refused {op}: {err.get('message', code)}",
                           {"op": op, "platform_message": err.get("message"), "graphql_code": code,
                            **({"retry_after_s": 60} if code in ("TOO_MANY_REQUESTS", "RATE_LIMITED") else {})})
        return require(body, "data", op)

    def publish(self, rendition: Rendition, destination: DestinationRef, connection: ConnectionRef,
                idempotency_key: str) -> dict:
        self.ensure_valid(rendition, destination, connection)
        pub_id = destination.params.get("publication_id")
        if not pub_id:
            raise AppError(ErrorCode.VALIDATION_ERROR, "Hashnode destination needs params.publication_id")
        marker = f"ltn:{key_marker(idempotency_key)}"
        data = self._gql(destination, connection, FIND, {"id": pub_id}, "find_existing")
        for edge in require(data, "publication.posts.edges", "find_existing"):
            node = edge["node"]
            if marker in ((node.get("content") or {}).get("markdown") or ""):
                return {"platform_post_id": str(require(node, "id", "find_existing")),
                        "live_url": require(node, "url", "find_existing"), "status": "published",
                        "warnings": ["IDEMPOTENT_REPLAY: a post for this idempotency key already exists; "
                                     "returned it instead of publishing again"]}
        inp = {"title": rendition.title, "publicationId": pub_id,
               "contentMarkdown": f"{rendition.body}\n\n[//]: # ({marker})",
               "tags": [{"slug": _slug(t), "name": t.lstrip("#")} for t in rendition.tags if _slug(t)]}
        if rendition.canonical_url:
            inp["originalArticleURL"] = rendition.canonical_url
        if rendition.media:
            inp["coverImageOptions"] = {"coverImageURL": rendition.media[0].url}
        post = require(self._gql(destination, connection, PUBLISH, {"input": inp}, "publish"),
                       "publishPost.post", "publish")
        return {"platform_post_id": str(require(post, "id", "publish")), "live_url": require(post, "url", "publish"),
                "status": "published", "warnings": []}

    def get_status(self, platform_post_id: str, connection: ConnectionRef,
                   destination: DestinationRef | None = None) -> dict:
        data = self._gql(destination, connection, STATUS, {"id": platform_post_id}, "get_status")
        post = require(data, "post", "get_status")
        if post is None:
            return {"status": "deleted", "live_url": None}
        return {"status": "published", "live_url": require(post, "url", "get_status")}

    def delete(self, platform_post_id: str, connection: ConnectionRef,
               destination: DestinationRef | None = None) -> dict:
        self._gql(destination, connection, REMOVE, {"input": {"id": platform_post_id}}, "delete")
        return {"deleted": True}
