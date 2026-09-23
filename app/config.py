"""
Central configuration. Everything that might need tuning per-deployment
lives here, not scattered through the pipeline.
"""
import os

from dotenv import load_dotenv

# Loads .env for local dev only — a no-op if the file doesn't exist, so
# this is safe on hosts (Vercel, Render, Railway) that inject env vars
# directly instead of via a .env file.
load_dotenv()

# --- Ollama (local LLM) ---
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120"))

# --- Search ---
SEARCH_MAX_RESULTS = int(os.getenv("SEARCH_MAX_RESULTS", "12"))
SEARCH_REGION = os.getenv("SEARCH_REGION", "in-en")  # bias toward India/English results

# --- Scoring weights (must sum to 1.0; generic, not category-specific) ---
WEIGHT_PRICE_FIT = float(os.getenv("WEIGHT_PRICE_FIT", "0.4"))
WEIGHT_SENTIMENT = float(os.getenv("WEIGHT_SENTIMENT", "0.35"))
WEIGHT_SPEC_RELEVANCE = float(os.getenv("WEIGHT_SPEC_RELEVANCE", "0.25"))

# --- Cache ---
# Vercel's filesystem is read-only at runtime except /tmp, and /tmp is
# ephemeral (wiped between cold starts/instances). Detect that environment
# automatically (Vercel sets VERCEL=1) and default the cache there instead
# of a project-root file that would fail to write. CACHE_DB_PATH always
# wins if set explicitly, on any platform.
_default_cache_path = "/tmp/cache.db" if os.getenv("VERCEL") else "cache.db"
CACHE_DB_PATH = os.getenv("CACHE_DB_PATH", _default_cache_path)
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", str(6 * 60 * 60)))  # 6 hours default

# --- App ---
APP_NAME = "Product Recommender"
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "INR")
