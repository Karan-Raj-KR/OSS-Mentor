"""Reply drafting for reviewer/maintainer comments on a student's PR.

Pipeline (2 model calls per PR check, regardless of how many comments):
  1. plain code: fetch comments, drop the PR author's own, bots, and ones already handled
  2. one CHEAP call classifies every new comment
  3. one SMART call drafts replies for the ones that need an answer
Nothing is posted unless the operator set AUTO_POST_REPLIES=true AND the caller approves the specific ids.
"""
import json
import re

from . import auth, config, db, github, llm

PR_URL = re.compile(r"(?:https?://)?(?:www\.)?github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)(?:[/?#].*)?", re.I)
MAX_REPLY = 6000


CLASSIFY_SYSTEM = """Classify each pull-request comment. Reply with JSON only: {"items":[{"id":N,"kind":"actionable|question|nit|praise|other","needs_code":true|false}]}
actionable = asks for a change; question = asks something; nit = tiny style remark; praise = thanks/approval; other = anything else."""

DRAFT_SYSTEM = """You write short, polite replies from a student contributor to maintainers' comments on their pull request.
Rules: be concise and humble; thank them once; never promise what is not done; if code must change, say what you will change and that you will push an update;
if you are not sure, ask a clarifying question; never claim tests passed unless told so. No emojis.
Reply with JSON only: {"replies":[{"id":N,"reply":"text"}]}"""


def _handled(uid: str, pr: str) -> set:
    with db.connect() as con:
        r = con.execute("SELECT handled FROM reply_state WHERE uid=? AND pr=?", (uid, pr)).fetchone()
    return set(json.loads(r[0])) if r else set()


def _mark(uid: str, pr: str, ids: list) -> None:
    both = sorted(_handled(uid, pr) | set(ids))
    with db.connect() as con:
        con.execute("INSERT INTO reply_state(uid, pr, handled) VALUES (?,?,?) ON CONFLICT(uid, pr) DO UPDATE SET handled=excluded.handled",
                    (uid, pr, json.dumps(both)))


def parse_pr(url: str) -> tuple[str, str, int]:
    """A github.com pull request link -> (owner, repo, number). Tolerates /files, ?query and #comment anchors; rejects other hosts
    and anything that is not a plain owner/repo (those values are spliced into API paths that post under the student's token)."""
    m = PR_URL.fullmatch((url or "").strip())
    if not m:
        raise github.BadInput("That does not look like a GitHub pull request URL (https://github.com/owner/repo/pull/123).")
    owner, repo = github.split_repo(f"{m.group(1)}/{m.group(2)}")
    return owner, repo, int(m.group(3))


def fetch_new_comments(owner: str, repo: str, number: int, me: str) -> tuple[list[dict], str]:
    pr = github.get(f"/repos/{owner}/{repo}/pulls/{number}")
    author = pr["user"]["login"]
    if me != "local" and author.lower() != me.lower():
        raise PermissionError(f"This PR belongs to @{author}. You can only draft replies for your own pull requests.")
    seen = _handled(me, f"{owner}/{repo}#{number}")
    comments = []
    for c in github.get(f"/repos/{owner}/{repo}/pulls/{number}/comments", {"per_page": 100}):
        comments.append({"id": c["id"], "kind_src": "review", "user": c["user"]["login"], "bot": c["user"]["type"] == "Bot",
                         "body": (c.get("body") or "")[:1200], "path": c.get("path"), "diff": (c.get("diff_hunk") or "")[-400:]})
    for c in github.get(f"/repos/{owner}/{repo}/issues/{number}/comments", {"per_page": 100}):
        comments.append({"id": c["id"], "kind_src": "issue", "user": c["user"]["login"], "bot": c["user"]["type"] == "Bot",
                         "body": (c.get("body") or "")[:1200], "path": None, "diff": ""})
    fresh = [c for c in comments if c["user"] != author and not c["bot"] and c["id"] not in seen]
    return fresh, pr["title"]


def _ai_flags(owner: str, repo: str) -> list[dict]:
    return github.policy_scan(owner, repo)["ai_flags"]


def _by_id(text: str, key: str) -> dict:
    """{id: item} from the model's JSON reply. Ids may come back as strings; an unreadable reply yields {} (callers degrade)."""
    try:
        data = llm.parse_json(text)
    except ValueError:
        return {}
    out = {}
    for it in (data.get(key) or []) if isinstance(data, dict) else []:
        try:
            out[int(it["id"])] = it
        except (KeyError, TypeError, ValueError):
            continue
    return out


def draft(pr_url: str, session_id: str) -> dict:
    owner, repo, number = parse_pr(pr_url)
    fresh, title = fetch_new_comments(owner, repo, number, session_id)
    if not fresh:
        return {"drafts": [], "note": "No new comments to answer.", "cost_usd": 0.0}
    cost = 0.0
    c = llm.ask(tier="cheap", system=CLASSIFY_SYSTEM, max_tokens=500, session_id=session_id,
                messages=[{"role": "user", "content": json.dumps([{"id": x["id"], "text": x["body"][:400]} for x in fresh])}])
    cost += c["cost_usd"]
    kinds = _by_id(c["text"], "items")  # unreadable classification: every comment is treated as needing a reply
    need = [x for x in fresh if kinds.get(x["id"], {}).get("kind") not in ("praise",)]
    replies = {}
    if need:
        payload = {"pr_title": title, "comments": [{"id": x["id"], "from": x["user"], "file": x["path"],
                                                       "code": x["diff"], "text": x["body"]} for x in need]}
        d = llm.ask(tier="smart", system=DRAFT_SYSTEM, max_tokens=900, session_id=session_id, effort="low",
                    messages=[{"role": "user", "content": json.dumps(payload)}])
        cost += d["cost_usd"]
        replies = {i: str(r.get("reply") or "") for i, r in _by_id(d["text"], "replies").items()}
    out = []
    for x in fresh:
        k = kinds.get(x["id"], {})
        out.append({"id": x["id"], "source": x["kind_src"], "user": x["user"], "comment": x["body"], "path": x["path"],
                    "kind": k.get("kind", "other"), "needs_code": bool(k.get("needs_code")),
                    "reply": replies.get(x["id"], "")})
    flags = _ai_flags(owner, repo)
    return {"drafts": out, "pr": {"owner": owner, "repo": repo, "number": number}, "ai_flags": flags,
            "can_post": config.AUTO_POST_REPLIES and bool(auth.current_token.get() or config.GITHUB_TOKEN) and not flags, "cost_usd": cost}


def send(pr_url: str, items: list[dict], disclose: bool = True, me: str = "local") -> dict:
    """items: [{id, source, reply}] with the (possibly edited) text the human approved."""
    if not config.AUTO_POST_REPLIES:
        raise PermissionError("Posting is disabled. Set AUTO_POST_REPLIES=true on the server to enable it.")
    if not (auth.current_token.get() or config.GITHUB_TOKEN):
        raise PermissionError("No GitHub token. Connect your GitHub account.")
    owner, repo, number = parse_pr(pr_url)
    if me != "local":
        author = github.get(f"/repos/{owner}/{repo}/pulls/{number}")["user"]["login"]
        if author.lower() != me.lower():
            raise PermissionError(f"This PR belongs to @{author}. You can only reply on your own pull requests.")
    if _ai_flags(owner, repo):
        raise PermissionError("This project's policy restricts AI-generated messages. Copy the draft, rewrite it in your own words, and post it yourself.")
    key = f"{owner}/{repo}#{number}"
    clean = []
    for it in items:  # validate everything before posting anything
        if not isinstance(it, dict) or it.get("source") not in ("review", "issue") or not isinstance(it.get("reply"), str):
            raise ValueError("Each reply needs a source ('review' or 'issue') and its text.")
        try:
            cid = int(it.get("id"))  # spliced into an API path: digits only
        except (TypeError, ValueError):
            raise ValueError("Each reply needs the numeric id of the comment it answers.")
        if len(it["reply"]) > MAX_REPLY:
            raise ValueError(f"Replies are limited to {MAX_REPLY} characters.")
        clean.append((cid, it["source"], it["reply"].strip()))
    done = _handled(me, key)
    sent, skipped = [], []
    for cid, source, body in clean:
        if not body:
            continue
        if cid in done:
            skipped.append(cid)  # already answered (e.g. a retry after a timeout): never post the same reply twice
            continue
        if disclose and config.AI_DISCLOSURE:
            body += f"\n\n_{config.AI_DISCLOSURE}_"
        if source == "review":
            github.post(f"/repos/{owner}/{repo}/pulls/{number}/comments/{cid}/replies", {"body": body})
        else:
            github.post(f"/repos/{owner}/{repo}/issues/{number}/comments", {"body": body})
        _mark(me, key, [cid])  # record each one as it goes, so a failure half-way does not lose track of what was posted
        sent.append(cid)
    return {"sent": sent, "skipped": skipped}
