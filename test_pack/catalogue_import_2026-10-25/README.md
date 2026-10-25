# Real catalogue import report (2026-10-25)

`catalogue_data/destinations.csv` (199 real destinations) imported through `POST /api/v1/catalogue/imports` on the local
stack, with reachability checks and relevance scoring on. Files: [report.csv](report.csv) (row by row) and
[job.json](job.json) (summary, manifest, links).

| Run | Result | What it showed |
|---|---|---|
| [run1_all_timeouts](run1_all_timeouts/report.csv) | 199 imported, **199 marked unreachable** (timeouts) | The importer sent up to 50 checks at once with no limit per site |
| [run2_burst_throttled](run2_burst_throttled/report.csv) | 68 unreachable: 51 timeouts, clustered on hosts with many communities (14 on programming.dev, 6 on lemmy.ml, a run of Discourse-hosted forums) | Busy hosts throttle a burst. Fix: at most 4 checks at a time per host (`CATALOGUE_PER_HOST_CONCURRENCY`) and one retry after a timeout; test `test_reachability_checks_are_polite_per_host_and_retry_a_timeout` |
| **final** ([report.csv](report.csv)) | 199 updated in 11.3 s, **0 timeouts**; 57 not reachable: 40 `ConnectError`, 15 `403`, 1 `401`, 1 `404` | See below |

**The 57 not reachable in the final run:**
- **30 of the 40 `ConnectError`s are lemmy.world**, which by then closed every connection from this IP during the TLS
  handshake (it answered normally earlier the same day). The two burst runs above most likely tripped its anti-bot
  protection, so no further runs were made against it. The other 10 are single hosts.
- **15 × `403` and 1 × `401`**: sites behind bot protection; they exist but refuse automated checks.
- **1 × `404`**: a page that has moved.

The research run on 2026-10-22 (`scripts/research_catalogue.py`, gentler, counts a bot-protection 401/403/429 as "site
exists") verified all 199 live; see `catalogue_data/RESEARCH.md`. Reachability is a point-in-time check from one IP:
a destination marked unreachable stays catalogued (L0) and is only excluded until it passes certification.
