"""FastAPI app + routes: the quality dashboard, run results, and the run diff.

Routes resolve the suite for each run, lazily score it (rule + judge) via
`report`, and render server-side with Jinja. HTMX is loaded for progressive
enhancement; the views work as plain links too.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app import __version__, report, store
from app.config import settings

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title="LLM Evaluation & Guardrails Harness", version=__version__)


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


templates.env.filters["pct"] = _pct


@app.get("/health")
def health() -> JSONResponse:
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


def _base_ctx() -> dict:
    return {
        "version": __version__,
        "provider": settings.model_provider,
        "judge_model": settings.judge_model,
        "target_version": settings.rag_version,
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    conn = store.connect()
    store.init_db(conn)
    try:
        runs = store.list_runs(conn, limit=50)
        # Lazily score + summarize each run so the dashboard shows real rates.
        summaries = []
        for run in runs:
            suite = report.find_suite_for_run(run.suite_id)
            rep = report.build_run_report(conn, run.id, suite)
            summaries.append(rep)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "dashboard.html", {**_base_ctx(), "reports": summaries}
    )


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(request: Request, run_id: int) -> HTMLResponse:
    conn = store.connect()
    store.init_db(conn)
    try:
        run = store.get_run(conn, run_id)
        if run is None:
            return HTMLResponse(f"<p>Run #{run_id} not found.</p>", status_code=404)
        suite = report.find_suite_for_run(run.suite_id)
        rep = report.build_run_report(conn, run_id, suite)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "run_detail.html", {**_base_ctx(), "rep": rep}
    )


@app.get("/compare", response_class=HTMLResponse)
def compare_runs(request: Request, base: int, candidate: int) -> HTMLResponse:
    conn = store.connect()
    store.init_db(conn)
    try:
        base_run = store.get_run(conn, base)
        if base_run is None or store.get_run(conn, candidate) is None:
            return HTMLResponse("<p>One or both runs not found.</p>", status_code=404)
        suite = report.find_suite_for_run(base_run.suite_id)
        cmp = report.compare(conn, base, candidate, suite)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "compare.html", {**_base_ctx(), "cmp": cmp}
    )
