"""
Shared data contracts between search -> extraction -> scoring -> API.
Keeping these in one place means each pipeline stage can be built/tested
independently against the same shapes.
"""
from typing import Optional
from pydantic import BaseModel, Field


class RecommendRequest(BaseModel):
    query: str = Field(..., description="Free-text ask, e.g. 'washing machine under 15k with good reviews'")
    budget: Optional[float] = Field(None, description="Max budget in DEFAULT_CURRENCY, if not embedded in query")
    category: Optional[str] = Field(None, description="Optional hint, e.g. 'laptop'. Never required.")


class SearchResult(BaseModel):
    """Raw output of the search step, before any LLM processing."""
    title: str
    snippet: str
    url: str
    source_engine: str = "duckduckgo"


class ProductCandidate(BaseModel):
    """Structured output of the LLM extraction step."""
    name: str
    price: Optional[float] = None
    currency: str = "INR"
    specs: dict[str, str] = Field(default_factory=dict)
    sentiment_summary: Optional[str] = None
    sentiment_score: Optional[float] = Field(None, ge=0, le=1, description="0=bad reviews, 1=great reviews")
    source_url: Optional[str] = None


class ScoredCandidate(BaseModel):
    candidate: ProductCandidate
    price_fit_score: float
    sentiment_score: float
    spec_relevance_score: float
    total_score: float


class RecommendResponse(BaseModel):
    product_name: str
    price: Optional[float] = None
    currency: str = "INR"
    reason: str
    source_url: Optional[str] = None
    cached: bool = False
