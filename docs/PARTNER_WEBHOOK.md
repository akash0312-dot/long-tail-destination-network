# Partner webhook protocol v1

How a partner website receives posts from the Long-Tail Destination Network. The partner implements three endpoints
under one base URL (the destination's `endpoint_url`) and shares a signing secret with us out of band.

## Requests we send

| Call | Method + path | Body |
|---|---|---|
| Create post | `POST {endpoint_url}` | `{"event": "post.create", "idempotency_key": "...", "post": {...}}` |
| Read status | `GET {endpoint_url}/{id}` | none |
| Delete post | `DELETE {endpoint_url}/{id}` | none |

`post` fields: `title, body` (markdown), `body_html, content_type, link_url, canonical_url, media[] {url, type,
mime_type, alt}, tags[], mentions[], language, ai_generated, ai_disclosure`.

Headers on every request:

| Header | Value |
|---|---|
| `X-Signature-Timestamp` | Unix time in seconds |
| `X-Signature` | `sha256=` + hex HMAC-SHA256(secret, `"{timestamp}.{raw body}"`) (empty body for GET/DELETE) |
| `X-Protocol-Version` | `1` |
| `Idempotency-Key` | On create. The same key must return the same post |

## What the partner must do

1. **Verify the signature** with a constant-time compare and **reject timestamps older than 5 minutes** → `401`.
2. **Honour `Idempotency-Key`**: first time → create and answer `201`; repeated key → answer `200` with the existing
   post and header `Idempotent-Replayed: true`. This is what makes our retries after a timeout safe.
3. Answer create/status with `{"id": "...", "url": "https://partner.example/...", "status": "published"}`.
4. `404` or `410` on status/delete when the post no longer exists.
5. To revoke our access, answer `403` (we pause and ask the owner to reconnect). Rate limits: `429` with `Retry-After`.
   Content refused: `422` with `{"error": "reason"}`.

## Verifying a signature (Python)

```python
import hashlib, hmac, time

def verify(secret: str, timestamp: str, body: bytes, header: str) -> bool:
    if abs(time.time() - int(timestamp)) > 300:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
```
