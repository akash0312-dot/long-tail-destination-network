# Publishing cost: 100/100 publications succeeded

Run 2026-10-24T22:27:51.752739+00:00 against http://localhost:8000: one certified destination on the mock Discourse, 100 publications through the API, the job queue and one worker (the default compose stack).

| Measure | Value |
|---|---|
| Worker time per successful publish | mean 40 ms, median 37 ms, p95 63 ms |
| Rescheduled by the connection's rate limit (Discourse: 60 requests/min) | 73 of 100, all published after the reset (229 job attempts) |
| Worker time per publication, rescheduled attempts included | 77 ms |
| First job created → last job finished | 185.4 s (32.4 posts/min on one connection) |
| Client time incl. this API's rate limit (6.2 s to submit) | 185.6 s |
| AI providers used | none |
| AI cost, whole run | $0.0 |
| **Per 1,000 publications: worker time** | **0.0214 worker-hours** |
| **Per 1,000 publications: AI cost** | **$0.0** |

Cost per 1,000 publications = worker-hours × the server's hourly price + AI cost. The default publish path uses no AI (rules and tailoring are deterministic), so the AI part is zero; AI is only used at catalogue time (rules capture, relevance) when `AI_PROVIDER=anthropic`.

Limits: the platform here is the local mock, so platform latency is near zero; real platforms add their own response time (the local Ghost certification took 2.1 s, mostly Ghost itself).
