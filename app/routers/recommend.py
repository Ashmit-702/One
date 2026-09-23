"""
The single API endpoint that ties the pipeline together. Kept thin on
purpose — all real logic lives in app.services.*; this module just wires
stages together, derives a usable search query/budget from the request,
and maps service-level errors to sensible HTTP responses.
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException

from app.models.schemas import RecommendRequest, RecommendResponse
from app.services import search as search_service
from app.services import extraction as extraction_service
from app.services import scoring as scoring_service
from app.services import cache as cache_service

logger = logging.getLogger(__name__)
router = APIRouter()

# Recognizes phrases like "under 20k", "below ₹15,000", "up to 1.5 lakh"
# so a budget embedded in free text works even if the caller doesn't also
# pass the separate `budget` field. Generic across categories.
_BUDGET_PATTERN = re.compile(
    r"(?:under|below|within|less than|max|up to|budget of)\s*"
    r"(?:rs\.?|inr|₹|\$|usd)?\s*"
    r"([\d,]+(?:\.\d+)?)\s*"
    r"(k|l|lakh|lakhs|thousand|crore|crores)?",
    re.IGNORECASE,
)

_MULTIPLIERS = {
    "k": 1_000,
    "thousand": 1_000,
    "l": 100_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
}


def _parse_budget_from_query(query: str) -> float | None:
    """Best-effort extraction of a budget number embedded in free text."""
    match = _BUDGET_PATTERN.search(query)
    if not match:
        return None
    number_str, suffix = match.groups()
    try:
        number = float(number_str.replace(",", ""))
    except ValueError:
        return None
    multiplier = _MULTIPLIERS.get((suffix or "").lower(), 1)
    return number * multiplier


def _compose_search_query(payload: RecommendRequest) -> str:
    """Fold category + budget into the search query when they add signal
    the free-text query doesn't already carry, without ever assuming a
    fixed category vocabulary."""
    parts: list[str] = []
    if payload.category and payload.category.lower() not in payload.query.lower():
        parts.append(payload.category)
    parts.append(payload.query)
    if payload.budget is not None and not _BUDGET_PATTERN.search(payload.query):
        parts.append(f"under {payload.budget:.0f}")
    return " ".join(parts)


def run_recommendation(payload: RecommendRequest) -> RecommendResponse:
    """
    Core pipeline: search -> extract -> score. Raises HTTPException on any
    failure. Shared by both the JSON API endpoint and the HTML form
    endpoint so there's exactly one place this logic lives.
    """
    effective_budget = payload.budget if payload.budget is not None else _parse_budget_from_query(payload.query)
    search_query = _compose_search_query(payload)

    # Cache is keyed on the user's actual constraints (query text, resolved
    # budget, category) rather than the search-engine-facing query string,
    # so it reflects "has this request been answered before" accurately.
    cache_key = cache_service.normalize_query_key(payload.query, effective_budget, payload.category)
    cached_response = cache_service.get_cached(cache_key)
    if cached_response is not None:
        cached_response["cached"] = True
        return RecommendResponse.model_validate(cached_response)

    try:
        results = search_service.search_products(search_query)
    except search_service.SearchError as exc:
        logger.error("Search failed for query=%r: %s", search_query, exc)
        raise HTTPException(status_code=502, detail=f"Search backend failed: {exc}") from exc

    if not results:
        raise HTTPException(status_code=404, detail="No search results found for your query. Try rephrasing it.")

    try:
        candidates = extraction_service.extract_candidates(payload.query, results)
    except extraction_service.ExtractionError as exc:
        logger.error("Extraction failed for query=%r: %s", payload.query, exc)
        raise HTTPException(status_code=502, detail=f"Local LLM extraction failed: {exc}") from exc

    if not candidates:
        raise HTTPException(
            status_code=404,
            detail="Found search results but couldn't identify specific products in them. Try a more specific query.",
        )

    try:
        best, reason = scoring_service.pick_best(payload.query, candidates, effective_budget)
    except scoring_service.ScoringError as exc:
        logger.error("Scoring failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Could not score candidates: {exc}") from exc

    response = RecommendResponse(
        product_name=best.candidate.name,
        price=best.candidate.price,
        currency=best.candidate.currency,
        reason=reason,
        source_url=best.candidate.source_url,
        cached=False,
    )

    cache_service.set_cached(cache_key, response.model_dump())

    return response


@router.post("/recommend", response_model=RecommendResponse)
def recommend(payload: RecommendRequest) -> RecommendResponse:
    return run_recommendation(payload)
