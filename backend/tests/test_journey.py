"""Deterministic review and OpenAI adapter tests; these do not prove live integration."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as N

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from backend import auth, config, db, github, journey, llm, profiles
from backend.app import app
from backend.tests.test_core import FakeClient, fake_resp

DIFF = 'diff --git a/parser.py b/parser.py\n--- a/parser.py\n+++ b/parser.py\n@@ -1 +1 @@\n-return items[:-1]\n+return items[:]\n'


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "GITHUB_CLIENT_ID", "")
    monkeypatch.setattr(config, "GITHUB_CLIENT_SECRET", "")
    monkeypatch.setattr(db, "PATH", tmp_path / "journeys.db")
    monkeypatch.setattr(github, "verify_issue", lambda *a: {"ok": True, "state": "open", "assignees": [], "claimed": False, "linked_prs": [], "unchecked": [], "checked_at": datetime.now(timezone.utc).isoformat()})
    monkeypatch.setattr(github, "policy_scan", lambda *a: {"status": "no_restriction_found", "files": ["CONTRIBUTING.md"], "sources": [], "ai_flags": [], "legal": []})
    monkeypatch.setattr(github, "get", lambda *a: {"title": "Parser drops last item", "body": "Keep the final item", "html_url": "https://github.com/o/r/issues/7"})
    monkeypatch.setattr(github, "repo_overview", lambda *a: {"readme": "Actual readme", "top_level": ["parser.py"]})
    journey.begin("local", "o/r", 7)
    yield TestClient(app)


def save(uid="local", diff=DIFF, evidence="Command: pytest\nExit: 0\nOutput: 2 passed"):
    return journey.save(uid, "o/r", 7, diff, evidence, "Fixes #7. Preserve last item.", "AI helped explain the loop; I reviewed the change.")


def scripted(monkeypatch, data):
    calls = []
    def ask(**kw):
        calls.append(kw)
        return {"text": json.dumps(data), "cost_usd": 0}
    monkeypatch.setattr(llm, "ask", ask)
    return calls


def complete(monkeypatch):
    scripted(monkeypatch, {"questions": ["Cause in parser.py?", "Why does the bound fix it?", "Test and remaining case?"]})
    journey.checkpoint("local", "o/r", 7)
    scripted(monkeypatch, {"results": [{"passed": True, "feedback": "Specific and correct"}] * 3})
    return journey.checkpoint("local", "o/r", 7, ["Exclusive bound", "Visits last item", "Invalid input remains"])


def test_readiness_requires_checkpoint_and_labels_evidence(world, monkeypatch):
    j = save()
    assert j["files"] == ["parser.py"] and not j["ready_for_review"]
    j = complete(monkeypatch)
    assert j["ready_for_review"] and "not executed" in j["evidence_label"]
    assert any("not executed" in u for u in j["unverified"])
    assert j["checkpoint"]["answers"] == journey.get("local", "o/r", 7)["checkpoint"]["answers"]


def test_diff_and_evidence_changes_reset_checkpoint(world, monkeypatch):
    save(); complete(monkeypatch)
    assert not save(diff=DIFF.replace('items[:]', 'list(items)'))["ready_for_review"]
    assert "checkpoint" not in journey.get("local", "o/r", 7)
    complete(monkeypatch)
    assert "checkpoint" not in save(diff=DIFF.replace('items[:]', 'list(items)'), evidence="Not run")


def test_checkpoint_is_grounded_and_supports_retry(world, monkeypatch):
    save()
    calls = scripted(monkeypatch, {"questions": ["Why parser.py?", "Which test?"]})
    journey.checkpoint("local", "o/r", 7)
    assert DIFF in json.loads(calls[0]["messages"][0]["content"])["diff"]
    assert "Keep the final item" in calls[0]["messages"][0]["content"]
    scripted(monkeypatch, {"results": [{"passed": False, "feedback": "Consider the exclusive bound"}, {"passed": True, "feedback": "Right"}]})
    j = journey.checkpoint("local", "o/r", 7, ["It sorts", "Last item"])
    assert not j["checkpoint"]["complete"] and not j["ready_for_review"]
    scripted(monkeypatch, {"results": [{"passed": True, "feedback": "Right"}] * 2})
    assert journey.checkpoint("local", "o/r", 7, ["Exclusive bound", "Last item"])['checkpoint']['complete']


@pytest.mark.parametrize("data", [{"questions": ["one"]}, {"questions": [1, 2]}, [], {"questions": "fake"}])
def test_invalid_questions_fail_closed(world, monkeypatch, data):
    save(); scripted(monkeypatch, data)
    with pytest.raises(journey.JourneyError):
        journey.checkpoint("local", "o/r", 7)
    assert "checkpoint" not in journey.get("local", "o/r", 7)


def test_invalid_evaluation_never_completes(world, monkeypatch):
    save(); scripted(monkeypatch, {"questions": ["Cause?", "Fix?"]}); journey.checkpoint("local", "o/r", 7)
    scripted(monkeypatch, {"results": [{"passed": "true", "feedback": "fine"}] * 2})
    with pytest.raises(journey.JourneyError):
        journey.checkpoint("local", "o/r", 7, ["a", "b"])
    assert not journey.get("local", "o/r", 7)["checkpoint"]["complete"]


def test_concurrent_edit_rejects_stale_evaluation(world, monkeypatch):
    save(); scripted(monkeypatch, {"questions": ["Cause?", "Fix?"]}); journey.checkpoint("local", "o/r", 7)
    def ask(**kw):
        save(diff=DIFF + '\n')
        return {"text": json.dumps({"results": [{"passed": True, "feedback": "ok"}] * 2})}
    monkeypatch.setattr(llm, "ask", ask)
    with pytest.raises(journey.JourneyError, match="another tab"):
        journey.checkpoint("local", "o/r", 7, ["a", "b"])
    assert not journey.view(journey.get("local", "o/r", 7))["ready_for_review"]


def test_stale_or_taken_issue_withholds_readiness(world, monkeypatch):
    save(); j = complete(monkeypatch)
    j["verification"]["checked_at"] = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    assert not journey.view(j)["ready_for_review"]
    monkeypatch.setattr(github, "verify_issue", lambda *a: {"ok": False, "reason": "assigned", "checked_at": datetime.now(timezone.utc).isoformat()})
    assert not journey.begin("local", "o/r", 7)["ready_for_review"]


@pytest.mark.parametrize("status", ["unknown", "restriction_found", "review_required"])
def test_policy_does_not_silently_pass(world, monkeypatch, status):
    save(); j = complete(monkeypatch); j["policy"]["status"] = status
    assert not journey.view(j)["ready_for_review"]


def test_source_context_is_fetched_and_missing_files_honest(world, monkeypatch):
    monkeypatch.setattr(github, "raw_file", lambda o, r, p: "return items[:-1]" if p == "parser.py" else None)
    j = journey.sources("local", "o/r", 7, ["parser.py", "missing.py"])
    assert j["sources"]["missing.py"]["text"] is None
    assert "return items[:-1]" in journey.context(j)
    with pytest.raises(journey.JourneyError):
        journey.sources("local", "o/r", 7, ["../secret"])


def test_review_is_owner_scoped_and_exported_deleted(world):
    save()
    assert not journey.get("mallory", "o/r", 7)
    assert len(profiles.export("local", {}, {})["journeys"]) == 1
    profiles.delete_everything("local")
    assert not journey.get("local", "o/r", 7)


def test_authenticated_routes_and_full_auto_need_sign_in(world, monkeypatch):
    monkeypatch.setattr(config, "GITHUB_CLIENT_ID", "cid"); monkeypatch.setattr(config, "GITHUB_CLIENT_SECRET", "secret")
    assert world.post('/api/jobs', json={"repo": "o/r", "number": 7, "consent": True}).status_code == 401
    assert world.get('/api/journey?repo=o/r&number=7').status_code == 401
    assert world.post('/api/journey/checkpoint', json={"repo": "o/r", "number": 7}).status_code == 401
    assert world.post('/api/journey/save', json={"repo": "o/r", "number": 7, "diff": DIFF}).status_code == 401


def test_unknown_pricing_prevents_request(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path); monkeypatch.setattr(config, "MODEL_CHEAP", "unpriced")
    fc = FakeClient(fake_resp()); monkeypatch.setattr(llm, "_client", fc)
    with pytest.raises(llm.MentorError):
        llm.ask(tier="cheap", system="x", messages=[{"role": "user", "content": "y"}], max_tokens=20)
    assert fc.calls == []


def test_refusal_is_charged_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path); monkeypatch.setattr(llm, "USAGE_FILE", tmp_path / "usage.json")
    monkeypatch.setattr(llm, "ledger", llm.Ledger()); monkeypatch.setattr(llm, "_inflight", {})
    fc = FakeClient(fake_resp("", cr=200)); monkeypatch.setattr(llm, "_client", fc)
    with pytest.raises(llm.MentorError):
        llm.ask(tier="cheap", system="x", messages=[{"role": "user", "content": "y"}], max_tokens=20)
    assert llm.ledger.total.cost_usd > 0 and llm.ledger.total.cache_read_tokens == 200
    assert llm._inflight == {} and len(list(tmp_path.glob('*.json'))) == 1


def test_sdk_auth_failure_is_sanitized_and_streamed(world, monkeypatch):
    from backend import agents
    def fail(*a, **kw):
        raise openai.AuthenticationError("sensitive diagnostic", response=httpx.Response(401, request=httpx.Request('POST', 'https://api.openai.com/v1/responses')), body=None)
    monkeypatch.setattr(agents, "tour", fail)
    r = world.post('/api/tour', json={"repo": "o/r", "issue_title": "x"})
    assert r.status_code == 503 and "OPENAI_API_KEY" in r.text and "sensitive" not in r.text
    monkeypatch.setattr(agents, "scout_stream", fail)
    r = world.post('/api/scout/stream', json={"org": "o", "profile": {}})
    assert '"kind": "auth"' in r.text and 'sensitive' not in r.text


def test_missing_usage_charges_reservation_without_fabricating_tokens(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path); monkeypatch.setattr(llm, "USAGE_FILE", tmp_path / "usage.json")
    monkeypatch.setattr(llm, "ledger", llm.Ledger()); monkeypatch.setattr(llm, "_inflight", {})
    resp = fake_resp(); resp.usage = None
    monkeypatch.setattr(llm, "_client", FakeClient(resp))
    with pytest.raises(llm.MentorError, match="did not report usage"):
        llm.ask(tier="cheap", system="x", messages=[{"role": "user", "content": "y"}], max_tokens=20)
    assert llm.ledger.total.cost_usd > 0 and llm.ledger.total.input_tokens == 0
    assert llm._inflight == {}


def test_failed_comments_are_unknown(monkeypatch):
    def fake_get(path, params=None, token=None):
        if path.endswith('/comments'):
            raise httpx.ReadTimeout('unavailable')
        if path.endswith('/timeline'):
            return []
        return {'state': 'open', 'assignees': []}
    monkeypatch.setattr(github, 'get', fake_get)
    j = github.verify_issue('o', 'r', 7)
    assert j['claimed'] is None and j['availability'] == 'unknown' and 'recent comments' in j['unchecked']


def test_claiming_comment_has_source_link(monkeypatch):
    def fake_get(path, params=None, token=None):
        if path.endswith('/comments'):
            return [{'body': "I'll take this", 'html_url': 'https://github.com/o/r/issues/7#issuecomment-1', 'created_at': '2026-10-10T00:00:00Z'}]
        if path.endswith('/timeline'):
            return []
        return {'state': 'open', 'assignees': []}
    monkeypatch.setattr(github, 'get', fake_get)
    j = github.verify_issue('o', 'r', 7)
    assert not j['ok'] and j['claiming_comments'][0]['url'].endswith('issuecomment-1')


def test_coach_uses_saved_source_context_not_browser_claims(world, monkeypatch):
    from backend import agents
    captured = []
    monkeypatch.setattr(agents, 'coach', lambda mode, context, history, uid: captured.append(context) or {'reply': 'hint'})
    r = world.post('/api/coach', json={'mode':'learn','repo':'o/r','number':7,'context':'Invented source and fake test result','history':[{'role':'user','content':'Where should I look?'}]})
    assert r.status_code == 200 and 'Keep the final item' in captured[0]
    assert 'Invented source' not in captured[0]
