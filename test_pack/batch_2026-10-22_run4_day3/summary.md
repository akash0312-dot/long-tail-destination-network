# Batch run 2026-10-22T17:33:27.937185+00:00

**13/13 scenarios passed**

| Scenario | Result | Description |
|---|---|---|
| 01_timeout_retry_same_key | PASS | Hard case 1: publish times out (post created, answer lost); job retries with the same key; no duplicate; client retry returns the same job |
| 02a_rate_limit_429_reset | PASS | Hard case 2a: 429 with a 120 s reset; the post is rescheduled for after the reset time |
| 02b_expired_token_then_reconnect | PASS | Hard case 2b: expired token pauses the connection; posting resumes only after reconnect |
| 02c_content_rejected | PASS | Hard case 2c: the platform refuses the content; its message is returned, no retry |
| 03_limits_and_media | PASS | Hard case 3: title longer than allowed and media the destination does not accept; field-level reasons, nothing posted |
| 04a_second_post_inside_weekly_cap | PASS | Hard case 4a: second post inside a 1-per-week cap is rescheduled with the next free slot |
| 04b_promo_to_no_promo_community | PASS | Hard case 4b: promotional post to a community that bans self-promotion is blocked with the reason |
| 05_renamed_field_demotes | PASS | Hard case 5: the platform renames a field; certification fails, the destination is demoted to L0 and excluded, an alert is written, the test post is cleaned up |
| 06_partner_revokes | PASS | Hard case 6b: a partner revokes permission (403); the connection is paused and later posts ask for a reconnect |
| 07_language_and_disclosure | PASS | Hard case 7: a Japanese community that requires AI disclosure; English content is refused, Japanese AI content gets a Japanese disclosure |
| 08_assisted_pack | PASS | Assisted tier: a community that bans automation gets a ready-to-post pack (caption, deep link, rules checklist) and nothing is posted |
| 09_catalogue_csv | PASS | Hard case 6a: a CSV with duplicates, bad URLs, a major platform, mixed languages and a dead site; row-by-row report, nothing silently dropped |
| 10_rules_capture_confirm | PASS | Advanced: the AI step reads a community's rules page and proposes rules; nothing changes until a person confirms them |
