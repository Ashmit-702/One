"""
App entrypoint. Run with:  uvicorn app.main:app --reload

Also the Vercel entrypoint: Vercel's Python runtime auto-detects a
FastAPI instance named `app` at app/main.py with zero extra config.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import APP_NAME
from app.models.schemas import RecommendRequest
from app.routers.recommend import router as recommend_router, run_recommendation
from app.services.cache import init_cache_db

# Resolve paths relative to this file rather than the process's working
# directory — the working directory isn't guaranteed to be the project
# root under every host (notably serverless runtimes like Vercel).
BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Cache is a nice-to-have, never a reason the whole app should fail to
    # start — mirrors the fail-open philosophy already used inside
    # get_cached/set_cached. A read-only filesystem, a missing /tmp, or any
    # other cache-init problem should degrade to "no cache", not a crash.
    try:
        init_cache_db()
    except Exception as exc:
        logging.getLogger(__name__).warning("Cache init failed, continuing without a persistent cache: %s", exc)
    yield


app = FastAPI(title=APP_NAME, lifespan=lifespan)

# Defensive: create static/ if it's missing (e.g. an empty directory that
# didn't survive a git commit, since git doesn't track empty dirs) rather
# than letting StaticFiles crash the whole app at import time.
_static_dir = BASE_DIR / "static"
_static_dir.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app.include_router(recommend_router)


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "result": None,
            "error": None,
            "query": "",
            "budget": None,
            "category": "",
        },
    )


@app.post("/recommend-form")
def recommend_form(
    request: Request,
    query: str = Form(...),
    budget: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
):
    """
    HTML-form counterpart to POST /recommend: same pipeline via
    run_recommendation(), but renders the result (or error) back into the
    page instead of returning JSON. Keeps the form fields sticky so the
    user doesn't lose their input on error.

    budget is accepted as a raw string because an empty form field posts
    as "" rather than being omitted, which Form(Optional[float]) rejects
    outright instead of treating as "not provided".
    """
    parsed_budget: Optional[float] = None
    if budget and budget.strip():
        try:
            parsed_budget = float(budget.strip())
        except ValueError:
            parsed_budget = None  # let query-text budget parsing (if any) take over

    payload = RecommendRequest(query=query, budget=parsed_budget, category=category or None)

    result = None
    error = None
    try:
        result = run_recommendation(payload)
    except HTTPException as exc:
        error = exc.detail

    # Avoid re-populating the number input as "15000.0" when the user typed a whole number
    display_budget = parsed_budget
    if display_budget is not None and display_budget == int(display_budget):
        display_budget = int(display_budget)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "result": result,
            "error": error,
            "query": query,
            "budget": display_budget,
            "category": category or "",
        },
    )


@app.get("/health")
def health():
    return {"status": "ok"}
