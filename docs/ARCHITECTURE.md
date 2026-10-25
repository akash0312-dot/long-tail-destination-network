# Architecture

```
                        ┌──────────────────────────── docker compose ─────────────────────────────┐
 Client / batch CLI     │                                                                          │
  ─────────────────────►│  API  (FastAPI, /api/v1/*, /connector/v1/*)                              │
  Idempotency-Key       │   middleware: auth token · rate limit · X-Correlation-Id · JSON logs     │
  X-Correlation-Id      │               idempotency store · error envelope                         │
                        │   ├─ /destinations /connections      (catalogue = configuration)         │
                        │   ├─ /publications ─────┐                                                │
                        │   ├─ /certifications ───┼─► jobs table (Postgres) ─► worker process      │
                        │   ├─ /catalogue/imports ┘          ▲                    │                 │
                        │   ├─ /rules/capture, /rule-proposals/{id}/confirm       ▼                 │
                        │   ├─ /tailor, /recommendations      │      For a publication:             │
                        │   └─ /jobs/{id}  ◄── status/progress┘       1 certification gate          │
                        │                                             2 tailor (format + style)     │
                        │                                             3 rules engine → ALLOW /      │
                        │                                               BLOCK(reason)/RESCHEDULE    │
                        │                                             4 connector validate+publish  │
                        │                                             5 ledger + manifest → S3      │
                        │                                             6 HMAC-signed callback        │
                        │  Connectors (one folder + manifest.yaml each)                            │
                        │   ghost · lemmy · discourse · devto · webhook (HMAC) · assisted          │
                        │   hashnode · mrss · peertube                                             │
                        │   shared HTTP client: per-connection rate limit, 429 backoff on the      │
                        │   reset time, error_map, circuit breaker (5 failures), kill switch;      │
                        │   SIMULATION_MODE sends every destination to the mock platform server    │
                        │        │                 │                    │                          │
                        │   Lemmy 0.19 (local)  Ghost 5 (local)   Mock platforms (Ghost, Lemmy,    │
                        │   + its Postgres      (SQLite)          Discourse, Dev.to, Hashnode,     │
                        │                                         PeerTube, partner webhook) with  │
                        │                                         fault injection                  │
                        │  Postgres: destinations, connections (secret references only), rules,    │
                        │            publications ledger, idempotency keys, jobs, certification    │
                        │            runs, tickets                                                 │
                        │  S3 (SeaweedFS): evidence, assist packs, manifests, reports, feeds       │
                        │  AI adapter (AI_PROVIDER): mock (default) | anthropic (Claude)           │
                        │  Scheduler (in the worker loop): re-certify before 30 days → demote,     │
                        │  exclude, alert, ticket                                                  │
                        └──────────────────────────────────────────────────────────────────────────┘
```

The live demo adds Caddy in front (HTTPS, only ports 80/443 public): [deploy/DEPLOY.md](../deploy/DEPLOY.md).

## Stack

Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Postgres 16 (SQLite in unit tests), httpx, PyJWT, PyYAML, Markdown,
boto3 against SeaweedFS (S3-compatible), pytest. Licences: [SOURCES.md](../SOURCES.md).

- **SeaweedFS instead of MinIO.** MinIO's public images could no longer be pulled, which would break `docker compose
  up` on a clean machine. SeaweedFS speaks the S3 API and is Apache-2.0; any S3 store can be swapped in through
  `S3_ENDPOINT_URL`.
- **A jobs table in Postgres instead of Redis + a queue library.** A job and its idempotency record are written in
  one transaction, so "same key → same job" is atomic, and there is one less service to fail. The worker claims jobs
  with `SELECT … FOR UPDATE SKIP LOCKED`, so several workers can run; `run_after` schedules rescheduled posts.

## Key design decisions

- **Two-layer idempotency.** (a) API: each `Idempotency-Key` is stored with a hash of the request; the same key and
  body return the stored response (the same job), and the same key with a different body is refused with
  `409 IDEMPOTENCY_KEY_CONFLICT`. (b) Connector: the key is embedded in the post (a Ghost slug, a hidden marker on
  Lemmy and Discourse, the partner's `Idempotency-Key` header, the feed item's guid), and `publish` first looks the
  post up on the platform. A retry after a lost response (timeout) returns the existing post instead of creating a
  second one.
- **The rules engine is pure and deterministic.** Input: the destination's rules, its publication ledger and the
  content. Output: `ALLOW`, `BLOCK(code, reason)` or `RESCHEDULE(not_before, reason)`. When unsure about promotion it
  blocks. AI only *proposes* rules from a rules page; a named person confirms them before they are used.
- **Strict response checks** in connectors: a renamed field raises `PLATFORM_SCHEMA_CHANGED`, so certification fails
  and the destination is demoted instead of posting blindly.
- **Mock platform server with fault injection** (`POST /__admin/faults`): timeout after create, 429 with a reset time,
  401, 422, 5xx, renamed fields. Every hard case is an automated test and a batch-runner scenario.
- **Secrets:** the database stores only secret *references* (`env:NAME`, `file:/run/app-secrets/x`); a log filter
  redacts tokens and every resolved secret value; gitleaks runs in CI.
- **Simulation mode:** `SIMULATION_MODE=true` points every destination at the mock platform server, so the whole
  system runs with no accounts.
