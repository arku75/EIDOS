# Glossary

Key terms used throughout EIDOS. Each is a real, working component — not a
metaphor.

## Core concepts

| Term | Definition |
|:-----|:-----------|
| **EIDOS** | The entire autonomous system. Not an LLM. A persistent entity with its own knowledge graph, memory, body, and identity. LLMs are its voice organs — the brain is the graph. |
| **knowledge graph** | A network of ~39,000 concepts and ~169,000 edges stored in `evolution_brain.db` (SQLite). EIDOS thinks by traversing this graph, not by predicting tokens. Nodes are concepts it has learned. Edges are relationships between them. |
| **brain-lite** | The deterministic central loop. Every action flows through it: `perceive → decide (EXECUTE/MODIFY/DISCARD) → act → verify → learn`. It decides; LLMs only advise. |
| **ChromaDB** | Vector database (~6,200 embeddings). The "semantic layer" of memory — find concepts by meaning, not just keywords. Runs as a microservice on port 8767. |

## Bridge & tools

| Term | Definition |
|:-----|:-----------|
| **Bridge** | A REST API on `127.0.0.1:8003`. The main door into EIDOS. Any external AI/script can call it with JSON and an API key to use EIDOS's powers: browse the web, search 61 engines, recall memory, reason over the graph, see the screen, move the mouse. Every interaction teaches EIDOS. |
| **X-API-Key** | The single header (`X-API-Key: <key>`) that authenticates ALL Bridge requests except `/health`. Set via `EIDOS_BRIDGE_KEY` in `~/.eidos/secrets.env`. |
| **/talk** | The main Bridge endpoint. General reasoning through Colony + brain-lite. |
| **/investiga** | Bridge endpoint that triggers deep research: searches sources, reads them, synthesizes an answer, AND persists new knowledge to the graph. |

## Colony

| Term | Definition |
|:-----|:-----------|
| **Colony** | A community of AI characters living inside EIDOS. **Mandatory middleware**: everything passes through at least one character. Provides distributed intelligence — different perspectives on every problem. |
| **Character** | A named agent inside Colony. Born from an external connection (LLM, search API, document…). Has its own knowledge subgraph, Hebbian synapses, personality traits, and a learning loop. There are 12 core characters + any you create. |
| **Absorption** | How a character learns from its source connection. Measured as `absorption_pct` (0% → 100%). At ~90%, the character becomes **sovereign** (the source connection is retired; it keeps learning from the world). |
| **Sovereign** | A character whose source connection has been absorbed. It continues learning autonomously and is eligible to **reproduce**. |
| **Reproduction** | Two sovereign characters can merge into a child. The child inherits 50% of each parent's Hebbian synapses, merged personality traits, and combined knowledge nodes. Real data inheritance in SQLite — not a simulation. |
| **Hebbian synapses** | Weighted connections between concepts, per character. Characters think differently because their synapses are different. Successful paths are reinforced; unused ones decay. |

## Body (physical)

| Term | Definition |
|:-----|:-----------|
| **BOM** | Body Operating Module. The closed loop `perceive → decide → act → verify → learn` that gives EIDOS physical agency. |
| **Bezier mouse** | Physical mouse control via `xdotool`. Movement follows natural cubic bezier curves with micro-pauses and overshoot — indistinguishable from a human. **OFF by default** (gated behind `EIDOS_BOM=1` + owner present). |
| **Dry mode** | The default for all mouse/screen actions. EIDOS *plans* the movement but does **not** execute it. Only switches to real mode with explicit human approval. |
| **Propioception** | EIDOS knows where its hand (cursor) is, what window it's touching, and its screen geometry. `body.py`. |

## Governance & safety

| Term | Definition |
|:-----|:-----------|
| **constitution.toml** | Hash-verified, immutable document. Defines what EIDOS can never do: delete `.git/`, force-push, escalate its own freedom, exfiltrate data, open ports, read secrets, or modify itself unsupervised. Cannot be changed by EIDOS or any automated process. |
| **owner_policy.toml** | Additional governance rules toggled only by SER (the human owner). |
| **Master Protocol** | The teacher-student mode. With `EIDOS_MASTER_MODE=1` and SER present, EIDOS asks SER first before acting. SER's answers are stored with confidence 0.95 as ground truth. |
| **Guardian** | One of six autonomous safety watchdogs: RAM Guardian (OOM prevention), Git Guardian (never pushes), Sentinel (anomaly detection), Phoenix (failure recovery), Mirror (sandbox), Watchdog (service supervision). |
| **SafetyGuard** | The runtime safety layer that validates every action BEFORE the BOM executes it. All actions are checked against a deny-list. |

## Memory system

| Term | Definition |
|:-----|:-----------|
| **Working memory** | Ring buffers in Python dicts — the immediate, volatile layer. |
| **Episodic memory** | Events and session logs in `episodic.db` (~800 episodes). |
| **Procedural memory** | Skills and motor patterns in `evolution_brain.db.motor_memory`. |
| **Vector memory** | ChromaDB (~6,200 embeddings). Semantic recall — find by meaning, not keyword. |

## Other

| Term | Definition |
|:-----|:-----------|
| **Study queue** | A priority queue of topics EIDOS studies autonomously. Add with `eidos study add "topic"`. Processed by the free/autonomous loop. |
| **Night study** | Background process that has processed 6,990 system tools and learned 4,935 from man pages. Runs every 30 minutes. |
| **Skill** | A generalized concept EIDOS has extracted from a specific action (e.g., "log into a website"). Once learned as a skill, EIDOS applies it to any new site without a per-site script. |
| **SER** | The human creator and owner of EIDOS. Directs its development, teaches it, and defines its constitution. |
