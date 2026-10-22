"""Base class every connector extends. Subclasses implement publish/get_status/delete for one platform type."""
import hashlib
import re
from typing import Any
from urllib.parse import urlparse

from app.config import get_settings
from app.connectors.http import PlatformHttp
from app.connectors.manifest import Manifest, Surface
from app.connectors.schemas import ConnectionRef, DestinationRef, Rendition
from app.errors import AppError, ErrorCode
from app.rules import rules_of, stateless_checks

URL_RE = re.compile(r"https?://[^\s)>\]]+", re.IGNORECASE)


def key_marker(idempotency_key: str) -> str:
    """Short stable fingerprint of the idempotency key, embedded in the post so retries can find it."""
    return hashlib.sha256(idempotency_key.encode()).hexdigest()[:20]


def instance_slug(base_url: str) -> str:
    p = urlparse(base_url)
    raw = f"{p.netloc}{p.path}".strip("/")
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-") or "default"


def schema_error(op: str, missing: str, payload: Any) -> AppError:
    keys = sorted(payload.keys()) if isinstance(payload, dict) else type(payload).__name__
    return AppError(ErrorCode.PLATFORM_SCHEMA_CHANGED,
                    f"Platform response for {op} no longer has expected field '{missing}'",
                    {"op": op, "missing_field": missing, "received_keys": keys})


def violation_error(violations: list[dict]) -> AppError:
    """A posting-rule violation outranks a format violation: it is the reason a person needs to see."""
    rule = next((v for v in violations if v.get("field") == "rules"), None)
    if rule:
        return AppError(ErrorCode(rule["code"]), rule["message"], {"violations": violations})
    return AppError(ErrorCode.DEST_SPEC_VIOLATION, "Content breaks the destination's format limits",
                    {"violations": violations})


def require(data: Any, path: str, op: str) -> Any:
    """Strictly read a dotted path from a platform response; a renamed field raises PLATFORM_SCHEMA_CHANGED."""
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise schema_error(op, path, data)
        cur = cur[part]
    return cur


class BaseConnector:
    manifest: Manifest

    def __init__(self, manifest: Manifest):
        self.manifest = manifest
        self.key = manifest.key

    # ---- contract methods -------------------------------------------------------------------------
    def validate(self, rendition: Rendition, destination: DestinationRef, connection: ConnectionRef) -> dict:
        surface = self.surface_for(rendition, destination)
        violations = self.spec_violations(rendition, surface)
        violations += self.rule_violations(rendition, destination)
        return {"ok": not violations, "violations": violations}

    @staticmethod
    def rule_violations(rendition: Rendition, destination: DestinationRef) -> list[dict]:
        """Posting rules carried on the destination (no history needed, so no frequency cap here)."""
        rules = rules_of(destination)
        if all(v is None for v in rules.values()):
            return []
        checks, _, _ = stateless_checks(rendition.model_dump(), rules, get_settings().own_domain_list())
        return [{"field": "rules", "rule": c.rule, "code": c.code, "message": c.detail}
                for c in checks if not c.passed]

    def publish(self, rendition: Rendition, destination: DestinationRef, connection: ConnectionRef,
                idempotency_key: str) -> dict:
        raise self._unsupported("publish")

    def get_status(self, platform_post_id: str, connection: ConnectionRef,
                   destination: DestinationRef | None = None) -> dict:
        raise self._unsupported("get_status")

    def delete(self, platform_post_id: str, connection: ConnectionRef,
               destination: DestinationRef | None = None) -> dict:
        raise self._unsupported("delete")

    def fetch_metrics(self, platform_post_id: str, connection: ConnectionRef, since: str | None,
                      destination: DestinationRef | None = None) -> dict:
        raise self._unsupported("fetch_metrics")

    def refresh_auth(self, connection: ConnectionRef, destination: DestinationRef | None = None) -> dict:
        raise self._unsupported("refresh_auth")

    def build_assist_pack(self, rendition: Rendition, destination: DestinationRef) -> dict:
        raise self._unsupported("build_assist_pack")

    # ---- helpers ----------------------------------------------------------------------------------
    def _unsupported(self, method: str) -> AppError:
        return AppError(ErrorCode.METHOD_NOT_SUPPORTED, f"Connector '{self.key}' does not support {method}",
                        {"connector": self.key, "method": method})

    def surface_for(self, rendition: Rendition, destination: DestinationRef | None) -> Surface:
        key = rendition.surface or (destination.surface if destination else None)
        return self.manifest.surface(key)

    def spec_violations(self, r: Rendition, surface: Surface) -> list[dict]:
        spec = surface.spec
        v: list[dict] = []
        if spec.title_required and not r.title.strip():
            v.append({"field": "title", "code": "TITLE_REQUIRED", "message": "This destination requires a title"})
        if spec.title_max_chars is not None and len(r.title) > spec.title_max_chars:
            v.append({"field": "title", "code": "TITLE_TOO_LONG", "limit": spec.title_max_chars,
                      "actual": len(r.title),
                      "message": f"Title is {len(r.title)} characters; limit is {spec.title_max_chars}"})
        body = r.body_html or r.body
        if spec.body_max_chars is not None and len(body) > spec.body_max_chars:
            v.append({"field": "body", "code": "BODY_TOO_LONG", "limit": spec.body_max_chars, "actual": len(body),
                      "message": f"Body is {len(body)} characters; limit is {spec.body_max_chars}"})
        if r.content_type not in surface.content_types:
            v.append({"field": "content_type", "code": "CONTENT_TYPE_UNSUPPORTED", "actual": r.content_type,
                      "limit": surface.content_types,
                      "message": f"Content type '{r.content_type}' not accepted on surface '{surface.key}'"})
        if not spec.links_allowed and (r.link_url or URL_RE.search(r.body)):
            v.append({"field": "body", "code": "LINKS_NOT_ALLOWED", "message": "This surface does not accept links"})
        if spec.max_media is not None and len(r.media) > spec.max_media:
            v.append({"field": "media", "code": "TOO_MANY_MEDIA", "limit": spec.max_media, "actual": len(r.media),
                      "message": f"{len(r.media)} media items; limit is {spec.max_media}"})
        if spec.media_types is not None:
            for i, m in enumerate(r.media):
                mime = m.mime_type or _guess_mime(m.url, m.type)
                if mime not in spec.media_types:
                    v.append({"field": f"media[{i}]", "code": "MEDIA_TYPE_UNSUPPORTED", "actual": mime,
                              "limit": spec.media_types,
                              "message": f"Media type '{mime}' is not accepted by this destination"})
        return v

    def ensure_valid(self, rendition: Rendition, destination: DestinationRef, connection: ConnectionRef) -> None:
        result = self.validate(rendition, destination, connection)
        if not result["ok"]:
            raise violation_error(result["violations"])

    def base_url(self, destination: DestinationRef | None, connection: ConnectionRef) -> str:
        """Instance URL from destination params (instance_param), else connection params; simulation rewrites it."""
        param = self.manifest.instance_param or "base_url"
        url = None
        if destination is not None:
            url = destination.params.get(param) or destination.url
        url = url or connection.params.get(param)
        if not url:
            raise AppError(ErrorCode.VALIDATION_ERROR,
                           f"No instance URL: set destination.params.{param} or connection.params.{param}",
                           {"instance_param": param})
        url = str(url).rstrip("/")
        s = get_settings()
        if s.simulation_mode and not url.startswith(s.mock_platform_url.rstrip("/")):
            url = f"{s.mock_platform_url.rstrip('/')}/sim/{self.key}/{instance_slug(url)}"
        return url

    def http(self, base_url: str, connection: ConnectionRef, map_error=None, headers: dict | None = None):
        rate_key = f"{self.key}|{connection.id or base_url}"
        return PlatformHttp(connector_key=self.key, base_url=base_url, rate_key=rate_key,
                            rate_limit=self.manifest.rate_limits["per_connection"],
                            error_map=self.manifest.error_map, map_error=map_error, headers=headers)


_EXT_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp",
             "mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm", "mp3": "audio/mpeg",
             "pdf": "application/pdf", "svg": "image/svg+xml", "heic": "image/heic"}


def _guess_mime(url: str, kind: str) -> str:
    ext = urlparse(url).path.rsplit(".", 1)[-1].lower() if "." in urlparse(url).path else ""
    return _EXT_MIME.get(ext, f"{kind}/unknown")
