"""Generic signed webhook for partner sites (protocol v1, docs/PARTNER_WEBHOOK.md).
One connector, every partner: destination.params.endpoint_url is the partner's posts endpoint.

Every request is signed: X-Signature = "sha256=" + HMAC-SHA256(secret, f"{timestamp}.{body}") with the timestamp in
X-Signature-Timestamp, so partners can reject forged or replayed calls. Partners must honour Idempotency-Key
(same key -> same post, answered 200 with Idempotent-Replayed: true), which makes retries after timeouts safe.
"""
import hashlib
import hmac
import json
import time

from app.connectors.base import BaseConnector, require
from app.connectors.schemas import ConnectionRef, DestinationRef, Rendition

PROTOCOL_VERSION = "1"


def signature(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


class Connector(BaseConnector):
    def _send(self, method: str, url_suffix: str, destination, connection: ConnectionRef, op: str,
              payload: dict | None = None, idempotency_key: str | None = None, allow_status=()):
        secret = connection.resolved().get("signing_secret", "")
        base = self.base_url(destination, connection)
        body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else b""
        ts = str(int(time.time()))
        headers = {"Content-Type": "application/json", "X-Signature-Timestamp": ts,
                   "X-Signature": signature(secret, ts, body), "X-Protocol-Version": PROTOCOL_VERSION}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        http = self.http(base, connection)
        try:
            return http.request(method, f"{base}{url_suffix}", op=op, content=body or None, headers=headers,
                                allow_status=allow_status)
        finally:
            http.close()

    @staticmethod
    def _result(data: dict, op: str) -> dict:
        return {"platform_post_id": str(require(data, "id", op)), "live_url": require(data, "url", op),
                "status": data.get("status", "published")}

    def publish(self, rendition: Rendition, destination: DestinationRef, connection: ConnectionRef,
                idempotency_key: str) -> dict:
        self.ensure_valid(rendition, destination, connection)
        post = rendition.model_dump(include={"title", "body", "body_html", "content_type", "link_url",
                                             "canonical_url", "media", "tags", "mentions", "language",
                                             "ai_generated", "ai_disclosure"})
        resp = self._send("POST", "", destination, connection, "publish",
                          {"event": "post.create", "idempotency_key": idempotency_key, "post": post},
                          idempotency_key=idempotency_key)
        result = self._result(resp.json(), "publish")
        replayed = resp.status_code == 200 or resp.headers.get("Idempotent-Replayed") == "true"
        result["warnings"] = (["IDEMPOTENT_REPLAY: partner returned the existing post for this idempotency key"]
                              if replayed else [])
        return result

    def get_status(self, platform_post_id: str, connection: ConnectionRef,
                   destination: DestinationRef | None = None) -> dict:
        resp = self._send("GET", f"/{platform_post_id}", destination, connection, "get_status",
                          allow_status=(404, 410))
        if resp.status_code in (404, 410):
            return {"status": "deleted", "live_url": None}
        r = self._result(resp.json(), "get_status")
        return {"status": r["status"], "live_url": r["live_url"]}

    def delete(self, platform_post_id: str, connection: ConnectionRef,
               destination: DestinationRef | None = None) -> dict:
        self._send("DELETE", f"/{platform_post_id}", destination, connection, "delete", allow_status=(404, 410))
        return {"deleted": True}
