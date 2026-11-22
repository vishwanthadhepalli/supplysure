"""Runtime configuration. Secrets come from the environment or .env only (never committed)."""

import os
from datetime import date
from pathlib import Path

DEFAULT_DATABASE_URL = "sqlite:///./supplysure.db"
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def load_dotenv(path: Path = ENV_FILE) -> None:
    """Minimal .env reader: KEY=VALUE lines, # comments. Never overrides the real environment."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def today() -> date:
    """The factory's "today", used to resolve "next Tuesday" and similar phrases.

    SUPPLYSURE_TODAY=YYYY-MM-DD pins it (the sample data lives in June 2027).
    """
    pinned = os.environ.get("SUPPLYSURE_TODAY", "").strip()
    return date.fromisoformat(pinned) if pinned else date.today()


def sarvam_api_key() -> str | None:
    return os.environ.get("SARVAM_API_KEY", "").strip() or None
