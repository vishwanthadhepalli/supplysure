"""FastAPI application entry point."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import router as api_router
from app.db import SessionLocal, create_schema
from app.security import PasswordMiddleware
from app.setup_api import router as setup_router
from app.supplygpt.api import router as supplygpt_router

WEB_DIR = Path(__file__).resolve().parent / "web"
log = logging.getLogger("supplysure")


def seed_if_empty() -> bool:
    """On a fresh hosted database, load the sample factory and replay the sample supplier
    messages (fixture-backed, no Sarvam calls). Does nothing if any order exists (D-63)."""
    from app.models import Order
    from scripts.sample_messages import replay
    from scripts.seed import seed

    with SessionLocal() as session:
        if session.query(Order).first() is not None:
            return False
        seed(session)
        replay(session)
    log.warning("empty database: loaded the sample factory and sample messages")
    return True


@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_schema()
    if os.environ.get("SEED_ON_START", "").strip().lower() in ("1", "true", "yes"):
        seed_if_empty()
    yield


app = FastAPI(title="SupplySure", lifespan=lifespan)
app.add_middleware(PasswordMiddleware)  # active only when APP_PASSWORD is set
app.include_router(api_router)
app.include_router(setup_router)
app.include_router(supplygpt_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# The web app. Mounted last so /api and /health win.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
