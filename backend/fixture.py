"""The controlled demo fixture: scripted mentor replies and a tiny labelled repository standing in for GitHub.

Used two ways, both clearly labelled in the UI and in /api/health ("demo_fixture": true):
  * `python -m backend.demo`  : loopback-only rehearsal server, fixture catalogue only
  * a PUBLIC deployment that has not finished its sign-in setup: the same fixture on top of the real, free catalogue matching, so
    visitors and automated evaluators can click through the whole guided journey. Nothing here calls OpenAI or GitHub, spends a key,
    reads a student's account or publishes anything. It switches itself off as soon as the deployment is fully configured.
"""
import json
import uuid

from . import agents, auth, config, github, llm, matcher

ROOT = config.ROOT / "demo"
REPO = "oss-mentor-demo/parser"
ISSUE = {"title": "parse() drops the last item", "body": "parse([1,2,3]) returns [1,2]. Preserve every item, including singleton inputs. Add regression coverage.", "number": 7, "html_url": "/static/../demo-issue", "state": "open", "assignees": [], "comments": 0}
FILES = {"parser.py": (ROOT / "parser.py").read_text(), "test_parser.py": (ROOT / "test_parser.py").read_text(),
         "README.md": "Controlled demo fixture. parse copies a list but drops its final element. Run python -m pytest -q.",
         "CONTRIBUTING.md": "Controlled fixture: explain your change, add tests, disclose AI help. AI assistance is permitted in this fixture."}


def fixture_get(path, params=None, token=None):
    if path == "/search/repositories":
        return {"items": [], "total_count": 0}          # live discovery is not part of the fixture
    if path == "/search/issues":
        if "oss-mentor-demo" not in str((params or {}).get("q", "")):
            return {"items": [], "total_count": 0}      # only the labelled demo project has scripted issues
        return {"items": [{**ISSUE, "html_url": "/demo-issue", "repository_url": f"https://api.github.com/repos/{REPO}", "labels": [{"name": "good first issue"}], "updated_at": "2026-10-10T00:00:00Z"}], "total_count": 1}
    if path.endswith(("/comments", "/timeline")):
        return []
    if path == f"/repos/{REPO}/issues/7":
        return {**ISSUE, "html_url": "/demo-issue"}
    if path == f"/repos/{REPO}":
        return {"description": "CONTROLLED DEMO FIXTURE — list parser", "language": "Python", "default_branch": "main"}
    if path == f"/repos/{REPO}/contents/":
        return [{"type": "file", "name": p} for p in FILES]
    raise github.BadInput("Offline fixture only supports the labelled demo repository.")


def fixture_file(owner, repo, path):
    if f"{owner}/{repo}" != REPO:
        raise github.BadInput("Only the controlled demo repository is available offline.")
    return FILES.get(path)


def fixture_ask(*, system, messages, **kwargs):
    text = "Controlled demo response. What loop bound includes the final item? Read parser.py before choosing a fix."
    if "CHECKPOINT_QUESTIONS" in system:
        text = json.dumps({"questions": ["Why does range(len(items) - 1) drop the last element in parser.py?", "Why does changing the bound to len(items) fix the singleton case?", "What does test_preserves_last_item cover, and what input contract remains untested?"]})
    elif "CHECKPOINT_EVALUATION" in system:
        payload = json.loads(messages[0]["content"].split("\n", 1)[1])
        answers = [a.lower() for a in payload["answers"]]
        passed = ["exclusive" in answers[0] and ("last" in answers[0] or "len" in answers[0]),
                  "singleton" in answers[1] and ("0" in answers[1] or "one" in answers[1]),
                  "last" in answers[2] and any(w in answers[2] for w in ["type", "iterable", "non-list", "invalid"])]
        text = json.dumps({"results": [{"passed": p, "feedback": "Correct for this fixture. Check the actual project contract too." if p else hint} for p, hint in zip(passed, ["range excludes its upper bound. Which index disappears?", "For one item, what index should the loop visit?", "The regression checks the last item; what types or invalid inputs remain outside coverage?"])]})
    elif system == agents.TOUR_SYSTEM:
        text = "## Controlled demo repository tour\nThis is fixture content, not live GitHub or OpenAI.\n\n`parser.py` copies items using an indexed loop. The supplied source excludes the final element. `test_parser.py` checks preservation, empty and singleton inputs.\n\nRun `python -m pytest -q` only in this controlled fixture. Fetch both files in Contribution review to inspect the actual source."
    elif system == agents.SCOUT_SYSTEM:
        text = json.dumps({"picks": [{"repo": REPO, "number": 7, "hours": 1, "fit": "Controlled fixture: Python beginner; a small loop-bound bug.", "learn": "Exclusive range bounds and regression testing", "risk": "Input types are not specified."}]})
    return {"text": text, "model": "offline-scripted-fixture", "cost_usd": 0, "cached": False, "cost_estimated": True}




DEMO_COOKIE = "oss_demo"
FIXTURE_ORG = {"name": "Controlled demo project", "github": "oss-mentor-demo", "repo": REPO, "languages": ["Python"], "domains": ["web", "devtools"], "beginner": 5,
               "setup": "light", "hackathon": 5, "legal": "none", "rated": "curated", "sources": ["controlled-fixture"],
               "notes": "CONTROLLED DEMO FIXTURE, pinned first: a tiny scripted repository, not a real GitHub project. Every other match is real but has no scripted issues."}


def install(app, only: bool = False) -> None:
    """Replace every outbound call with the fixture and give each visitor a private demo identity. Idempotent per process."""
    if getattr(app.state, "fixture_installed", False):
        return
    app.state.fixture_installed = True
    config.DEMO_INSTALLED = True
    github.get = fixture_get
    github.raw_file = fixture_file
    llm.ask = fixture_ask
    real_catalogue, real_rank_all = matcher.catalogue, matcher.rank_all
    if only:
        matcher.catalogue = lambda: [FIXTURE_ORG]
    else:
        matcher.catalogue = lambda: [FIXTURE_ORG] + [o for o in real_catalogue() if o["github"] != FIXTURE_ORG["github"]]

        def rank_all(profile, top=3, others=24):
            top_, rest = real_rank_all(profile, top, others)  # real, deterministic scoring; the fixture is simply pinned first
            mine = matcher.score_org(FIXTURE_ORG, profile)
            pool = [s for s in top_ + rest if s["github"] != FIXTURE_ORG["github"]]
            return ([mine] + pool[:top - 1], pool[top - 1:top - 1 + others])
        matcher.rank_all = rank_all

    from fastapi import Request
    from fastapi.responses import HTMLResponse

    async def demo_user(request: Request) -> auth.User:
        did = getattr(request.state, "demo_id", "") or "visitor"
        return auth.User(login=f"demo-{did}", token="", name="Demo visitor", gh_login="demo")  # private storage per browser; no GitHub identity

    app.dependency_overrides[auth.require_user] = demo_user

    @app.get("/demo-issue")
    def demo_issue():
        return HTMLResponse("<h1>Controlled demo fixture #7</h1><p>parse() drops the last item. Preserve every item, including singleton inputs.</p><p>No live issue availability is claimed.</p>")

    @app.middleware("http")
    async def demo_identity(request: Request, call_next):
        did = request.cookies.get(DEMO_COOKIE, "")
        fresh = not (did.isalnum() and 8 <= len(did) <= 32)
        did = uuid.uuid4().hex[:16] if fresh else did
        request.state.demo_id = did
        resp = await call_next(request)
        if fresh:
            resp.set_cookie(DEMO_COOKIE, did, httponly=True, samesite="lax", max_age=86400, secure=request.url.scheme == "https")
        return resp
