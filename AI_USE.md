# AI use

> **Draft for the candidate to check.** This file was drafted by the AI coding assistant from the build sessions. The
> candidate must read it, correct anything that does not match how they worked, and complete the last section before
> submitting. The brief treats manual work presented as automated, and uncredited work, as disqualifying, so this
> file states plainly what the AI did.

## Tools

| Tool | Version | Used for |
|---|---|---|
| Claude Code (Anthropic), in the Claude desktop app | model Claude Opus 5.5 (`claude-opus-5-5`) | Writing the code, tests, scripts and documents; running the stack, tests and batches; debugging |
| Claude via the Anthropic API (inside the service) | `claude-opus-5-5`, optional | Rules capture and relevance at catalogue time, only with `AI_PROVIDER=anthropic`. Not run live for this submission; see [SOURCES.md](SOURCES.md) |

No other AI tools were used.

## Who did what

The work happened in one long conversation between the candidate and Claude Code, 2026-10-22 to 2026-10-25.

**The candidate**
- Supplied the brief and the Submission Form, and asked for a full analysis, a scoring breakdown and a risk-driven plan.
- Set the constraints: a private repository, the repository's name, and neutral naming throughout.
- Chose the order of work (Day 1 core → Core connectors and rules → Advanced → Outstanding → evidence → live demo)
  and approved each step before it started.
- Created the GitHub repository (its first commit, `2dcee17`, is theirs), signed in to GitHub, ran the later pushes
  themselves, and checked the CI runs.
- Chose the live-demo host after the options were checked (Oracle needs a credit card; AWS's Free plan accepts a
  debit card), created the AWS account and the server, attached the Elastic IP, opened the ports, and approved the
  deployment plan before anything went public.
- Reviewed the recommender's labelled set in the review workbook and kept every draft label (signed 2026-10-25).
- Asked the questions that changed the work, for example whether the service needs a frontend (it does not; the
  `/docs` page gained an Authorize button instead).

**Claude Code**
- Analysed the brief and wrote [docs/PLAN.md](docs/PLAN.md) and the tracker [PROGRESS.md](PROGRESS.md).
- Wrote nearly all of the code: the service, the 9 connectors and their manifests, the rules engine, certification,
  scheduler, recommender, catalogue tools, the mock platform server, bootstrap, Docker Compose, CI and the live-demo
  deployment.
- Wrote the test suite (216 tests plus 2 real-instance E2E tests) and the 14 batch scenarios, ran them, and fixed what
  failed. The failed runs are kept in
  `test_pack/`, together with the fixes they led to (for example the SSRF guard).
- Drafted the list of 226 candidate destinations from general knowledge, and wrote the script that verified each one
  live and kept 199 (`scripts/research_catalogue.py`).
- **Drafted the recommender's labelled set** (`catalogue_data/labelled_set.json`). The brief asks for a set the
  candidate labels, so the candidate reviewed it in a spreadsheet on 2026-10-25 and kept every label unchanged
  (0 added, 0 removed; recorded in the file). Precision@10 is reported as AI-drafted labels with that review.
- Drafted clarification questions for the hiring team (the candidate decided not to send them).
- Deployed the live demo to the candidate's AWS server over SSH once the plan was approved (server setup, code copy,
  generated secrets kept out of the repository), then verified it from outside and ran every batch scenario.
- Wrote the documents: README, REPORT, SOURCES, this file, the no-spam policy, the partner webhook protocol and the
  deployment guide.

**Commits.** Claude Code made the commits at the candidate's request, and each carries a
`Co-Authored-By: Claude Opus 5.5` trailer. The history before the first push was squashed into one Day 1 commit, to meet the
candidate's request that a name be removed from every file. Every later commit is as it was made.

## What is automated, and what a person does

| Step | Automated | A person |
|---|---|---|
| Posting to destinations | Yes, after certification and the rules check | Communities that ban automation get an assist pack, and **a person posts** |
| Posting rules from a community's rules page | Proposed by the heuristics (or Claude) | **A named reviewer confirms** each proposal before it is used |
| Catalogue research | Verified live by script | Reviewing the rules defaults (175 of 199 rows) is still to do |
| Recommender labels | Drafted by AI | **The candidate reviewed them** (2026-10-25) and kept the draft unchanged |
| Test posts | Created and deleted by the certification harness | None |

## To complete by the candidate

- [ ] Which parts you reviewed line by line, and which you checked by running them.
- [ ] Anything you wrote or changed yourself. Known from the sessions (check and extend): the repository's first
      commit; the live-demo server on AWS; the review of the labelled set.
- [ ] How you prepared to explain the code live (architecture walkthrough, one output field traced from request to
      file, the rules engine, idempotency).
- [ ] Confirm or correct every statement above, then delete the draft note at the top.
