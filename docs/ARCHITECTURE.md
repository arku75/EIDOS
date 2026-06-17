# Architecture — How EIDOS works from the inside

> Real architecture verified against the running code (June 2026).

---

## Component map

```
                        SER (human owner)
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
         CLI (eidos)    Bridge API     Web Panel
         (22 commands)  (:8003)        (:8080)
              │              │              │
              └──────────────┼──────────────┘
                             │
                        ┌────▼────┐
                        │ COLONY  │  ← MANDATORY middleware
                        │ 12 chars│     EVERYTHING passes here
                        └────┬────┘
                             │
                     ┌───────▼───────┐
                     │  brain-lite   │  ← deterministic loop
                     │  (DECIDES)    │     EXECUTE/MODIFY/DISCARD
                     └───────┬───────┘
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
    ┌──────────┐     ┌──────────────┐    ┌──────────┐
    │  Memory  │     │   Research   │    │  Body    │
    │Graph+Chr │     │ web+browser  │    │ mouse+OCR│
    └──────────┘     └──────────────┘    └──────────┘
          │                  │                  │
          └──────────────────┼──────────────────┘
                             │
                    ┌────────▼────────┐
                    │ 6 GUARDIANS    │
                    │ RAM/Git/Phoenix│
                    │ Mirror/Watchdog│
                    │    + Healer     │
                    └─────────────────┘
```

---

## Key services (12 systemd user units)

| Service | Port | Role |
|:--------|:-----|:-----|
| `eidos-bridge` | **8003** | Main REST API — 40+ endpoints (talk, research, browse, vision, GUI…) |
| `eidos-webpanel` | **8080** | Dashboard + neural graph + system monitor |
| `eidos-trinity` | **8001** | Service coordination & orchestration |
| `eidos-chroma-http` | **8767** | ChromaDB vector database (Rust CLI) |
| `eidos-brain-lite` | — | Deterministic central decision loop |
| `eidos-daemon` | — | Lifecycle + Colony characters |
| `eidos-vivo` | — | Autonomous vital cycle |
| `eidos-healer` | — | Health monitor + auto-repair |
| `eidos-telegram` | — | Telegram bot polling |
| `eidos-tunnel-in` | — | Inbound tunnel (Mac → Kali) |
| `eidos-tunnel-out` | — | Outbound tunnel (Kali → Mac) |

---

## Data flow (a complete action end-to-end)

```
1. Bridge receives POST /talk {"text": "investigate n8n"}
        │
2. Colony: characters discuss (Analyst suggests research, Coder adds context)
        │
3. brain-lite: DECIDES → EXECUTE (mode: research)
        │
4. Research pipeline:
   ├── eidos_deep_research.crawl() → DuckDuckGo-lite + BeautifulSoup
   ├── eidos_active_research.research_now() → DeepSeek synthesizes
   └── eidos_quality_gate.evaluate() → admit? quality_score > 0.5?
        │
5. Persist to graph:
   ├── knowledge_nodes (SQLite) → new concept
   └── ChromaDB → vector embedding (for semantic recall)
        │
6. Guardians verify:
   ├── Mirror: did the new node pass quality gate?
   ├── Git Guardian: snapshot the graph change
   └── Watchdog: is the Bridge still responding?
        │
7. Response to caller: {"answer": "...", "learned": true, "sources": [...]}
```

---

## Memory architecture (4 layers)

| Layer | Storage | Contents |
|:------|:--------|:---------|
| 1. Working | Python dicts | Ring buffers, immediate |
| 2. ChromaDB | Vector DB (:8767) | ~6,200 embeddings (nomic-embed-text) |
| 3. Episodic | `episodic.db` | ~800 episodes, session logs |
| 4. Procedural | `evolution_brain.db` | Skills, motor patterns |

---

## Databases

| File | Contents |
|:-----|:---------|
| `~/.eidos/evolution_brain.db` | **Primary** — 38,701 nodes + 168,818 edges + motor memory |
| `~/.eidos/lifecycle.db` | Characters: birth, absorption, reproduction, genealogy |
| `~/.eidos/self.db` | Self-model: 25K+ events, 9K+ self_states, 26K+ meta-thoughts |
| `~/.eidos/episodic.db` | ~800 episodes, session logs |

**Golden rule**: Never `sqlite3.connect()` directly. Always use
`from core.db import get_conn` (applies `busy_timeout=30000`, WAL,
`mmap_size=256M`, `cache_size=-40000`).

---

## Extension points (for contributors)

| What | Where |
|:-----|:------|
| Add a Bridge endpoint | `core/bridge_to_eidos.py` (Flask `@app.route`) |
| Add a Colony character | `core/character_lifecycle.py` → `birth_from_connection()` |
| Add a skill | `core/eidos_skills.py` → `learn_skill()` |
| Add a search engine | `core/eidos_action_executor.py` → `_SEARCH_ENGINES` dict |
| Add a Guardian | New file in `core/`, register in `eidos-healer` |
| Add a study source | `core/eidos_deep_research.py` → `_SKIP_DOMAINS` + parser |

---

## Operational limits (from `constitution.toml`)

| Limit | Value |
|:------|:------|
| Files modified per cycle | 3 max |
| Lines per file edit | 50 max |
| New files per hour | 5 max |
| Anti-loop | ring buffer 32, 3 reps in 30s → DISCARD |
| Heavy ops | subprocess worker only (transcription/Playwright/crawl) |
