"""
Wraps duckduckgo-search to turn a free-text query into a list of
SearchResult. No category-specific logic belongs here — just generic
query construction, retries, and result normalization. Works for
"mobiles" or "bikes" or anything else without knowing what either is.
"""
from __future__ import annotations

import logging
import time

from app.models.schemas import SearchResult
from app.config import SEARCH_MAX_RESULTS, SEARCH_REGION

logger = logging.getLogger(__name__)

# Generic suffix that biases results toward pages carrying price/spec/review
# info, regardless of product category. No product-type keywords here.
_QUERY_SUFFIX = "price specifications reviews"

_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 2.0


class SearchError(Exception):
    """Raised when the search backend fails after retries, or can't be loaded at all."""


def _build_search_query(query: str) -> str:
    """Append a generic bias suffix unless the query already implies it."""
    lower = query.lower()
    if any(term in lower for term in ("price", "review", "spec")):
        return query
    return f"{query} {_QUERY_SUFFIX}"


def search_products(query: str, max_results: int = SEARCH_MAX_RESULTS) -> list[SearchResult]:
    """
    Take a free-text product query and return raw web search results.

    Retries on rate-limit/transient errors with linear backoff. Raises
    SearchError if the backend keeps failing — callers (the API layer)
    decide how to surface that to the user.

    The duckduckgo_search import is deliberately deferred to inside this
    function rather than done at module level. It pulls in `primp`, a
    compiled (Rust) native dependency — on some serverless runtimes a
    mismatched prebuilt wheel can fail to import. Importing it lazily
    means that failure only breaks search requests (a clean SearchError
    -> 502), not the entire app at cold start, which is what happens if a
    module-level import throws during a `from app.routers.recommend
    import ...` chain that main.py depends on to boot at all.
    """
    try:
        from duckduckgo_search import DDGS
        from duckduckgo_search.exceptions import DuckDuckGoSearchException, RatelimitException
    except Exception as exc:  # ImportError or a native-extension load failure
        raise SearchError(f"duckduckgo_search could not be loaded on this platform: {exc}") from exc

    search_query = _build_search_query(query)
    last_error: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with DDGS() as ddgs:
                raw_hits = ddgs.text(
                    search_query,
                    region=SEARCH_REGION,
                    safesearch="moderate",
                    max_results=max_results,
                )
            break
        except RatelimitException as exc:
            last_error = exc
            logger.warning("DuckDuckGo rate limited (attempt %s/%s): %s", attempt, _MAX_RETRIES, exc)
            time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
        except DuckDuckGoSearchException as exc:
            last_error = exc
            logger.warning("DuckDuckGo search error (attempt %s/%s): %s", attempt, _MAX_RETRIES, exc)
            time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
    else:
        raise SearchError(f"Search failed after {_MAX_RETRIES} attempts: {last_error}") from last_error

    if not raw_hits:
        return []

    results: list[SearchResult] = []
    for hit in raw_hits:
        title = (hit.get("title") or "").strip()
        url = (hit.get("href") or "").strip()
        snippet = (hit.get("body") or "").strip()
        if not title or not url:
            # Skip malformed hits rather than let one bad result break extraction
            continue
        results.append(SearchResult(title=title, snippet=snippet, url=url))

    return results
