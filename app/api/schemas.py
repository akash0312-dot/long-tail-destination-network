from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from app.connectors.schemas import Rendition


class ConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")  # inline secrets are rejected: only references are stored
    connector_key: str
    name: str
    auth_type: str = "api_key"
    secret_refs: dict[str, str] = Field(default_factory=dict,
                                        description="name -> env:VAR or file:/path. Never the secret itself.")
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("secret_refs")
    @classmethod
    def _refs(cls, v: dict[str, str]) -> dict[str, str]:
        for name, ref in v.items():
            if not ref.startswith(("env:", "file:")):
                raise ValueError(f"secret_refs.{name} must be a reference (env:NAME or file:/path)")
        return v


class ConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str | None = Field(default=None, pattern="^(active|expired|revoked)$")
    secret_refs: dict[str, str] | None = None
    params: dict[str, Any] | None = None


class DestinationCreate(BaseModel):
    """One catalogue row. Adding a forum of a supported type needs nothing else."""
    model_config = ConfigDict(extra="forbid")
    name: str
    url: str
    connector_key: str
    platform_key: str | None = None
    surface: str = "post"
    params: dict[str, Any] = Field(default_factory=dict)
    connection_id: str | None = None
    category: str | None = None
    audience_tags: list[str] = Field(default_factory=list)
    language: str = "en"
    geo: str | None = None
    est_audience: int | None = None
    rules_url: str | None = None
    frequency_cap: dict | None = None
    self_promo_allowed: bool = False
    link_policy: dict | None = None
    disclosure_required: bool = False
    automation_allowed: bool = True
    relevance_score: int | None = Field(default=None, ge=1, le=5)
    relevance_reason: str | None = None
    level: str = Field(default="L0", pattern="^L[0-4]$")


class DestinationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    params: dict[str, Any] | None = None
    connection_id: str | None = None
    frequency_cap: dict | None = None
    self_promo_allowed: bool | None = None
    link_policy: dict | None = None
    disclosure_required: bool | None = None
    automation_allowed: bool | None = None
    enabled: bool | None = None
    level: str | None = Field(default=None, pattern="^L[0-4]$")


class CertificationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination_id: str
    callback_url: HttpUrl | None = None


class PublicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination_id: str
    rendition: Rendition
    callback_url: HttpUrl | None = None
    is_test: bool = False
    on_cap: Literal["reschedule", "reject"] = Field(
        default="reschedule", description="When the destination's frequency cap is reached: queue the post for "
                                          "the next free slot, or reject it")
