# Progress Tracker

Plan and full requirements: [`docs/PLAN.md`](docs/PLAN.md). Status keys: ✅ done · 🟡 partial / in progress · ⬜ not started · ❌ blocked.
Every ✅ must link to evidence (test, file, log) — this feeds the Submission Form. Mark honestly: a false "Yes" in the
form disqualifies; "Partial" with a reason does not.

**Test status (2026-10-22):** `docker compose --profile test run --rm tests` → 50 passed, 2 skipped (E2E needs stack).
`RUN_E2E=1 pytest tests/test_e2e_local.py` → 2 passed (real local Ghost 5 + Lemmy 0.19.8).

## Must-haves
| # | Must-have | Status | Evidence | Notes |
|---|---|---|---|---|
| 1 | Exact connector contract | 🟡 | `app/connectors/router.py`, `tests/test_contract.py` | All 7 methods routed, manifests validated, shared codes. Exact JSON field names unconfirmed → clarification email |
| 2 | Six Core connectors certified | 🟡 | `tests/test_e2e_local.py` | Ghost ✅ Lemmy ✅ (publish/status/delete on real local instances). Discourse, Dev.to, webhook, assisted ⬜. Certification harness + stored evidence ⬜ |
| 3 | Configuration, not code | 🟡 | `tests/test_connectors.py::test_one_connector_many_destinations`, `POST /api/v1/destinations` | Works for Ghost/Lemmy; re-verify for each new connector |
| 4 | Rules checked before every post | ⬜ | | Day 2 |
| 5 | Never spam, never cheat | 🟡 | `app/connectors/http.py` (429 reset-time backoff, client rate limit, circuit breaker); E2E deletes test posts in seconds | Evidence pack + log review ⬜ |
| 6 | Safe retries (no duplicate after timeout) | ✅ | `tests/test_connectors.py::test_timeout_then_retry_same_key_never_duplicates` (both connectors), `tests/test_api.py::test_same_idempotency_key_returns_same_job_and_one_post` | Mutation-checked: removing reconcile makes the test fail. Real-instance timeout test ⬜ |
| 7 | Secrets protected + simulation mode | 🟡 | `tests/test_logging.py`; container log scan: 0 secrets, 4 redactions; `SIMULATION_MODE=true` | gitleaks in CI ⬜ |
| 8 | `docker compose up` with local Lemmy + Ghost + mocks | 🟡 | `docker-compose.yml`, `bootstrap/bootstrap.py` | Works on dev machine. **Clean-machine test ⬜** |
| 9 | Integration Standard + live demo up | ⬜ | | Live demo not deployed |

## Levels
| Level | Item | Status | Evidence |
|---|---|---|---|
| Core | All must-haves | ⬜ | |
| Core | Full certification run per Core connector | ⬜ | |
| Core | Platform errors mapped to shared codes | 🟡 | Ghost + Lemmy: 429, expired token, rejected, 5xx, schema change — `tests/test_connectors.py` |
| Advanced | Catalogue import (1,000 rows < 5 min, row report) | ⬜ | |
| Advanced | AI rules capture + human confirm | ⬜ | |
| Advanced | Relevance score 1–5 + reason | ⬜ | |
| Advanced | Tailoring | ⬜ | |
| Advanced | Three more connectors | ⬜ | |
| Outstanding | Recommender + precision@10 | ⬜ | |
| Outstanding | fetch_metrics on ≥ 3 connectors | ⬜ | |
| Outstanding | Recertification scheduler | ⬜ | |
| Outstanding | 150+ real destinations | ⬜ | |

## Integration Standard
| # | Point | Status | Evidence |
|---|---|---|---|
| 1 | One-command start and test | 🟡 | `docker compose up -d`; `docker compose --profile test run --rm tests`; `.env.example`. Real-instance E2E is a separate command |
| 2 | openapi.yaml matches service | ⬜ | FastAPI serves OpenAPI 3.1 at `/openapi.json`; export + match test ⬜ |
| 3 | Background jobs (202, job status, HMAC callback) | ✅ | `app/jobs.py`, `tests/test_api.py::test_publication_job_end_to_end`, `::test_callback_is_hmac_signed` |
| 4 | Idempotency-Key on every POST | ✅ | `app/middleware.py`, `tests/test_api.py` (required / replay / conflict) |
| 5 | JSON error envelope, codes in README | ✅ | `app/errors.py`, `README.md#error-codes` |
| 6 | All configuration from env | ✅ | `app/config.py`, `.env.example` |
| 7 | Files in (upload/URL), files out (S3 links) | 🟡 | Out: manifests to SeaweedFS with signed links. In: ⬜ |
| 8 | JSON logs + correlation id, /health, /ready | ✅ | `app/logging_setup.py`, `tests/test_api.py::test_correlation_id_is_echoed`, `::test_health_and_ready` |
| 9 | Output manifest on every result | 🟡 | Publication results ✅ (`app/output_manifest.py`); other job kinds as they are added |
| 10 | Batch runner CLI | ⬜ | |

## Hard cases
| # | Situation | Status | Test |
|---|---|---|---|
| 1 | Timeout + retry same idempotency key | ✅ | `test_timeout_then_retry_same_key_never_duplicates` |
| 2 | 429 with reset; expired token; content rejected | ✅ | `test_429_*`, `test_expired_token_*`, `test_content_rejected_*` |
| 3 | Title/body too long; unsupported media | ✅ | `test_title_too_long_*`, `test_unsupported_media_*` |
| 4 | 2nd post in frequency cap; promo to no-promo | ⬜ | Rules engine (Day 2) |
| 5 | Renamed API field → cert fails, demoted | 🟡 | Detection ✅ `test_renamed_api_field_is_schema_changed`; demotion needs certification harness |
| 6 | Bad CSV; partner revokes permission | 🟡 | Revoked ✅ `test_revoked_connection_fails_with_reconnect_message`; CSV ⬜ |
| 7 | Non-English community; AI disclosure required | ⬜ | |

## Deliverables
| Item | Status |
|---|---|
| README.md | 🟡 (Day 1 version) |
| openapi.yaml | ⬜ |
| REPORT.md | ⬜ |
| SOURCES.md / AI_USE.md | ⬜ |
| Test pack | ⬜ |
| Certification pack | ⬜ |
| Catalogue files | ⬜ |
| Demo video | ⬜ |
| Live demo deployed | ⬜ |
| Clarification email sent to the hiring team | ⬜ (questions drafted in `docs/PLAN.md` §5) |
| Git repo with real commit history | 🟡 local repo on `main`, first commit 2026-10-22; private GitHub repo + reviewer invite ⬜ |
| Submission Form filled | ⬜ |

## Decisions log
| Date | Decision | Why |
|---|---|---|
| 10-22 | DB-backed job queue (no Redis) | Job + idempotency in one transaction; one less service on a clean machine |
| 10-22 | SeaweedFS instead of MinIO | MinIO public images no longer pullable (Docker Hub + Quay refuse); Apache-2.0 vs AGPL |
| 10-22 | Reconcile-before-publish idempotency (Ghost slug, Lemmy hidden marker) | Only way to survive "created but response lost" timeouts |
| 10-22 | `PLATFORM_SCHEMA_CHANGED` extension code | Renamed-field case needs a non-retryable, specific code; to confirm with the reviewers |
| 10-22 | Ghost owner password generated at bootstrap | Ghost rejects weak/"ghost"-containing passwords; also keeps credentials out of the repo |

## Daily log
### Day 1 — 2026-10-22
- Created `docs/PLAN.md` (full analysis, architecture, risk register, plan) and this tracker.
- Built: config, JSON logging with redaction, error envelope, DB models, jobs (sync/inprocess/worker) + HMAC callbacks,
  Idempotency-Key middleware, auth + rate limit, S3/local storage, output manifest, connector framework (manifest
  validation, HTTP client with rate limiting / 429 reset-time backoff / circuit breaker / error mapping, kill switch,
  simulation mode), Ghost + Lemmy connectors, mock platform server with fault injection, compose stack with real
  Ghost + Lemmy + bootstrap, 52 tests.
- Issues found and fixed: MinIO images gone → SeaweedFS; Ghost rejects "insecure" passwords → generated; Ghost 5 returns
  full `id:secret` in `secret` → handled; redaction over-matched "ghost is up" → token-shaped only; pytest import path in
  container.
- Observed: after a failed auth the connection is marked `expired` and posting stops until the owner reconnects
  (correct per Appendix A). **Day 2:** add `POST /api/v1/connections/{id}/reconnect` (calls `refresh_auth`, reactivates).
- `git init` on `main`; first commit of the Day 1 work. Commit after each feature from here on (brief checks history).
- Still to do for Day 1 goals: clarification email, push to a private GitHub repo, deploy skeleton to a VM,
  clean-machine compose test.
