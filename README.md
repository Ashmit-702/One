# Product Recommender

Ask for a product recommendation in any category with constraints (e.g.
"mobile under 20k INR") and get back exactly one recommended product with a
one-line reason. No hardcoded categories — retrieval + local LLM extraction
generalize across product types.

## Stack
- **Backend:** FastAPI
- **Search:** `duckduckgo-search` (no API key)
- **Extraction:** local Ollama (`llama3.1:8b`) turning messy snippets into
  structured JSON
- **Scoring:** generic weighted scoring (price fit, review sentiment, spec
  relevance) — no per-category logic
- **Cache:** SQLite with TTL
- **Frontend:** server-rendered Jinja2, minimal JS

## Project layout
```
app/
  main.py              # FastAPI app + Vercel entrypoint (mounts router/static/templates)
  config.py            # all tunables in one place
  routers/
    recommend.py        # POST /recommend
  services/
    search.py            # step 2: duckduckgo-search wrapper
    extraction.py         # step 3: Ollama -> structured JSON
    scoring.py            # step 4: generic weighted scoring
    cache.py               # step 7: SQLite cache
  models/
    schemas.py           # shared Pydantic contracts
  templates/
    index.html           # form + result
  static/
requirements.txt
.env.example
vercel.json            # Vercel function config (maxDuration)
.python-version        # pins the Python runtime version for Vercel
.vercelignore
```

## Build status
All 7 build steps are complete and each has been tested locally (with the
search and extraction services mocked where a live DuckDuckGo/Ollama
connection wasn't available in the build environment):

1. ✅ Scaffolding + requirements.txt
2. ✅ `services/search.py` — DuckDuckGo search wrapper, with retries/backoff and query augmentation
3. ✅ `services/extraction.py` — Ollama structured extraction, schema-validated and deduped
4. ✅ `services/scoring.py` — generic weighted scoring (price fit, sentiment, spec relevance) + reason generation
5. ✅ `routers/recommend.py` — `POST /recommend`, with budget parsing from free text and per-stage error handling
6. ✅ `templates/index.html` + `POST /recommend-form` — minimal sticky form, cache-hit badge
7. ✅ `services/cache.py` — SQLite cache, TTL-based, order/case-insensitive key normalization, fails open on any DB error

### Still worth doing before relying on this
- Run it against a **real** Ollama instance and real DuckDuckGo results —
  the extraction prompt was validated against a hand-crafted mocked
  response, not the actual messiness of live search snippets. Watch for
  the model missing the `candidates` JSON wrapper or hallucinating specs
  on noisy input, and tighten the prompt/parsing if so.
- Tune the scoring weights (`WEIGHT_PRICE_FIT`, `WEIGHT_SENTIMENT`,
  `WEIGHT_SPEC_RELEVANCE` in `.env`) against a few real queries you care about.
- Consider rate-limiting `/recommend` before deploying publicly, since
  each uncached call is a real search + LLM call.

## Local setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Install and run Ollama separately: https://ollama.com
ollama pull llama3.1:8b
ollama serve   # usually already running as a background service

cp .env.example .env   # adjust if needed
uvicorn app.main:app --reload
```
Visit `http://localhost:8000`.

## ⚠️ Deployment note: Ollama does not fit free hosting tiers
`llama3.1:8b` needs roughly 5–6 GB of RAM just to load, plus CPU headroom for
inference. Render's and Railway's free/hobby tiers typically cap out around
512 MB–1 GB RAM, so **Ollama cannot run on the same free instance as the web
app**. Recommended alternative for a free-tier setup:

- Run **Ollama locally** (your own machine, or a home server) and expose it
  with a tunnel (e.g. `ngrok`, `cloudflared`) so `OLLAMA_HOST` in the
  deployed app's env points at your tunnel URL.
- Deploy only the **FastAPI app itself** (lightweight) to Render/Railway's
  free tier; it calls out to your tunneled Ollama instance for extraction.
- Alternative if you want everything hosted and free: swap in a smaller
  quantized model (e.g. a 1–3B instruct model) that might just fit a
  slightly larger free-tier container — but this trades off extraction
  quality, and most true free tiers still won't have enough RAM.
- If budget ever opens up even slightly, a small always-on VPS (e.g. a
  few dollars/month) running Ollama is the simplest fix and keeps
  everything free-API-wise (no paid LLM API calls, just paid compute).

None of this affects local development — `docker-compose`-style local
Ollama + local FastAPI works out of the box with the defaults above.

## Deploying to Vercel
This app is Vercel-ready: Vercel's Python runtime auto-detects the `app`
FastAPI instance in `app/main.py` with **zero extra config** — no separate
`api/` wrapper file needed, since `app/main.py` is already one of Vercel's
supported entrypoint filenames.

```bash
npm i -g vercel      # if you don't have the CLI
vercel                # deploy from the project root (or connect the repo via the dashboard)
```

What's already set up for you:
- **`vercel.json`** sets `maxDuration: 120` on the function, since a local
  LLM call over a tunnel can take a while — raise this (Pro plans allow up
  to 800s) if you increase `OLLAMA_TIMEOUT_SECONDS` beyond ~100s.
- **`.python-version`** pins Python 3.12.
- **`.vercelignore`** keeps local venvs, `.env`, and the dev SQLite file out of the deployment bundle.
- `app/config.py` automatically switches the SQLite cache to `/tmp/cache.db`
  when it detects Vercel's environment (`VERCEL=1`, set automatically) — the
  rest of the filesystem is read-only at runtime. **This cache is ephemeral**:
  it survives across requests on a warm instance (Vercel's Fluid compute keeps
  instances warm and can serve multiple requests on one), but resets on cold
  starts and new deployments. For a durable cache in production, swap the
  cache backend for something like Vercel KV/Upstash Redis instead of SQLite.
- `app/main.py` resolves the `templates/` and `static/` directories from its
  own file location rather than the process's working directory, since that's
  not guaranteed to be the project root under a serverless runtime.

**Set these in your Vercel project's Environment Variables** (Project →
Settings → Environment Variables) — see `.env.example` for the full list,
but at minimum:
- `OLLAMA_HOST` — Vercel Functions can't run Ollama themselves (see below),
  so this needs to point at a reachable tunnel/host running it.
- Any of the scoring weights, TTL, etc. you want to override from the defaults.

### The same Ollama constraint applies here
A Vercel Function can't run a persistent Ollama process — no GPU, no
long-lived daemon, and it doesn't get the ~5–6GB RAM `llama3.1:8b` needs
anyway (same constraint as the Render/Railway note above). Run Ollama
locally or on a small VPS, tunnel it (`ngrok`, `cloudflared`), and point
`OLLAMA_HOST` at that tunnel URL in your Vercel project's environment
variables. Only the lightweight FastAPI app itself deploys to Vercel.
