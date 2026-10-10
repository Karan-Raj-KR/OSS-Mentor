"""FastAPI server: JSON API for the agents plus the static UI. Run: uvicorn backend.app:app"""
import html
import json
import os
import sys
from contextlib import asynccontextmanager

import openai
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agents, auth, catalogue, config, db, github, jobs, journey, llm, matcher, profiles, replier, usage


@asynccontextmanager
async def lifespan(_app):
    if os.getenv("CATALOGUE_AUTO_REFRESH", "true").lower() == "true":
        catalogue.maybe_refresh_in_background()  # never blocks startup; no-op when the file is fresh
    yield


FULL_AUTO_AVAILABLE = not config.demo_active()  # worker: OpenAI Codex CLI on the student's machine (backend/runner.py); needs a real signed-in deployment

app = FastAPI(title="oss-mentor", lifespan=lifespan)
app.include_router(auth.router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith(("/api/", "/auth/")):
        resp.headers.setdefault("Cache-Control", "no-store")  # personal data and one-time tokens must never be cached
    return resp


@app.middleware("http")
async def deploy_guard(request: Request, call_next):
    """A public deployment refuses to serve the API until sign-in, a session secret and a database are configured.
    Without this, a fresh deploy would be an open proxy to the operator's OpenAI and GitHub credentials."""
    missing = config.deploy_missing()
    p = request.url.path
    if missing and p.startswith(("/api/", "/auth/")) and p not in PREVIEW_PATHS:
        return JSONResponse({"error": "Sign-in is not switched on for this deployment yet, so this part of OSS Mentor is paused.",
                             "missing": missing, "kind": "setup"}, status_code=503)
    return await call_next(request)


# Until a public deployment is configured it runs as a PREVIEW: only these endpoints work. None of them spends an API key, reads
# GitHub, or touches a student's stored data: interview questions, deterministic matching and the shipped catalogue. Visitors (and
# judges' agents, which cannot complete OAuth anyway) can explore the product instead of hitting a blank wall.
PREVIEW_PATHS = {"/api/health", "/api/me", "/api/questions", "/api/match", "/api/orgs", "/api/catalogue/status", "/api/catalogue/browse"}


@app.middleware("http")
async def same_origin_posts(request: Request, call_next):
    """CSRF guard for cookie auth: state-changing requests (POST, and the PUT/DELETE that save or erase a profile) must come
    from our own origin."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and auth.hosted():
        origin = request.headers.get("origin", "")
        if origin and origin.rstrip("/") != config.BASE_URL:
            return JSONResponse({"error": "Cross-origin request blocked."}, status_code=403)
    return await call_next(request)

QUESTIONS = [
    {"id": "mode", "q": "How do you want to work?", "multi": False, "options": [
        ["learn", "Learn: I write the code, you coach me"],
        ["semi", "Semi-auto: I vibe code with AI, you quiz me"],
        ["full", "Full-auto: Codex does it, I supervise"]]},
    {"id": "languages", "q": "Which languages can you read comfortably?", "multi": True, "options": [
        [x, x] for x in ["Python", "JavaScript", "TypeScript", "Java", "Go", "Rust", "C", "C++", "Ruby", "PHP", "Kotlin", "Swift", "Dart"]]},
    {"id": "skill", "q": "How strong are you in your best language?", "multi": False, "options": [
        ["beginner", "Beginner (courses only)"], ["intermediate", "Intermediate (built projects)"], ["advanced", "Advanced"]]},
    {"id": "interests", "q": "What are you most curious about?", "multi": True, "options": [
        ["web", "Web apps"], ["ai-ml-data", "AI / ML / data"], ["cloud-devops", "Cloud / DevOps"],
        ["security", "Security"], ["mobile", "Mobile"], ["devtools", "Developer tools"], ["education", "Docs / education"],
        ["social", "Social good / open science"], ["creative", "Games / creative coding"]]},
    {"id": "goal", "q": "What is your main goal?", "multi": False, "options": [
        ["hackathon", "A hackathon"], ["job", "Internship / job"], ["gsoc", "Google Summer of Code"], ["learn", "Learn and build a portfolio"]]},
    {"id": "time", "q": "How much time do you have?", "multi": False, "options": [
        ["lt5", "Under 5 hours a week"], ["5to10", "5 to 10 hours a week"], ["gt10", "10+ hours a week"], ["event", "A hackathon weekend"]]},
    {"id": "git_level", "q": "How comfortable are you with Git and GitHub?", "multi": False, "options": [
        ["never", "Never used it"], ["commit", "Commit and push only"], ["pr", "Branches and PRs"], ["rebase", "Fork and rebase"]]},
    {"id": "contrib_type", "q": "What first contribution do you want?", "multi": False, "options": [
        ["bug", "Bug fix"], ["docs", "Docs"], ["tests", "Tests"], ["feature", "Small feature"], ["any", "Pick for me"]]},
    {"id": "machine", "q": "What can your machine run?", "multi": False, "options": [
        ["low", "Low-spec laptop"], ["normal", "Normal laptop"], ["docker", "Docker and big builds"]]},
    {"id": "legal", "q": "OK with DCO/CLA sign-offs and disclosing AI help when asked?", "multi": False, "options": [
        ["both", "Yes to both"], ["dco", "DCO only"], ["unsure", "Not sure, explain it"]]},
]


class Profile(BaseModel):
    mode: str = "learn"
    languages: list[str] = []
    skill: str = "beginner"
    interests: list[str] = []
    goal: str = "learn"
    time: str = "lt5"
    git_level: str = "never"
    contrib_type: str = "any"
    machine: str = "normal"
    legal: str = "both"


class ScoutReq(BaseModel):
    org: str
    repo: str | None = None
    fallback_orgs: list[str] = []
    profile: Profile
    session: str = "default"


class TourReq(BaseModel):
    repo: str
    issue_title: str
    level: str = "beginner"
    session: str = "default"


class CoachReq(BaseModel):
    repo: str | None = None
    number: int | None = None
    mode: str
    context: str = ""
    history: list[dict]
    session: str = "default"


class DraftReq(BaseModel):
    pr_url: str
    session: str = "default"


class SendReq(BaseModel):
    pr_url: str
    items: list[dict]
    disclose: bool = True


@app.exception_handler(github.BadInput)
async def _bad_input(_, e):
    return JSONResponse({"error": str(e), "kind": "input"}, status_code=400)


@app.exception_handler(profiles.ProfileError)
async def _profile_err(_, e):
    return JSONResponse({"error": str(e), "kind": "profile"}, status_code=422)


@app.exception_handler(jobs.JobError)
async def _job(_, e):
    return JSONResponse({"error": str(e), "kind": "job"}, status_code=e.status)


@app.exception_handler(llm.MentorError)
async def _mentor(_, e):
    return JSONResponse({"error": str(e), "kind": "setup"}, status_code=503)


@app.exception_handler(llm.BudgetExceeded)
async def _budget(_, e):
    return JSONResponse({"error": str(e), "kind": "budget"}, status_code=402)


@app.exception_handler(openai.AuthenticationError)
async def _auth(_, e):
    return JSONResponse({"error": "OpenAI API key is missing or invalid. Set OPENAI_API_KEY in .env.", "kind": "auth"}, status_code=503)


@app.exception_handler(openai.APIError)
async def _api(_, e):
    return JSONResponse({"error": f"OpenAI request failed. Retry or check server configuration.", "kind": "openai"}, status_code=502)


@app.exception_handler(httpx.HTTPError)
async def _gh(_, e):
    return JSONResponse({"error": f"GitHub request failed: {e}", "kind": "github"}, status_code=502)


@app.exception_handler(PermissionError)
async def _perm(_, e):
    return JSONResponse({"error": str(e), "kind": "disabled"}, status_code=403)


@app.get("/api/health")
def health():
    miss = config.deploy_missing()
    return {"configured": not miss, "preview": bool(miss), "demo_fixture": config.DEMO_INSTALLED, "missing": miss, "owner_missing": config._missing() if config.PUBLIC_DEPLOY else [], "public_deploy": config.PUBLIC_DEPLOY, "storage": db.backend(),
            "openai_key": bool(os.getenv("OPENAI_API_KEY")), "github_token": bool(config.GITHUB_TOKEN) or auth.hosted(),
            "full_auto_available": FULL_AUTO_AVAILABLE, "full_auto_worker": "OpenAI Codex CLI (runs on the student's machine)", "cost_estimated": True, "posting_enabled": config.AUTO_POST_REPLIES, "hosted": auth.hosted(), "models": {"cheap": config.MODEL_CHEAP, "smart": config.MODEL_SMART},
            "session_budget_usd": config.SESSION_BUDGET_USD}


@app.get("/api/orgs")
def orgs():
    """Featured set for the 3D galaxy (curated + this year's GSoC organisations). The matcher scores the whole catalogue."""
    seen, out = set(), []
    this_year = __import__("datetime").datetime.now().year
    for o in matcher.catalogue():
        if not (o.get("rated") == "curated" or this_year in o.get("gsoc_years", [])):
            continue
        k = o["name"].lower()
        if k in seen:
            continue
        seen.add(k)
        out.append({k2: o.get(k2) for k2 in ("name", "github", "repo", "languages", "domains", "beginner", "notes")} |
                   {"sources": o.get("sources", []), "gsoc": bool(o.get("gsoc_years")), "lfx": bool(o.get("lfx"))})
        if len(out) >= 300:
            break
    return out


@app.get("/api/catalogue/status")
def catalogue_status():
    st = catalogue.status()
    st["size"] = len(matcher.catalogue())
    st["curated"] = len(matcher.curated())
    return st


@app.get("/api/catalogue/browse")
def catalogue_browse(q: str = "", source: str = "", language: str = "", domain: str = "", page: int = 1, size: int = 24):
    """Search and filter every organisation we know about. Facet counts reflect the current filters."""
    size = max(1, min(size, 60))
    ql = q.lower().strip()

    def match(o, skip=()):
        if ql and ql not in (o["name"] + " " + (o.get("notes") or "") + " " + (o.get("repo") or o["github"])).lower():
            return False
        if "source" not in skip and source and not (source in o.get("sources", []) or (source == "gsoc" and o.get("gsoc_years")) or (source == "lfx" and o.get("lfx")) or (source == "cncf" and o.get("cncf")) or (source == "apache" and o.get("apache"))):
            return False
        if "language" not in skip and language and language not in o["languages"]:
            return False
        if "domain" not in skip and domain and domain not in o["domains"]:
            return False
        return True

    allo = matcher.catalogue()
    hits = [o for o in allo if match(o)]
    hits.sort(key=lambda o: (o.get("rated") != "curated", -(o.get("gfi") or 0), -(o.get("stars") or 0)))
    start = (max(page, 1) - 1) * size

    def facet(vals, skip):
        c = {}
        for o in allo:
            if match(o, skip=(skip,)):
                for v in vals(o):
                    c[v] = c.get(v, 0) + 1
        return dict(sorted(c.items(), key=lambda kv: -kv[1])[:25])

    cols = ("name", "github", "repo", "languages", "domains", "notes", "sources", "gsoc_years", "lfx", "cncf", "apache", "stars", "gfi", "url", "ideas_url", "rated")
    return {"total": len(hits), "page": page, "size": size, "items": [{k: o.get(k) for k in cols} for o in hits[start:start + size]],
            "facets": {"language": facet(lambda o: o["languages"], "language"), "domain": facet(lambda o: o["domains"], "domain"),
                       "source": facet(lambda o: ([s for s in ("gsoc", "lfx", "cncf", "apache") if (o.get(s) or (s == "gsoc" and o.get("gsoc_years")))] + (["curated"] if o.get("rated") == "curated" else []) + (["beginner-list"] if "beginner-list" in o.get("sources", []) else [])), "source")}}


@app.post("/api/catalogue/refresh")
def catalogue_refresh(user: auth.User = Depends(auth.require_user)):
    admins = {a.strip().lower() for a in os.getenv("ADMIN_LOGINS", "").split(",") if a.strip()}
    if auth.hosted() and user.login.lower() not in admins:
        raise PermissionError("Only an operator can refresh the catalogue.")
    import threading
    if not catalogue.STATE["refreshing"]:
        threading.Thread(target=lambda: catalogue.refresh(True, log=lambda *_: None), daemon=True).start()
    return catalogue.status()


@app.get("/api/questions")
def questions():
    return QUESTIONS


@app.post("/api/match")
def match(p: Profile):
    """Deterministic scoring of the shipped catalogue: no API key, no GitHub read, nothing stored, so it needs no sign-in."""
    top, others = matcher.rank_all(p.model_dump())
    return {"ranking": top, "others": others, "catalogue_size": len(matcher.catalogue()), "cost_usd": 0.0}


@app.get("/api/discover")
def discover(languages: str = "", interests: str = "", user: auth.User = Depends(auth.require_user)):
    """Live GitHub search for repos with open good-first-issues that are not already in our catalogue."""
    known = {o["github"].lower() for o in matcher.catalogue()}
    # only values the quiz offers: these are spliced into a GitHub search query
    offered = {q["id"]: {o[0] for o in q["options"]} for q in QUESTIONS}
    langs = [x for x in languages.split(",") if x in offered["languages"]]
    ints = [x for x in interests.split(",") if x in offered["interests"]]
    return {"repos": github.discover(langs, ints, known), "cost_usd": 0.0}


def _check_targets(r: ScoutReq) -> None:
    """Validate every name the scout will splice into GitHub queries, before a stream starts (an error mid-stream cannot be a 400)."""
    github.check_owner(r.org)
    if r.repo:
        github.split_repo(r.repo)
    for f in r.fallback_orgs[:5]:
        github.check_owner(f)


@app.post("/api/scout")
def scout(r: ScoutReq, user: auth.User = Depends(auth.require_user)):
    _check_targets(r)
    return agents.scout(r.org, r.profile.model_dump(), user.login, r.repo, r.fallback_orgs)


@app.post("/api/scout/stream")
def scout_stream(r: ScoutReq, user: auth.User = Depends(auth.require_user)):
    """Newline-delimited JSON: progress steps as they happen, then one result (or one error) event."""
    _check_targets(r)

    def gen():
        try:
            for ev in agents.scout_stream(r.org, r.profile.model_dump(), user.login, r.repo, r.fallback_orgs):
                yield json.dumps(ev) + "\n"
        except llm.MentorError as e:
            yield json.dumps({"type": "error", "kind": "setup", "error": str(e)}) + "\n"
        except llm.BudgetExceeded as e:
            yield json.dumps({"type": "error", "kind": "budget", "error": str(e)}) + "\n"
        except openai.AuthenticationError:
            yield json.dumps({"type": "error", "kind": "auth", "error": "auth"}) + "\n"
        except httpx.HTTPError as e:
            yield json.dumps({"type": "error", "kind": "github", "error": f"GitHub request failed: {e}"}) + "\n"
        except openai.APIError as e:
            yield json.dumps({"type": "error", "kind": "openai", "error": "OpenAI request failed. Retry or check server configuration."}) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


class VerifyReq(BaseModel):
    repo: str
    number: int


@app.post("/api/verify")
def verify(r: VerifyReq, user: auth.User = Depends(auth.require_user)):
    """Fresh, uncached check that an issue is still open, unassigned, unclaimed and has no open PR."""
    owner, repo = github.split_repo(r.repo)
    if r.number < 1:
        raise github.BadInput("Issue numbers start at 1.")
    return github.verify_issue(owner, repo, r.number)


class ReviewReq(VerifyReq):
    diff: str = ""
    evidence: str = ""
    pr_description: str = ""
    disclosure: str = ""


class SourcesReq(VerifyReq):
    paths: list[str]


class CheckpointReq(VerifyReq):
    answers: list[str] | None = None


@app.exception_handler(journey.JourneyError)
async def _journey_error(_, e):
    return JSONResponse({"error": str(e), "kind": "review"}, status_code=400)


@app.post("/api/journey/begin")
def journey_begin(r: VerifyReq, user: auth.User = Depends(auth.require_user)):
    return journey.begin(user.login, r.repo, r.number)


@app.get("/api/journey")
def journey_get(repo: str, number: int, user: auth.User = Depends(auth.require_user)):
    return journey.view(journey.get(user.login, repo, number))


@app.post("/api/journey/save")
def journey_save(r: ReviewReq, user: auth.User = Depends(auth.require_user)):
    return journey.save(user.login, r.repo, r.number, r.diff, r.evidence, r.pr_description, r.disclosure)


@app.post("/api/journey/sources")
def journey_sources(r: SourcesReq, user: auth.User = Depends(auth.require_user)):
    return journey.sources(user.login, r.repo, r.number, r.paths)


@app.post("/api/journey/checkpoint")
def journey_checkpoint(r: CheckpointReq, user: auth.User = Depends(auth.require_user)):
    try:
        return journey.checkpoint(user.login, r.repo, r.number, r.answers)
    except ValueError as e:
        raise journey.JourneyError(str(e))


@app.post("/api/tour")
def tour(r: TourReq, user: auth.User = Depends(auth.require_user)):
    return agents.tour(r.repo, r.issue_title, r.level, user.login)


@app.post("/api/coach")
def coach(r: CoachReq, user: auth.User = Depends(auth.require_user)):
    if r.mode not in ("learn", "semi"):
        raise HTTPException(400, "Coach chat is for learn and semi modes.")
    try:
        context = r.context
        if r.repo and r.number:
            j = journey.get(user.login, r.repo, r.number)
            if not j:
                raise HTTPException(400, "Start the guided journey first.")
            context = journey.context(j)
        return agents.coach(r.mode, context, r.history, user.login)
    except github.BadInput:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/replies/draft")
def replies_draft(r: DraftReq, user: auth.User = Depends(auth.require_user)):
    try:
        return replier.draft(r.pr_url, user.login)
    except ValueError:
        raise HTTPException(400, "That does not look like a pull request URL.")


@app.post("/api/replies/send")
def replies_send(r: SendReq, user: auth.User = Depends(auth.require_user)):
    try:
        return replier.send(r.pr_url, r.items, r.disclose, user.login)
    except github.BadInput:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e) or "That does not look like a pull request URL.")


@app.get("/api/usage")
def usage_endpoint(user: auth.User = Depends(auth.require_user)):
    return {"session": llm.session_usage(user.login),
            "total": {"cost_usd": usage.today_all()} if usage.persistent() else llm.ledger.total.as_dict(),
            "session_budget_usd": config.SESSION_BUDGET_USD}


# ------------------------------------------------------------------ profile
class ProfileReq(BaseModel):
    answers: dict | None = None
    settings: dict | None = None


def _usage_for(user: auth.User) -> dict:
    return {"session": llm.session_usage(user.login), "session_budget_usd": config.SESSION_BUDGET_USD}


@app.get("/api/profile")
def profile_get(user: auth.User = Depends(auth.require_user)):
    p = profiles.get(user.login)
    return {"identity": auth.public(user), "answers": p["answers"], "settings": p["settings"], "updated": p["updated"],
            "activity": profiles.activity(user.login), "usage": _usage_for(user),
            "auth": {"mode": "github" if auth.hosted() else "local", "session_days": auth.SESSION_DAYS},
            "stored": ["Your quiz answers and settings", "Your guided journey, attached diff, evidence and checkpoint answers", "Your Full-auto jobs and their progress events", "Mentor usage totals (kept in memory)",
                       "Your GitHub login, name and avatar for this session only (your GitHub token stays on the server and is never sent to your browser)"]}


@app.put("/api/profile")
def profile_put(r: ProfileReq, user: auth.User = Depends(auth.require_user)):
    return profiles.save(user.login, r.answers, r.settings, QUESTIONS)


@app.get("/api/profile/export")
def profile_export(user: auth.User = Depends(auth.require_user)):
    data = profiles.export(user.login, auth.public(user), _usage_for(user))
    return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="oss-mentor-my-data.json"'})


@app.delete("/api/profile")
def profile_delete(request: Request, user: auth.User = Depends(auth.require_user)):
    out = profiles.delete_everything(user.login)
    llm.ledger.sessions.pop(user.login, None)
    usage.delete(user.login)
    resp = JSONResponse({"ok": True, **out})
    if auth.hosted():  # deleting your data also signs you out, everywhere
        if config.SECRET_KEY:
            auth.revoke(user.login)
        auth.SESSIONS.pop(request.cookies.get(auth.COOKIE, ""), None)
        resp.delete_cookie(auth.COOKIE)
    return resp


# ------------------------------------------------------------------ Full-auto jobs (the worker runs on the student's machine)
class JobReq(BaseModel):
    repo: str
    number: int
    title: str = ""
    consent: bool = False
    signoff: bool = False


class ApproveReq(BaseModel):
    pr_title: str
    pr_body: str
    cla_confirmed: bool = False


@app.post("/api/jobs")
def jobs_create(r: JobReq, request: Request, user: auth.User = Depends(auth.require_user)):
    if not FULL_AUTO_AVAILABLE:
        raise HTTPException(503, "Full-auto runs the OpenAI Codex CLI on YOUR machine against a real repository, so it is off in the controlled demo. Use Learn or Semi-auto here; run OSS Mentor locally or on a signed-in deployment for Full-auto.")
    j = jobs.create(user.login, r.repo, r.number, r.title, r.consent, r.signoff)
    if auth.hosted():  # the student's machine has no copy of this project: the command fetches the runner itself
        j["command"] = jobs.runner_command(j["id"], j["token"], config.BASE_URL, repo_url=config.RUNNER_REPO_URL)
    else:              # same machine as the server: reuse this checkout and this Python
        j["command"] = jobs.runner_command(j["id"], j["token"], str(request.base_url).rstrip("/"), local_root=str(config.ROOT), python=sys.executable)
    return j


@app.get("/api/jobs")
def jobs_list(user: auth.User = Depends(auth.require_user)):
    return jobs.list_for(user.login)


@app.get("/api/jobs/{job_id}")
def jobs_get(job_id: str, since: int = 0, user: auth.User = Depends(auth.require_user)):
    return jobs.get(user.login, job_id, since)


@app.post("/api/jobs/{job_id}/approve")
def jobs_approve(job_id: str, r: ApproveReq, user: auth.User = Depends(auth.require_user)):
    return jobs.approve(user.login, job_id, r.pr_title, r.pr_body, r.cla_confirmed)


@app.post("/api/jobs/{job_id}/cancel")
def jobs_cancel(job_id: str, user: auth.User = Depends(auth.require_user)):
    return jobs.cancel(user.login, job_id)


class RunnerEvent(BaseModel):
    stage: str = ""
    text: str


class RunnerDone(BaseModel):
    pr_url: str | None = None
    error: str | None = None


@app.get("/api/runner/{job_id}/spec")
def runner_spec(job_id: str, x_job_token: str = Header("")):
    return jobs.spec(job_id, x_job_token)


@app.post("/api/runner/{job_id}/event")
def runner_event(job_id: str, e: RunnerEvent, x_job_token: str = Header("")):
    jobs.runner_event(job_id, x_job_token, e.stage, e.text)
    return {"ok": True}


@app.post("/api/runner/{job_id}/result")
def runner_result(job_id: str, payload: dict, x_job_token: str = Header("")):
    return jobs.runner_result(job_id, x_job_token, payload)


@app.get("/api/runner/{job_id}/status")
def runner_status(job_id: str, x_job_token: str = Header("")):
    return jobs.runner_status(job_id, x_job_token)


@app.post("/api/runner/{job_id}/done")
def runner_done(job_id: str, d: RunnerDone, x_job_token: str = Header("")):
    return jobs.runner_done(job_id, x_job_token, d.pr_url, d.error)


@app.get("/")
def index():
    return FileResponse(config.ROOT / "frontend" / "v2" / "index.html")


@app.get("/classic")
def classic():
    return FileResponse(config.ROOT / "frontend" / "index.html")


# ------------------------------------------------------------------ agent- and judge-readable pages (no sign-in, no JavaScript needed)
TEXT = "text/plain; charset=utf-8"  # text/markdown makes some browsers download instead of display


@app.get("/llms.txt", include_in_schema=False)
def llms_txt():
    return FileResponse(config.ROOT / "llms.txt", media_type=TEXT)


@app.get("/AGENTS.md", include_in_schema=False)
@app.get("/agents.md", include_in_schema=False)
def agents_md():
    return FileResponse(config.ROOT / "AGENTS.md", media_type=TEXT)


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return PlainTextResponse("User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /auth/\n\n# Start here: /llms.txt and /about\nSitemap: /sitemap.xml\n")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request):
    base = str(request.base_url).rstrip("/")
    urls = "".join(f"<url><loc>{base}{p}</loc></url>" for p in ("/", "/about", "/llms.txt", "/AGENTS.md", "/api/health"))
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>', media_type="application/xml")


REPO_BLOB = "https://github.com/Karan-Raj-KR/OSS-Mentor/blob/main/"  # repository-relative links in AGENTS.md point here on the site


@app.get("/about", include_in_schema=False)
def about():
    """AGENTS.md as a page. The full text is in the HTML itself, so crawlers that do not run JavaScript read everything;
    browsers that do run it get the same markdown rendered (sanitised)."""
    src = html.escape((config.ROOT / "AGENTS.md").read_text())
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>About OSS Mentor: how it works</title>
<meta name="description" content="How OSS Mentor works: problem, solution, product, OpenAI usage, architecture, API, how to run it, and what is verified.">
<link rel="alternate" type="text/plain" href="/AGENTS.md" title="AGENTS.md (raw)">
<style>
:root{{--bg:#05070b;--fg:#e8edf5;--mute:#a1a1aa;--line:rgba(255,255,255,.12);--acc:#7dd3fc}}
@media (prefers-color-scheme:light){{:root{{--bg:#fbfaf7;--fg:#18181b;--mute:#52525b;--line:rgba(0,0,0,.12);--acc:#0369a1}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.65 Manrope,system-ui,sans-serif}}
main{{max-width:900px;margin:0 auto;padding:32px 16px 80px}} a{{color:var(--acc)}}
nav{{display:flex;gap:16px;flex-wrap:wrap;font-weight:700;margin-bottom:24px}}
pre{{white-space:pre-wrap;word-break:break-word;font:14px/1.6 ui-monospace,monospace}}
#doc table{{border-collapse:collapse;display:block;overflow-x:auto}} #doc td,#doc th{{border:1px solid var(--line);padding:6px 10px;vertical-align:top}}
#doc code{{background:rgba(127,127,127,.15);padding:.1em .35em;border-radius:5px;font-size:.9em}} #doc pre code{{background:none;padding:0}}
#doc pre{{border:1px solid var(--line);border-radius:12px;padding:14px;overflow-x:auto}} #doc h1{{font-size:2rem;line-height:1.2}}
</style></head><body><main>
<nav><a href="/">← Open the app</a><a href="/AGENTS.md">Raw AGENTS.md</a><a href="/llms.txt">llms.txt</a><a href="/api/health">/api/health</a><a href="https://github.com/Karan-Raj-KR/OSS-Mentor">GitHub</a></nav>
<article id="doc"><pre id="src">{src}</pre></article>
</main>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.1.6/purify.min.js"></script>
<script>try{{const s=document.getElementById('src').textContent;const d=document.getElementById('doc');d.innerHTML=DOMPurify.sanitize(marked.parse(s));
for(const h of d.querySelectorAll('h1,h2,h3,h4'))h.id=h.textContent.trim().toLowerCase().replace(/[^\\w\\- ]/g,'').replace(/ /g,'-');
for(const a of d.querySelectorAll('a[href]')){{const h=a.getAttribute('href');if(!/^(https?:|\/|#|mailto:)/.test(h))a.href='{REPO_BLOB}'+h;}}}}catch(e){{}}</script>
</body></html>""")


app.mount("/static", StaticFiles(directory=config.ROOT / "frontend"), name="static")


if config.demo_active():  # last, so every route exists: swaps outbound calls for the labelled fixture (see backend/fixture.py)
    from . import fixture
    fixture.install(app, only=os.getenv("DEMO_CATALOGUE_ONLY", "").lower() == "true")
