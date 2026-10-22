"""Connector manifest (one YAML per connector), validated against the Appendix A format."""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.errors import ErrorCode

CAPABILITIES = {"publish", "status", "delete", "metrics", "refresh_auth", "assist"}


class SurfaceSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    title_max_chars: int | None = None
    title_min_chars: int | None = None
    body_max_chars: int | None = None
    body_min_chars: int | None = None
    max_tags: int | None = None
    links_allowed: bool = True
    title_required: bool = False
    media_types: list[str] | None = None  # accepted MIME types; None = any
    max_media: int | None = None


class Surface(BaseModel):
    key: str
    content_types: list[str]
    spec: SurfaceSpec = Field(default_factory=SurfaceSpec)


class RateLimit(BaseModel):
    requests: int = Field(gt=0)
    per_seconds: int = Field(gt=0)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="allow")
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    tier: Literal["native", "protocol", "bridge", "feed", "assisted"]
    auth: Literal["oauth2", "api_key", "bot_token", "none"]
    surfaces: list[Surface] = Field(min_length=1)
    rate_limits: dict[str, RateLimit]
    capabilities: list[str]
    error_map: dict[str, ErrorCode] = Field(default_factory=dict)
    instance_param: str | None = None

    @field_validator("capabilities")
    @classmethod
    def _caps(cls, v: list[str]) -> list[str]:
        unknown = set(v) - CAPABILITIES
        if unknown:
            raise ValueError(f"unknown capabilities: {sorted(unknown)}")
        return v

    @field_validator("rate_limits")
    @classmethod
    def _rl(cls, v: dict) -> dict:
        if "per_connection" not in v:
            raise ValueError("rate_limits.per_connection is required")
        return v

    def surface(self, key: str | None) -> Surface:
        if key:
            for s in self.surfaces:
                if s.key == key:
                    return s
        return self.surfaces[0]


def load_manifest(path: Path) -> Manifest:
    with open(path, encoding="utf-8") as f:
        return Manifest.model_validate(yaml.safe_load(f))
