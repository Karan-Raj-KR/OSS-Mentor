"""Settings, read from the environment (.env is loaded in app.py). Nothing secret lives in code."""
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

ON_VERCEL = bool(os.getenv("VERCEL"))
# A public deployment must never run in the single-user "local" mode (anyone could spend the operator's keys).
PUBLIC_DEPLOY = ON_VERCEL or os.getenv("PUBLIC_DEPLOY", "").lower() == "true"
# Serverless filesystems are read-only except /tmp, which is per-instance and ephemeral: only caches may live there.
DATA = Path(os.getenv("DATA_DIR") or ("/tmp/oss-mentor" if ON_VERCEL else ROOT / "data"))
DATA.mkdir(parents=True, exist_ok=True)

# State that must survive between requests/instances lives in Postgres when DATABASE_URL (or Vercel's POSTGRES_URL) is set; SQLite otherwise.
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or ""
# Signs and encrypts the sign-in cookie, so sessions work across serverless instances with no server-side session store.
SECRET_KEY = os.getenv("SECRET_KEY", "")

# Model tiers: cheap model for classification/triage, smart model for teaching and drafting.
MODEL_CHEAP = os.getenv("MODEL_CHEAP", "gpt-4.1-mini")
MODEL_SMART = os.getenv("MODEL_SMART", "gpt-4.1")

# Estimated USD per million tokens (input, output, cached input).
# Official model pages checked 2026-10-10. Unknown models fail closed.
PRICES = {
    "gpt-4.1-mini": (0.40, 1.60, 0.10),
    "gpt-4.1-mini-2025-04-14": (0.40, 1.60, 0.10),
    "gpt-4.1": (2.00, 8.00, 0.50),
    "gpt-4.1-2025-04-14": (2.00, 8.00, 0.50),
}

SESSION_BUDGET_USD = float(os.getenv("SESSION_BUDGET_USD", "0.50"))
GLOBAL_BUDGET_USD = float(os.getenv("GLOBAL_BUDGET_USD", "10.00"))

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
# Where a student's machine gets the Full-auto runner from when it has no copy yet (hosted mode). Override for a fork of this project.
RUNNER_REPO_URL = os.getenv("RUNNER_REPO_URL", "https://github.com/Karan-Raj-KR/OSS-Mentor.git")


def _github_token() -> str:
    """Env var first; otherwise reuse the local `gh` login for read access (60 req/h unauthenticated is too low)."""
    t = os.getenv("GITHUB_TOKEN", "")
    if t:
        return t
    if GITHUB_CLIENT_ID:
        return ""  # hosted mode: never fall back to the operator's identity
    try:
        return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


GITHUB_TOKEN = _github_token()
# Posting replies on GitHub is off unless explicitly enabled by the operator.
AUTO_POST_REPLIES = os.getenv("AUTO_POST_REPLIES", "false").lower() == "true"
AI_DISCLOSURE = os.getenv("AI_DISCLOSURE", "Drafted with AI assistance and reviewed by the author.")


def _missing() -> list[str]:
    need = []
    if not (GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET):
        need.append("GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET (a GitHub OAuth App, so only signed-in students can use your API keys)")
    if len(SECRET_KEY) < 32:
        need.append("SECRET_KEY (at least 32 random characters)")
    if not DATABASE_URL:
        need.append("DATABASE_URL (a Postgres database such as Neon)")
    return need


DEMO_INSTALLED = False  # set by backend/fixture.py once every outbound call has really been replaced; only then is the API opened


def demo_active() -> bool:
    """Controlled demo mode (backend/fixture.py): scripted mentor and a labelled fixture repository, no OpenAI, no GitHub, no spend.
    On when DEMO_MODE=true, and AUTOMATICALLY on a public deployment that has not finished its sign-in setup (so a fresh deploy is
    clickable instead of a blank wall). DEMO_MODE=false opts out. Off as soon as the deployment is fully configured."""
    flag = os.getenv("DEMO_MODE", "").lower()
    if flag == "true":
        return True
    return PUBLIC_DEPLOY and flag != "false" and bool(_missing())


def deploy_missing() -> list[str]:
    """What a PUBLIC deployment still needs before it may serve REAL requests. Empty list = safe to serve (or not a public deployment,
    or demo mode, where nothing real is reachable: every outbound call is replaced by the fixture)."""
    if not PUBLIC_DEPLOY or DEMO_INSTALLED:
        return []
    return _missing()
