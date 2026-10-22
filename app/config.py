"""All settings come from environment variables (or .env). Nothing is hard-coded elsewhere."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # service
    service_name: str = "longtail-network"
    environment: str = "local"
    log_level: str = "INFO"
    public_base_url: str = "http://localhost:8000"
    internal_base_url: str = ""  # how the worker reaches this API (compose: http://api:8000); for live checks

    # database
    database_url: str = "sqlite:///./data/longtail.db"

    # demo protection
    api_token: str = ""  # empty = no auth (local dev); set on the live demo
    rate_limit_per_minute: int = 120

    # jobs
    job_executor: str = "inprocess"  # inprocess | worker
    worker_poll_interval_s: float = 1.0
    callback_signing_secret: str = ""
    callback_signature_header: str = "X-Signature"
    callback_timeout_s: float = 5.0

    # storage (S3-compatible: SeaweedFS in compose, any S3 in prod) or local filesystem for tests
    storage_backend: str = "local"  # local | s3
    storage_local_dir: str = "./data/storage"
    s3_endpoint_url: str = "http://localhost:8333"
    s3_public_endpoint_url: str = ""  # URL clients use for download links; defaults to s3_endpoint_url
    s3_bucket: str = "longtail-network"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"
    download_url_ttl_s: int = 3600

    # connectors
    connectors_dir: str = "app/connectors"
    disabled_connectors: str = ""  # comma-separated kill switch, e.g. "ghost,lemmy"
    connector_http_timeout_s: float = 10.0
    max_inline_backoff_s: float = 10.0  # 429 waits longer than this are returned as PLATFORM_RATE_LIMIT
    max_rate_limit_retries: int = 2
    publish_retries: int = 2  # in-job retries after a timeout / 5xx, same idempotency key
    circuit_breaker_threshold: int = 5
    circuit_breaker_cooldown_s: float = 60.0

    # simulation mode: every destination is served by the mock platform server, no real accounts needed
    simulation_mode: bool = True
    mock_platform_url: str = "http://localhost:9000"

    # idempotency
    idempotency_ttl_hours: int = 72

    # AI provider (rules capture, relevance): mock = deterministic heuristics, no key needed
    ai_provider: str = "mock"  # mock | anthropic
    ai_model: str = "claude-opus-5-5"
    ai_effort: str = "low"  # extraction and scoring are simple tasks
    ai_max_tokens: int = 4000
    ai_price_input_per_mtok: float = 4.0  # USD, claude-opus-5-5
    ai_price_output_per_mtok: float = 20.0
    ai_refusal_fallback: bool = True  # server-side fallback model if the main model declines

    # catalogue import
    catalogue_concurrency: int = 50
    catalogue_reachability_timeout_s: float = 5.0
    rules_fetch_max_bytes: int = 500_000
    crawler_user_agent: str = "LongTailDestinationBot/1.0 (+catalogue rules check)"
    # SSRF guard: refuse user-supplied URLs on private/loopback/single-label hosts. Only the local stack and tests
    # (whose mock platforms live on such hosts) turn this on.
    allow_internal_hosts: bool = False

    # posting rules and certification
    own_domains: str = ""  # comma-separated domains that count as self-promotion when linked
    require_certification: bool = True  # only certified destinations receive real posts
    certification_max_cleanup_s: int = 600  # test posts must be deleted within 10 minutes
    alert_webhook_url: str = ""  # optional: POST a JSON alert here when a destination is demoted
    recertify_after_days: int = 25  # re-test before the 30-day validity runs out
    scheduler_interval_s: int = 3600  # how often the worker looks for destinations due
    # short, rights-cleared clip used when certifying video destinations (PeerTube imports it from this URL)
    certification_test_video_url: str = "https://example.com/certification-test.mp4"

    def own_domain_list(self) -> list[str]:
        return [d.strip().lower() for d in self.own_domains.split(",") if d.strip()]

    def disabled_connector_set(self) -> set[str]:
        return {c.strip() for c in self.disabled_connectors.split(",") if c.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
