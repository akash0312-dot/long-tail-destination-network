# Long-Tail Destination Network — Project Plan

> Source: *Engineering Assessment 4 brief* (9 pages) and the *Submission Form* (8 tabs).
> The brief is confidential — this file paraphrases it for planning; do not publish the repo publicly.
> Progress is tracked in [`../PROGRESS.md`](../PROGRESS.md).

---

## 1. Objective

The client already publishes creators' content to 15 major platforms (YouTube, Instagram, Facebook, LinkedIn, TikTok, X,
Threads, Pinterest, Bluesky, Mastodon, Reddit, Vimeo, WordPress, Telegram, Discord) and RSS. **Do not rebuild those.**

We build the **long tail**: Discourse forums, Lemmy communities, Dev.to / Hashnode, Ghost blogs, newsletters, Slack
communities, partner websites, CTV / video-aggregator feeds, and communities where a *person* must post.

A **containerised connector service** that:

1. Publishes through the client's **connector contract** (Appendix A) — same methods, manifest and error codes.
2. **Never spams** — a posting-rules engine checks every post first.
3. **Proves every destination works** — certification: real test post → confirm live → status → delete → evidence.
4. **Tailors** content per destination and **recommends** where content should go.

**Core idea — one connector, many destinations.** A *destination* = one place/one audience (one forum). A *connector* =
code for one kind of platform. Adding forum #41 = **one catalogue row, no code**. A destination does not count until it
is **certified** (format limits + rules recorded, a real test post succeeded and was cleaned up).

**Out of scope:** the 15 majors + RSS, paid boosting/ads, lead generation, cold email/outreach, billing.

**Relevance audience:** AI, video, marketing, SaaS, creators, founders.

Time allowed by brief: 10 calendar days. Our internal plan: 3 days to Advanced, remaining days for Outstanding + polish.

---

## 2. Scoring and what it takes to be hired

| Part | Marks | Rule |
|---|---|---|
| Must-haves (9) | pass/fail | **Any failure → not selected** |
| Levels | 40 | Core 15 · Advanced 15 · Outstanding 10. **All-or-nothing per level**; a level counts only if the level below is verified |
| Integration Standard | 20 | 10 points × 2 (Partial = 1) |
| Hidden tests | 20 | 5 × 4 marks: 4 correct · 2 clear flaw · 0 wrong/crash/**silent failure** |
| Live session | 20 | 4 parts × 5 |

- Live session invite: all must-haves verified **and screening ≥ 60/80**.
- **Shortlist (hire):** total ≥ 75 and live ≥ 14/20. **Top pick:** total ≥ 85, Outstanding verified, live ≥ 14.
- Tie-break: live session → hidden tests → Integration Standard.

**Critical arithmetic:** Core only → max screening 15 + 20 + 20 = **55 < 60** → never invited.
**Advanced is effectively mandatory** (every one of its 5 items fully "Yes"). Outstanding is required for Top pick.

**Disqualifiers:** any false claim, fake/edited evidence, manual work presented as automated, uncredited work,
using people's voice/face/content without rights, **not being able to explain your own code**.
Marking "No"/"Partial" honestly is always better than overclaiming. A claim without evidence scores 0.

---

## 3. Requirements (complete)

### 3.1 What we build
1. Connectors following Appendix A exactly.
2. Posting-rules engine: frequency cap, self-promotion, link rules, AI-disclosure — stored and checked before every post.
3. Certification harness: publish test post, confirm live, read status, delete (or agreed sandbox), store evidence.
4. Catalogue tools (Advanced): import spreadsheet, clean, score relevance, capture rules.
5. Tailoring (Advanced) and recommendation (Outstanding).

### 3.2 Must-haves (all must pass)
| # | Must-have | How the reviewers verify |
|---|---|---|
| 1 | Exact connector contract: methods, manifest, error codes | Their contract test suite against our service |
| 2 | Six Core connectors certified: **Discourse, Lemmy, Dev.to, Ghost, generic HMAC-signed webhook, assisted** (ready-to-post pack: media, text, link, rules checklist, submit link) | Certification evidence + their runs on local test instances |
| 3 | Configuration, not code: new instance of a supported type = one catalogue row | They add an instance during review |
| 4 | Rules before every post: frequency cap, self-promo, link policy, AI disclosure; violation → blocked or rescheduled **with reason** | 2nd post inside weekly cap; promo post to no-promo destination |
| 5 | Never spam / never cheat: only own test instances or agreed sandboxes, **deleted within 10 minutes**; no fake accounts; no disallowed scraping; **back off on 429 using reset time** | Review logs, code, evidence |
| 6 | Safe retries: same idempotency key never creates a second post, **even after a timeout** | Force timeout and retry |
| 7 | Secrets protected + simulation mode (mock platforms, no real accounts) | Search logs and repo; run in simulation |
| 8 | `docker compose up` starts service + **local Lemmy and Ghost** + mocks for the rest | Compose on a clean machine |
| 9 | Integration Standard followed; live demo works for whole review period | See §3.4 |

### 3.3 Levels
**Core:** all must-haves · each Core connector passes full certification (publish, confirm live URL, status, delete) with
stored evidence · platform errors mapped to shared codes (expired tokens, rate limits, rejected content).

**Advanced:**
- Catalogue import: CSV validated, de-duplicated (normalised URL + platform), reachability-checked, enriched (e.g. member
  count); **1,000 rows < 5 minutes** with row-by-row error report.
- Rules capture: AI reads a community's rules page and proposes posting rules; **a person confirms** before use.
- Relevance score 1–5 against the target audience with short explanation.
- Tailoring: discussion-first framing for forums, canonical URL for articles, word-boundary truncation, correct
  hashtag/mention style.
- Three more connectors from: Hashnode, Slack app, Kit or Mailchimp, MRSS video feed, PeerTube.

**Outstanding:**
- Recommender: rank destinations for a content item by fit (audience, rules, past results) with reasons; precision@10 on a
  labelled set.
- `fetch_metrics` on ≥ 3 connectors normalised to views, likes, comments, shares, clicks.
- Recertification scheduler: re-test before 30 days; failing destination demoted + excluded automatically with ticket/alert.
- Real catalogue: ≥ 150 real, relevant destinations with rules, relevance score, connector type (catalogue only).

### 3.4 Integration Standard (2 marks each)
1. **One-command start and test:** `docker compose up` starts everything (DB, storage, local test servers); one command runs
   all tests (unit, integration, ≥ 1 E2E) with no API keys; `.env.example` lists every setting.
2. **Documented HTTP API:** `openapi.yaml` (OpenAPI 3.1) matching the running service exactly; all routes under `/api/v1/`.
3. **Background jobs:** anything > 2 s is a job — POST → `202` + `job_id`; `GET /api/v1/jobs/{job_id}` → status
   (queued/running/succeeded/failed), progress 0–100, result links; optional callback signed HMAC-SHA256 in
   the brief's vendor-named signature header (configurable: `CALLBACK_SIGNATURE_HEADER`).
4. **Safe retries:** `Idempotency-Key` header on every POST; same key → same job, work never done twice.
5. **Clear errors:** `{"error": {"code", "message", "retryable", "details"}}`, UPPER_SNAKE_CASE codes listed in README.
6. **Configuration:** all settings/paths/model names/limits from env or config file; nothing hard-coded.
7. **Files in, files out:** inputs as upload or URL; outputs to S3-compatible storage (MinIO) returned as download links.
8. **Logs and health:** structured JSON logs with timestamp, level, job_id, X-Correlation-Id; `GET /health`, `GET /ready`.
9. **Output manifest:** every result has a JSON manifest — models/providers + versions, settings, seed, timings, cost
   estimate, warnings, licence/rights.
10. **Batch runner:** CLI runs a folder/file of test inputs against the API and writes all results to one folder.

### 3.5 Tool rules
1. Disclose everything: `SOURCES.md` (models, APIs, libraries, datasets, media) and `AI_USE.md` (AI coding tools and for what).
2. Commercially usable default path and outputs. No NC/research-only. **Avoid AGPL in the shipped service** (Lemmy is
   AGPL but runs only as a local *test instance*, never shipped).
3. Swappable providers: every external AI service behind a config-selected adapter + a mock adapter.
4. Know your cost: cost and time per unit of output.
5. No secrets in code, repo, logs or screenshots.

### 3.6 Appendix A — connector contract
Every method: `POST /connector/v1/{connector_key}/{method}` with JSON bodies (Python 3.11+ or Node 20+).

```
validate(rendition, destination, connection)               -> {ok, violations[]}
publish(rendition, destination, connection, idempotency_key)-> {platform_post_id, live_url, status}
get_status(platform_post_id, connection)                   -> {status, live_url}
delete(platform_post_id, connection)                       -> {deleted}
fetch_metrics(platform_post_id, connection, since)         -> {views, likes, comments, shares, saves, watch_time_s, clicks}  # optional
refresh_auth(connection)                                   -> {expires_at}                                               # optional
build_assist_pack(rendition, destination)                  -> {files[], caption, deep_link, checklist[]}                 # assisted only
```
Methods return results only; they **never write to the client's database** (the runtime does).

Manifest (one YAML per connector): `key, version, tier (native|protocol|bridge|feed|assisted), auth
(oauth2|api_key|bot_token|none), surfaces[{key, content_types, spec{title_max_chars, body_max_chars, links_allowed}}],
rate_limits.per_connection{requests, per_seconds}, capabilities, error_map{"429": PLATFORM_RATE_LIMIT, ...},
instance_param`.

Shared error codes:

| Code | Retry? | Action |
|---|---|---|
| `DEST_SPEC_VIOLATION` | No | Field-level reasons |
| `DEST_RULE_FREQUENCY_CAP` | Later | Reschedule after cap window |
| `CONNECTION_AUTH_EXPIRED` | After reconnect | Pause, ask owner to reconnect |
| `PLATFORM_RATE_LIMIT` | Yes | Back off using platform reset time |
| `PLATFORM_QUOTA_EXHAUSTED` | Next window | Reschedule |
| `PLATFORM_CONTENT_REJECTED` | No | Return platform message |
| `PLATFORM_UNAVAILABLE` | Yes | Stop after 5 consecutive failures (circuit breaker) |
| `CONNECTOR_DISABLED` | No | Kill switch on |

Destination record: `name, url, platform_key, surface, category, audience_tags, language, geo, est_audience, rules_url,
frequency_cap, self_promo_allowed, link_policy, disclosure_required, automation_allowed, connector_key,
relevance_score (1-5), relevance_reason, level, last_certified_at`.

Levels: **L0** catalogued · **L1** assisted · **L2** bridged/feed · **L3** native API (publish/status/delete tested) ·
**L4** native + metrics.

### 3.7 Hard cases (5 hidden tests drawn from these)
1. Publish times out, retried with same idempotency key.
2. 429 with reset time; expired token; content rejected.
3. Title/body over limit; unsupported media.
4. Second post inside frequency cap; promo post to no-promo community.
5. Platform renames an API field → certification fails, destination demoted.
6. CSV with duplicates, bad URLs, mixed languages; partner revokes permission.
7. Non-English community; destination requiring AI disclosure.

Always say what happened (warnings / error codes). Silent failure = 0.

### 3.8 Metrics to report (form tab 5)
Certification (destinations per connector, time per run, evidence) · Reliability (success rate sim + local; **duplicates
under retry = 0**) · Rule safety (caught vs missed) · Catalogue (rows, dupes, errors, time per 1,000) · Recommendation
(precision@10) · Cost per 1,000 publications. Real measurements only — "Not measured" beats a guess.

### 3.9 Live session (60–90 min, 4 × 5 marks)
1. **Walkthrough** — architecture, trace one output field from request to final file.
2. **Predict and run** — unseen input; predict, then run; explain mismatches fast.
3. **Debugging** — they break a setting/dependency/provider response; find via logs + code, fix properly.
4. **Live change** — e.g. new connector from template certified on a mock, or new posting rule; **test first**.

### 3.10 Deliverables
- [ ] Submission Form `Assessment4_<Name>.xlsx` — all tabs, every claim linked to evidence
- [ ] Live demo URL (token, rate limit, cold-start time stated, up until results)
- [ ] Private GitHub repo, reviewer invited, **real commit history**
- [ ] README.md (what, architecture diagram, setup < 10 commands, tests, batch runner, all settings, error codes)
- [ ] openapi.yaml
- [ ] REPORT.md (2–4 pages: approaches, choice, works/doesn't, numbers, cost per unit, next steps)
- [ ] SOURCES.md, AI_USE.md
- [ ] Test pack (all inputs/outputs incl. failures, logs, claim → file index)
- [ ] Demo video (unedited ≤ 8 min: clean run, a failure handled, tests running, one design decision in code)
- [ ] Certification pack per destination (run record, live URL/API response, screenshot, deletion confirmation)
- [ ] Catalogue CSVs, import reports, labelled recommender set

---

## 4. Architecture

```
                        ┌──────────────────────────── docker compose ─────────────────────────────┐
 Client / batch CLI     │                                                                          │
  ─────────────────────►│  API  (FastAPI, /api/v1/*)                                               │
  Idempotency-Key       │   middleware: auth token · rate limit · X-Correlation-Id · JSON logs     │
  X-Correlation-Id      │               idempotency store · error envelope                         │
                        │   ├─ /destinations /connections      (catalogue = config, not code)      │
                        │   ├─ /publications ─────┐                                                │
                        │   ├─ /certifications ───┼─► jobs table (Postgres) ─► worker process      │
                        │   ├─ /catalogue/import ─┘          ▲                    │                 │
                        │   ├─ /rules/capture, /rules/{id}/confirm                 ▼                 │
                        │   ├─ /tailor, /recommendations      │      Orchestrator:                  │
                        │   └─ /jobs/{id}  ◄── status/progress┘       1 tailor (spec + style)       │
                        │                                             2 rules engine → ALLOW /      │
                        │                                               BLOCK(reason)/RESCHEDULE    │
                        │                                             3 connector.validate+publish  │
                        │                                             4 ledger + manifest → MinIO   │
                        │                                             5 HMAC callback               │
                        │  Connector service  POST /connector/v1/{key}/{method}                    │
                        │   ghost · lemmy · discourse · devto · webhook(HMAC) · assisted           │
                        │   + hashnode · mrss · peertube         (manifest.yaml each)              │
                        │   shared HTTP client: per-connection rate limit, 429 backoff on reset    │
                        │   time, error_map, circuit breaker (5 failures), kill switch,            │
                        │   SIMULATION_MODE → all base URLs rewritten to the mock platform server  │
                        │        │                 │                    │                          │
                        │   Lemmy (local)     Ghost (local)      Mock platforms (Ghost/Lemmy/      │
                        │   + pictrs + pg     (sqlite)           Discourse/Dev.to/Hashnode/webhook │
                        │                                        + fault injection)                │
                        │  Postgres: destinations, connections (secret refs only), rules,          │
                        │            publications ledger, idempotency, jobs, certification runs    │
                        │  S3 (SeaweedFS): evidence, screenshots, assist packs, manifests, reports │
                        │  AI adapter (config): mock | anthropic | openai                          │
                        │  Scheduler: recertify < 30 days → demote + alert                         │
                        └──────────────────────────────────────────────────────────────────────────┘
```

### Stack
Python 3.12 (container), FastAPI, Pydantic v2, SQLAlchemy 2, Postgres 16 (SQLite for unit tests), httpx, PyJWT, PyYAML,
Markdown, boto3 → **SeaweedFS** (S3-compatible), pytest. All MIT / BSD / Apache-2.0 (psycopg is LGPL — dynamic
linking, commercial use OK).

**Decision (Day 1): SeaweedFS instead of MinIO.** MinIO no longer publishes public container images (Docker Hub and
Quay both refuse the pull), which would break `docker compose up` on a clean machine (risk R1). SeaweedFS is
S3-compatible and Apache-2.0 (MinIO server is AGPL). The service only speaks the S3 API, so any S3 store can be swapped
in via `S3_ENDPOINT_URL`.

**Decision: DB-backed job queue instead of Redis + RQ.** Jobs and idempotency records live in the same Postgres
transaction, so "same key → same job" is atomic; one fewer service to break on a clean machine (risk R1); RQ workers
don't run on Windows. Worker claims jobs with `SELECT … FOR UPDATE SKIP LOCKED`.

### Key design decisions (to defend live)
- **Two-layer idempotency.** (a) API: `Idempotency-Key` → stored `(key, request_hash) → response/job`. Same key + same
  body → same response; same key + different body → `409 IDEMPOTENCY_KEY_CONFLICT`. (b) Connector: the key is embedded
  in the post (Ghost slug, Lemmy hidden markdown marker). `publish` first looks the post up on the platform — so a retry
  after a *lost response* (timeout) returns the existing post instead of creating a second one.
- **Rules engine is pure and deterministic:** input = destination rules + publication ledger + content; output =
  `ALLOW | BLOCK(code, reason) | RESCHEDULE(not_before, reason)`. When unsure about promotion → block.
- **Strict response-schema checks** in connectors: a renamed field raises `PLATFORM_SCHEMA_CHANGED` → certification
  fails → destination demoted (hard case 5).
- **Mock server with fault injection** (`POST /__admin/faults`): timeout-after-create, 429 + reset, 401, 422, 5xx,
  field rename. Every hard case is an automated test and a batch-runner input.
- **Secrets:** DB stores only secret *references* (`env:NAME`, `file:/run/secrets/x`); a log filter redacts JWTs,
  `Bearer`/`Ghost` tokens and every resolved secret value; gitleaks in CI.
- **Simulation mode:** `SIMULATION_MODE=true` rewrites every destination's base URL to the mock platform server and
  supplies dummy credentials — the full system runs with no accounts.

---

## 5. Risk register

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| R1 | `docker compose up` fails on clean machine (Lemmy config, pulls, ports, start order) | Must-have 8 → **out** | High | Pin image versions, healthchecks + `service_healthy`, init/bootstrap containers; **fresh-clone test on a separate VM every evening** |
| R2 | Live demo down/slow during review | Must-have 9 → **out** | Med | Deploy skeleton **Day 1**, `restart: always`, uptime monitor, ≥ 4 GB RAM VM, state cold-start |
| R3 | Contract suite expects JSON shapes we guessed differently | Must-have 1 → **out** | Med | Use Appendix A names literally; **email clarification questions Day 1** |
| R4 | Timeout + retry duplicates (post created, response lost) | Must-have 6 → **out** | High if naive | Reconcile-before-publish via embedded key; test on Day 1 with mock timeout fault |
| R5 | No local Discourse / Dev.to; Dev.to delete semantics | Must-have 2 questioned | Med | Mocks faithful to real API; ask whether mock certification accepted; state honestly |
| R6 | Screening < 60 (Advanced all-or-nothing) | No live session | High | All 5 Advanced items mandatory; cheapest 3 extra connectors; timebox |
| R7 | Silent failure in hidden tests | −20 marks | Med | Every path returns a code or warning; one test per hard case, written with each feature |
| R8 | Missed rule violation | "Serious" | Med | Deterministic engine; block when unsure; table-driven rule test set with caught/missed count |
| R9 | Secret leak | Must-have 7 + declaration | Low–Med | Redaction filter, gitleaks, `.env` ignored, secret refs only |
| R10 | Lemmy setup eats hours | Slip | High | Spike in hour 1, timebox 3 h, minimal official config |
| R11 | Overclaiming / can't explain AI code | **Disqualified** | Med | Honest Partial/No; review every generated file; walkthrough notes |
| R12 | Evidence/docs crunch on last day | Claims score 0 | High | Scripts write cert packs, metrics, test-pack index automatically from Day 1 |
| R13 | Licence issues (AGPL, NC data) | Rule breach | Low | MIT/Apache/BSD only in service; Lemmy only as test instance; SOURCES.md updated as we go |

### Clarification questions to email the hiring team (Day 1, R3/R5)
1. Exact JSON request/response schemas for each contract method (field names inside `rendition`, `destination`,
   `connection`)? Is the HTTP status for errors specified?
2. Is certification against a faithful mock accepted for Discourse and Dev.to, or is a real self-hosted Discourse expected?
3. Dev.to's public API has no article delete — is unpublish (`published: false`) acceptable as "delete" for certification?
4. May we add error codes beyond the shared list (e.g. `PLATFORM_SCHEMA_CHANGED`, `DEST_RULE_SELF_PROMO`)?
5. Should `/connector/v1/*` POSTs also require the `Idempotency-Key` header, or only `/api/v1/*`?

---

## 6. Plan (risk-driven)

### Day 1 — kill the "instant out" risks (R1 R2 R3 R4 R10)
1. Send clarification email.
2. Compose: Postgres, MinIO, mock platforms, **Ghost, Lemmy (+pictrs, pg)**, bootstrap container (Ghost owner +
   integration key, Lemmy admin + test community → secrets volume). Pinned versions, healthchecks.
3. API core: settings, JSON logs + correlation + redaction, error envelope, `/health` `/ready`, jobs + worker,
   Idempotency-Key middleware, storage adapter (MinIO / local), output manifest, HMAC callback.
4. Connector framework: base class, manifest loader/validator, `/connector/v1/{key}/{method}` router, HTTP client
   (rate limit, 429 backoff, circuit breaker, error map), kill switch, simulation mode.
5. **Ghost + Lemmy connectors** with reconcile-before-publish idempotency.
6. Tests same day: timeout+retry → 0 duplicates; 429 backoff; 401 → `CONNECTION_AUTH_EXPIRED`; spec violations.
7. Evening: deploy skeleton to VM; fresh-clone compose test; evidence auto-writer on.

### Day 2 — all must-haves + Core (R5 R7 R8 R9)
1. Mock: Discourse, Dev.to, webhook receiver (+ faults).
2. Connectors: **Discourse, Dev.to, webhook (HMAC, revoked permission), assisted pack** — each with hard-case tests in the
   same commit.
3. Rules engine + table-driven rule test set (caught/missed metric auto-generated).
4. Certification harness: strict schema → fail → demote; delete < 10 min; screenshot; evidence to MinIO.
5. `openapi.yaml` export + test it matches the app; batch runner; gitleaks; simulation verified.
6. Evening gate: clean-VM compose + `make test` with no keys; redeploy; batch over all hard cases.
   **If any must-have is not green, Day 3 starts there.**

### Day 3 — Advanced, all 5 items (R6 R12)
1. MRSS, Hashnode, PeerTube connectors (~2 h, from template).
2. Catalogue import + 1,000-row benchmark + row report (~2 h).
3. Relevance 1–5 + reason (heuristic + LLM adapter, mock default) (~1 h).
4. Tailoring (~1.5 h).
5. AI rules capture + human confirm (~1.5 h).
6. Redeploy, full batch on live demo, metrics, README/REPORT/SOURCES/AI_USE, honest form, unedited video.

### Days 4–10 (Outstanding → Top pick)
Recommender + precision@10 · fetch_metrics ×3 · recertification scheduler + demote/alert · 150+ real destinations ·
live-session rehearsal (trace a field, new connector from template in ~20 min test-first, debug from logs).

### Cut list if behind (last to first)
Live-demo polish → PeerTube (swap Slack/Kit mock) → rich enrichment (member count only) → LLM quality (heuristic path,
described honestly). **Never cut:** must-haves and their evidence.
