"""Loopback-only, offline rehearsal server. Never calls OpenAI or GitHub and never publishes.
The scripted pieces live in backend/fixture.py; here only the fixture project is in the catalogue."""
import os

os.environ["CATALOGUE_AUTO_REFRESH"] = "false"
os.environ["DATA_DIR"] = os.getenv("DEMO_DATA_DIR", "/tmp/oss-mentor-demo")
os.environ["DATABASE_URL"] = ""
os.environ["POSTGRES_URL"] = ""
os.environ["GITHUB_CLIENT_ID"] = ""
os.environ["GITHUB_CLIENT_SECRET"] = ""
os.environ["GITHUB_TOKEN"] = ""
os.environ["DEMO_MODE"] = "true"
os.environ["DEMO_CATALOGUE_ONLY"] = "true"
from . import config

if config.PUBLIC_DEPLOY:
    raise RuntimeError("The rehearsal server is loopback-only; a public deployment gets demo mode automatically when unconfigured.")
config.GITHUB_TOKEN = ""
from .app import app  # noqa: E402  (installs the fixture because DEMO_MODE is set)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8001")))
