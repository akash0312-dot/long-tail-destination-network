# Test pack index: each claim → its evidence

Every claim in the README, REPORT and Submission Form, with the file that proves it. Tests live in `../tests/` and run
with `docker compose --profile test run --rm tests` (216 passed, 2 skipped on 2026-10-23; the 2 skipped need the
running stack and run in CI). Batch runs below are the raw inputs and outputs of `python -m app.batch`, failed runs
included. The newest full run is [batch_2026-10-23_demo_rehearsal_https](batch_2026-10-23_demo_rehearsal_https/summary.md)
(14/14, through HTTPS and the API token). Scenario names below refer to its folders.

## Must-haves

| # | Claim | Evidence |
|---|---|---|
| 1 | Connector contract: 7 methods, manifests, shared codes | `tests/test_contract.py` (`test_manifests_load_and_use_only_shared_codes`, `test_every_shared_code_exists_with_retry_semantics`, `test_optional_method_not_supported`, `test_kill_switch`); `app/connectors/*/manifest.yaml` |
| 2 | Six Core connectors certified (plus MRSS, Hashnode, PeerTube) | [../certification_pack/](../certification_pack/README.md): run record, live snapshot, screenshot and deletion for each; `tests/test_certification.py::test_full_certification_cycle`; `tests/test_e2e_local.py` (real Ghost + Lemmy) |
| 3 | A new destination is one catalogue row | `tests/test_connectors.py::test_one_connector_many_destinations`; every batch scenario adds its destination by API (`steps.json`, first steps) |
| 4 | Rules before every post, blocked or rescheduled with a reason | `tests/test_rules.py` (21 rule cases + API tests); batch `04a_second_post_inside_weekly_cap`, `04b_promo_to_no_promo_community`, `07_language_and_disclosure`; `test_contract_publish_also_enforces_rules` |
| 5 | Never spam: own instances and mocks only, test posts deleted fast, 429 backoff | [log_audit/report.md](log_audit/report.md) (hosts, 22/22 deleted, slowest 2.46 s, 429 waits); [../docs/NO_SPAM.md](../docs/NO_SPAM.md); `tests/test_log_audit.py` |
| 6 | No duplicate after a timeout | `tests/test_connectors.py::test_timeout_then_retry_same_key_never_duplicates` (7 connectors); `tests/test_api.py::test_publication_job_retries_timeout_with_same_key_and_never_duplicates`; batch `01_timeout_retry_same_key` |
| 7 | Secrets protected; simulation mode | CI `secret-scan` (gitleaks, full history); `../.gitleaks.toml`; `tests/test_logging.py` (redaction); `tests/test_api.py::test_inline_secret_rejected`, `test_connection_view_hides_refs`; `SIMULATION_MODE` in `../.env.example` |
| 8 | `docker compose up` with local Lemmy, Ghost and mocks | CI `clean-machine` job ([workflow](../.github/workflows/ci.yml)): fresh runner, `.env.example` only, every seed certifies, tests + E2E + batch + log audit |
| 9 | Integration Standard; live demo | Table below; demo config [../deploy/](../deploy/DEPLOY.md) and its rehearsal [batch_2026-10-23_demo_rehearsal_https](batch_2026-10-23_demo_rehearsal_https/summary.md). Public server: not deployed yet |

## Levels

| Level | Item | Evidence |
|---|---|---|
| Core | Full certification per Core connector | [../certification_pack/](../certification_pack/README.md); `tests/test_certification.py` |
| Core | Platform errors → shared codes | `tests/test_connectors.py` matrix: 429 short/long reset, expired token, rejected content, renamed field, circuit breaker; batch `02a`–`02d` |
| Advanced | Catalogue import, 1,000 rows < 5 min, row report | `tests/test_catalogue.py::test_one_thousand_rows_under_five_minutes` (~10 s), `test_hard_case_csv_duplicates_bad_urls_mixed_languages`, `test_import_from_csv_url`; batch `09_catalogue_csv` (its `report.csv` link) |
| Advanced | AI rules capture, a person confirms | `test_rules_capture_proposes_and_a_person_confirms`, `test_rejected_proposal_changes_nothing`, `test_rules_capture_respects_robots_txt`; batch `10_rules_capture_confirm`; Claude path: `test_claude_adapter_structured_output_cost_and_fallback`, `test_claude_adapter_refusal_and_truncation_are_explicit_errors` (fake client) |
| Advanced | Relevance 1–5 with a reason | `tests/test_catalogue.py::test_relevance_endpoint`; `relevance_reason` column in `../catalogue_data/destinations.csv` |
| Advanced | Tailoring | `tests/test_tailoring.py` (9 tests: discussion-first framing, canonical URL, word-boundary truncation, mention style) |
| Advanced | Three more connectors | MRSS `tests/test_feeds.py`; Hashnode and PeerTube in the `tests/test_connectors.py` matrix; all three in the certification pack |
| Outstanding | Recommender + precision@10 | `tests/test_recommender.py`; [recommender_eval/report.md](recommender_eval/report.md) (0.41), [baseline](recommender_eval_v1_baseline/report.md) (0.31), [rejected variant](recommender_eval_v3_short_tokens_rejected/report.md) (0.38). Labels drafted by AI, **not yet reviewed** |
| Outstanding | `fetch_metrics` on ≥ 3 connectors | `tests/test_metrics.py` (Lemmy, Discourse, Dev.to, PeerTube); L4 rows in the certification pack (real Lemmy included) |
| Outstanding | Recertification scheduler, demotion, ticket, alert | `tests/test_scheduler.py` (4 tests incl. `test_failing_recertification_demotes_excludes_and_opens_one_ticket`, `test_temporary_failure_is_inconclusive_not_a_demotion`); batch `05_renamed_field_demotes` (demoted to L0, then refused with `DEST_NOT_CERTIFIED`) |
| Outstanding | ≥ 150 real destinations | `../catalogue_data/destinations.csv` (199), [RESEARCH.md](../catalogue_data/RESEARCH.md), `dropped.csv`; `tests/test_real_catalogue.py`. Rules: 24 read from the page, 175 defaults |

## Integration Standard

| # | Point | Evidence |
|---|---|---|
| 1 | One-command start and test | `../docker-compose.yml`, `../.env.example`; CI `clean-machine` |
| 2 | `openapi.yaml` matches the service | `tests/test_openapi.py::test_openapi_file_matches_running_service`, `test_openapi_version_and_route_prefixes` |
| 3 | Jobs: 202 + job_id, status, signed callback | `tests/test_api.py::test_publication_job_end_to_end`, `test_callback_is_hmac_signed` |
| 4 | `Idempotency-Key` on every POST | `test_post_without_idempotency_key_rejected`, `test_same_idempotency_key_returns_same_job_and_one_post`, `test_same_key_different_body_conflicts`, `test_same_upload_with_same_key_is_one_job` |
| 5 | Error envelope, codes in README | `test_job_not_found_envelope`, `test_unknown_connector_uses_error_envelope`; [README error codes](../README.md#error-codes) |
| 6 | Configuration from the environment | `../app/config.py`, `../.env.example` |
| 7 | Files in (upload or URL), files out (S3 links) | `test_import_from_csv_url`, `test_proxy_storage_links_go_through_the_api`; `links` in every batch `steps.json` |
| 8 | JSON logs, correlation ID, `/health`, `/ready` | `tests/test_logging.py::test_json_log_line_has_required_fields`; `test_correlation_id_is_echoed`, `test_health_and_ready` |
| 9 | Output manifest on every result | `manifest` in every job result in the batch `steps.json` files and in `publishing_cost_2026-10-25/jobs.json` |
| 10 | Batch runner | `../app/batch.py`, `../test_inputs/`, the batch folders below |

## Hard cases

| # | Case | Test | Batch scenario |
|---|---|---|---|
| 1 | Timeout, retried with the same key | `test_timeout_then_retry_same_key_never_duplicates` | `01_timeout_retry_same_key` |
| 2 | 429 with reset; expired token; rejected content | `test_429_short_reset_backs_off_then_succeeds`, `test_429_long_reset_returns_rate_limit_with_retry_time`, `test_expired_token_maps_to_connection_auth_expired`, `test_content_rejected_returns_platform_message` | `02a`, `02d`, `02b`, `02c` |
| 3 | Over the limit; unsupported media | `test_title_too_long_blocked_before_any_platform_call`, `test_unsupported_media_reported_by_validate` | `03_limits_and_media` |
| 4 | Second post inside the cap; promo to a no-promo community | `test_second_post_inside_weekly_cap_is_rescheduled`, `test_promo_post_to_no_promo_community_is_blocked_with_reason` | `04a`, `04b` |
| 5 | Renamed API field → certification fails, demoted | `test_renamed_field_fails_certification_demotes_alerts_and_cleans_up` | `05_renamed_field_demotes` |
| 6 | Bad CSV; partner revokes permission | `test_hard_case_csv_duplicates_bad_urls_mixed_languages`, `test_partner_revokes_permission_pauses_connection` | `09_catalogue_csv`, `06_partner_revokes` |
| 7 | Non-English community; AI disclosure required | `test_non_english_community_rejects_english_content`, `test_disclosure_uses_destination_language`, `test_ai_disclosure_appended_for_destination_that_requires_it` | `07_language_and_disclosure` |

## Metrics

| Metric | Evidence |
|---|---|
| Certification time per run, cleanup time | [../certification_pack/README.md](../certification_pack/README.md) (run time column); [log_audit/report.md](log_audit/report.md) |
| Reliability, cost per 1,000 publications | [publishing_cost_2026-10-25/report.md](publishing_cost_2026-10-25/report.md) (100/100, 77 ms worker time each, $0 AI), raw jobs in `jobs.json`; script `../scripts/measure_publishing.py` |
| Duplicates under retry (0) | hard case 1 above |
| Rule safety (21 caught / 0 missed) | `tests/test_rules.py::RULE_CASES` |
| Catalogue time per 1,000 rows | `test_one_thousand_rows_under_five_minutes` |
| Precision@10 | [recommender_eval/report.md](recommender_eval/report.md); script `../scripts/eval_recommender.py` |
| Claude cost per rules page | Not measured (needs an API key) |

## Runs in this folder

| Folder | Result | Why it is kept |
|---|---|---|
| [batch_2026-10-22_run1_client_hit_api_rate_limit](batch_2026-10-22_run1_client_hit_api_rate_limit/summary.md) | 9/11 | The batch client hit this API's own rate limit; the runner learned to wait for `Retry-After` |
| [batch_2026-10-22_run2](batch_2026-10-22_run2/summary.md) | 11/11 | First full pass of the Day 2 scenarios |
| [batch_2026-10-22_run3_internal_host_rejected_led_to_ssrf_guard](batch_2026-10-22_run3_internal_host_rejected_led_to_ssrf_guard/summary.md) | 12/13 | The importer would fetch any URL it was given; led to the SSRF guard |
| [batch_2026-10-22_run4_day3](batch_2026-10-22_run4_day3/summary.md) | 13/13 | End of Advanced |
| [batch_2026-10-22_run5_outstanding](batch_2026-10-22_run5_outstanding/summary.md) | 13/13 | End of Outstanding |
| [batch_2026-10-23_demo_rehearsal_https](batch_2026-10-23_demo_rehearsal_https/summary.md) | 14/14 | Live-demo configuration rehearsed locally: HTTPS, API token, `/mock` behind the token |
| [log_audit/](log_audit/report.md) | passed | Must-have 5: where requests went, test-post deletion, 429 handling, secrets |
| [recommender_eval*/](recommender_eval/report.md) | 0.31 → 0.41, 0.38 rejected | Precision@10 of each recommender variant |
| [publishing_cost_2026-10-25/](publishing_cost_2026-10-25/report.md) | 100/100 | Worker time and AI cost per publication |
