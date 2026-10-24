# Report: Long-Tail Destination Network

A containerised connector service that publishes creator content to long-tail destinations (forums, Lemmy
communities, developer blogs, Ghost, partner sites, video feeds, and communities where a person must post) through
one connector contract. Every post is checked against the destination's posting rules first, and only certified
destinations receive posts. It also imports and scores a catalogue of destinations and recommends where a piece of
content should go.

Status: every Core, Advanced and Outstanding item is implemented and tested. Two Outstanding items still need the
candidate's review before they can be claimed (below). Evidence for each claim: [test_pack/INDEX.md](test_pack/INDEX.md).

## 1. Approaches considered and what was chosen

| Problem | Options considered | Chosen | Why |
|---|---|---|---|
| Background jobs | Redis + RQ; Celery; a jobs table in Postgres | **Jobs table** (`SELECT … FOR UPDATE SKIP LOCKED`) | The job and its idempotency record are written in one transaction, so "same key → same job" is atomic. One less service to fail on a clean machine. Scheduled work (`run_after`) comes for free. |
| Object storage | MinIO; SeaweedFS; local disk | **SeaweedFS** (S3 API) | MinIO's public images could no longer be pulled, which would have broken `docker compose up` on a clean machine. SeaweedFS is Apache-2.0 (MinIO is AGPL). Any S3 store can be swapped in. |
| No duplicate after a timeout | API idempotency key only; key + platform lookup | **Both layers** | The API key alone cannot help when the platform created the post but the answer was lost. Each connector embeds the key in the post (Ghost slug, hidden Lemmy marker, partner `Idempotency-Key`, feed guid) and looks it up before publishing again. |
| Posting rules | Ask an AI model at post time; a deterministic engine | **Deterministic engine**, AI only *proposes* rules | Same input, same decision, testable case by case. When unsure about promotion it blocks: a missed violation is worse than a false block. A person confirms AI-proposed rules before they are used. |
| Proving destinations work | Real accounts on third-party sites; local instances + mocks | **Real local Ghost and Lemmy; faithful mocks for the rest** | The brief forbids posting to real communities without the owner's permission. The mocks follow each platform's public API and inject faults (timeouts, 429, 401, 422, 5xx, renamed fields). |
| Recommender | A learned ranking model; a transparent weighted score | **Weighted score with reasons** | 10 labelled items and 199 destinations are far too few to train on. A weighted score explains every rank. Three variants were measured (section 3). |
| Live demo | A platform-as-a-service; one VM running the same compose | **One VM + Caddy** | Reviewers run the same stack they can run locally. Only 80/443 are public, and every API route needs the token. |

## 2. What works

- **Connector contract** (`POST /connector/v1/{key}/{method}`): all 7 methods, validated YAML manifests, shared error
  codes. 9 connectors: Ghost, Lemmy, Discourse, Dev.to, HMAC-signed webhook, assisted, MRSS, Hashnode, PeerTube.
- **One connector, many destinations:** a new forum is one `POST /api/v1/destinations` row; every batch scenario
  adds its own destination this way.
- **Safety on every post:** certification gate, then rules (frequency cap → rescheduled; self-promotion, link policy,
  language, automation → blocked with a reason; AI disclosure → added in the community's language), then format
  validation, then publish.
- **Platform failures:** a shared HTTP client with a per-connection rate limiter, 429 backoff from `Retry-After` /
  reset headers, a circuit breaker after 5 failures, a kill switch, and an error map to the shared codes.
- **Certification:** real test post → live URL answers → status → delete → status deleted, with evidence in object
  storage. A failure demotes the destination, opens a ticket and writes an alert. The scheduler re-tests before 30
  days.
- **Catalogue tools:** CSV import with validation, de-duplication, reachability, enrichment and relevance; rules
  capture with a person's confirmation; tailoring per destination; recommendations with reasons.
- **Integration Standard:** all 10 points (one-command start and test, `openapi.yaml` checked against the service,
  jobs with signed callbacks, `Idempotency-Key` on every POST, error envelope, configuration from the environment,
  files in and out, JSON logs with correlation IDs, output manifests, batch runner).
- **Clean machine:** CI starts the stack from `.env.example` on a fresh runner on every push. It requires all seeds
  to certify and runs the tests, the real Ghost/Lemmy E2E, every batch scenario and the log audit. A gitleaks scan
  covers the full history.

## 3. Numbers

All measured on the local stack (Windows laptop, Docker Desktop) unless stated otherwise.

| Metric | Result | Conditions / evidence |
|---|---|---|
| Certification: destinations per connector | 1 seeded per connector (9), plus one per batch scenario | [certification_pack/](certification_pack/README.md) |
| Certification: time per run | 0.10–0.18 s on the mocks, 1.23 s real Lemmy, 2.09 s real Ghost, 0.06 s assisted | same |
| Test posts deleted | 22 of 22, slowest 2.46 s (limit 600 s) | [log audit](test_pack/log_audit/report.md) of a fresh-stack run |
| Reliability (simulation) | 100/100 publications succeeded, 73 of them after a rate-limit reschedule | [publishing cost run](test_pack/publishing_cost_2026-10-25/report.md) |
| Reliability (local instances) | Ghost and Lemmy E2E pass in every CI run; batch 14/14 scenarios | CI; [rehearsal batch](test_pack/batch_2026-10-23_demo_rehearsal_https/summary.md) |
| Duplicates under retry | 0 | timeout-after-create tests on 7 connectors; batch `01` |
| Rule safety (caught / missed) | 21 / 0 | our 21-case rule set (`tests/test_rules.py::RULE_CASES`); not an external benchmark |
| Catalogue: 1,000 rows | ~10 s (9.6–10.8 s over 3 runs; limit 5 min): 921 imported, 49 duplicates, 30 invalid, 20 unreachable | `test_one_thousand_rows_under_five_minutes`, 50 concurrent checks against the local mock |
| Real catalogue | 199 kept of 226 candidates, verified live on 2026-10-22; 27 dropped with reasons | [RESEARCH.md](catalogue_data/RESEARCH.md) |
| Recommendation precision@10 | **0.41** (baseline 0.31; a rejected variant 0.38) | 10 items, labels drafted by AI and **not yet reviewed** |
| Cost per 1,000 publications | **0.021 worker-hours + $0 AI** | 77 ms worker time per publication including rescheduled attempts |
| Claude cost per rules page | **Not measured** | needs an `ANTHROPIC_API_KEY`; each result manifest reports tokens and cost |

**Cost per unit.** Publishing uses no AI: rules, tailoring and recommendations are deterministic. The cost is server
time. 1,000 publications need 0.021 worker-hours, so the cost is 0.021 × the server's hourly price; for example, a
server at $0.05 an hour gives about $0.001 per 1,000 (example price, not a quote). A real platform adds its own
response time: Ghost took about 2 s per certification here, mostly Ghost itself. Throughput per connection is set by
the platform's limit, not by this service: Discourse allows 60 requests a minute, which gave 32 posts a minute on one
connection. AI is used only at catalogue time (rules capture, relevance) and only with `AI_PROVIDER=anthropic`. Its
cost is reported per call but has not been measured live.

## 4. What does not work, or is not proven yet

- **Third-party platforms are proven on mocks only.** Discourse, Dev.to, the webhook, Hashnode and PeerTube certify
  against the mock platform server, which follows their public APIs. Only Ghost and Lemmy run as real instances.
  Whether mock certification is accepted is one of the clarification questions for the hiring team.
- **Contract field names are not confirmed.** The methods, manifest and codes follow the brief. The exact JSON field
  names in a few places are inferred and listed in the clarification questions.
- **The Claude provider is not proven live.** It is unit-tested with a fake client; the default provider is the
  deterministic one. There is no live cost per rules page yet.
- **The recommender is weak on some topics, and its labels are not reviewed.** Precision@10 is 0.41 over only 10
  items. Two items score 0 (UX of AI chat, creator income): the catalogue has few such destinations, and word
  overlap misses synonyms. The labels were drafted by AI, and the chosen variant was picked on the same set (no
  held-out set), so 0.41 is optimistic.
- **Most catalogue rules are defaults.** Rules come from the community's own text for 24 of 199 rows. The other 175
  use conservative defaults, labelled as defaults per row.
- **Rescheduling is correct but wasteful.** Posts rescheduled by a rate limit all wake at the same reset, and most
  are rescheduled again: 100 posts took 229 job attempts. Nothing is lost or duplicated, but it costs worker time.
- **Dev.to `delete` only unpublishes,** because the public Forem API has no delete. The result says so in `note`.
- **The callback signature header name is a setting** (`CALLBACK_SIGNATURE_HEADER`). The default is `X-Signature`;
  the deployment sets the name the brief specifies.
- **The live demo** is configured and rehearsed locally over HTTPS (14/14 scenarios) but is not yet on a public
  server.

## 5. Next steps

1. Get the hiring team's answers on field names and mock certification; adjust the contract tests if needed.
2. Review and correct the labelled set, add held-out items, and re-measure precision@10. Then improve topic matching
   (synonyms or embeddings behind the existing AI adapter) and add destinations for thin topics.
3. Run rules capture with Claude over the catalogue, with a person confirming each proposal, and record the real
   cost per page.
4. Spread rescheduled posts over the next windows (reserve a slot when rescheduling) instead of waking them all at the
   reset.
5. Certify against real Discourse, Forem and PeerTube sandboxes once their owners agree.
6. Deploy the live demo and keep an uptime monitor on `/health` for the review period.
