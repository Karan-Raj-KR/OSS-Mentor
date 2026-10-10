# Verification — October 10, 2026

## Local automated checks

- `.venv/bin/python -m pytest -q`: **236 passed, 9 skipped** in 24.48 seconds. Fifty pre-existing-style Starlette per-request cookie deprecation warnings; no failing tests. Skips are PostgreSQL-dependent checks with no available configured test server.
- Coverage includes official SDK request/response shape, text extraction, cached-input accounting, disk-cache separation, incomplete/refused output, missing usage charging, unknown-model prices, budget reservations and failure release, sanitised authentication/NDJSON errors, live checks becoming unavailable/stale, missing comments, claim source links, persisted checkpoint feedback/retry, diff/evidence invalidation, concurrency, server-grounded coaching, owner-scoped storage/export/deletion and disabled new Full-auto jobs.
- Preserved legacy worker safety tests use fake `gh` / Claude CLIs and disposable local Git repositories. They verify token isolation and human approval before any simulated publication. They do not prove a real Claude/Codex/GitHub integration.
- After the final bounded-context/disclosure edits, focused core/hardening/journey regression: **138 passed** in 1.75 seconds.
- Python compilation and `git diff --check` pass. The test suite's OpenAI and issue fixtures are mocks, not live integration proof.

## Actual controlled-fixture execution

The engineer copied only the known `demo/parser.py` and its regression tests into a disposable temporary directory. `python -m pytest -q` returned **exit 1, two failures before** correcting the loop bound, and **exit 0, two passes after**. See [recorded commands/status/output](../demo/test-evidence.json). No unfamiliar repository scripts ran. Attaching this evidence to the review preserves its student-supplied label; OSS Mentor does not attest that a guided runner ran it.

## Browser execution

The app was run with `./run.sh` at `http://127.0.0.1:8000`, and the separate fixture with `.venv/bin/python -m backend.demo` at `http://127.0.0.1:8001`.

Chromium exercised the actual fixture UI/API/storage: interview → deterministic match → simulated fresh issue/policy checks → repo tour → source fetching → diff/evidence save → incorrect checkpoint answers and targeted feedback → successful retry → readiness with tests still unverified → reload/resume → diff edit/reset. Checked 1440×1000 and 390×844, no horizontal page overflow and no page errors. Normal homepage and missing-key HTTP 503 behaviour were also verified at both sizes. Narrow header and homepage decoration overflow found in QA were fixed.

Artifacts: [fixture browser report](media/hackathon/browser-verification.json), [normal browser report](media/hackathon/normal-browser-verification.json), [desktop review](media/hackathon/review-desktop.png), [narrow review](media/hackathon/review-mobile.png), [recorded fixture walkthrough](media/hackathon/controlled-fixture-walkthrough.webm).

The fixture uses scripted mentor responses and simulated GitHub reads. Its successful workflow is not live OpenAI evidence, a third-party contribution or a general-quality checkpoint evaluation. UI libraries/fonts still depend on CDN access/cache. Recording is the network-independent fallback.

The mechanical UI detector was run once; it reported stylistic warnings around existing gradient/glass/spacing patterns. Existing visual identity was preserved as requested. Browser verification does not establish a full accessibility audit, every browser, or every possible policy/model response.

## External evidence verified live

- GitHub REST read of `havinash-007/moreee` succeeded (the first five issue results included no ordinary issues).
- GitHub search, issue/timeline/comments and policy reads for [Flask #6146](https://github.com/pallets/flask/issues/6146) succeeded at **2026-10-10 07:03:07 UTC (12:33:07 IST)**. Observed open, no assignee, no open linked PR and no recent claim in the inspected data. Scanned policy files were absent, so policy is **unknown**, not “AI allowed.” This timestamped observation is not a recommendation or a guarantee the issue remains available. [Snapshot](media/hackathon/github-live-check.json).
- Official OpenAI [Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create) and [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini)/[GPT-4.1](https://developers.openai.com/api/docs/models/gpt-4.1) pages were fetched to verify documented IDs, response contract and estimated token rates. No real inference was performed: **OPENAI_API_KEY was absent**. Account-level model access is unverified.
- [Organiser event page](https://luma.com/0bdmf0sw) confirms session time 10:00 AM–2:30 PM IST, judging criteria and ten-ticket pool. No separate submission deadline was found in workspace Markdown or that page. Pre-existing-project eligibility needs organiser confirmation.

Agent Reach's installed CLI failed to import its package; source checks used the browser web tool and its documented Jina-reader fallback instead. No existing Graphify index was present; connected source/callers were inspected directly rather than generating an unrelated graph artifact.

## Remaining boundaries

- Missing `OPENAI_API_KEY` blocks live mentor inference/evaluation; add it securely to the server environment and verify a real request before a live-AI presentation.
- PostgreSQL parity, hosted OAuth, deployment, provider-account model access and live checkpoint quality remain unverified.
- Full-auto Codex migration was intentionally deferred. Legacy worker code remains Claude-based; new job creation is disabled and primary demo uses guided contribution.
- Guided tests/diffs are supplied by the student. No runner executes them in this build. Repo checkout identity, CI, policy compliance, legal sign-offs and maintainer acceptance require human verification.
- Budgets are estimates; reservations are per process, not atomic across hosted instances. Do not claim production-wide spend guarantees.
- Review edits require Save before navigating away; chat/guide checkbox progress is not server-attested evidence.
- No credentials were committed, no commits/pushes/deploys/external PRs/reviewer messages were performed. Existing uncommitted source work was preserved. See [built today](BUILT_TODAY.md).
