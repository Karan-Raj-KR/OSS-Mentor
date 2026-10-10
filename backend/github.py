"""Thin GitHub REST client. Reads are cheap and cached briefly; writes require an explicit token."""
import hashlib
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import auth, config

API = "https://api.github.com"
_cache: dict[str, tuple[float, object]] = {}
TTL = 300
MAX_CACHE = 2000  # entries; the server is long-running, so the cache must not grow without bound
NAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})")


class BadInput(ValueError):
    """A request named something that cannot be a GitHub owner, repository or issue. Shown to the student as a 400."""


def split_repo(full: str) -> tuple[str, str]:
    """'owner/name' -> (owner, name). Anything else is rejected before it is spliced into an API path (no '..', no extra slashes)."""
    parts = (full or "").strip().split("/")
    if len(parts) != 2 or not all(NAME_RE.fullmatch(x) and x not in (".", "..") for x in parts):
        raise BadInput(f"Not a repository: {full!r}. Use the form owner/name.")
    return parts[0], parts[1]


def check_owner(owner: str) -> str:
    if not NAME_RE.fullmatch(owner or ""):
        raise BadInput(f"Not a GitHub user or organisation: {owner!r}.")
    return owner


def _headers(token: str | None = None) -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    t = token or auth.current_token.get() or config.GITHUB_TOKEN
    if t:
        h["Authorization"] = f"Bearer {t}"
    return h


def _who(token: str | None = None) -> str:
    return hashlib.sha256((token or auth.current_token.get() or "").encode()).hexdigest()[:12]


def _key(path: str, params: dict | None, token: str | None = None) -> str:
    return f"{_who(token)}:{path}?{sorted((params or {}).items())}"  # never share cached reads between users


def _remember(key: str, data) -> None:
    now = time.time()
    if len(_cache) >= MAX_CACHE:
        for k in [k for k, (t, _) in _cache.items() if now - t >= TTL]:
            _cache.pop(k, None)
        while len(_cache) >= MAX_CACHE:  # still full of fresh entries: drop the oldest (dicts keep insertion order)
            _cache.pop(next(iter(_cache)))
    _cache[key] = (now, data)


def forget(path: str) -> None:
    """Drop this user's cached reads of one path (any params), so the next read is live. Other users' entries are untouched."""
    prefix = f"{_who()}:{path}?"
    for k in [k for k in _cache if k.startswith(prefix)]:
        _cache.pop(k, None)


def get(path: str, params: dict | None = None, token: str | None = None):
    key = _key(path, params, token)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    r = httpx.get(f"{API}{path}", params=params, headers=_headers(token), timeout=20)
    r.raise_for_status()
    data = r.json()
    _remember(key, data)
    return data


def post(path: str, body: dict, token: str | None = None):
    r = httpx.post(f"{API}{path}", json=body, headers=_headers(token), timeout=20)
    r.raise_for_status()
    return r.json()


def raw_file(owner: str, repo: str, path: str) -> str | None:
    try:
        data = get(f"/repos/{owner}/{repo}/contents/{path}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None  # file genuinely absent
        raise  # rate limit / auth / outage must NOT look like "no policy found"
    if isinstance(data, dict) and data.get("encoding") == "base64":
        import base64
        return base64.b64decode(data["content"]).decode("utf-8", "replace")
    return None


POLICY_FILES = [".github/AI_POLICY.md", "AI_POLICY.md", "AGENTS.md", "CONTRIBUTING.md", ".github/CONTRIBUTING.md"]
# Whole words only: the old substring test matched "ai " inside "said ", "email " or "detail ", so ordinary lines such as
# "Emails will be closed..." were flagged, and "AI." or "(AI)" at the end of a sentence was missed.
AI_WORDS = re.compile(r"\b(ai|a\.i\.|llms?|copilot|chatgpt|gpt(-?\d+)?|genai|generative|language models?|machine[- ]generated)\b", re.I)
BAN_WORDS = re.compile(r"\b(no|ban(ned|s)?|prohibit(ed|s)?|forbid(den|s)?|disallow(ed|s)?|reject(ed|s)?|not (be )?(accept(ed)?|allowed|permitted|welcome)|"
                       r"(do|does|will|we) not accept|do not (use|submit|open)|don'?t (use|submit|open)|must not|may not|will be closed)\b", re.I)
# A line naming AI-generated work is worth a human read even without a ban word (it is usually a disclosure rule).
AI_GENERATED = re.compile(r"\b(ai|llm|machine)[- ](generated|written|authored|assisted)\b|\bgenerated (by|with|using) (an? )?(ai|llms?|chatgpt|copilot)\b", re.I)


def policy_scan(owner: str, repo: str) -> dict:
    """Cheap keyword scan of policy files (each fetched once). A hit means 'a human must read this', not a verdict."""
    texts = {f: raw_file(owner, repo, f) for f in POLICY_FILES}
    found = [f for f, t in texts.items() if t]
    flags, legal = [], set()
    for f in found:
        txt = texts[f]
        low = txt.lower()
        for line in txt.splitlines():
            if AI_GENERATED.search(line) or (AI_WORDS.search(line) and BAN_WORDS.search(line)):
                flags.append({"file": f, "line": line.strip()[:240], "url": f"https://github.com/{owner}/{repo}/blob/HEAD/{f}", "restriction": bool(AI_WORDS.search(line) and BAN_WORDS.search(line))})
        if "signed-off-by" in low or re.search(r"\bdco\b", low):
            legal.add("DCO")
        if re.search(r"\bcla\b", low) or "contributor license" in low or "easycla" in low:
            legal.add("CLA")
    return {"files": found, "ai_flags": flags[:5], "legal": sorted(legal),
            "status": "restriction_found" if any(f["restriction"] for f in flags) else "review_required" if flags else "no_restriction_found" if found else "unknown",
            "sources": [{"file": f, "url": f"https://github.com/{owner}/{repo}/blob/HEAD/{f}"} for f in found]}


LABEL_TIERS = [
    ["good first issue", "good-first-issue", "first-timers-only", "beginner", "starter", "easy"],
    ["help wanted", "help-wanted", "contributions welcome"],
    None,  # last resort: any open, unassigned, unlinked issue; the ranking step judges suitability
]


def search_free_issues(scope: str, tier: int, limit: int = 40) -> tuple[list[dict], int]:
    """ONE search call: open, unassigned, no linked PR, not archived, optionally label-filtered.
    scope is 'org:x', 'user:x' or 'repo:x/y'. Replaces the old per-issue PR lookups that exhausted the search rate limit."""
    q = f"{scope} is:issue is:open no:assignee -linked:pr archived:false"
    labels = LABEL_TIERS[tier]
    if labels:
        q += " label:" + ",".join(f'"{l}"' for l in labels)
    else:
        q += " comments:<4"
    data = get("/search/issues", {"q": q, "sort": "updated", "order": "desc", "per_page": limit})
    out = []
    for i in data.get("items", []):
        out.append({
            "number": i["number"], "title": i["title"], "url": i["html_url"],
            "repo": i["repository_url"].split("/repos/")[1],
            "labels": [l["name"] for l in i.get("labels", [])], "comments": i.get("comments", 0),
            "updated": i["updated_at"], "body": (i.get("body") or "")[:1500],
        })
    return out, data.get("total_count", len(out))


# Word boundaries matter: without them "claim" matched inside "disclaimer"/"reclaim" and "ill do" inside "will do",
# which hid perfectly free issues as "someone is working on it".
CLAIM_RE = re.compile(r"\b(i(['’]ll| will) (take|work|do|fix|pick)|i can (take|work|fix|do)|working on (this|it)|can i (work|take|pick|have)|"
                      r"assign (this |it )?(to )?me|i(['’]d| would) (like|love) to (work|take|fix)|i want to (work|fix|take)|claim(ing)?( this| it)?)\b", re.I)


def recent_claim(owner: str, repo: str, number: int, days: int = 14) -> bool:
    """True if someone (not just the author) recently said they are working on it."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        cs = get(f"/repos/{owner}/{repo}/issues/{number}/comments", {"since": since, "per_page": 30})
    except httpx.HTTPError:
        raise  # verification must report unavailable comments, never infer no claim
    return any(CLAIM_RE.search(c.get("body") or "") for c in cs)


def verify_issue(owner: str, repo: str, number: int, light: bool = False) -> dict:
    """Check an issue against what is true right now: open, unassigned, no open PR referencing it, nobody claiming it.
    light=True (used while scouting) trusts the search for open/unassigned and skips the uncached issue fetch."""
    base = f"/repos/{owner}/{repo}/issues/{number}"
    if light:
        i = {"state": "open", "assignees": []}
    else:
        for p in (base, base + "/timeline", base + "/comments"):
            forget(p)  # a live answer for this issue only; other cached reads (and other students' caches) stay warm
        i = get(base)
    linked, unchecked = [], []
    try:
        timeline = get(base + "/timeline", {"per_page": 100})
        if len(timeline) == 100:
            unchecked.append("timeline beyond the first 100 events")
        for ev in timeline:
            src = (ev.get("source") or {}).get("issue") or {}
            if ev.get("event") == "cross-referenced" and "pull_request" in src and src.get("state") == "open":
                linked.append(src["number"])
    except httpx.HTTPStatusError:
        unchecked.append("linked pull requests")  # say so: a failed read must not look like "no PR"
    claiming_comments = []
    try:
        claimed = recent_claim(owner, repo, number)
        if claimed:
            since = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
            comments = get(base + "/comments", {"since": since, "per_page": 30})
            claiming_comments = [{"text": (c.get("body") or "")[:240], "url": c.get("html_url"), "created_at": c.get("created_at")}
                                 for c in comments if CLAIM_RE.search(c.get("body") or "")][:5]
    except httpx.HTTPError:
        claimed = None
        unchecked.append("recent comments")
    if i.get("comments", 0) > 30:
        unchecked.append("comments beyond the first 30")
    ok = i.get("state") == "open" and not i.get("assignees") and not linked and not claimed
    reason = ("closed" if i.get("state") != "open" else "assigned" if i.get("assignees") else
              f"an open pull request already references it (#{linked[0]})" if linked else "someone said they are working on it" if claimed else "")
    return {"state": i.get("state"), "assignees": [a.get("login") for a in i.get("assignees", [])],
            "claimed": claimed, "claiming_comments": claiming_comments, "availability": "unknown" if unchecked else "unavailable" if not ok else "no_conflict_found",
            "ok": ok, "reason": reason, "checked_at": datetime.now(timezone.utc).isoformat(), "linked_prs": linked, "unchecked": unchecked}


def repo_overview(owner: str, repo: str) -> dict:
    info = get(f"/repos/{owner}/{repo}")
    tree = get(f"/repos/{owner}/{repo}/contents/")
    names = [("dir/" if t["type"] == "dir" else "") + t["name"] for t in tree] if isinstance(tree, list) else []
    return {
        "description": info.get("description"), "language": info.get("language"),
        "stars": info.get("stargazers_count"), "default_branch": info.get("default_branch"),
        "top_level": names[:80],
        "readme": (raw_file(owner, repo, "README.md") or "")[:4000],
        "contributing": (raw_file(owner, repo, "CONTRIBUTING.md") or raw_file(owner, repo, ".github/CONTRIBUTING.md") or "")[:3000],
    }


TOPICS = {
    "web": ["web", "frontend"], "ai-ml-data": ["machine-learning", "data-science"],
    "cloud-devops": ["devops", "kubernetes"], "security": ["security"], "mobile": ["android", "mobile"],
    "devtools": ["developer-tools", "cli"], "education": ["education"],
    "social": ["social-good", "hacktoberfest"], "creative": ["game-engine", "creative-coding"],
}


def discover(languages: list[str], interests: list[str], known: set[str], limit: int = 12) -> list[dict]:
    """Live search for active repos with open good-first-issues, grouped by owner.
    GitHub's search qualifiers do the filtering (no model tokens). Results are unrated and unverified."""
    from datetime import date, timedelta
    since = (date.today() - timedelta(days=90)).isoformat()
    langs = (languages or [None])[:2]
    topics = [t for i in (interests or [])[:2] for t in TOPICS.get(i, [])[:1]] or [None]
    seen: dict[str, dict] = {}
    for lang in langs:
        for topic in topics[:2]:
            q = f"good-first-issues:>3 stars:>200 pushed:>{since} archived:false fork:false"
            if lang:
                q += f" language:{lang}"
            if topic:
                q += f" topic:{topic}"
            data = get("/search/repositories", {"q": q, "sort": "help-wanted-issues", "order": "desc", "per_page": 15})
            for r in data.get("items", []):
                owner = r["owner"]["login"]
                if owner.lower() in known or r.get("full_name") in seen:
                    continue
                seen[r["full_name"]] = {
                    "repo": r["full_name"], "owner": owner, "description": (r.get("description") or "")[:200],
                    "language": r.get("language"), "stars": r.get("stargazers_count", 0),
                    "pushed": (r.get("pushed_at") or "")[:10], "topics": (r.get("topics") or [])[:5],
                    "license": (r.get("license") or {}).get("spdx_id"), "url": r["html_url"],
                    "open_issues": r.get("open_issues_count", 0), "matched": {"language": lang, "topic": topic},
                }
    # one repo per owner, most-starred first, so the list shows variety rather than one org's whole portfolio
    best: dict[str, dict] = {}
    for r in sorted(seen.values(), key=lambda x: x["stars"], reverse=True):
        best.setdefault(r["owner"].lower(), r)
    return list(best.values())[:limit]
