> ⚖️ **EIDOS © 2026 SER · Licencia [ESSL v1.0](../LICENSE) — propietaria, source-available.** Prohibida la replicación, el uso comercial y construir un producto competidor. Todo cambio o propuesta debe documentarse en `THIRD_PARTY_CHANGES.md` y comunicarse a SER. EIDOS **no** es open source.

# Known Issues & Honest Status

EIDOS is an **experimental, single‑developer research organism**. It is real and
it runs, but it is not a polished product. In the spirit of honesty (which is one
of its core rules), here is what is genuinely incomplete or broken — measured from
the code and databases, not marketing.

## Deep / structural gaps

### 1. It accumulates knowledge but rarely reuses it
- **~89% of knowledge nodes have `usage_count = 0`** — written once, never read
  again in reasoning. Only a few hundred nodes are used more than once.
- The machinery to increment reuse exists (`ganglia`, `rl_madmax`,
  `auto_research_claw`) but the everyday reasoning/search path does not exercise
  it. The graph behaves more like a warehouse than a working memory.
- **Fix in progress:** wire "do I already know this?" + `usage_count` increment
  into the main search/reason path.

### 2. It ingests text but doesn't always conclude
- The crawler follows sub‑links and stores text, but does not write **its own
  synthesized conclusion** back to the graph at the end of a crawl.
- A comprehension engine exists (`eidos_deep_comprehension.comprehend()`) but is
  **not yet hooked** to the end of `colony_studier` / `eidos_deep_research`.
- No cross‑source **contradiction detection** yet.

### 3. Three search paths are not unified
- (A) chat → Colony → `eidos_action_executor` (recently fixed to search **and**
  read results), (B) the Bridge fast‑path (still opens Google), (C)
  `eidos_deep_research` (DuckDuckGo‑lite crawl). They overlap and behave slightly
  differently. Planned: a single `research(query, mode=quick|deep, visible)` API.

### 4. The Body (BOM) is real but nascent
- ~41 motor‑memory rows and ~51 learned skills exist, but the BOM has **only run
  in dry mode** — it has never learned from a full **real** GUI session. Real mode
  is gated behind `EIDOS_BOM=1` + owner present.

### 5. Autonomous learning loop can stall
- The night/autonomous study loop runs slowly (≈1 concept per cycle), hits LLM
  rate limits, and has stalled for days at a time. "Alive" currently means
  "services up", not "continuously compounding".

## Practical / smaller issues

- **Result parsing is fragile**: `eidos_deep_research._http_get` uses a single
  User‑Agent, no captcha/challenge detection, no multi‑engine fallback. It works
  today (DuckDuckGo‑lite responds) but is a single point of failure.
- **Graph composition is inflated**: roughly half the nodes are EIDOS indexing its
  **own source code** + man‑page/dictionary entries; some `research:duckduckgo`
  nodes are low quality. The 39k figure overstates "world knowledge".
- **Local LLMs are slow**: developed on a laptop with an AMD iGPU that does **not**
  accelerate inference. Cloud (DeepSeek/Groq) is primary; Ollama is a slow
  fallback.
- **The repo was heavy**: the project tree contains large vendored third‑party
  material and private dev notes that are excluded from this public release via
  `.gitignore`. If you clone the full dev tree elsewhere, mind the size.
- **LLMs hallucinate internal names**: when asking an LLM about EIDOS's own code,
  always verify function/file names against the source — they are often invented.

## SQLite / runtime gotchas (will bite you)

- **Never** `sqlite3.connect()` directly in `core/` — use `from core.db import
  get_conn` (applies `busy_timeout`, WAL, mmap). Otherwise: `database is locked`.
- **Never** set `max_tokens`/`num_predict` on LLM calls — by design EIDOS runs them
  unbounded; setting limits truncates responses.
- **Don't chain‑restart the Bridge** — each start rebuilds the in‑memory graph
  (~5 cores for minutes); under load it can cascade.
- **ChromaDB** ≥1.5 Python client has a thread‑safety regression — use the bundled
  1.4.4 CLI microservice (port 8767).

## Roadmap

See the [README roadmap](../README.md#roadmap). Top priorities, in order: reuse
wiring (#1), crawl→conclusion (#2), unify search (#3), then real BOM sessions (#4).

---

*This document is intentionally candid. If you hit something not listed here,
please open an issue — honest bug reports are the most useful contribution.*
