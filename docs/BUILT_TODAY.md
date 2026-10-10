# Built today — October 10, 2026

OSS Mentor existed before this hackathon. The starting checkout already contained uncommitted edits in agents, API, GitHub checks, jobs, profiles, replies, runner, runner tests, project context and UI, plus an untracked hardening test. These were preserved and are not credited as today's new implementation. A pre-edit tracked diff was saved locally at `/tmp/oss-mentor-preexisting.patch`; it is not a complete archive of pre-existing untracked files.

## Prior implementation reused

- Profile interview, deterministic organisation scoring, existing catalogue and live GitHub discovery/scout.
- Repository tour, Learn/Semi-auto chat, existing visual style and no-build React loader.
- SQLite/Postgres storage, GitHub OAuth, deployment/CSRF gates, reply drafting and disabled-by-default posting.
- Claude wrapper budgets/cache/usage patterns and legacy worker approval, sandbox, isolation and failure tests.

## Additions in this run

- Primary mentor inference migrated to official OpenAI Responses SDK, GPT-4.1 configurable defaults, correct cached-input accounting, explicitly estimated costs, unknown-price blocking, sanitized errors and usage/cache regression coverage.
- Guided review persistence, source-file fetching, server-grounded coaching, unified diff/file inspection, student evidence labels, editable PR copy and disclosure, computed readiness and data export/deletion.
- Actual diff-grounded questions/evaluation, targeted feedback/retry, stored answers, diff/evidence invalidation and concurrent edit protection.
- Visible issue details and policy distinctions/source links; unavailable comment reads remain unknown; fresh checks before entry and readiness.
- Exact requested homepage message, guided-first mode selection, disabled new Full-auto jobs, narrow header overflow fix and review recovery states.
- Controlled fixture, actual before/after test evidence in a disposable directory, browser walkthrough/recording, 90-second script, checklist/reset, submission and architecture/verification documentation.

## Not claimed

No real OpenAI inference (key absent), live OAuth, third-party test execution, new external PR, reviewer message, deploy, merged contribution, production readiness or guaranteed ticket. GitHub reads were verified separately. Full-auto was not migrated to Codex. Organisers must confirm pre-existing-project eligibility and actual submission deadline.
