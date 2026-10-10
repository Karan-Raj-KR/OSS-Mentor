"""Student profile: the saved quiz answers (so a returning student skips the interview) and a few settings.
Everything here is the student's own data: they can see it, export it and delete it (profile page)."""
import json
import time

from . import db, jobs, journey

SETTINGS = {"signoff_default": bool}
MAX_LIST = 12


class ProfileError(Exception):
    pass


def _allowed(questions: list[dict]) -> dict:
    return {q["id"]: ({o[0] for o in q["options"]}, bool(q["multi"])) for q in questions}


def validate(answers: dict, questions: list[dict]) -> dict:
    """Only known questions and only values the quiz actually offers. Unknown keys are rejected, never silently stored."""
    allowed, out = _allowed(questions), {}
    if not isinstance(answers, dict):
        raise ProfileError("answers must be an object")
    for k, v in answers.items():
        if k not in allowed:
            raise ProfileError(f"Unknown field: {k}")
        opts, multi = allowed[k]
        if multi:
            if not isinstance(v, list) or len(v) > MAX_LIST or any(not isinstance(x, str) or x not in opts for x in v):
                raise ProfileError(f"Invalid value for {k}")
            out[k] = sorted(set(v), key=v.index)
        else:
            if not isinstance(v, str) or v not in opts:
                raise ProfileError(f"Invalid value for {k}")
            out[k] = v
    return out


def validate_settings(settings: dict) -> dict:
    if not isinstance(settings, dict):
        raise ProfileError("settings must be an object")
    out = {}
    for k, v in settings.items():
        if k not in SETTINGS:
            raise ProfileError(f"Unknown setting: {k}")
        if not isinstance(v, SETTINGS[k]):
            raise ProfileError(f"Invalid value for {k}")
        out[k] = v
    return out


def get(user: str) -> dict:
    with db.connect() as con:
        r = con.execute("SELECT * FROM profiles WHERE uid=?", (user,)).fetchone()
    if not r:
        return {"answers": {}, "settings": {"signoff_default": False}, "updated": None}
    return {"answers": json.loads(r["answers"]), "settings": {"signoff_default": False, **json.loads(r["settings"])}, "updated": r["updated"]}


def save(user: str, answers: dict | None, settings: dict | None, questions: list[dict]) -> dict:
    cur = get(user)
    new_answers = validate(answers, questions) if answers is not None else cur["answers"]
    new_settings = {**cur["settings"], **(validate_settings(settings) if settings is not None else {})}
    now = time.time()
    with db.connect() as con:
        con.execute("INSERT INTO profiles(uid, answers, settings, created, updated) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(uid) DO UPDATE SET answers=excluded.answers, settings=excluded.settings, updated=excluded.updated",
                    (user, json.dumps(new_answers), json.dumps(new_settings), now, now))
        con.commit()
    return get(user)


def delete(user: str) -> None:
    with db.connect() as con:
        con.execute("DELETE FROM profiles WHERE uid=?", (user,))
        con.commit()


def replies_handled(user: str) -> dict:
    """Which reviewer comments the student already answered, per pull request (kept so the same comment is never answered twice)."""
    with db.connect() as con:
        rows = con.execute("SELECT pr, handled FROM reply_state WHERE uid=?", (user,)).fetchall()
    return {r["pr"]: json.loads(r["handled"]) for r in rows}


def activity(user: str) -> dict:
    js = jobs.list_for(user)
    return {"journeys": [{"repo": j["repo"], "number": j["number"], "title": j["issue"]["title"], "url": j["issue"]["url"]} for j in journey.list_for(user)],
            "jobs": [{k: j[k] for k in ("id", "repo", "number", "title", "status", "pr_url", "created", "error")} for j in js],
            "prs": [j["pr_url"] for j in js if j["pr_url"]]}


def export(user: str, identity: dict, usage: dict) -> dict:
    """Everything we hold about this student. No tokens: those are never stored with the profile."""
    p = get(user)
    return {"exported_at": time.time(), "identity": {k: identity.get(k) for k in ("login", "name", "url", "mode")}, "profile": p,
            "jobs": [{**j, **{"events": jobs.get(user, j["id"])["events"]}} for j in jobs.list_for(user)], "usage": usage,
            "replies_handled": replies_handled(user), "journeys": journey.list_for(user),
            "note": "Your GitHub token is never stored with your profile, and is not included here."}


def delete_everything(user: str) -> dict:
    n = jobs.delete_all(user)
    delete(user)
    with db.connect() as con:
        con.execute("DELETE FROM journeys WHERE uid=?", (user,))
        r = con.execute("DELETE FROM reply_state WHERE uid=?", (user,))
        replies = max(r.rowcount or 0, 0)
    return {"deleted_jobs": n, "deleted_profile": True, "deleted_reply_records": replies}
