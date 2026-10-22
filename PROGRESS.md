# Progress Tracker

Plan and full requirements: [`docs/PLAN.md`](docs/PLAN.md). Status keys: ✅ done · 🟡 partial / in progress · ⬜ not started · ❌ blocked.
Every ✅ must link to evidence (test, file, log) — this feeds the Submission Form. Mark honestly: a false "Yes" in the
form disqualifies; "Partial" with a reason does not.

**Test status (2026-10-22, end of Day 2):**
- `docker compose --profile test run --rm tests` → **136 passed**, 2 skipped (the 2 real-instance E2E tests need the stack)
- `RUN_E2E=1 pytest tests/test_e2e_local.py` → **2 passed** (real local Ghost 5 + Lemmy 0.19.8)
- `python -m app.batch --input test_inputs ...` → **11/11 scenarios passed** ([test_pack/batch_2026-10-22_run2](test_pack/batch_2026-10-22_run2/summary.md))
- Bootstrap on a fresh stack → **6/6 seeded destinations certified** (Ghost L3, Lemmy L3, Discourse L3, Dev.to L3, webhook L3, assisted L1)

## Must-haves
| # | Must-have | Status | Evidence | Notes |
|---|---|---|---|---|
| 1 | Exact connector contract | 🟡 | `app/connectors/router.py`, `tests/test_contract.py`, 6 manifests | All 7 methods routed, manifests validated, shared codes. Exact JSON field names unconfirmed → clarification email |
| 2 | Six Core connectors certified | 🟡 | `tests/test_certification.py`, bootstrap log, `tests/test_e2e_local.py` | All 6 certify. Ghost + Lemmy on real local instances; Discourse, Dev.to, webhook on faithful mocks — **ask whether mock certification is accepted**. Certification pack export to repo ⬜ |
| 3 | Configuration, not code | ✅ | `test_one_connector_many_destinations` (all 5 posting connectors); every batch scenario adds its destination as one API row | |
| 4 | Rules checked before every post | ✅ | `tests/test_rules.py` (21-case rule set + API tests), batch `04a`, `04b`, `07` | Also enforced on the raw contract path via `validate()` |
| 5 | Never spam, never cheat | 🟡 | 429 reset-time backoff (`app/connectors/http.py`), certification cleanup always deletes test posts (`test_lost_publish_response_is_still_cleaned_up`), only own instances/mocks used | Log review evidence + written policy in REPORT ⬜ |
| 6 | Safe retries (no duplicate after timeout) | ✅ | `test_timeout_then_retry_same_key_never_duplicates` (5 connectors), `test_publication_job_retries_timeout_with_same_key_and_never_duplicates`, batch `01` | Mutation-checked on Day 1 |
| 7 | Secrets protected + simulation mode | 🟡 | `tests/test_logging.py`; container log scan clean on Day 1; `SIMULATION_MODE=true` | gitleaks in CI ⬜; repeat log scan ⬜ |
| 8 | `docker compose up` with local Lemmy + Ghost + mocks | 🟡 | `docker-compose.yml`, `bootstrap/bootstrap.py`; fresh-volume start worked twice today | **Clean-machine test ⬜** |
| 9 | Integration Standard + live demo up | ⬜ | | Live demo not deployed |

## Levels
| Level | Item | Status | Evidence |
|---|---|---|---|
| Core | All must-haves | ⬜ | blocked on 1, 2, 5, 7, 8, 9 above |
| Core | Full certification run per Core connector | 🟡 | `test_certification_passes_and_promotes` (6 connectors); bootstrap certifies all 6 on the stack. Export of evidence files ⬜ |
| Core | Platform errors mapped to shared codes | ✅ | `tests/test_connectors.py` matrix × 5 connectors: 429, expired token, rejected, 5xx/circuit, schema change |
| Advanced | Catalogue import (1,000 rows < 5 min, row report) | ⬜ | Day 3 |
| Advanced | AI rules capture + human confirm | ⬜ | Day 3 |
| Advanced | Relevance score 1–5 + reason | ⬜ | Day 3 |
| Advanced | Tailoring | 🟡 | disclosure in community language, Forem tag normalisation done; title/text framing, truncation ⬜ |
| Advanced | Three more connectors | ⬜ | Day 3 (MRSS, Hashnode, PeerTube) |
| Outstanding | Recommender + precision@10 | ⬜ | |
| Outstanding | fetch_metrics on ≥ 3 connectors | ⬜ | |
| Outstanding | Recertification scheduler | 🟡 | demotion + alert done; scheduler ⬜ |
| Outstanding | 150+ real destinations | ⬜ | |

## Integration Standard
| # | Point | Status | Evidence |
|---|---|---|---|
| 1 | One-command start and test | ✅ | `docker compose up -d`; `docker compose --profile test run --rm tests` (unit, integration, simulated E2E, no keys); `.env.example` |
| 2 | openapi.yaml matches service | ✅ | `openapi.yaml`, `tests/test_openapi.py` (drift test), `scripts/export_openapi.py` |
| 3 | Background jobs (202, job status, HMAC callback) | ✅ | `app/jobs.py`, `test_publication_job_end_to_end`, `test_callback_is_hmac_signed`; scheduled jobs (`run_after`) |
| 4 | Idempotency-Key on every POST | ✅ | `app/middleware.py`, `tests/test_api.py`, batch `01` (`client_retry` gets the same job) |
| 5 | JSON error envelope, codes in README | ✅ | `app/errors.py`, `README.md#error-codes` |
| 6 | All configuration from env | ✅ | `app/config.py`, `.env.example` |
| 7 | Files in (upload/URL), files out (S3 links) | 🟡 | Out: manifests, evidence, assist packs in SeaweedFS with links. In: ⬜ (Day 3 with CSV import) |
| 8 | JSON logs + correlation id, /health, /ready | ✅ | `app/logging_setup.py`, `test_correlation_id_is_echoed`, `test_health_and_ready` |
| 9 | Output manifest on every result | 🟡 | publication, assist pack, certification ✅; catalogue jobs as they are added |
| 10 | Batch runner CLI | ✅ | `app/batch.py`, `test_inputs/`, `test_pack/` |

## Hard cases
| # | Situation | Status | Test / scenario |
|---|---|---|---|
| 1 | Timeout + retry same idempotency key | ✅ | `test_timeout_then_retry_same_key_never_duplicates`; batch `01` |
| 2 | 429 with reset; expired token; content rejected | ✅ | connector matrix; batch `02a`, `02b`, `02c` |
| 3 | Title/body too long; unsupported media | ✅ | connector matrix; batch `03` |
| 4 | 2nd post in frequency cap; promo to no-promo | ✅ | `tests/test_rules.py`; batch `04a`, `04b` |
| 5 | Renamed API field → cert fails, demoted | ✅ | `test_renamed_field_fails_certification_demotes_alerts_and_cleans_up`; batch `05` |
| 6 | Bad CSV; partner revokes permission | 🟡 | Revocation ✅ (`test_partner_revokes_permission_pauses_connection`, batch `06`); CSV ⬜ (Day 3) |
| 7 | Non-English community; AI disclosure required | ✅ | `test_non_english_community_rejects_english_content`, `test_ai_disclosure_appended_*`; batch `07` |

## Deliverables
| Item | Status |
|---|---|
| README.md | 🟡 (Day 2 version) |
| openapi.yaml | ✅ |
| REPORT.md | ⬜ |
| SOURCES.md / AI_USE.md | ⬜ |
| Test pack | 🟡 `test_pack/` batch runs; claim → file index ⬜ |
| Certification pack | 🟡 evidence in object storage; export to repo + screenshots ⬜ |
| Catalogue files | ⬜ |
| Demo video | ⬜ |
| Live demo deployed | ⬜ |
| Clarification email sent to the hiring team | ⬜ (questions drafted in `docs/PLAN.md` §5) |
| Git repo with real commit history | 🟡 7 commits on `main`; push to private GitHub repo `long-tail-destination-network` waiting on `gh auth login` |
| Submission Form filled | ⬜ |

## Decisions log
| Date | Decision | Why |
|---|---|---|
| 10-22 | DB-backed job queue (no Redis) | Job + idempotency in one transaction; one less service on a clean machine |
| 10-22 | SeaweedFS instead of MinIO | MinIO public images no longer pullable (Docker Hub + Quay refuse); Apache-2.0 vs AGPL |
| 10-22 | Reconcile-before-publish idempotency (slug / hidden marker / partner Idempotency-Key) | Only way to survive "created but response lost" timeouts |
| 10-22 | `PLATFORM_SCHEMA_CHANGED` and `DEST_RULE_*` extension codes | Specific, non-retryable reasons; to confirm with the reviewers |
| 10-22 | Ghost owner password generated at bootstrap | Ghost rejects weak passwords; keeps credentials out of the repo |
| 10-22 | Neutral naming throughout; callback signature header configurable (`CALLBACK_SIGNATURE_HEADER`) | Owner's request: the company name appears nowhere in the repo. Set the header the brief names in the deployment `.env` |
| 10-22 | Rules engine is pure and blocks when unsure about promotion | Deterministic, testable; a missed violation is worse than a false block |
| 10-22 | Capped posts become scheduled jobs (`run_after`), `on_cap=reject` optional | "Blocked or rescheduled with a reason" — rescheduling keeps the creator's intent |
| 10-22 | Destinations that ban automation get an assist pack automatically | A person posts; nothing is automated where it is not allowed |
| 10-22 | Dev.to `delete` = unpublish, reported in `note` | Public Forem API has no delete; honest about it |
| 10-22 | Certification cleanup re-publishes with the same key to find a lost post — only after a publish was attempted | Never leave a test post behind; never create one that was not needed (bug caught by a test) |
| 10-22 | Local-only default passwords for Lemmy/Postgres stay in `.env.example` for now | They only open containers we own, bound to 127.0.0.1; generating them needs a config renderer that risks R1. Revisit before submission |

## Daily log
### Day 1 — 2026-10-22
- Plan + tracker; service core (config, JSON logs with redaction, error envelope, models, jobs + HMAC callbacks,
  Idempotency-Key middleware, auth + rate limit, storage, output manifest); connector framework; Ghost + Lemmy
  connectors; mock platform server with fault injection; compose with real Ghost + Lemmy + bootstrap.
- Fixed: MinIO images gone → SeaweedFS; Ghost "insecure password"; Ghost 5 key format; redaction over-match; pytest
  import path in container.

### Day 2 — 2026-10-22
- Removed the company name from code, docs, identifiers and git history (history squashed before the first push).
- **Rules engine** (cap → reschedule, self-promo, link policy, AI disclosure in the community's language, language
  match, automation ban), wired into the orchestrator and every connector's `validate()`.
- **Connectors:** Discourse, Dev.to/Forem, signed webhook (protocol v1 in `docs/PARTNER_WEBHOOK.md`), assisted packs.
  All six Core connectors exist; the five posting ones share the hard-case test matrix.
- **Certification harness** with evidence, promotion, demotion + alert, and guaranteed cleanup; reconnect endpoint;
  bootstrap certifies all six seeded destinations.
- In-job retries after timeouts; long platform 429s rescheduled to the reset time.
- `openapi.yaml` + drift test; **batch runner** + 11 hard-case scenarios; test pack runs committed.
- Bugs caught by tests: promo+cap reported the wrong reason; cleanup could create an unneeded test post; log extras
  could overwrite `level`; assisted pack check used a host-only URL from inside the worker.
- **Next (Day 3):** Advanced level (CSV import + 1,000-row benchmark, relevance 1–5, tailoring, AI rules capture with
  human confirm, MRSS + Hashnode + PeerTube), certification-pack export, gitleaks, clean-machine test, live demo,
  REPORT/SOURCES/AI_USE.
