# 90-second demo

## Preflight

- Confirm organiser deadline and eligibility for an existing project. The public session ends at 2:30 PM IST; no actual submission deadline is recorded in this workspace.
- Launch normal build with `./run.sh` at port 8000. Set `OPENAI_API_KEY` server-side and verify a real request before claiming live OpenAI. This run did not have that key.
- Use the repeatable controlled fixture at port 8001 for the prepared demo: `.venv/bin/python -m backend.demo`.
- Say **“This is a controlled fixture showing the working journey; its mentor responses and GitHub checks are scripted.”** The banner remains visible. It is not evidence of a live integration.
- UI CDN libraries/fonts need internet or browser cache. Keep the [recorded walkthrough](media/hackathon/controlled-fixture-walkthrough.webm) available for a network failure.
- Have `demo/change.diff` and `demo/test-evidence.json` ready. The latter records actual tests executed by the engineer in a disposable directory; when attached, they remain student-supplied, not runner-attested.
- Pre-fill a saved profile and rehearse: Learn, Python, beginner, web, hackathon, weekend, commit/push, pick for me, normal laptop, yes to both.
- Pre-fetch `parser.py, test_parser.py` and paste the diff/evidence for a 90-second presentation. Leave the checkpoint incomplete. Start from the homepage with a saved profile so **Use my saved profile** demonstrates the matching transition quickly.

## Spoken walkthrough

| Time | Action and narration |
|---|---|
| 0–12s | “Students often pick an issue that is already taken, then submit a change they cannot explain. OSS Mentor helps them choose and understand.” Show the short saved profile; click Use my saved profile. |
| 12–25s | Show deterministic match reasons, select the controlled parser organisation, then its issue. Point to open state, assignees, PR/claim checks, policy source and timestamp. “These are observations, never a guarantee of availability.” |
| 25–38s | Enter the journey; show Repo tour. “The mentor uses fetched repository material.” Open Contribution review and the fetched `parser.py`: the loop excludes its last index. |
| 38–52s | Show the attached diff and changed file. Show recorded command, exit status and output in the explicitly student-supplied evidence field. “This app did not run the tests; it does not turn notes into proof.” |
| 52–72s | Generate the three questions. Answer incorrectly: “It sorts the list,” “It makes it faster,” “All inputs pass.” Submit. Point to the targeted hints and incomplete status. Retry with the prepared answers below. |
| 72–90s | Show completed checkpoint and “Ready for human review — tests remain unverified.” Show editable PR description, AI disclosure and remaining checks. “Nothing is published here. A human reviews and submits. Today we added OpenAI inference and this grounded review/checkpoint journey to the existing project.” |

Prepared retry answers (for this fixture only):

1. `range has an exclusive upper bound, so subtracting one excludes the last index.`
2. `For a singleton, range(1) visits index 0 and preserves the one element.`
3. `The test preserves the last item; invalid input types and non-list iterables remain untested.`

The scripted fixture evaluator checks these concepts to make the demonstration repeatable; it is not a general model evaluation or proof of competence. The normal build sends saved issue/diff/questions/answers to OpenAI and validates the returned feedback schema.

## Reset and recovery

- Fixture state lives at `/tmp/oss-mentor-demo/jobs.db`, separate from real `data/`. At the fixture's Profile screen, **Delete my data** clears it. Verify the CONTROLLED FIXTURE banner before deleting anything.
- For an automatic clean recording, run `.venv/bin/python tools/demo_walkthrough.py`. It checks the fixture health flag before resetting that fixture profile. It exercises reload and diff invalidation, then leaves an incomplete checkpoint intentionally.
- To rehearse the successful ending, generate questions and answer again, then re-check issue/policy if its timestamp is older than five minutes.
- Restart fixture with the same `DEMO_DATA_DIR` to retain its state, or choose a new directory for a separate rehearsal: `DEMO_DATA_DIR=/tmp/oss-mentor-rehearsal PORT=8001 .venv/bin/python -m backend.demo`.
- Unsaved edits stay only in the component; click Save review before navigating away. Reload/resume restores saved diff, evidence and checkpoint; chat and self-reported guide progress are not readiness proof.
- If normal mentor inference fails: show the explicit error, do not call it live success, and switch to the labelled fixture or recording.
- If GitHub is unavailable, show the failed request; do not convert it to a free issue. The fixture is the fallback, not a substitute live claim.
- Full-auto is unavailable and excluded. Never demonstrate old Claude worker output as OpenAI/Codex.

## Evidence available

[Desktop feedback](media/hackathon/checkpoint-feedback-desktop.png), [desktop review](media/hackathon/review-desktop.png), [narrow review](media/hackathon/review-mobile.png), [browser verification](media/hackathon/browser-verification.json), [recording](media/hackathon/controlled-fixture-walkthrough.webm). These artifacts are controlled-fixture evidence, not third-party contributions.
