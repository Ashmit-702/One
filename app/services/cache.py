"""
SQLite-backed cache so identical or near-identical queries skip the
search + LLM round trip. Kept isolated behind get/set so the rest of the
pipeline doesn't need to know caching exists — and every function here
fails safe: a cache problem degrades to "just do the work again", never
breaks the request.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
from contextlib import contextmanager

from app.config import CACHE_DB_PATH, CACHE_TTL_SECONDS

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9.]+")


@contextmanager
def _connect():
    conn = sqlite3.connect(CACHE_DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def init_cache_db() -> None:
    """Create the cache table if it doesn't exist. Call once on app startup."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recommendation_cache (
                query_key TEXT PRIMARY KEY,
                response_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.commit()


def normalize_query_key(query: str, budget: float | None, category: str | None) -> str:
    """
    Build a deterministic cache key. Tokenizing + sorting words makes the
    key robust to case, punctuation, extra whitespace, and word order
    ("mobile under 20k" / "Mobile, under   20K" / "under 20k mobile" all
    collapse to the same key), while still keeping budget and category as
    separate, exact fields so they can't accidentally blur two different
    requests together.
    """
    tokens = sorted(_TOKEN_RE.findall(query.lower()))
    normalized_query = " ".join(tokens)
    budget_part = f"{budget:.2f}" if budget is not None else "none"
    category_part = (category or "").strip().lower()
    raw = f"{normalized_query}|{budget_part}|{category_part}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached(query_key: str) -> dict | None:
    """
    Return the cached response dict if present and not expired, else
    None. Lazily deletes the row if it's found expired, keeping the table
    from growing unbounded with stale entries.
    """
    try:
        with _connect() as conn:
            cur = conn.execute(
                "SELECT response_json, created_at FROM recommendation_cache WHERE query_key = ?",
                (query_key,),
            )
            row = cur.fetchone()
            if row is None:
                return None

            response_json, created_at = row
            if time.time() - created_at > CACHE_TTL_SECONDS:
                conn.execute("DELETE FROM recommendation_cache WHERE query_key = ?", (query_key,))
                conn.commit()
                return None

            return json.loads(response_json)
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        logger.warning("Cache read failed, proceeding without cache: %s", exc)
        return None


def set_cached(query_key: str, response: dict) -> None:
    """Store a response dict under query_key, overwriting any existing entry."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO recommendation_cache (query_key, response_json, created_at) VALUES (?, ?, ?)",
                (query_key, json.dumps(response), time.time()),
            )
            conn.commit()
    except sqlite3.Error as exc:
        logger.warning("Cache write failed (non-fatal): %s", exc)
