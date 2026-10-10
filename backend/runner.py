"""Local Full-auto runner. Runs on the STUDENT'S machine with their own `gh` login and the OpenAI Codex CLI.

    python3 -m backend.runner <job_id> <token> --server http://localhost:8000

What it does, in order:
  1. fetch the job spec (repo, issue) from the web app
  2. fork + clone the repo into ~/oss-mentor-jobs/<job>/ and branch from the upstream default branch
  3. run `codex exec` headless with the worker prompt (agents/worker_prompt.md): reproduce, failing test, smallest fix
     - Codex's OS sandbox (Seatbelt on macOS, Landlock/bubblewrap on Linux) lets it write only inside the clone and blocks the network
  4. upload the diff + the commands Codex REALLY ran, with their exit codes, for the student's REVIEW in the browser
  5. wait for the student's approval; ONLY THEN commit, push to the student's fork and open the PR with their own gh login
Safety notes: this runs a stranger's build and test commands on this machine. They run inside Codex's sandbox with no network, so a
build that must download dependencies will fail and is reported as not verified (opt in to the network with --network, knowingly).
The agent never receives your GitHub token, the runner refuses to continue if the agent touched .git/config or .git/hooks, commits
with git hooks disabled, and caps time and tool calls. Test results come from commands the runner watched execute, never from the
agent's own claims. A throwaway VM or container is still the safest place for repos you do not trust.
"""
from __future__ import annotations  # the runner must work on the stock macOS python3 (3.9)

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DISCLOSURE = "Prepared with AI assistance (OpenAI Codex) and reviewed by the author."
MAX_TOOL_CALLS = 90
SECRET_ENV = ("GITHUB_TOKEN", "GH_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_PAT", "NPM_TOKEN", "PYPI_TOKEN", "AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID")
TEST_CMD = re.compile(r"\b(pytest|unittest|tox|nox|jest|vitest|mocha|rspec|phpunit|ctest|go test|cargo test|cargo nextest|npm (run )?test|yarn test|pnpm test|"
                      r"make (test|check)|mvn (-\w+ )*test|gradle(w)? (-\w+ )*test|dotnet test|swift test|flutter test|dart test)\b")
# Git config for every git call the runner makes on the agent's checkout: never run a hook or fsmonitor program it may have planted.
GIT_SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]


# ------------------------------------------------------------------ pure helpers (unit-tested)
def scrub_env(env: dict) -> dict:
    """The agent's environment: everything except tokens it has no business reading."""
    # Codex signs in from its own login file (CODEX_HOME), so no API key needs to reach the agent's environment
    return {k: v for k, v in env.items() if k not in SECRET_ENV and not k.endswith(("_TOKEN", "_SECRET", "_API_KEY", "_PASSWORD"))}


def load_template() -> str:
    md = (ROOT / "agents" / "worker_prompt.md").read_text()
    m = re.search(r"<!-- PROMPT START -->\n(.*?)<!-- PROMPT END -->", md, re.S)
    if not m:
        raise RuntimeError("agents/worker_prompt.md is missing its PROMPT START/END markers")
    return m.group(1).strip()


def build_prompt(spec: dict, template: str) -> str:
    out = template
    for k, v in {"REPO": spec["repo"], "ISSUE": str(spec["number"]), "BUG": spec.get("title", ""), "ISSUE_URL": spec.get("issue_url", "")}.items():
        out = out.replace("{{" + k + "}}", v)
    return out


def build_command(codex: str, prompt: str, cwd: Path | str = ".", network: bool = False) -> list[str]:
    """`codex exec` for one headless, sandboxed worker run. Flags verified against codex-cli 0.160:
    --json streams JSONL events; workspace-write confines writes to the clone; approval_policy=never means nothing waits for a human;
    --ignore-user-config/--ignore-rules ignore the student's and the repository's Codex config (a repository must not loosen the sandbox);
    --ephemeral keeps no session files. The prompt is the last argument."""
    return [codex, "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
            "-s", "workspace-write", "-C", str(cwd),
            "-c", 'approval_policy="never"', "-c", f"sandbox_workspace_write.network_access={'true' if network else 'false'}",
            prompt]


def git_guard(repo_dir: Path) -> str:
    """Fingerprint of the files that make git run programs: .git/config (aliases, fsmonitor, filters, credential helpers) and hooks.
    Taken before the agent runs and compared after; any change means the checkout can no longer be trusted with your credentials."""
    h = hashlib.sha256()
    g = repo_dir / ".git"
    for f in [g / "config", *sorted((g / "hooks").glob("*"))] if g.is_dir() else []:
        h.update(str(f.relative_to(g)).encode() + b"\0")
        h.update(f.read_bytes() if f.is_file() else b"<not a file>")
    return h.hexdigest()


def diff_hash(diff: str) -> str:
    return hashlib.sha256(diff.encode()).hexdigest()


def describe_command(cmd: str) -> str:
    """`/bin/zsh -lc 'pytest -q'` -> `pytest -q` (Codex wraps every command in a login shell)."""
    m = re.match(r"^(?:/\S+/)?(?:ba|z)?sh -\w*c (.+)$", cmd.strip(), re.S)
    inner = m.group(1).strip() if m else cmd.strip()
    if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in "'\"":
        inner = inner[1:-1]
    return inner


def parse_stream_line(line: str) -> dict:
    """One JSONL line of `codex exec --json` ->
    {'events': [progress text], 'tools': int, 'commands': [{command, exit_code, output}], 'message': str|None, 'usage': dict|None, 'error': str|None}.
    `commands` holds only commands that FINISHED, with the exit code Codex observed: the source of test evidence."""
    out = {"events": [], "tools": 0, "commands": [], "message": None, "usage": None, "error": None}
    try:
        d = json.loads(line)
    except ValueError:
        return out
    kind, item = d.get("type"), d.get("item") or {}
    if kind == "item.started" and item.get("type") == "command_execution":
        out["tools"] += 1
        out["events"].append("Ran: " + describe_command(str(item.get("command", "")))[:90])
    elif kind == "item.completed" and item.get("type") == "command_execution":
        out["commands"].append({"command": describe_command(str(item.get("command", ""))), "exit_code": item.get("exit_code"),
                                "output": str(item.get("aggregated_output") or "")[-600:]})
    elif kind == "item.completed" and item.get("type") == "file_change":
        out["events"].append("Edited " + ", ".join(str(c.get("path", ""))[-60:] for c in (item.get("changes") or [])[:3]))
    elif kind == "item.completed" and item.get("type") == "agent_message":
        out["message"] = str(item.get("text") or "")
    elif kind == "turn.completed":
        out["usage"] = d.get("usage") or {}
    elif kind in ("turn.failed", "error"):
        out["error"] = str((d.get("error") or {}).get("message") or d.get("message") or "the agent reported an error")[:300]
    return out


def evidence_from(commands: list[dict]) -> tuple[list[str], list[dict]]:
    """Test evidence from commands the runner WATCHED finish. -> (tests_run lines for the review screen, all commands with exit codes)."""
    tests, allc = [], []
    for c in commands:
        entry = {"command": c["command"][:300], "exit_code": c["exit_code"], "output": c["output"][-400:]}
        allc.append(entry)
        if TEST_CMD.search(c["command"]):
            tests.append(f"`{c['command'][:120]}` exited {c['exit_code']} (executed by the runner)")
    return tests[-20:], allc[-40:]


def extract_result(text: str) -> dict | None:
    """The worker ends with a fenced ```json block. Take the last one."""
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text or "", re.S)
    for raw in reversed(blocks):
        try:
            d = json.loads(raw)
            if isinstance(d, dict) and d.get("status") in ("fixed", "stopped"):
                return d
        except ValueError:
            continue
    return None


def pr_body_with_footer(body: str, number: int) -> str:
    body = body.strip()
    if not re.search(rf"(fix(es|ed)?|close[sd]?|resolve[sd]?)\s+#{number}\b", body, re.I):
        body += f"\n\nFixes #{number}"
    return body + f"\n\n---\n{DISCLOSURE}"


# ------------------------------------------------------------------ side effects
class Api:
    def __init__(self, server: str, job: str, token: str):
        self.base, self.job, self.h = server.rstrip("/"), job, {"X-Job-Token": token}

    def _r(self, method: str, path: str, body: dict | None = None):
        # standard library only, so a fresh checkout runs with plain `python3`: no virtualenv, no pip install
        data = None if body is None else json.dumps(body).encode()
        headers = {**self.h, "Accept": "application/json", "User-Agent": "oss-mentor-runner"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{self.base}/api/runner/{self.job}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                text = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"server said {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"could not reach {self.base}: {getattr(e, 'reason', e)}")
        return json.loads(text or "{}")

    def spec(self): return self._r("GET", "/spec")
    def status(self): return self._r("GET", "/status")
    def event(self, stage: str, text: str):
        try:
            self._r("POST", "/event", {"stage": stage, "text": text})
        except RuntimeError as e:
            print("  (could not report progress:", e, ")")
    def result(self, payload: dict): return self._r("POST", "/result", payload)
    def done(self, pr_url=None, error=None): return self._r("POST", "/done", {"pr_url": pr_url, "error": error})


def sh(args: list[str], cwd: Path | None = None, check: bool = True, env: dict | None = None) -> str:
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        lines = [l.strip() for l in (p.stderr or p.stdout).splitlines() if l.strip()]
        first = next((l for l in lines if not l.lower().startswith(("usage", "flags", "error:"))), lines[0] if lines else "no output")
        raise RuntimeError(f"`{' '.join(args[:3])}` failed: {first[:220]}")  # one useful line, not a screen of usage text
    return p.stdout


def prepare_repo(spec: dict, work: Path, login: str, api=None, sleep=time.sleep) -> tuple[Path, str]:
    """Fork (without cloning), verify the fork really descends from the project, clone the fork, ensure an `upstream` remote.
    Written against the real `gh`: `--remote` is rejected when a repository argument is given, and a fresh fork can take a few
    seconds to become cloneable. Returns (repo_dir, upstream_default_branch)."""
    repo, name = spec["repo"], spec["repo"].split("/")[1]
    owner = repo.split("/")[0]
    say = (lambda t: api.event("fork", t)) if api else (lambda t: None)
    parent_of = lambda n: sh(["gh", "repo", "view", f"{login}/{n}", "--json", "parent", "-q", ".parent.nameWithOwner"], work, check=False).strip()
    # The student may already own a repo called `name` that is NOT a fork of this project (e.g. their own `keploy`). Reuse a real fork
    # if there is one, otherwise fork under `name`, then `name-owner`, never touching a repo that is not a fork of the project.
    fork_name = ""
    for cand in (name, f"{name}-{owner}"):
        if parent_of(cand).lower() == repo.lower():
            fork_name = cand
            say(f"You already have a fork ({login}/{cand}). Using it.")
            break
        if sh(["gh", "repo", "view", f"{login}/{cand}", "--json", "name", "-q", ".name"], work, check=False).strip():
            say(f"{login}/{cand} already exists and is not a fork of {repo}; leaving it alone.")
            continue
        say("Forking the repository to your account…" if cand == name else f"Forking it as {login}/{cand}…")
        cmd = ["gh", "repo", "fork", repo, "--clone=false", "--default-branch-only"] + (["--fork-name", cand] if cand != name else [])
        sh(cmd, work)
        for _ in range(15):  # wait until GitHub reports the fork (up to ~30 s)
            if parent_of(cand).lower() == repo.lower():
                fork_name = cand
                break
            sleep(2)
        if fork_name:
            break
    if not fork_name:
        raise RuntimeError(f"Could not create a fork of {repo}: {login}/{name} and {login}/{name}-{owner} are taken or not ready. Rename or delete one, then try again.")
    say("Fork ready. Cloning it…")
    repo_dir = work / fork_name
    last = None
    for _ in range(5):  # a brand-new fork is sometimes not cloneable for a moment
        try:
            sh(["gh", "repo", "clone", f"{login}/{fork_name}"], work)
            last = None
            break
        except RuntimeError as e:
            last = e
            sleep(3)
    if last:
        raise last
    if sh(["git", "remote", "get-url", "upstream"], repo_dir, check=False).strip() == "":
        sh(["git", "remote", "add", "upstream", f"https://github.com/{repo}.git"], repo_dir)
    default = sh(["gh", "repo", "view", repo, "--json", "defaultBranchRef", "-q", ".defaultBranchRef.name"]).strip() or "main"
    say(f"Branching from upstream/{default}…")
    return repo_dir, default


def collect_diff(repo_dir: Path) -> tuple[str, list[str]]:
    sh(["git", *GIT_SAFE, "add", "-N", "."], repo_dir, check=False)  # make new files visible to `git diff`
    diff = sh(["git", *GIT_SAFE, "diff", "--binary"], repo_dir, check=False)  # --binary: a binary change is shown, not hidden
    files = [l for l in sh(["git", *GIT_SAFE, "diff", "--name-only"], repo_dir, check=False).splitlines() if l]
    return diff, files


def run_worker(cmd: list[str], cwd: Path, api: Api, timeout: int) -> tuple[str, dict, bool]:
    """Run `codex exec` headless and stream progress to the server.
    Returns (agent's final message, {'commands': [...], 'usage': {...}, 'error': str}, hit_limit)."""
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=scrub_env(dict(os.environ)))
    start, tools, last_sent, text, limit = time.time(), 0, 0.0, "", False
    info: dict = {"commands": [], "usage": {}, "error": "", "other": []}
    try:
        for line in proc.stdout:
            if line.strip() and not line.lstrip().startswith("{"):
                info["other"] = (info["other"] + [line.strip()[:300]])[-12:]
            p = parse_stream_line(line)
            tools += p["tools"]
            info["commands"] += p["commands"]
            info["usage"] = p["usage"] or info["usage"]
            info["error"] = p["error"] or info["error"]
            text = p["message"] if p["message"] is not None else text
            for ev in p["events"]:
                if time.time() - last_sent > 1.0:
                    api.event("working", ev)
                    last_sent = time.time()
            if tools > MAX_TOOL_CALLS or time.time() - start > timeout:
                limit = True
                api.event("working", "Reached the safety limit (commands or time); stopping the worker.")
                proc.kill()
                break
            try:  # honour Cancel in the browser within a few seconds
                if time.time() - info.get("polled", 0) > 5:
                    info["polled"] = time.time()
                    if api.status()["status"] in ("cancelled", "failed", "expired"):
                        limit = True
                        proc.kill()
                        break
            except RuntimeError:
                pass
    finally:
        if proc.poll() is None:
            proc.kill()
    if not text and (info["error"] or info["other"]):
        text = "Codex ended without a result: " + (info["error"] or " | ".join(info["other"]))
    return text, info, limit


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="OSS Mentor local Full-auto runner")
    ap.add_argument("job"); ap.add_argument("token")
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--workdir", default=str(Path.home() / "oss-mentor-jobs"))
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--codex", default=shutil.which("codex") or "codex", help="path to the OpenAI Codex CLI")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--network", action="store_true",
                    help="let the agent's commands use the network (e.g. to install dependencies). Weakens the sandbox: only for repositories you trust")
    a = ap.parse_args(argv)

    for tool, hint in (("gh", "https://cli.github.com"), ("git", "https://git-scm.com"), (a.codex, "npm install -g @openai/codex  (then: codex login)")):
        if not shutil.which(tool):
            print(f"Missing required tool: {tool}. Install: {hint}")
            return 2
    if subprocess.run([a.codex, "login", "status"], capture_output=True).returncode != 0:
        print("Run `codex login` first (sign in with ChatGPT or an API key).")
        return 2
    if subprocess.run(["gh", "auth", "status"], capture_output=True).returncode != 0:
        print("Run `gh auth login` first.")
        return 2

    api = Api(a.server, a.job, a.token)
    try:
        spec = api.spec()
    except RuntimeError as e:  # expired/used token, cancelled job, wrong server, offline: one readable line, not a traceback
        print(f"Could not start this job: {e}")
        print("Each command works once and expires after 2 hours. Start a new Full-auto job in the browser and paste the new command.")
        return 1
    print(f"Job {spec['id']}: {spec['repo']} #{spec['number']} - {spec['title']}")
    print("\nThis will run the project's build and test commands on THIS machine, driven by an AI agent.")
    if a.network:
        print("WARNING: --network: those commands can reach the internet. Writes still stay in the clone.")
    else:
        print("They run in Codex's sandbox: writes stay in the clone and there is no network (dependency installs will fail and be reported).")
    print("Only do this for projects you are comfortable running code from (a throwaway VM or container is safer).")
    if not a.yes and input("Type YES to continue: ").strip() != "YES":
        api.done(error="Cancelled at the runner prompt.")
        return 1

    work = Path(a.workdir) / spec["id"]
    work.mkdir(parents=True, exist_ok=True)
    try:
        login = sh(["gh", "api", "user", "-q", ".login"]).strip()
        repo_dir, default = prepare_repo(spec, work, login, api)
        branch = f"fix/{spec['number']}-oss-mentor"
        sh(["git", *GIT_SAFE, "fetch", "upstream", default], repo_dir)
        sh(["git", *GIT_SAFE, "switch", "-c", branch, f"upstream/{default}"], repo_dir)

        api.event("working", "The worker is reproducing the bug and writing a fix…")
        prompt = build_prompt(spec, load_template())
        guard = git_guard(repo_dir)
        text, info, limit = run_worker(build_command(a.codex, prompt, repo_dir, network=a.network), repo_dir, api, a.timeout)
        if git_guard(repo_dir) != guard:
            raise RuntimeError(f"The worker changed .git/config or .git/hooks, which could run code with your GitHub login. Stopped; nothing was "
                               f"pushed. Delete {work} and do not reuse that checkout.")
        res = extract_result(text) or {}
        diff, files = collect_diff(repo_dir)
        tests_run, commands = evidence_from(info["commands"])  # from commands the runner watched finish, never from the agent's own claims
        status = res.get("status") if res.get("status") in ("fixed", "stopped") else ("fixed" if diff.strip() and not limit else "stopped")
        api.event("review", "Uploading the change for your review…")
        api.result({"status": status, "summary": res.get("summary") or text[-1500:], "diff": diff, "files": files, "tests_run": tests_run, "commands": commands,
                    "not_verified": res.get("not_verified", []) + (["Stopped at a safety limit or was cancelled before finishing."] if limit else []) +
                                    ([] if tests_run else ["No test command ran to completion under the runner, so no test result is attested."]),
                    "pr_title": res.get("pr_title") or spec["title"], "pr_body": res.get("pr_body", ""), "cost_usd": 0.0})
        if status != "fixed" or not diff.strip():
            print("The worker did not produce a verified fix. Nothing was pushed.")
            return 0

        print("\nChange uploaded. Review it in your browser and click Approve to open the PR (waiting)…")
        deadline = time.time() + 4 * 3600
        while time.time() < deadline:
            st = api.status()
            if st["status"] == "approved":
                break
            if st["status"] in ("cancelled", "failed", "expired", "done"):
                print(f"Job is {st['status']}. Stopping; nothing was pushed.")
                return 0
            time.sleep(3)
        else:
            print("Timed out waiting for approval.")
            return 0

        # What gets committed must be exactly what the student reviewed: nothing may have changed in the checkout since the upload.
        if git_guard(repo_dir) != guard:
            raise RuntimeError("The checkout's .git/config or hooks changed while waiting for approval. Stopped; nothing was pushed.")
        now_diff, _ = collect_diff(repo_dir)
        if st.get("diff_sha256") and diff_hash(now_diff) != st["diff_sha256"]:
            raise RuntimeError("The files changed after you reviewed them, so they no longer match what you approved. Stopped; nothing was pushed.")
        api.event("opening", "Committing and pushing to your fork…")
        sh(["git", *GIT_SAFE, "add", "-A"], repo_dir)
        commit = ["git", *GIT_SAFE]
        if not (sh(["git", "config", "user.email"], repo_dir, check=False).strip() and sh(["git", "config", "user.name"], repo_dir, check=False).strip()):
            if st["signoff"]:
                raise RuntimeError("DCO sign-off needs your real name and email: run `git config --global user.name ...` and `user.email ...` first.")
            commit += ["-c", f"user.name={login}", "-c", f"user.email={login}@users.noreply.github.com"]
        commit += ["commit", "--no-verify"] + (["-s"] if st["signoff"] else []) + ["-m", st["pr_title"]]
        sh(commit, repo_dir)
        sh(["git", *GIT_SAFE, "push", "--no-verify", "-u", "origin", branch], repo_dir)
        body_file = work / "pr_body.md"
        body_file.write_text(pr_body_with_footer(st["pr_body"], spec["number"]))
        url = sh(["gh", "pr", "create", "--repo", spec["repo"], "--head", f"{login}:{branch}", "--title", st["pr_title"], "--body-file", str(body_file)], repo_dir).strip().splitlines()[-1]
        api.done(pr_url=url)
        print("PR opened:", url)
        return 0
    except Exception as e:  # report instead of leaving the job hanging
        print("Runner error:", e)
        try:
            api.done(error=str(e)[:400])
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
