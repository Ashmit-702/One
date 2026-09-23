"""
Prompts a local Ollama model to turn messy search snippets into
structured ProductCandidate JSON. This is the piece that makes the app
category-agnostic — the LLM infers what fields/specs matter from the
query itself, instead of a hardcoded per-category schema.
"""
from __future__ import annotations

import json
import logging
import re

import ollama
from pydantic import ValidationError

from app.models.schemas import SearchResult, ProductCandidate
from app.config import OLLAMA_MODEL, OLLAMA_HOST, OLLAMA_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a product-data extraction engine. You read raw web search
snippets about products and output ONLY structured JSON — no prose,
no markdown, no explanation.

Given a user's product query and a numbered list of search results,
identify distinct real products mentioned and extract what you can
about each one. Work for ANY product category (electronics, appliances,
vehicles, furniture, anything) using only what's in the text — never
invent a category-specific field that isn't supported by the snippets.

Output a single JSON object of the exact shape:
{
  "candidates": [
    {
      "name": "string, the specific product name/model",
      "price": number or null (numeric value only, no currency symbols/commas),
      "currency": "3-letter code, e.g. INR, USD; default INR if unclear",
      "specs": {"key": "value", ...} (short factual specs mentioned in the text; omit if none),
      "sentiment_summary": "string or null, one short phrase on review sentiment if mentioned",
      "sentiment_score": number from 0.0 (very negative reviews) to 1.0 (very positive) or null if no review signal,
      "source_url": "the url of the search result this came from, or null"
    }
  ]
}

Rules:
- Only include products actually named in the snippets. Do not hallucinate products.
- If the same product appears in multiple snippets, merge into ONE candidate using the best available info.
- If price isn't stated, use null rather than guessing.
- If nothing usable is found, return {"candidates": []}.
- Output ONLY the JSON object, nothing else.
"""

_PRICE_CLEAN_RE = re.compile(r"[^\d.]")


class ExtractionError(Exception):
    """Raised when the local LLM is unreachable or returns unusable output."""


def _build_user_prompt(query: str, results: list[SearchResult]) -> str:
    lines = [f"User query: {query}", "", "Search results:"]
    for i, r in enumerate(results, start=1):
        lines.append(f"{i}. Title: {r.title}\n   URL: {r.url}\n   Snippet: {r.snippet}")
    return "\n".join(lines)


def _coerce_price(value) -> float | None:
    """Handle prices the model still returns as strings like '₹12,999' or '12999.00'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = _PRICE_CLEAN_RE.sub("", value)
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _coerce_specs(value) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if v is not None}


def _coerce_sentiment_score(value) -> float | None:
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, score))


def _parse_model_json(content: str) -> dict:
    """
    Ollama's format="json" guarantees syntactically valid JSON, but models
    occasionally wrap it in markdown fences anyway. Strip those defensively.
    """
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned.strip(), flags=re.IGNORECASE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"Model did not return valid JSON: {exc}") from exc


def _dedupe(candidates: list[ProductCandidate]) -> list[ProductCandidate]:
    """Collapse near-duplicate names (case/whitespace-insensitive), keeping the first seen."""
    seen: dict[str, ProductCandidate] = {}
    for c in candidates:
        key = re.sub(r"\s+", " ", c.name.strip().lower())
        if key not in seen:
            seen[key] = c
    return list(seen.values())


def extract_candidates(query: str, results: list[SearchResult]) -> list[ProductCandidate]:
    """
    Convert raw search results into structured product candidates using
    a local Ollama model. Returns [] if results is empty. Raises
    ExtractionError if Ollama is unreachable or returns unusable output.
    """
    if not results:
        return []

    client = ollama.Client(host=OLLAMA_HOST, timeout=OLLAMA_TIMEOUT_SECONDS)
    user_prompt = _build_user_prompt(query, results)

    try:
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            format="json",
            options={"temperature": 0.1},
        )
    except ollama.ResponseError as exc:
        # e.g. model not pulled locally
        raise ExtractionError(f"Ollama model error (is '{OLLAMA_MODEL}' pulled? try `ollama pull {OLLAMA_MODEL}`): {exc}") from exc
    except Exception as exc:
        # covers connection errors if the Ollama server isn't running
        raise ExtractionError(f"Could not reach Ollama at {OLLAMA_HOST}: {exc}") from exc

    content = response["message"]["content"]
    parsed = _parse_model_json(content)
    raw_candidates = parsed.get("candidates", [])
    if not isinstance(raw_candidates, list):
        raise ExtractionError("Model JSON did not contain a 'candidates' list")

    candidates: list[ProductCandidate] = []
    for item in raw_candidates:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        try:
            candidates.append(
                ProductCandidate.model_validate(
                    {
                        "name": str(item.get("name")).strip(),
                        "price": _coerce_price(item.get("price")),
                        "currency": (item.get("currency") or "INR").upper()[:3],
                        "specs": _coerce_specs(item.get("specs")),
                        "sentiment_summary": item.get("sentiment_summary") or None,
                        "sentiment_score": _coerce_sentiment_score(item.get("sentiment_score")),
                        "source_url": item.get("source_url") or None,
                    }
                )
            )
        except ValidationError as exc:
            logger.warning("Skipping invalid candidate from model output: %s", exc)
            continue

    return _dedupe(candidates)
