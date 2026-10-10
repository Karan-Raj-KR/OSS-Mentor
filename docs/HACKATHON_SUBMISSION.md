# OSS Mentor — submission draft

**Your first open-source contribution—with understanding, not guesswork.**

OSS Mentor helps a student move from a profile to a suitable organisation, inspect current GitHub issue conflicts and contribution policies, understand fetched repository material, then prepare a contribution they can explain. A saved review presents the actual student-supplied diff, test notes, PR description and AI disclosure. Focused questions catch misunderstandings, provide hints and support retries; changing the diff resets understanding status. Readiness retains unverified evidence instead of inventing passing tests or guaranteed availability.

| Judging criterion | Demonstrated value |
|---|---|
| Usefulness | Reduces wasted time choosing taken issues and gives first-time contributors a structured learning/review path. Student usefulness remains a hypothesis until user trials. |
| Technical execution | Official OpenAI Responses SDK for primary inference; usage-based estimated budgets/cache; fresh GitHub reads; per-user persisted reviews; diff-bound checkpoint invalidation and conditional writes against concurrent edits; preserved auth/approval gates. |
| Originality | The proposed differentiator joins issue conflict checks with code-grounded coaching and a contribution the student can explain. We do not claim global novelty or lack of incumbents. |
| How well it works | Automated regressions, browser journey at desktop/narrow sizes, disposable-fixture before/after tests and a repeatable labelled recording. Exact evidence and live-vs-mock boundaries are in VERIFICATION.md. |

OpenAI use: the delivered normal backend uses server-side `OPENAI_API_KEY`, configurable documented model IDs and the official Python SDK for ranking, tours, coaching and checkpoint questions/feedback. Codex was used to implement and verify these local changes. A real OpenAI request was not possible during this run because the key was absent. The prepared fallback explicitly substitutes scripted responses; it must not be presented as live OpenAI.

This is a pre-existing project extended on October 10, 2026. See BUILT_TODAY.md for attribution boundaries. Full-auto remains a legacy Claude worker, is disabled for new jobs and is not in the primary demo. No new third-party contribution, reviewer message, deployment or merged PR is claimed.

The [organiser page](https://luma.com/0bdmf0sw) lists the session ending at 2:30 PM IST, the four judging criteria and a pool of ten tickets. No actual submission deadline was found in the workspace or public page. Organisers must confirm the deadline and eligibility for pre-existing work. This is a draft to submit manually only after those details are confirmed; selection is not guaranteed.
