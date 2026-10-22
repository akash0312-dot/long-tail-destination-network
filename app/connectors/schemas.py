"""Request bodies of the connector contract (Appendix A). Names follow the brief literally."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.secret_refs import resolve_credentials


class Media(BaseModel):
    model_config = ConfigDict(extra="allow")
    url: str
    type: Literal["image", "video", "audio", "file"] = "image"
    mime_type: str | None = None
    alt: str | None = None


class Rendition(BaseModel):
    """One piece of content shaped for one destination."""
    model_config = ConfigDict(extra="allow")
    title: str = ""
    body: str = ""  # markdown
    body_html: str | None = None
    content_type: str = "text"  # text | image | video_link | article | video
    link_url: str | None = None
    canonical_url: str | None = None
    media: list[Media] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    mentions: list[str] = Field(default_factory=list)
    language: str = "en"
    ai_generated: bool = False
    ai_disclosure: str | None = None
    is_promotional: bool | None = None
    surface: str | None = None


class DestinationRef(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str | None = None
    name: str | None = None
    url: str | None = None
    surface: str | None = None
    language: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    # posting rules: when present, validate() checks them too (frequency cap needs history: orchestrator only)
    rules_url: str | None = None
    self_promo_allowed: bool | None = None
    link_policy: dict[str, Any] | None = None
    disclosure_required: bool | None = None
    automation_allowed: bool | None = None
    frequency_cap: dict[str, Any] | None = None


class ConnectionRef(BaseModel):
    """Either inline credentials (runtime-supplied) or secret references (env:/file:)."""
    model_config = ConfigDict(extra="allow")
    id: str | None = None
    auth_type: str | None = None
    credentials: dict[str, SecretStr] = Field(default_factory=dict)
    secret_refs: dict[str, str] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)

    def resolved(self) -> dict[str, str]:
        creds = resolve_credentials(self.secret_refs)
        for k, v in self.credentials.items():
            from app.logging_setup import register_secret
            register_secret(v.get_secret_value())
            creds[k] = v.get_secret_value()
        return creds


class ValidateRequest(BaseModel):
    rendition: Rendition
    destination: DestinationRef
    connection: ConnectionRef = Field(default_factory=ConnectionRef)


class PublishRequest(ValidateRequest):
    idempotency_key: str = Field(min_length=1, max_length=200)


class PostRefRequest(BaseModel):
    platform_post_id: str
    connection: ConnectionRef = Field(default_factory=ConnectionRef)
    destination: DestinationRef | None = None  # extension: lets the instance base_url come from the destination


class MetricsRequest(PostRefRequest):
    since: str | None = None


class RefreshAuthRequest(BaseModel):
    connection: ConnectionRef
    destination: DestinationRef | None = None


class AssistPackRequest(BaseModel):
    rendition: Rendition
    destination: DestinationRef
