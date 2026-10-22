# Progress Tracker

Plan and full requirements: [`docs/PLAN.md`](docs/PLAN.md). Status keys: ✅ done · 🟡 partial / in progress · ⬜ not started · ❌ blocked.
Every ✅ must link to evidence (test, file, log) — this feeds the Submission Form. Mark honestly: a false "Yes" in the
form disqualifies; "Partial" with a reason does not.

**Test status (2026-10-22, Outstanding work):**
- `docker compose --profile test run --rm tests` → **208 passed**, 2 skipped (the 2 real-instance E2E tests need the stack)
- Batch on a fresh stack → **13/13** ([run 5](test_pack/batch_2026-10-22_run5_outstanding/summary.md)); seeds certify at Ghost L3, **Lemmy L4 (real)**, Discourse L4, Dev.to L4, webhook L3, assisted L1

**Earlier (end of Day 3):**
- `docker compose --profile test run --rm tests` → 185 passed, 2 skipped
- `RUN_E2E=1 pytest tests/test_e2e_local.py` → 2 passed on Day 2 (real local Ghost 5 + Lemmy 0.19.8); re-run before submission ⬜
- `python -m app.batch --input test_inputs ...` → **13/13 scenarios passed** ([run 4](test_pack/batch_2026-10-22_run4_day3/summary.md))
- Fresh stack bootstrap → **6/6 seeded destinations certified**
- **1,000-row catalogue import: ~10 s** with reachability checks on (limit 5 min) — `test_one_thousand_rows_under_five_minutes`

## Remaining before submission
Ordered by risk: a single failed must-have means "not selected", so those come first.

**A. Must-have gaps**
- [ ] **Live demo** deployed and kept up for the whole review (must-have 9): VM with ≥ 4 GB RAM, `API_TOKEN`, rate limit, `CALLBACK_SIGNATURE_HEADER` set to the brief's header name, cold-start time noted — *you: choose/pay for the VM; me: deploy config + steps*
- [ ] **Clean-machine test** of `docker compose up` + tests (must-have 8) — workflow written (`.github/workflows/ci.yml`); *first run result on GitHub ⬜ — check the Actions tab (the CLI here is not signed in)*
- [x] **Secret scan** (gitleaks) — clean locally over the full history; also runs in CI. Repeat log scan still ⬜
- [x] **Certification pack** exported into the repo (must-have 2 evidence) — [certification_pack/](certification_pack/README.md): 9 destinations, one per connector (L1–L4), each with run.json, live snapshot, screenshot and deletion confirmation; cleanup 0.1–2.1 s. Credentials in captured pages are redacted (Ghost embeds its Content API key). Re-export: `python scripts/export_certification_pack.py`
- [ ] **No-spam evidence** written up: log review + policy in REPORT (must-have 5) — *me*
- [ ] **Clarification email** (contract JSON shape; is mock certification accepted for Discourse/Dev.to/webhook) (must-haves 1, 2) — *you send; questions drafted in docs/PLAN.md §5*
- [ ] Re-run the real-instance E2E (`RUN_E2E=1`) on the final build — *me*

**B. Outstanding level (counts only if every item is verified)**
- [ ] Review and correct `catalogue_data/labelled_set.json`, set `reviewed_by`, re-run `python scripts/eval_recommender.py` — *you*
- [ ] Catalogue rules: 175 of 199 rows use defaults — run Claude rules capture (needs `ANTHROPIC_API_KEY`, small cost) or review by hand — *your decision*
- [ ] Optional: one live run of the Claude provider to replace "fake-client only" with real evidence and a real cost per page — *needs your key*

**C. Documents and evidence**
- [ ] README: recommender, metrics, scheduler/tickets, real catalogue — *me*
- [ ] REPORT.md (2–4 pages), SOURCES.md, AI_USE.md — *me drafts; you check AI_USE describes how you worked*
- [ ] Test-pack index: each claim → its file — *me*
- [ ] Missing metrics: certification time per run, cost per 1,000 publications, Claude cost per rules page — *me (Claude cost needs your key)*
- [ ] Decide on local-only default passwords in `.env.example` (generate them, or keep and justify) — *your call*

**D. Only you can do**
- [ ] Demo video: unedited, ≤ 8 min (clean run, a failure handled, tests running, one design decision in code)
- [ ] Invite the reviewer to the GitHub repo (username from the email)
- [ ] Fill and sign the Submission Form (I can pre-fill a draft from this tracker); send before the deadline in the email
- [ ] Practise the live session: architecture walkthrough, predict-and-run, debugging from logs, adding a connector test-first

## Must-haves
| # | Must-have | Status | Evidence | Notes |
|---|---|---|---|---|
| 1 | Exact connector contract | 🟡 | `app/connectors/router.py`, `tests/test_contract.py`, 9 manifests | All 7 methods routed, manifests validated, shared codes. Exact JSON field names unconfirmed → clarification email |
| 2 | Six Core connectors certified | 🟡 | `tests/test_certification.py`, bootstrap log, `tests/test_e2e_local.py`, [certification_pack/](certification_pack/README.md) | All 6 certify (plus MRSS, Hashnode, PeerTube). Ghost + Lemmy on real local instances; Discourse, Dev.to, webhook on faithful mocks — **ask whether mock certification is accepted** |
| 3 | Configuration, not code | ✅ | `test_one_connector_many_destinations` (7 posting connectors); every batch scenario adds its destination as one API row | |
| 4 | Rules checked before every post | ✅ | `tests/test_rules.py` (21-case rule set + API tests), batch `04a`, `04b`, `07` | Also enforced on the raw contract path via `validate()` |
| 5 | Never spam, never cheat | 🟡 | 429 reset-time backoff, certification cleanup always deletes test posts, robots.txt honoured by rules capture, SSRF guard, only own instances/mocks used | Log review evidence + written policy in REPORT ⬜ |
| 6 | Safe retries (no duplicate after timeout) | ✅ | `test_timeout_then_retry_same_key_never_duplicates` (7 connectors), `test_publication_job_retries_timeout_with_same_key_and_never_duplicates`, batch `01` | Mutation-checked on Day 1 |
| 7 | Secrets protected + simulation mode | 🟡 | `tests/test_logging.py`; container log scan clean on Day 1; `SIMULATION_MODE=true`; **gitleaks v8.18.4 over all 19 commits: no leaks** (`.gitleaks.toml`: default rules + a 2-pattern allowlist for idempotency keys and one fake test password; a planted fake key is still caught); CI job `secret-scan` in `.github/workflows/ci.yml` | First CI run result ⬜; repeat log scan ⬜ |
| 8 | `docker compose up` with local Lemmy + Ghost + mocks | 🟡 | `docker-compose.yml`, `bootstrap/bootstrap.py`; fresh-volume start worked 4 times locally; CI job `clean-machine` (fresh Ubuntu runner: `.env` from `.env.example`, compose up, bootstrap must certify all 9 seeds, test suite, real E2E, batch; logs uploaded as an artifact) | **First CI run result ⬜** |
| 9 | Integration Standard + live demo up | ⬜ | | Live demo not deployed |

## Levels
| Level | Item | Status | Evidence |
|---|---|---|---|
| Core | All must-haves | ⬜ | blocked on 1, 2, 5, 7, 8, 9 above |
| Core | Full certification run per Core connector | 🟡 | `test_certification_passes_and_promotes`; bootstrap certifies all 6 on the stack. Export of evidence files ⬜ |
| Core | Platform errors mapped to shared codes | ✅ | `tests/test_connectors.py` matrix × 7 connectors: 429, expired token, rejected, 5xx/circuit, schema change |
| Advanced | Catalogue import (1,000 rows < 5 min, row report) | ✅ | `app/catalogue/importer.py`; `test_one_thousand_rows_under_five_minutes` (~10 s); `test_hard_case_csv_*`; batch `09` |
| Advanced | AI rules capture + human confirm | ✅ | `app/catalogue/rules_capture.py`; `test_rules_capture_proposes_and_a_person_confirms`, `test_rejected_proposal_changes_nothing`, `test_rules_capture_respects_robots_txt`; batch `10`. Default provider = heuristics; **Claude path unit-tested with a fake client, not yet run against the live API** |
| Advanced | Relevance score 1–5 + reason | ✅ | `app/catalogue/heuristics.py`, `POST /api/v1/destinations/{id}/relevance`, scored on every import; `test_relevance_endpoint` |
| Advanced | Tailoring | ✅ | `app/tailoring.py`, `POST /api/v1/tailor`, `tailor: true` on publications; `tests/test_tailoring.py` |
| Advanced | Three more connectors | ✅ | MRSS feed (`tests/test_feeds.py`, certifies at L2), Hashnode + PeerTube (hard-case matrix + certification at L3, on mocks) |
| Outstanding | Recommender + precision@10 | 🟡 | `app/recommender.py`, `POST /api/v1/recommendations`, `tests/test_recommender.py`; **precision@10 = 0.41** on 10 items ([report](test_pack/recommender_eval/report.md); baseline 0.31 and a rejected 0.38 variant kept). **Labels are AI-drafted: you must review/correct `catalogue_data/labelled_set.json` and re-run** — the brief says "a set you label". Variant chosen on the same set (no held-out set) |
| Outstanding | fetch_metrics on ≥ 3 connectors | ✅ | Lemmy, Discourse, Dev.to, PeerTube (`tests/test_metrics.py`); **real local Lemmy certifies at L4** (metrics verified); `POST /api/v1/publications/{id}/metrics` |
| Outstanding | Recertification scheduler | ✅ | `app/services/scheduler.py` (worker loop + `POST /api/v1/recertifications/run`), tickets (`GET /api/v1/tickets`), demotion + exclusion + alert, auto-resolve; temporary failures are inconclusive, not demotions — `tests/test_scheduler.py` |
| Outstanding | 150+ real destinations | 🟡 | `catalogue_data/destinations.csv`: **199 verified live** (190 with relevance ≥ 3), connector type, relevance + reason, member counts from platform APIs, 0 invalid on import. **Rules: only 24 rows have a rule read from the page; 175 use labelled conservative defaults.** To close: run rules capture with Claude (`AI_PROVIDER=anthropic`, needs your key) or review rules by hand |

## Integration Standard
| # | Point | Status | Evidence |
|---|---|---|---|
| 1 | One-command start and test | ✅ | `docker compose up -d`; `docker compose --profile test run --rm tests` (unit, integration, simulated E2E, no keys); `.env.example` |
| 2 | openapi.yaml matches service | ✅ | `openapi.yaml`, `tests/test_openapi.py` (drift test), `scripts/export_openapi.py` |
| 3 | Background jobs (202, job status, HMAC callback) | ✅ | `app/jobs.py`, `test_publication_job_end_to_end`, `test_callback_is_hmac_signed`; scheduled jobs (`run_after`) |
| 4 | Idempotency-Key on every POST | ✅ | `app/middleware.py` (multipart boundary normalised), `tests/test_api.py`, `test_same_upload_with_same_key_is_one_job`, batch `01` |
| 5 | JSON error envelope, codes in README | ✅ | `app/errors.py`, `README.md#error-codes` |
| 6 | All configuration from env | ✅ | `app/config.py`, `.env.example` |
| 7 | Files in (upload/URL), files out (S3 links) | ✅ | In: CSV upload or `csv_url` (`test_hard_case_csv_*`, `test_import_from_csv_url`). Out: reports, manifests, evidence, assist packs, feeds in SeaweedFS with links |
| 8 | JSON logs + correlation id, /health, /ready | ✅ | `app/logging_setup.py`, `test_correlation_id_is_echoed`, `test_health_and_ready` |
| 9 | Output manifest on every result | ✅ | publication, assist pack, certification, catalogue import, rules capture (providers, model, tokens, cost) |
| 10 | Batch runner CLI | ✅ | `app/batch.py`, `test_inputs/` (13 scenarios), `test_pack/` |

## Hard cases
| # | Situation | Status | Test / scenario |
|---|---|---|---|
| 1 | Timeout + retry same idempotency key | ✅ | `test_timeout_then_retry_same_key_never_duplicates`; batch `01` |
| 2 | 429 with reset; expired token; content rejected | ✅ | connector matrix; batch `02a`, `02b`, `02c` |
| 3 | Title/body too long; unsupported media | ✅ | connector matrix; batch `03` |
| 4 | 2nd post in frequency cap; promo to no-promo | ✅ | `tests/test_rules.py`; batch `04a`, `04b` |
| 5 | Renamed API field → cert fails, demoted | ✅ | `test_renamed_field_fails_certification_demotes_alerts_and_cleans_up`; batch `05` |
| 6 | Bad CSV; partner revokes permission | ✅ | `test_hard_case_csv_duplicates_bad_urls_mixed_languages`, batch `09`; `test_partner_revokes_permission_pauses_connection`, batch `06` |
| 7 | Non-English community; AI disclosure required | ✅ | `test_non_english_community_rejects_english_content`, `test_ai_disclosure_appended_*`; batch `07` |

## Metrics measured so far (for form tab 5)
| Metric | Value | Conditions |
|---|---|---|
| Catalogue: time for 1,000 rows | ~10 s (9.6–10.8 s over 3 runs) | Windows laptop, reachability on, 50 concurrent checks against the local mock; 921 imported, 49 duplicates, 30 invalid, 20 unreachable |
| Real catalogue | 199 verified / 226 candidates; 190 relevance ≥ 3 | live checks 2026-10-22; 27 dropped with reasons (`catalogue_data/dropped.csv`) |
| Recommendation precision@10 | 0.41 (10 items) | AI-drafted labels, not yet reviewed; variant chosen on this set |
| Certification time per run | ⬜ to extract from evidence (`duration_s` in run.json) | |
| Duplicates under retry tests | 0 | all timeout/retry tests and batch `01` |
| Rule safety (caught / missed) | 21 / 0 on our rule set | `tests/test_rules.py::RULE_CASES` |
| Cost per 1,000 publications | $0 on the default path (no AI on publish); AI only in rules capture/relevance | ⬜ measure Claude cost per rules page |

## Deliverables
| Item | Status |
|---|---|
| README.md | 🟡 Day 3 version: recommender, metrics, scheduler/tickets and the real catalogue are not documented yet |
| openapi.yaml | ✅ |
| REPORT.md | ⬜ |
| SOURCES.md / AI_USE.md | ⬜ |
| Test pack | 🟡 `test_pack/` batch runs incl. failed runs; claim → file index ⬜ |
| Certification pack | ✅ [certification_pack/](certification_pack/README.md) — 9 destinations with run record, live snapshot, screenshot, deletion confirmation |
| Catalogue files | 🟡 `catalogue_data/` (destinations.csv 199 rows, candidates, dropped, RESEARCH.md) + `labelled_set.json`; labels need your review |
| Demo video | ⬜ |
| Live demo deployed | ⬜ |
| Clarification email sent to the hiring team | ⬜ (questions drafted in `docs/PLAN.md` §5) |
| Git repo with real commit history | 🟡 Private repo https://github.com/akash0312-dot/long-tail-destination-network, 19 commits, all pushed (2026-10-23). Reviewer invite ⬜ (username comes in the invitation email) |
| Submission Form filled | ⬜ |

## Decisions log
| Date | Decision | Why |
|---|---|---|
| 10-22 | DB-backed job queue (no Redis) | Job + idempotency in one transaction; one less service on a clean machine |
| 10-22 | SeaweedFS instead of MinIO | MinIO public images no longer pullable; Apache-2.0 vs AGPL |
| 10-22 | Reconcile-before-publish idempotency (slug / hidden marker / partner Idempotency-Key / feed guid) | Only way to survive "created but response lost" timeouts |
| 10-22 | `PLATFORM_SCHEMA_CHANGED`, `DEST_RULE_*`, `AI_*` extension codes | Specific reasons; to confirm with the reviewers |
| 10-22 | Neutral naming; callback signature header configurable (`CALLBACK_SIGNATURE_HEADER`) | Owner's request; set the brief's header name in the deployment `.env` |
| 10-22 | Rules engine is pure and blocks when unsure about promotion | Deterministic, testable; a missed violation is worse than a false block |
| 10-22 | Capped posts become scheduled jobs (`run_after`); long platform 429s too | "Blocked or rescheduled with a reason" |
| 10-22 | Destinations that ban automation get an assist pack automatically | A person posts; nothing automated where it is not allowed |
| 10-22 | Dev.to `delete` = unpublish, reported in `note` | Public Forem API has no delete |
| 10-22 | Certification cleanup recovers lost posts by reconcile — only after a publish was attempted | Never leave a test post; never create an unneeded one |
| 10-22 | MRSS is a hosted feed (we publish, aggregators pull) at a public, token-free URL | That is how CTV apps / aggregators ingest video |
| 10-22 | Tailoring is deterministic; translation is flagged, not automatic | Explainable output; machine translation into a community is a quality and rules risk |
| 10-22 | AI behind `AI_PROVIDER` (mock default, Claude optional); Claude default model `claude-opus-5-5`, effort `low`, schema-constrained JSON, refusal fallback on | Brief: swappable providers + mock; no key needed for tests; cost reported per call |
| 10-22 | Rule proposals apply only after `confirm` by a named reviewer | Brief: "a person confirms them before they are used" |
| 10-22 | SSRF guard on every user-supplied URL; `ALLOW_INTERNAL_HOSTS` only for the local stack | Catalogue, rules and CSV URLs make the server fetch arbitrary URLs |
| 10-22 | Local-only default passwords for Lemmy/Postgres stay in `.env.example` for now | Only open containers we own on 127.0.0.1; revisit before submission |

## Daily log
### Day 1 — 2026-10-22
- Plan + tracker; service core; connector framework; Ghost + Lemmy; mock platforms with fault injection; compose with
  real Ghost + Lemmy + bootstrap.

### Day 2 — 2026-10-22
- Name removed everywhere (history squashed before first push). Rules engine; Discourse, Dev.to, webhook, assisted;
  certification harness with demotion/alert/cleanup; reconnect; in-job retries; openapi.yaml; batch runner + 11
  scenarios. Pushed to the private GitHub repo.

### Day 3 — 2026-10-22
- **Advanced level, all 5 items:** MRSS + Hashnode + PeerTube connectors; tailoring; AI provider adapter (mock +
  Claude); relevance 1–5; catalogue import (1,000 rows in ~10 s); AI rules capture with human confirm.
- SSRF guard after a batch scenario showed the importer would fetch any URL it was given.
- Bugs caught by tests: certification sent text to a video-only feed; PeerTube mock ignored faults on one endpoint
  (masked the circuit breaker); mock page lookup used the wrong key.
- Day 3 pushed to GitHub (14 commits).

### Day 4 — Outstanding level — 2026-10-22
- **fetch_metrics** on Lemmy, Discourse, Dev.to, PeerTube (null when not exposed); stored snapshots; L4 in certification.
- **Recommender** with reasons and exclusions; **precision@10 0.41** on an AI-drafted labelled set (needs your review).
- **Recertification scheduler** + tickets; design fix: a temporary 429/timeout is *inconclusive*, never a demotion.
- **Real catalogue**: 199 live-verified destinations from 226 candidates (14 guessed Lemmy communities did not exist
  and were dropped); rules mostly defaults — the weakest part, documented.
- Bugs caught: relevance read hostnames (`lemmy.ml` → "ML"); a broad phrase gave a self-promotion false positive.
- **Your actions needed:** review `catalogue_data/labelled_set.json` (then `python scripts/eval_recommender.py`);
  decide whether to run Claude rules capture over the catalogue (costs money; needs `ANTHROPIC_API_KEY`).
- **Then:** certification-pack export, gitleaks, clean-machine test, live demo, REPORT / SOURCES / AI_USE,
  clarification email, Submission Form.
