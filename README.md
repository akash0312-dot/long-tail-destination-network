# Long-Tail Destination Network

Containerised connector service that publishes creator content to long-tail destinations (forums, Lemmy communities,
developer blogs, Ghost, partner sites…) through a standard connector contract — with idempotent retries, rate-limit
backoff, error mapping to shared codes, and a mock platform server for simulation.

> Status: Day 1 of the build. See [PROGRESS.md](PROGRESS.md) for what works today and [docs/PLAN.md](docs/PLAN.md) for
> requirements, architecture and plan.

## Quick start (≤ 10 commands)

```bash
cp .env.example .env
docker compose up -d --build          # api, worker, postgres, S3 (SeaweedFS), mock platforms, Ghost, Lemmy, bootstrap
docker compose wait bootstrap         # provisions Ghost/Lemmy test accounts and seeds the catalogue
curl http://localhost:8000/ready
open http://localhost:8000/docs       # interactive API docs
```

## Tests (one command, no API keys)

```bash
docker compose --profile test run --rm tests       # unit + integration (simulation mode)
RUN_E2E=1 E2E_API_URL=http://localhost:8000 pytest tests/test_e2e_local.py   # real posts on local Ghost + Lemmy
```

## Architecture

```
client ──► API (/api/v1, /connector/v1) ──► jobs table ──► worker ──► connectors ──► Ghost | Lemmy | mock platforms
             │ idempotency · auth · rate limit · correlation id · JSON logs
             └── Postgres (catalogue, ledger, jobs, idempotency)   S3/SeaweedFS (manifests, evidence)
```
Full diagram and design decisions: [docs/PLAN.md §4](docs/PLAN.md#4-architecture).

## Connector contract

`POST /connector/v1/{connector_key}/{method}` with JSON bodies — `validate`, `publish`, `get_status`, `delete`,
`fetch_metrics`, `refresh_auth`, `build_assist_pack`. Manifests: `app/connectors/<key>/manifest.yaml`.
Adding a connector = a new folder with `manifest.yaml` + `connector.py`. Adding a destination = one catalogue row
(`POST /api/v1/destinations`).

| Connector | Tier | Status |
|---|---|---|
| ghost | native | publish / status / delete / refresh_auth — verified on local Ghost 5 |
| lemmy | native | publish / status / delete / refresh_auth — verified on local Lemmy 0.19.8 |

## Error codes

All errors: `{"error": {"code", "message", "retryable", "details"}}`.

| Code | HTTP | Retry | Meaning |
|---|---|---|---|
| `DEST_SPEC_VIOLATION` | 422 | no | Content breaks format limits; `details.violations[]` has field-level reasons |
| `DEST_RULE_FREQUENCY_CAP` | 409 | later | Frequency cap reached; reschedule after the window |
| `CONNECTION_AUTH_EXPIRED` | 401 | after reconnect | Token expired/revoked; owner must reconnect |
| `PLATFORM_RATE_LIMIT` | 429 | yes | Platform rate limit; `details.retry_after_s` from the platform's reset time |
| `PLATFORM_QUOTA_EXHAUSTED` | 429 | next window | Daily quota used up |
| `PLATFORM_CONTENT_REJECTED` | 422 | no | Platform refused the content; `details.platform_message` |
| `PLATFORM_UNAVAILABLE` | 503 | yes | Timeout / 5xx / unreachable; circuit opens after 5 consecutive failures |
| `CONNECTOR_DISABLED` | 503 | no | Kill switch on (`DISABLED_CONNECTORS`) |
| `PLATFORM_SCHEMA_CHANGED` | 502 | no | *Extension:* platform response lost an expected field (renamed API) |
| `CONNECTOR_NOT_FOUND` | 404 | no | *Extension:* unknown connector key |
| `METHOD_NOT_SUPPORTED` | 405 | no | *Extension:* connector lacks this optional method |
| `VALIDATION_ERROR` | 400 | no | Request body invalid |
| `NOT_FOUND` | 404 | no | Resource not found |
| `UNAUTHORIZED` | 401 | no | Missing/invalid API token (live demo) |
| `RATE_LIMITED` | 429 | yes | Too many requests to this API |
| `IDEMPOTENCY_KEY_REQUIRED` | 400 | no | POST under /api/v1 without `Idempotency-Key` |
| `IDEMPOTENCY_KEY_CONFLICT` | 409 | no | Key reused with a different body |
| `IDEMPOTENCY_REQUEST_IN_PROGRESS` | 409 | yes | Same key still processing |
| `SECRET_UNAVAILABLE` | 500 | no | A secret reference could not be resolved |
| `INTERNAL_ERROR` | 500 | yes | Unexpected error (logged with correlation id) |

## Settings

Every setting is an environment variable; see [.env.example](.env.example) for the full annotated list.

## Secrets

The database stores only secret *references* (`env:NAME`, `file:/run/app-secrets/x`). Logs redact JWTs,
`Bearer`/`Ghost` tokens and every resolved secret value. Local Ghost credentials are generated at bootstrap and kept
in the `app-secrets` Docker volume.
