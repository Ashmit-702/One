"""
Generic, category-agnostic scoring. No product-type branching — every
candidate is scored on the same three axes:
  1. price_fit       -- how well price matches the stated budget
  2. sentiment        -- review sentiment score from extraction
  3. spec_relevance    -- how well specs match keywords/constraints in the query
Weights are configured centrally in app.config, not hardcoded here.
"""
from __future__ import annotations

import math
import re

from app.models.schemas import ProductCandidate, ScoredCandidate
from app.config import WEIGHT_PRICE_FIT, WEIGHT_SENTIMENT, WEIGHT_SPEC_RELEVANCE

# Generic words that carry query *intent* (budget, quality) rather than
# *product attributes* — excluded from spec-overlap matching so they don't
# inflate relevance scores. Nothing category-specific in here.
_STOPWORDS = {
    "a", "an", "the", "for", "with", "and", "or", "in", "of", "to", "on",
    "under", "over", "below", "above", "around", "near",
    "best", "good", "great", "top", "nice", "decent",
    "recommend", "recommendation", "recommended", "suggest", "suggestion",
    "budget", "price", "priced", "cost",
    "review", "reviews", "rated", "rating", "ratings",
    "me", "my", "i", "want", "need", "looking", "please",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_CURRENCY_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}


class ScoringError(Exception):
    """Raised when scoring can't produce a result (e.g. no candidates)."""


def _tokenize(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 2 and t not in _STOPWORDS}


def _format_price(price: float, currency: str) -> str:
    symbol = _CURRENCY_SYMBOLS.get(currency.upper())
    if symbol:
        return f"{symbol}{price:,.0f}"
    return f"{price:,.0f} {currency.upper()}"


def score_price_fit(price: float | None, budget: float | None) -> float:
    """
    No price or no budget -> neutral (0.5), since we can't judge fit.
    At-or-under budget -> 0.6-1.0, scaled toward 1.0 the closer price gets
    to budget (rewards using the budget well, without punishing cheaper
    options harshly — a much cheaper item still scores reasonably).
    Over budget -> decays smoothly toward 0 the further over it goes, but
    isn't zeroed out instantly, so a candidate only slightly over budget
    can still surface if it's clearly better on other axes.
    """
    if budget is None or price is None or budget <= 0 or price < 0:
        return 0.5

    if price <= budget:
        ratio = price / budget if budget > 0 else 0.0
        return round(0.6 + 0.4 * ratio, 4)

    overage_ratio = (price - budget) / budget
    score = 0.6 * math.exp(-3 * overage_ratio)
    return round(max(0.0, score), 4)


def score_spec_relevance(query: str, specs: dict[str, str]) -> float:
    """
    Generic keyword overlap between the query's meaningful terms and the
    specs dict (keys + values), normalized to 0-1. No notion of "RAM" vs
    "battery capacity" baked in here — just text overlap — which is what
    keeps this category-agnostic.
    """
    query_tokens = _tokenize(query)
    if not query_tokens:
        return 0.5

    if not specs:
        return 0.3  # mild penalty for missing spec info, not a hard zero

    spec_text = " ".join(f"{k} {v}" for k, v in specs.items())
    spec_tokens = _tokenize(spec_text)
    if not spec_tokens:
        return 0.3

    overlap = query_tokens & spec_tokens
    return round(min(1.0, len(overlap) / len(query_tokens)), 4)


def _build_reason(scored: ScoredCandidate, budget: float | None) -> str:
    c = scored.candidate
    parts: list[str] = []

    if c.price is not None and budget is not None:
        price_str = _format_price(c.price, c.currency)
        budget_str = _format_price(budget, c.currency)
        if c.price <= budget:
            parts.append(f"fits your {budget_str} budget at {price_str}")
        else:
            parts.append(f"best overall match, though slightly over budget at {price_str}")
    elif c.price is not None:
        parts.append(f"priced at {_format_price(c.price, c.currency)}")

    if scored.sentiment_score >= 0.7:
        parts.append(c.sentiment_summary.lower() if c.sentiment_summary else "strong review sentiment")
    elif scored.sentiment_score <= 0.3 and c.sentiment_summary:
        parts.append(f"note: {c.sentiment_summary.lower()}")

    if scored.spec_relevance_score >= 0.6:
        parts.append("specs match what you asked for")

    if not parts:
        parts.append("best overall match among the available options")

    reason = "; ".join(parts) + "."
    return reason[0].upper() + reason[1:]


def pick_best(
    query: str,
    candidates: list[ProductCandidate],
    budget: float | None,
) -> tuple[ScoredCandidate, str]:
    """
    Score every candidate on the three axes, combine via the configured
    weights, and return the top ScoredCandidate plus a short reason.
    Raises ScoringError if candidates is empty.
    """
    if not candidates:
        raise ScoringError("No candidates to score")

    scored_list: list[ScoredCandidate] = []
    for c in candidates:
        price_fit = score_price_fit(c.price, budget)
        spec_relevance = score_spec_relevance(query, c.specs)
        sentiment = c.sentiment_score if c.sentiment_score is not None else 0.5

        total = (
            WEIGHT_PRICE_FIT * price_fit
            + WEIGHT_SENTIMENT * sentiment
            + WEIGHT_SPEC_RELEVANCE * spec_relevance
        )
        scored_list.append(
            ScoredCandidate(
                candidate=c,
                price_fit_score=price_fit,
                sentiment_score=sentiment,
                spec_relevance_score=spec_relevance,
                total_score=round(total, 4),
            )
        )

    scored_list.sort(key=lambda s: s.total_score, reverse=True)
    best = scored_list[0]
    reason = _build_reason(best, budget)
    return best, reason
