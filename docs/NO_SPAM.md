# Never spam, never cheat: policy and evidence (must-have 5)

> *Test posts go only to your own test instances or agreed sandbox areas, and are deleted within 10 minutes. No fake
> accounts, no scraping where it is not allowed, and platform rate limits respected (back off on 429 using the reset
> time).*

Each clause below maps to the mechanism that enforces it in code, the test that proves it, and the evidence from a
real run. The log evidence comes from [`test_pack/log_audit/`](../test_pack/log_audit/report.md): the
complete logs of a fresh stack (bootstrap certification of 9 destinations, the real Ghost/Lemmy end-to-end tests and
all batch scenarios), checked by [`scripts/log_audit.py`](../scripts/log_audit.py). The same audit runs in CI on
every push and fails the build on any violation.

## Policy

1. **Test posts go only to instances we own or to mocks.** Certification and end-to-end posts go to the local Ghost and
   Lemmy containers started by `docker compose`, and to the mock platform server. No test post is ever sent to a real
   community.
2. **Every test post is deleted, within 10 minutes.** Certification deletes its post and verifies it is gone. If any
   step fails, a cleanup step still deletes it, even when the post's id was lost (timeout, renamed field).
3. **No fake accounts.** The only accounts we create are the owner of the local Ghost and the admin of the local
   Lemmy, both on containers we run. Nothing registers accounts on real platforms.
4. **No posting to real communities without the owner's agreement.** Every third-party destination in the real
   catalogue is `automation_allowed=false` and `permission_status=needed`: it only ever gets an assist pack for a
   person to post by hand. Publishing also requires a certified destination, and the real catalogue is not certified.
5. **No scraping where it is not allowed.** Rules pages are fetched only after `robots.txt` allows it; research uses
   public APIs (Lemmy, Discourse, PeerTube) and an identifying user agent; catalogue checks send a single HEAD/GET.
   User-supplied URLs on private or internal hosts are refused (SSRF guard).
6. **Platform rate limits are respected.** Each connection has a client-side limit from its manifest; on a 429 we wait
   for the platform's reset time (`Retry-After` or `X-RateLimit-Reset`), and a long wait reschedules the post instead
   of retrying; after 5 consecutive failures the circuit opens and calls stop.
7. **Community rules are checked before every post**: frequency caps, self-promotion (blocked when unsure), link
   policy, AI disclosure, language. A post that breaks one is blocked or rescheduled, with the reason.

## Controls and evidence

| Clause | Enforced by | Proven by (test) | Evidence from a real run |
|---|---|---|---|
| Own instances / mocks only | Compose stack; seeds in `bootstrap/bootstrap.py`; certification gate in `app/services/publishing.py` | `test_uncertified_destination_refused_when_certification_required` | Log audit: **every request went to `ghost:2368`, `lemmy:8536` or `mock:9000`** (134+ platform calls) |
| Deleted within 10 minutes | `app/services/certification.py` (delete + verify gone, `cleaned_within_limit`, always-on cleanup) | `test_certification_passes_and_promotes`, `test_lost_publish_response_is_still_cleaned_up`, `test_renamed_field_fails_*_and_cleans_up`, `test_e2e_local.py` (< 600 s) | Log audit: **22 test posts created, 22 deleted, slowest 2.46 s**; [certification pack](../certification_pack/README.md): deletion confirmed for all 9 destinations |
| Never create a test post that is not needed | Cleanup runs only after a publish was attempted | `test_missing_rules_fail_certification` (0 platform calls) | — |
| No fake accounts | Bootstrap creates only the local Ghost owner and Lemmy admin | — (code review: `bootstrap/bootstrap.py`) | No account-creation calls in the logs |
| No posting to real communities without permission | `scripts/research_catalogue.py` sets `automation_allowed=false`; assisted fallback in the orchestrator | `test_catalogue_has_150_plus_relevant_researched_destinations`, `test_destination_without_automation_gets_assist_pack_not_a_post` | `catalogue_data/destinations.csv`: every third-party row `permission_status=needed` |
| No scraping where not allowed | `app/catalogue/rules_capture.py` (robots.txt), SSRF guard `app/urls.py` | `test_rules_capture_respects_robots_txt`, `test_ssrf_guard_refuses_internal_hosts` | `catalogue_data/RESEARCH.md` (public APIs, identifying user agent) |
| Back off on 429 using the reset time | `app/connectors/http.py` (`parse_reset_seconds`, inline wait, reschedule, circuit breaker) | `test_429_short_reset_backs_off_then_succeeds`, `test_429_long_reset_returns_rate_limit_with_retry_time`, `test_long_rate_limit_reschedules_job_to_reset_time`, `test_circuit_breaker_stops_after_five_failures` | Log audit: Dev.to publish **waited 2.0 s from `Retry-After`**, then succeeded; long resets returned `PLATFORM_RATE_LIMIT` with the retry time (batch `02a`, `02d`) |
| Rules before every post | `app/rules.py`, connector `validate()` | `tests/test_rules.py` (21 cases, 0 missed) | Batch `04a`, `04b`, `07` |
| No secrets in logs | Redaction in `app/logging_setup.py`; gitleaks | `tests/test_logging.py`, `tests/test_log_audit.py` | Log audit: 0 JWTs, 0 tokens, 0 default passwords; CI `secret-scan` passed |

## Re-running the evidence

```bash
docker compose up -d --build && docker compose wait bootstrap
RUN_E2E=1 E2E_API_URL=http://localhost:8000 pytest tests/test_e2e_local.py
python -m app.batch --input test_inputs --output test_pack/my_run
docker compose logs --no-color > compose.log
python scripts/log_audit.py compose.log --out test_pack/log_audit
```

## Limits

- Discourse, Dev.to, the partner webhook, Hashnode and PeerTube are certified against mock servers, not real
  instances: posting test content to real third-party communities would itself break this policy without an agreed
  sandbox. Ghost and Lemmy are real local instances.
- The audit checks the logs it is given. It proves what happened in the recorded runs, not every possible run; the
  tests above cover the code paths.
