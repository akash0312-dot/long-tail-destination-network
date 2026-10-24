# Sources

Everything this project uses that it did not write itself: models, APIs, libraries, container images, data and
media. How AI tools were used to build it is in [AI_USE.md](AI_USE.md).

Licences for the Python libraries were read from the installed packages' metadata (the service image, 2026-10-25).
The default path and its outputs are commercially usable. The only AGPL software is the local Lemmy test instance,
which is never shipped or modified.

## AI models and services

| What | Used for | When | Terms |
|---|---|---|---|
| Claude (`claude-opus-5-5`) via the Anthropic Messages API, `anthropic` Python SDK 2.1.0 | Rules capture (reads a rules page, proposes posting rules) and relevance scoring | Only with `AI_PROVIDER=anthropic`; **not run live for this submission** (unit-tested with a fake client) | Anthropic commercial terms; outputs are the user's |
| Server-side refusal fallback (`server-side-fallback-2026-07-01` beta) | If the model declines, the API retries on a fallback model; the manifest records which model answered | Same | Same |
| Mock provider `heuristics-v1` (this repo, `app/catalogue/heuristics.py`) | The default for rules capture and relevance: deterministic, no key, no cost | Always, unless `AI_PROVIDER=anthropic` | Own code |

No AI runs on the publishing path: rules, tailoring and recommendations are deterministic code.

## Platform APIs integrated

Built from each platform's public API documentation. The mock platform server (`mock_platforms/`, own code) imitates
these APIs for simulation and fault injection.

| Platform | API | Connector |
|---|---|---|
| Ghost 5 | Admin API (JWT from an Admin API key) | `ghost` |
| Lemmy 0.19 | HTTP API v3 | `lemmy` |
| Discourse | REST API (`Api-Key` / `Api-Username`) | `discourse` |
| Dev.to (Forem) | Forem API (`/api/articles`, `api-key` header) | `devto` |
| Hashnode | GraphQL API | `hashnode` |
| PeerTube | REST API with OAuth 2 (password grant), video import from URL | `peertube` |
| Media RSS | RSS 2.0 + the Media RSS (MRSS) namespace | `mrss` |
| Partner websites | Own protocol: HMAC-SHA256-signed webhook, [docs/PARTNER_WEBHOOK.md](docs/PARTNER_WEBHOOK.md) | `webhook` |

**Read-only public endpoints used to research the real catalogue** (`scripts/research_catalogue.py`, 2026-10-22):
Lemmy `/api/v3/community`, Discourse `/about.json` and `/guidelines`, PeerTube `/api/v1/config/about`, and plain HTTP
checks of other pages. robots.txt was honoured, and the user agent names the bot. Nothing was posted anywhere.

## Python libraries

| Library | Version | Licence | Part of |
|---|---|---|---|
| FastAPI | 0.115.12 | MIT | service |
| Starlette | 0.46.2 | BSD-3-Clause | service (via FastAPI) |
| Uvicorn | 0.34.2 | BSD-3-Clause | service |
| Pydantic | 2.11.4 | MIT | service |
| pydantic-settings | 2.9.1 | MIT | service |
| SQLAlchemy | 2.0.40 | MIT | service |
| psycopg (+ psycopg-binary) | 3.2.9 | LGPL-3.0 | service (used as an unmodified library) |
| HTTPX | 0.28.1 | BSD-3-Clause | service, batch runner |
| PyJWT | 2.10.1 | MIT | service (Ghost Admin API tokens) |
| PyYAML | 6.0.2 | MIT | service (manifests), OpenAPI export |
| boto3 / botocore | 1.38.13 / 1.38.46 | Apache-2.0 | service (S3 storage) |
| python-multipart | 0.0.20 | Apache-2.0 | service (CSV uploads) |
| Markdown | 3.8 | BSD-3-Clause | service (Markdown to HTML for Ghost) |
| anthropic | 2.1.0 | Apache-2.0 | service (optional Claude provider) |
| pytest | 8.3.5 | MIT | tests only |
| Playwright | 1.52.0 | Apache-2.0 | evidence tooling only (certification-pack screenshots), with the Chromium it downloads |

## Container images and tools

| Image / tool | Licence | Role |
|---|---|---|
| `python:3.12-slim` | PSF licence + Debian packages | base image of the service |
| `postgres:16-alpine` | PostgreSQL Licence | service database; Lemmy's database |
| `chrislusf/seaweedfs:3.80` | Apache-2.0 | S3-compatible object storage |
| `ghost:5-alpine` | MIT | local Ghost **test instance** |
| `dessalines/lemmy:0.19.8` | AGPL-3.0 | local Lemmy **test instance only**: not shipped, not modified, not linked |
| `caddy:2.8.4-alpine` | Apache-2.0 | HTTPS reverse proxy, live demo only |
| `zricethezav/gitleaks:v8.18.4` | MIT | secret scan in CI and locally |
| GitHub Actions `checkout@v4`, `setup-python@v5`, `upload-artifact@v4` | MIT | CI |

## Data

| Data | Source | Notes |
|---|---|---|
| `catalogue_data/candidates.csv` (226 candidates) | Names and URLs of public communities, listed with the AI coding assistant from general knowledge | Every candidate was then verified live by script; 27 were dropped with reasons (`dropped.csv`) |
| `catalogue_data/destinations.csv` (199 destinations) | The communities' own public pages and APIs | Member counts from the platforms' APIs. `description` and `rules_evidence` hold short excerpts of each community's public description and rules, kept as evidence next to the source URL |
| `catalogue_data/labelled_set.json` | Drafted by the AI coding assistant | **Must be reviewed and corrected by the candidate** before precision@10 is reported (`reviewed_by` is empty until then) |
| Test CSVs and scenario inputs (`test_inputs/`, tests) | Written for this project | Synthetic |

## Media

No third-party media is used. Certification posts are generated text. `CERTIFICATION_TEST_VIDEO_URL` defaults to a
placeholder (`https://example.com/certification-test.mp4`) that only the mock video platforms receive. A real
deployment must point it at a clip the owner has rights to. The certification-pack screenshots were rendered by this
project from responses of its own local instances and mocks.
