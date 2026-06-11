"""FastAPI app + routes.

Phase 0: boots, serves a health check, and renders the base dashboard shell.
Result/diff/dashboard routes land in later phases (see TODO.md).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import settings

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title="LLM Evaluation & Guardrails Harness", version=__version__)


@app.get("/health")
def health() -> JSONResponse:
    """Liveness + a peek at how the harness is wired (no secrets)."""
    return JSONResponse(
        {
            "status": "ok",
            "version": __version__,
            "judge_provider": settings.model_provider,
            "judge_model": settings.judge_model,
            "target_adapter": settings.rag_adapter,
            "target_version": settings.rag_version,
        }
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "version": __version__,
            "provider": settings.model_provider,
            "judge_model": settings.judge_model,
            "target_version": settings.rag_version,
        },
    )
