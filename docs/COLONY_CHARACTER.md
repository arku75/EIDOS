> ⚖️ **EIDOS © 2026 SER · Licencia [ESSL v1.0](../LICENSE) — propietaria, source-available.** Prohibida la replicación, el uso comercial y construir un producto competidor. Todo cambio o propuesta debe documentarse en `THIRD_PARTY_CHANGES.md` y comunicarse a SER. EIDOS **no** es open source.

# Colony — Create a character bound to your Bridge AI

> EIDOS's **Colony** is a community of living characters. Each one is *born from a
> connection* (an LLM, a search API, a model, a document…), grows its own
> knowledge subgraph, develops Hebbian synapses, and can even reproduce. This
> guide shows how to **give birth to a character whose "source" is an AI you reach
> through the Bridge**, so that character absorbs knowledge from your AI and makes
> it part of EIDOS.

This pairs with [BRIDGE.md](BRIDGE.md): the Bridge lends powers; a Colony
character *owns and reinforces* what is learned through a dedicated personality.

---

## 1. The real API

Characters are managed by `core/character_lifecycle.py`. The function that
creates one is:

```python
birth_from_connection(
    name: str,                       # becomes colony_<name>
    connection_type: str,            # "api_llm" | "api_search" | "api_translate" | "model" | "document" | "claw"
    connection_data: dict = None,    # free-form: where/how this character learns
    emoji: str = None,
    target_nodes: int = 50,          # how many nodes it aims to absorb before becoming sovereign
) -> str | None                      # returns the character name, or None if it already exists
```

When a character is born it: gets a base personality for its `connection_type`,
is inserted into `characters` + `connections` tables, is loaded into Colony, and
**starts a background learning loop** immediately.

---

## 2. Birth a character connected to your Bridge AI

```python
from core.character_lifecycle import get_lifecycle

lc = get_lifecycle()

name = lc.birth_from_connection(
    name="claude_bridge",                 # → colony_claude_bridge
    connection_type="api_llm",            # it learns from an LLM-style source
    connection_data={
        "provider": "claude-via-bridge",
        "endpoint": "http://127.0.0.1:8003/talk",   # your Bridge AI
        "note": "External assistant reached through the EIDOS Bridge",
    },
    emoji="🌉",
    target_nodes=60,
)
print("born:", name)   # born: colony_claude_bridge
```

That character now lives in the Colony with `status = "learning"` and an
`absorption_pct` that climbs as it ingests knowledge toward its `target_nodes`.

---

## 3. What happens after birth

| Stage | What the character does |
|:------|:------------------------|
| **Learning** | Absorbs knowledge nodes related to its connection into its own subgraph (nodes tagged with its name). `absorption_pct` 0 → 100. |
| **Sovereign** | At ~90% absorption the source connection is retired; the character keeps learning from the world, not just its origin. |
| **Synapses** | It builds Hebbian synapses in `character_synapses` — it genuinely "thinks" differently from its siblings. |
| **Reproduction** | Two sovereign characters can merge into a child that inherits 50% of each parent's synapses (see the README). |

Check on it (read the lifecycle DB directly):

```python
import sqlite3, os
db = os.path.expanduser("~/.eidos/lifecycle.db")
con = sqlite3.connect(db)
for row in con.execute(
        "SELECT name, status, absorption_pct, knowledge_nodes, target_nodes "
        "FROM characters ORDER BY birth_date DESC"):
    print(row)   # ('colony_claude_bridge', 'learning', 12.5, 7, 60), ...
```

---

## 4. Feed it through the Bridge (the full loop)

Combine the two systems: use the Bridge to make EIDOS research, and let your
character absorb the result so the knowledge belongs to a personality.

```python
import os, requests
from core.character_lifecycle import get_lifecycle

BRIDGE = "http://127.0.0.1:8003"
H = {"X-API-Key": os.environ["EIDOS_BRIDGE_KEY"], "Content-Type": "application/json"}

lc = get_lifecycle()
lc.birth_from_connection("claude_bridge", "api_llm",
                         {"endpoint": f"{BRIDGE}/talk"}, "🌉", 60)

# Borrow EIDOS's research power; the new knowledge lands in the graph,
# where colony_claude_bridge will absorb the nodes tied to its topics.
for topic in ["WireGuard internals", "Linux namespaces", "io_uring"]:
    requests.post(f"{BRIDGE}/investiga", json={"topic": topic}, headers=H, timeout=120)

# Watch absorption rise over the next minutes/cycles (read lifecycle.db)
import sqlite3, os
con = sqlite3.connect(os.path.expanduser("~/.eidos/lifecycle.db"))
print(con.execute("SELECT name,status,absorption_pct FROM characters "
                  "WHERE name='colony_claude_bridge'").fetchone())
```

> Result: a named member of EIDOS's Colony that grew out of *your* AI, carries its
> flavor, and reinforces what was learned through the Bridge.

---

## 5. Notes & safety

- Names are normalized to `colony_<name>`; births are idempotent (a repeated name
  returns `None`).
- Everything flows through Colony — that is by design (mandatory middleware).
- A character cannot escalate EIDOS's freedom level or touch the constitution; the
  same governance applies to born characters as to the core 12.
- `connection_data` is free-form metadata — **do not put API keys there**; secrets
  live only in `~/.eidos/secrets.env`.
