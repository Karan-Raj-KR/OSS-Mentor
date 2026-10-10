"""OpenAI Responses wrapper: bounded output, disk cache, estimated costs and reserved budgets."""
import hashlib
import json
import os
import threading
from dataclasses import dataclass, field

import openai

from . import config, usage

CACHE_DIR = config.DATA / "cache"
CACHE_DIR.mkdir(exist_ok=True)
USAGE_FILE = config.DATA / "usage.json"
_lock = threading.Lock()


class MentorError(Exception):
    pass


class BudgetExceeded(Exception):
    pass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    cache_hits: int = 0  # answered from the disk cache, cost nothing

    def as_dict(self) -> dict:
        return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


@dataclass
class Ledger:
    sessions: dict[str, Usage] = field(default_factory=dict)
    total: Usage = field(default_factory=Usage)

    def session(self, sid: str) -> Usage:
        return self.sessions.setdefault(sid, Usage())


ledger = Ledger()
# Worst-case cost of calls that passed the budget check but have not finished. Counted against the budget so that several
# requests arriving together (double-clicks, parallel tabs) cannot each see the same headroom and overspend it together.
_inflight: dict[str, float] = {}


def _load_total() -> None:
    try:
        ledger.total = Usage(**json.loads(USAGE_FILE.read_text()))
    except (OSError, ValueError, TypeError):
        pass


_load_total()


def cost_of(model: str, inp: int, out: int, cache_read: int = 0, cache_write: int = 0) -> float:
    if model not in config.PRICES:
        raise MentorError(f"No verified pricing configured for {model}. Choose a documented model in backend/config.py.")
    pin, pout, pcached = config.PRICES[model]
    return (max(0, inp - cache_read) * pin + out * pout + cache_read * pcached) / 1_000_000


def estimate_input_tokens(*texts: str) -> int:
    return sum(len(t.encode("utf-8")) for t in texts) + 32  # conservative byte bound plus message overhead


def _charge(u: Usage, model: str, usage) -> float:
    inp = usage.input_tokens or 0
    out = usage.output_tokens or 0
    cr = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
    cw = 0
    c = cost_of(model, inp, out, cr, cw)
    u.input_tokens += inp
    u.output_tokens += out
    u.cache_read_tokens += cr
    u.cache_write_tokens += cw
    u.cost_usd += c
    u.calls += 1
    return c


def session_usage(uid: str) -> dict:
    """What this student has spent: today's total in the database when persistent, else this process's session ledger."""
    return usage.summary(uid) if usage.persistent() else ledger.session(uid).as_dict()


def _check_budget(session_id: str, model: str, est_in: int, max_tokens: int) -> float:
    """Raise BudgetExceeded, or return the worst-case cost of this call (the caller reserves it while the call runs)."""
    worst = cost_of(model, est_in, max_tokens)
    spent = (usage.today(session_id) if usage.persistent() else ledger.session(session_id).cost_usd) + _inflight.get(session_id, 0.0)
    window = "Daily" if usage.persistent() else "Session"
    if spent + worst > config.SESSION_BUDGET_USD:
        raise BudgetExceeded(
            f"{window} budget ${config.SESSION_BUDGET_USD:.2f} would be exceeded "
            f"(spent ${spent:.4f}, this call could cost up to ${worst:.4f})."
        )
    total = (usage.today_all() if usage.persistent() else ledger.total.cost_usd) + sum(_inflight.values())
    if total + worst > config.GLOBAL_BUDGET_USD:
        raise BudgetExceeded(f"Global budget ${config.GLOBAL_BUDGET_USD:.2f} reached.")
    return worst


def _release(session_id: str, amount: float) -> None:
    left = _inflight.get(session_id, 0.0) - amount
    if left > 1e-12:
        _inflight[session_id] = left
    else:
        _inflight.pop(session_id, None)


_client: openai.OpenAI | None = None


def client() -> openai.OpenAI:
    global _client
    if _client is None:
        if not os.getenv("OPENAI_API_KEY"):
            raise MentorError("Set OPENAI_API_KEY server-side in .env to use the mentor.")
        _client = openai.OpenAI(timeout=45, max_retries=0)
    return _client


def ask(
    *,
    tier: str,
    system: str,
    messages: list[dict],
    max_tokens: int,
    session_id: str = "default",
    effort: str = "low",
    use_cache: bool = True,
) -> dict:
    """Return {"text", "model", "cost_usd", "cached"}. `tier` is "cheap" or "smart"."""
    model = config.MODEL_CHEAP if tier == "cheap" else config.MODEL_SMART
    key = hashlib.sha256(json.dumps(["openai-responses-v1", session_id, model, system, messages, max_tokens], sort_keys=True).encode()).hexdigest()
    path = CACHE_DIR / f"{key}.json"
    hit = None
    if use_cache and path.exists():
        try:
            hit = json.loads(path.read_text())["text"]
        except (OSError, ValueError, KeyError, TypeError):
            hit = None  # unreadable cache entry: ask again and overwrite it
    if hit is not None:
        with _lock:
            ledger.session(session_id).cache_hits += 1
            ledger.total.cache_hits += 1
        return {"text": hit, "model": model, "cost_usd": 0.0, "cost_estimated": True, "cached": True}

    est = estimate_input_tokens(system, json.dumps(messages))
    with _lock:
        reserved = _check_budget(session_id, model, est, max_tokens)
        _inflight[session_id] = _inflight.get(session_id, 0.0) + reserved

    kwargs = dict(model=model, instructions=system, input=messages,
                  max_output_tokens=max_tokens, store=False)
    try:
        resp = client().responses.create(**kwargs)
    except BaseException:
        with _lock:
            _release(session_id, reserved)
        raise
    text = resp.output_text or ""
    reported_usage = resp.usage
    refused = any(getattr(part, "type", "") == "refusal" for item in getattr(resp, "output", [])
                  for part in getattr(item, "content", []))
    with _lock:
        _release(session_id, reserved)
        if reported_usage is None:
            c = reserved  # missing usage must not make a potentially billed request free
            for u in (ledger.session(session_id), ledger.total):
                u.cost_usd += c
                u.calls += 1
        else:
            c = _charge(ledger.session(session_id), model, reported_usage)
            _charge(ledger.total, model, reported_usage)
        if usage.persistent():
            usage.add(session_id, c, reported_usage.input_tokens or 0 if reported_usage else 0, reported_usage.output_tokens or 0 if reported_usage else 0)
        else:
            USAGE_FILE.write_text(json.dumps(ledger.total.as_dict()))
    if reported_usage is None:
        raise MentorError("OpenAI did not report usage. The reserved worst-case cost estimate was charged; no answer was cached.")
    if use_cache and text and not refused and resp.status == "completed":  # a truncated answer must not be served forever from the cache
        tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"text": text}))
        tmp.replace(path)  # atomic: a concurrent reader never sees half a file
    if not text or refused or resp.status != "completed":
        raise MentorError("OpenAI returned an incomplete or refused answer. Try a shorter request.")
    return {"text": text, "model": model, "cost_usd": c, "cost_estimated": True, "cached": False}


def parse_json(text: str):
    """Pull the first JSON object/array out of a model reply."""
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    if start == -1:
        raise ValueError("no JSON in reply")
    dec = json.JSONDecoder()
    obj, _ = dec.raw_decode(text[start:])
    return obj
