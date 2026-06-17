# The Bridge — Lend any AI the powers of EIDOS

> **One sentence:** A plain language model can only produce text. Point it at the
> EIDOS Bridge and suddenly it can *browse the web, search 61 engines, see the
> screen, act on a GUI, remember across sessions and reason over a 39k‑node
> graph* — using EIDOS's body and brain. And **every interaction teaches EIDOS**:
> what the external AI asks and what comes back is persisted to EIDOS's knowledge
> graph. The relationship is symbiotic.

This is the most important door into EIDOS. It is exactly how the maintainers use
it: an external assistant (e.g. Claude) connects to the Bridge and **acts as
EIDOS** — driving its real capabilities — while EIDOS records and learns from the
session.

```
┌─────────────────────┐        HTTP (JSON)        ┌──────────────────────────────┐
│  Any AI / LLM / app │  ───────────────────────► │  EIDOS Bridge  (port 8003)   │
│  "powerless":       │   POST /talk /browse ...  │  ─ Colony (12 characters)    │
│  text in, text out  │ ◄───────────────────────  │  ─ brain‑lite (decides)      │
│                     │      results + learning   │  ─ body (mouse/vision/web)   │
└─────────────────────┘                           │  ─ graph (39k nodes) + memory│
        the borrower                              └──────────────────────────────┘
                                                    EIDOS keeps what it learned
```

---

## 1. Start the Bridge and set a key

```bash
# In ~/.eidos/secrets.env
echo 'EIDOS_BRIDGE_KEY=choose-a-long-random-string' >> ~/.eidos/secrets.env

# Start (the Bridge listens on 127.0.0.1:8003 only — never expose it raw to the internet)
eidos start                 # or: systemctl --user start eidos-bridge

# Verify (the only endpoint that needs no key)
curl http://127.0.0.1:8003/health
# {"status":"ok","graph_nodes":38701,...}
```

Every endpoint except `/health`, `/dashboard`, `/panel` requires the header
`X-API-Key: <EIDOS_BRIDGE_KEY>`.

---

## 2. The powers you are lending

| Endpoint | Method | What the borrower AI gains |
|:---------|:------:|:---------------------------|
| `/talk` | POST | General reasoning through Colony + brain‑lite (the main door) |
| `/browse` | POST | **Navigate the real web** (open a URL, read it) |
| `/investiga` | POST | **Deep research** — searches, reads sources, synthesizes, learns |
| `/semantic` | POST | **Vector recall** — ask EIDOS's memory (ChromaDB) by meaning |
| `/ask_brain` | POST | Direct lookup in the knowledge graph (concept → definition) |
| `/reason` | POST | Run the graph reasoner over a question |
| `/vision` | POST | **See the screen** (OCR + VLM) |
| `/gui` | POST | **Act on a GUI** through the Body (gated by `EIDOS_BOM=1` + SER present) |
| `/study` | POST | Queue a topic for autonomous learning |
| `/graph/search` | POST | Search nodes/edges in the graph |
| `/status` | GET | Full system state (services, graph, Colony, memory) |

> The borrower never needs tools of its own. It only speaks JSON; EIDOS provides
> the hands, eyes, web and memory.

---

## 3. Quick example (curl)

```bash
KEY="your-EIDOS_BRIDGE_KEY"

# Make EIDOS research something for your AI and LEARN it on the way
curl -s -X POST http://127.0.0.1:8003/investiga \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"topic":"how WireGuard handshakes work"}'

# Make EIDOS open and read a page for your AI
curl -s -X POST http://127.0.0.1:8003/browse \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"url":"https://www.kernel.org/doc/html/latest/"}'

# Ask EIDOS's memory (semantic recall)
curl -s -X POST http://127.0.0.1:8003/semantic \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"query":"what do I already know about nmap?"}'
```

---

## 4. Full example — a "powerless" AI that uses EIDOS as its body & memory

This is a complete loop: an external LLM that has **no tools at all** gets the
power to research, remember and act — by delegating to EIDOS. Save as
`borrower_agent.py`.

```python
import os, requests

BRIDGE = "http://127.0.0.1:8003"
KEY = os.environ["EIDOS_BRIDGE_KEY"]
H = {"X-API-Key": KEY, "Content-Type": "application/json"}

def eidos(endpoint: str, **payload):
    """Call one of EIDOS's powers."""
    r = requests.post(f"{BRIDGE}{endpoint}", json=payload, headers=H, timeout=120)
    r.raise_for_status()
    return r.json()

# Your external LLM produced this plan (pseudo): it cannot browse, so it borrows.
def handle(user_goal: str):
    # 1) Does EIDOS already know it? (free, instant — reuse its memory)
    known = eidos("/semantic", query=user_goal)
    if known.get("answer"):
        return known["answer"]

    # 2) It doesn't — lend it the power to research the live web and LEARN it
    res = eidos("/investiga", topic=user_goal)     # EIDOS searches, reads, synthesizes
    #    ↑ this ALSO writes new nodes into EIDOS's graph: next time, step 1 hits.

    # 3) Optionally make EIDOS open a specific source and read it
    if res.get("sources"):
        page = eidos("/browse", url=res["sources"][0])
        res["first_source_text"] = page.get("text", "")[:2000]
    return res

if __name__ == "__main__":
    print(handle("explain how systemd socket activation works"))
```

What just happened:

1. The borrower asked EIDOS's **memory** first — no cost if already learned.
2. When unknown, it borrowed EIDOS's **web + research** powers.
3. EIDOS **kept the new knowledge** in its graph (`source = research:*`), so the
   borrower — or any future caller — gets it instantly next time.

That is the symbiosis SER designed: *the AI gains a body and a memory; EIDOS gains
experience.*

---

## 5. Acting on the real GUI (advanced, gated)

`/gui` and the Body (mouse/keyboard/vision) are **off by default**. Real screen
control requires `EIDOS_BOM=1` **and** the human owner (SER) present — this is a
hard safety rule (see `constitution.toml`). In dry mode EIDOS *plans* the
movements but does not execute them.

```bash
# Only with SER present:
EIDOS_BOM=1 curl -s -X POST http://127.0.0.1:8003/gui \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"instruction":"open the terminal and type: uname -a"}'
```

---

## 6. How EIDOS learns from the Bridge

Every meaningful call leaves a trace:

- `/investiga`, `/browse`, `/talk` persist new **knowledge nodes** through the
  quality gate (`eidos_quality_gate`) into `evolution_brain.db`.
- Sessions are recorded in **episodic memory** (`episodic.db`).
- Vectors go to **ChromaDB** so future `/semantic` recall improves.

> Tip: pair this with a **Colony character bound to your AI** so the learning is
> owned and reinforced by a dedicated personality — see
> [COLONY_CHARACTER.md](COLONY_CHARACTER.md).

---

## 7. Security

- The Bridge binds to **127.0.0.1 only**. To reach it from another machine use an
  SSH tunnel, never a public bind.
- Keep `EIDOS_BRIDGE_KEY` in `~/.eidos/secrets.env` (`chmod 600`). Never commit it.
- The constitution forbids EIDOS from opening listening ports or exfiltrating data
  on its own; the Bridge is started by you, the owner.
