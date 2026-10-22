# Real destination catalogue: research report

Generated 2026-10-22T18:01:03Z by `scripts/research_catalogue.py` from `catalogue_data/candidates.csv`. Catalogue only: nothing was posted anywhere.

- Candidates: 226; **kept: 199**; dropped: 27 (see `dropped.csv`)
- Kept by connector: assisted 55, devto 12, discourse 50, hashnode 6, lemmy 59, mrss 8, peertube 9
- Relevance (1-5): 2: 9, 3: 103, 4: 62, 5: 25
- Rules from the destination's own rules text: 24; defaults only: 175

## Method

1. **Verified live.** Lemmy communities through `/api/v3/community` (dropped if missing, NSFW, removed or moderator-only); Discourse forums through `/about.json`; PeerTube instances through `/api/v1/config/about`; everything else over HTTP (a bot-protection 401/403/429 counts as 'site exists' and is recorded as such).
2. **Rules read** from the community's own text: the Lemmy sidebar and PeerTube terms via their APIs, Discourse `/guidelines`, and the listed rules pages, fetched with the app's fetcher (robots.txt honoured).
3. **Rules extracted** with the deterministic extractor (`app/catalogue/heuristics.py`); each extracted field has an evidence quote in `rules_evidence`.
4. **Conservative defaults** fill fields the page does not state; they are listed per row in `rules_defaults_applied`, never presented as the community's own rule.
5. **Permission:** every third-party community is `automation_allowed=false`, `permission_status=needed`: a person posts (assist pack) until the owner agrees to automated posting.
6. **Relevance 1-5** against AI, video, marketing, SaaS, creators and founders, with the reason.

## Defaults when a rules page is silent

| Connector | Frequency cap | Self-promotion | Links |
|---|---|---|---|
| lemmy | 1/7d | False | max:1 |
| discourse | 1/7d | False | max:1 |
| devto | 2/7d | True | max:3 |
| hashnode | 2/7d | True | max:3 |
| peertube | 3/7d | True | max:3 |
| mrss | 10/1d | True | any |
| assisted | 1/30d | by category | max:1 |

## Limits

- Rule extraction is pattern-based; a person should confirm rules before relying on them (the same confirm step as the rules-capture API).
- Member counts come from the platforms' own APIs where available; other sites have none.
- Verification is a point-in-time check (`verified_at`).
