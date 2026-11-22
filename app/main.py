"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import router as api_router
from app.db import create_schema
from app.setup_api import router as setup_router
from app.supplygpt.api import router as supplygpt_router

WEB_DIR = Path(__file__).resolve().parent / "web"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_schema()
    yield


app = FastAPI(title="SupplySure", lifespan=lifespan)
app.include_router(api_router)
app.include_router(setup_router)
app.include_router(supplygpt_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Read-only web app. Mounted last so /api and /health win.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
