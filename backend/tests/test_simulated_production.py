"""Run the REAL server as a separate process, configured like a public deployment (PUBLIC_DEPLOY, Postgres, session secret),
and restart it between requests the way serverless instances come and go. Nothing mocked."""
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

KEY = "s" * 48


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class Server:
    def __init__(self, env):
        self.port, self.env, self.p = free_port(), env, None

    def start(self):
        self.p = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app:app", "--port", str(self.port), "--log-level", "warning"],
                                  env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for _ in range(150):
            try:
                httpx.get(self.url + "/api/health", timeout=2); return self
            except httpx.HTTPError:
                time.sleep(0.2)
        raise RuntimeError("server did not start: " + (self.p.stdout.read() if self.p.poll() is not None else "timeout"))

    def stop(self):
        if self.p and self.p.poll() is None:
            self.p.terminate()
            try: self.p.wait(5)
            except subprocess.TimeoutExpired: self.p.kill()

    @property
    def url(self): return f"http://127.0.0.1:{self.port}"


def base_env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GITHUB", "ANTHROPIC", "DATABASE", "POSTGRES", "SECRET", "VERCEL", "PUBLIC"))}
    env.update(PUBLIC_DEPLOY="true", DATA_DIR=str(tmp_path), CATALOGUE_AUTO_REFRESH="false", PYTHONPATH=os.getcwd(), **extra)
    return env


def test_unconfigured_public_server_is_inert_when_demo_is_opted_out(tmp_path):
    s = Server(base_env(tmp_path, DEMO_MODE="false")).start()
    try:
        h = httpx.get(s.url + "/api/health").json()
        assert h["configured"] is False and len(h["missing"]) == 3
        assert httpx.get(s.url + "/api/questions").status_code == 200       # keyless preview
        assert httpx.get(s.url + "/api/profile").status_code == 503
        assert httpx.post(s.url + "/api/scout/stream", json={"org": "x", "profile": {}}).status_code == 503
        assert httpx.get(s.url + "/").status_code == 200
    finally:
        s.stop()


def test_configured_server_keeps_state_and_sessions_across_restarts(tmp_path, pg_url):
    if not pg_url:
        pytest.skip("no PostgreSQL available")
    env = base_env(tmp_path, GITHUB_CLIENT_ID="cid", GITHUB_CLIENT_SECRET="csecret", SECRET_KEY=KEY, DATABASE_URL=pg_url, BASE_URL="http://localhost")
    # mint a cookie exactly as the OAuth callback would
    code = ("import os; os.environ.update(SECRET_KEY=%r); from backend import auth; "
            "print(auth.seal(auth.User('alice','gho_FAKE',name='Alice',gh_login='alice')))" % KEY)
    cookie = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout.strip()
    s = Server(env).start()
    try:
        assert httpx.get(s.url + "/api/health").json()["configured"] is True
        assert httpx.get(s.url + "/api/profile").status_code == 401                       # signed-out visitors get nothing
        c = {"oss_session": cookie}
        assert httpx.get(s.url + "/api/profile", cookies=c).json()["identity"]["login"] == "alice"
        r = httpx.put(s.url + "/api/profile", json={"answers": {"skill": "advanced", "languages": ["Go"]}}, cookies=c)
        assert r.status_code == 200
        hdr = httpx.get(s.url + "/api/me", cookies=c).headers
        assert hdr["x-content-type-options"] == "nosniff" and hdr["x-frame-options"] == "DENY" and hdr["cache-control"] == "no-store"
        s.stop(); s.port = free_port(); s.start()                                           # a brand-new instance: empty memory
        got = httpx.get(s.url + "/api/profile", cookies=c).json()
        assert got["answers"] == {"skill": "advanced", "languages": ["Go"]}                # data survived (Postgres)
        assert got["identity"]["login"] == "alice"                                          # session survived (stateless cookie)
        # sign out: the SAME cookie must stop working on a brand-new instance too
        assert httpx.post(s.url + "/auth/logout", cookies=c).status_code == 200
        s.stop(); s.port = free_port(); s.start()
        assert httpx.get(s.url + "/api/profile", cookies=c).status_code == 401
        # delete my data wipes it, signed-in again
        time.sleep(0.05)
        cookie2 = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout.strip()
        assert httpx.get(s.url + "/api/profile", cookies={"oss_session": cookie2}).json()["answers"] == {"skill": "advanced", "languages": ["Go"]}
        assert httpx.delete(s.url + "/api/profile", cookies={"oss_session": cookie2}).status_code == 200
        assert httpx.get(s.url + "/api/profile", cookies={"oss_session": cookie2}).status_code == 401
    finally:
        s.stop()



def test_unconfigured_public_server_runs_the_whole_journey_as_a_labelled_demo(tmp_path):
    """A fresh public deployment (no OAuth, no database, no keys) must be clickable end to end, and must never reach OpenAI or GitHub."""
    s = Server(base_env(tmp_path, OPENAI_API_KEY="must-never-be-used", GITHUB_TOKEN="must-never-be-used")).start()
    try:
        with httpx.Client(base_url=s.url, timeout=30) as c:
            h = c.get("/api/health").json()
            assert h["demo_fixture"] is True and h["full_auto_available"] is False and h["owner_missing"]       # labelled; owner still told what is missing
            assert "demo" in c.get("/api/me").json()["login"].lower() and c.cookies.get("oss_demo")             # a private demo identity per browser
            profile = {"mode": "learn", "languages": ["Python"], "skill": "beginner", "interests": ["web"], "goal": "hackathon", "time": "event",
                       "git_level": "commit", "contrib_type": "any", "machine": "normal", "legal": "both"}
            m = c.post("/api/match", json=profile).json()
            assert m["ranking"][0]["github"] == "oss-mentor-demo" and len(m["ranking"]) == 3 and "FIXTURE" in m["ranking"][0]["notes"]
            events = [__import__("json").loads(l) for l in c.post("/api/scout/stream", json={"org": "oss-mentor-demo", "repo": "oss-mentor-demo/parser", "profile": profile}).text.splitlines() if l]
            res = events[-1]
            assert res["type"] == "result" and res["picks"][0]["repo"] == "oss-mentor-demo/parser" and res["picks"][0]["number"] == 7
            j = c.post("/api/journey/begin", json={"repo": "oss-mentor-demo/parser", "number": 7}).json()
            assert j["verification"]["ok"] is True and j["policy"]
            t = c.post("/api/tour", json={"repo": "oss-mentor-demo/parser", "issue_title": "parse() drops the last item", "level": "beginner"}).json()
            assert "Controlled demo" in t["markdown"]
            # a real (non-fixture) organisation answers honestly: nothing scripted there
            other = [__import__("json").loads(l) for l in c.post("/api/scout/stream", json={"org": "AOSSIE", "profile": profile}).text.splitlines() if l][-1]
            assert other["picks"] == []
            # Full-auto and anything that would publish stay off
            assert c.post("/api/jobs", json={"repo": "oss-mentor-demo/parser", "number": 7, "consent": True}).status_code == 503
            # two browsers never see each other's saved work
            c2 = httpx.Client(base_url=s.url, timeout=30)
            assert c2.get("/api/me").json()["login"] and c2.cookies.get("oss_demo") != c.cookies.get("oss_demo")
    finally:
        s.stop()
