# Long-Tail Destination Network

Containerised connector service that publishes creator content to long-tail destinations (forums, Lemmy communities,
developer blogs, Ghost, partner sites…) through a standard connector contract — with idempotent retries, rate-limit
backoff, error mapping to shared codes, and a mock platform server for simulation.

> Status: Day 3 of the build (Core + Advanced implemented). See [PROGRESS.md](PROGRESS.md) for what works today and [docs/PLAN.md](docs/PLAN.md) for
> requirements, architecture and plan.

## Quick start (≤ 10 commands)

```bash
cp .env.example .env
docker compose up -d --build          # api, worker, postgres, S3 (SeaweedFS), mock platforms, Ghost, Lemmy, bootstrap
docker compose wait bootstrap         # test accounts, 6 seeded destinations (one per Core connector), certifies them
curl http://localhost:8000/ready
open http://localhost:8000/docs       # interactive API docs
```

## Tests (one command, no API keys)

```bash
docker compose --profile test run --rm tests       # unit + integration + simulated end-to-end
RUN_E2E=1 E2E_API_URL=http://localhost:8000 pytest tests/test_e2e_local.py   # real posts on local Ghost + Lemmy
```

## Batch runner

```bash
python -m app.batch --input test_inputs --output test_pack/my_run    # all hard-case scenarios against the stack
```
Each scenario in `test_inputs/` creates its own destination, certifies it, then exercises one hard case. Results go
to `<output>/<scenario>/steps.json` plus `summary.json` / `summary.md`; the exit code is 1 if any check fails.

## How a post flows

1. `POST /api/v1/publications` (with `Idempotency-Key`) → `202` + `job_id`.
2. Worker: **certification gate** (only certified destinations; demoted ones are excluded) → **posting rules**
   (frequency cap → reschedule; self-promotion, link policy, language → block with reason; AI disclosure → appended
   in the community's language) → **format validation** → **publish** (timeouts retried with the same key; long
   rate limits rescheduled to the platform's reset time) → ledger + manifest.
3. Destinations that ban automation get an **assist pack** (caption, deep link, rules checklist) instead of a post.

## Certification

`POST /api/v1/certifications {"destination_id"}` runs: rules recorded → format limits → real test post → live URL
answers → status published → delete → status deleted, within 10 minutes. Evidence (`run.json`, live snapshot) goes to
object storage. Pass → level L1/L2/L3 by tier; fail → demoted to L0, excluded, alert written. A created test post is
always cleaned up, even when a step fails.

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
| ghost | native | publish / status / delete / refresh_auth — certified on local Ghost 5 |
| lemmy | native | publish / status / delete / refresh_auth — certified on local Lemmy 0.19.8 |
| discourse | native | publish / status / delete — certified on the mock (Discourse API) |
| devto | native | publish / status / delete (= unpublish; the Forem API cannot delete) — certified on the mock |
| webhook | protocol | partner sites, HMAC-signed ([protocol](docs/PARTNER_WEBHOOK.md)) — certified on the mock partner |
| assisted | assisted | build_assist_pack for communities where a person must post |
| mrss | feed | hosted Media RSS feed per destination at `/api/v1/feeds/{feed_key}.xml` (public) — certifies at L2 |
| hashnode | native | GraphQL publish / status / remove — certified on the mock |
| peertube | native | OAuth, video import from URL, status, delete, refresh_auth — certified on the mock |

## Catalogue, relevance, rules capture, tailoring

- `POST /api/v1/catalogue/imports` (CSV `file` upload or `csv_url`) → job: validation, de-duplication (normalised
  URL + platform), language detection, reachability, member-count enrichment, relevance 1–5 with a reason, and a
  row-by-row `report.csv`. 1,000 rows take about 10 s.
- `POST /api/v1/destinations/{id}/rules/capture` → job: reads the rules page (robots.txt honoured), proposes posting
  rules with evidence quotes. `POST /api/v1/rule-proposals/{id}/confirm` (named reviewer, optional corrections) is the
  only way they are applied; `/reject` discards them.
- `POST /api/v1/tailor` previews each destination's rendition with its list of changes; `tailor: true` on a
  publication applies it.
- AI provider: `AI_PROVIDER=mock` (default: deterministic heuristics, no key) or `anthropic` (Claude, model from
  `AI_MODEL`, token cost in every result manifest).

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
| `DEST_RULE_SELF_PROMO` | 422 | no | *Extension:* destination bans self-promotion and the content is (or may be) promotional |
| `DEST_RULE_LINK_POLICY` | 422 | no | *Extension:* links banned, too many, disallowed domain or URL shortener |
| `DEST_RULE_DISCLOSURE_REQUIRED` | 422 | no | *Extension:* AI-content disclosure required but missing (contract path) |
| `DEST_RULE_LANGUAGE_MISMATCH` | 422 | no | *Extension:* content language differs from the community's |
| `DEST_RULE_AUTOMATION_NOT_ALLOWED` | 422 | no | *Extension:* a person must post; use an assist pack |
| `DEST_NOT_CERTIFIED` | 409 | no | *Extension:* destination not certified, or demoted after a failed certification |
| `CERTIFICATION_FAILED` | 422 | no | *Extension:* a certification step failed; destination demoted to L0 |
| `PLATFORM_SCHEMA_CHANGED` | 502 | no | *Extension:* platform response lost an expected field (renamed API) |
| `AI_PROVIDER_ERROR` | 502 | depends | *Extension:* the AI provider failed (rate limit, credentials, cut-off answer) |
| `AI_REFUSED` | 422 | no | *Extension:* the AI model declined the request (`details.category`) |
| `RULES_PAGE_UNAVAILABLE` | 422 | no | *Extension:* rules page blocked by robots.txt, unreachable, internal or empty |
| `CATALOGUE_INPUT_INVALID` | 400 | no | *Extension:* CSV missing name/url columns, not UTF-8, too large, or URL not public |
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

## Live demo

The same stack plus Caddy for HTTPS: `docker compose -f docker-compose.yml -f deploy/docker-compose.demo.yml up -d`.
Only `/docs`, `/health`, `/ready`, `/openapi.json` and the MRSS feeds are public; everything else needs the API token
(use **Authorize** in `/docs`), and object storage, Ghost, Lemmy and Postgres are never exposed. Step-by-step server
setup, settings and checks: [deploy/DEPLOY.md](deploy/DEPLOY.md).

## Settings

Every setting is an environment variable; see [.env.example](.env.example) for the full annotated list.

## Secrets

The database stores only secret *references* (`env:NAME`, `file:/run/app-secrets/x`). Logs redact JWTs,
`Bearer`/`Ghost` tokens and every resolved secret value. Local Ghost credentials are generated at bootstrap and kept
in the `app-secrets` Docker volume.
