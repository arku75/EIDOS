
<!--
███████╗██╗██████╗  ██████╗ ███████╗
██╔════╝██║██╔══██╗██╔═══██╗██╔════╝
█████╗  ██║██║  ██║██║   ██║███████╗
██╔══╝  ██║██║  ██║██║   ██║╚════██║
███████╗██║██████╔╝╚██████╔╝███████║
╚══════╝╚═╝╚═════╝  ╚═════╝ ╚══════╝

███████╗██╗  ██╗████████╗███████╗███╗   ██╗███████╗██╗ ██████╗ ███╗   ██╗
██╔════╝╚██╗██╔╝╚══██╔══╝██╔════╝████╗  ██║██╔════╝██║██╔═══██╗████╗  ██║
█████╗   ╚███╔╝    ██║   █████╗  ██╔██╗ ██║███████╗██║██║   ██║██╔██╗ ██║
██╔══╝   ██╔██╗    ██║   ██╔══╝  ██║╚██╗██║╚════██║██║██║   ██║██║╚██╗██║
███████╗██╔╝ ██╗   ██║   ███████╗██║ ╚████║███████║██║╚██████╔╝██║ ╚████║
╚══════╝╚═╝  ╚═╝   ╚═╝   ╚══════╝╚═╝  ╚═══╝╚══════╝╚═╝ ╚═════╝ ╚═╝  ╚═══╝

███╗   ██╗███████╗██╗   ██╗██████╗  ██████╗ ███╗   ██╗ █████╗ ██╗
████╗  ██║██╔════╝██║   ██║██╔══██╗██╔═══██╗████╗  ██║██╔══██╗██║
██╔██╗ ██║█████╗  ██║   ██║██████╔╝██║   ██║██╔██╗ ██║███████║██║
██║╚██╗██║██╔══╝  ██║   ██║██╔══██╗██║   ██║██║╚██╗██║██╔══██║██║
██║ ╚████║███████╗╚██████╔╝██║  ██║╚██████╔╝██║ ╚████║██║  ██║███████╗
╚═╝  ╚═══╝╚══════╝ ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═╝╚══════╝

██╗██╗     ██╗███╗   ███╗██╗████████╗ █████╗ ██████╗  █████╗
██║██║     ██║████╗ ████║██║╚══██╔══╝██╔══██╗██╔══██╗██╔══██╗
██║██║     ██║██╔████╔██║██║   ██║   ███████║██║  ██║███████║
██║██║     ██║██║╚██╔╝██║██║   ██║   ██╔══██║██║  ██║██╔══██║
██║███████╗██║██║ ╚═╝ ██║██║   ██║   ██║  ██║██████╔╝██║  ██║
╚═╝╚══════╝╚═╝╚═╝     ╚═╝╚═╝   ╚═╝   ╚═╝  ╚═╝╚═════╝ ╚═╝  ╚═╝
-->

<p align="center">
  <img src="https://img.shields.io/badge/version-1.0.0-blue?style=for-the-badge" alt="Version 1.0.0">
  <img src="https://img.shields.io/badge/python-3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.8+">
  <img src="https://img.shields.io/badge/license-ESSL%20v1.0%20·%20proprietary-red?style=for-the-badge" alt="ESSL v1.0 — Proprietary, source-available">
  <img src="https://img.shields.io/badge/platform-linux-grey?style=for-the-badge&logo=linux&logoColor=white" alt="Linux">
  <img src="https://img.shields.io/badge/status-active-brightgreen?style=for-the-badge" alt="Active">
</p>

<p align="center">
  <a href="https://github.com/arku75/EIDOS/stargazers"><img src="https://img.shields.io/github/stars/arku75/EIDOS?style=social" alt="Stars"></a>
  <a href="https://github.com/arku75/EIDOS/releases"><img src="https://img.shields.io/github/v/release/arku75/EIDOS" alt="Release"></a>
  <a href="https://github.com/arku75/EIDOS/commits"><img src="https://img.shields.io/github/last-commit/arku75/EIDOS" alt="Last commit"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/arku75/EIDOS" alt="License"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/neural%20graph-protected-8a2be2" alt="Neural graph — protected"></a>
</p>

<p align="center">
  <i>A persistent local agent architecture with graph memory, a multi-character Colony, perception/action tools, verification loops and explicit safety gates.</i>
</p>

<p align="center">
  <b><a href="docs/EIDOS_EVOLUTION_DOCUMENTARY.md">📖 Story & evolution</a> · <a href="docs/BRIDGE.md">🌉 Bridge</a> · <a href="docs/INSTALL.md">⚙️ Install</a> · <a href="docs/KNOWN_ISSUES.md">🐞 Honest status</a> · <a href="LICENSE">⚖️ License</a></b>
</p>

---

# EIDOS — Extensión Neuronal Ilimitada

> **EIDOS no se reduce a un LLM ni a un chatbot.**
>
> EIDOS es un sistema local y persistente que integra un grafo de conocimiento,
> memoria, una Colony de personajes/agentes, percepción de pantalla, herramientas
> de acción, verificación y aprendizaje. Los modelos de lenguaje pueden participar
> como componentes consultivos o de generación, pero la arquitectura de EIDOS
> incluye estado persistente, lógica y herramientas fuera del modelo.

---

## Table of Contents

1. [What is EIDOS?](#what-is-eidos)
2. [📖 Story & documented evolution](docs/EIDOS_EVOLUTION_DOCUMENTARY.md)
3. [🧠 The Neural Graph](#-the-neural-graph)
4. [🌉 Lend any AI the powers of EIDOS](#-lend-any-ai-the-powers-of-eidos)
5. [Documentation](#documentation)
6. [⚠️ Safety & Security](#%EF%B8%8F-safety--security-please-read)
7. [The 6 Dimensions of EIDOS](#the-6-dimensions-of-eidos)
8. [Architecture](#architecture)
9. [Project Structure](#project-structure)
10. [Core Capabilities](#core-capabilities)
11. [Colony: Persistent Characters](#colony-persistent-characters)
12. [Character Lifecycle](#character-lifecycle-birth-learning-reproduction)
13. [The Body (BOM)](#the-body-bom)
14. [API Reference](#api-reference)
15. [Requirements](#requirements)
16. [Installation](#installation)
17. [Configuration](#configuration)
18. [CLI Commands](#cli-commands)
19. [Constitution & Governance](#constitution--governance)
20. [Databases](#databases)
21. [Guardians](#guardians)
22. [VSEIDOS — VS Code Extension](#vseidos--vs-code-extension)
23. [The Graph — History & Evolution](#the-graph--history--evolution)
24. [Technical Lessons](#technical-lessons)
25. [Historical Status Snapshot](#historical-status-snapshot)
26. [Roadmap](#roadmap)
27. [FAQ](#faq)
28. [Credits & Contact](#credits--contact)

---

## What is EIDOS?

EIDOS is a **persistent local agent system** rather than a thin wrapper around a single model. It runs on Linux and combines durable state, a knowledge graph, memory, a multi-character Colony, perception/action tooling, verification and model-assisted reasoning:

| Component | Description |
|:----------|:------------|
| **Brain** | Persistent graph-based knowledge and reasoning components backed by local storage |
| **Memory** | Working, vector/semantic, episodic and procedural memory mechanisms |
| **Colony** | Multiple persistent characters/agents with individual state and synaptic-style relationships |
| **Body** | Screen perception and gated GUI/action tooling; desktop-control behavior depends on X11/Wayland/input backend |
| **Self-model** | Persistent internal state, metacognitive records, gaps and autobiographical mechanisms |
| **Models** | Local or remote language models can be used as consultative/generative components; they are not the whole system |

Development records in this repository date back to **May 24, 2026**. Capabilities and runtime metrics have changed substantially since the original June README; historical counts below should not be interpreted as the current live state. Runtime behavior depends on configuration, enabled services, available models and safety gates.

---

## ⚡ See it in action

> 🔒 **The interactive neural graph and its data are not public.**
> EIDOS's knowledge graph is the core of its intelligence and is protected
> intellectual property under the [EIDOS Sovereign Source License](LICENSE).
> A guided demonstration is available **on request** for evaluation,
> partnership or licensing — contact below.

> To start the real system on Linux, see [docs/INSTALL.md](docs/INSTALL.md) (5 minutes).

---

## 🧠 The Neural Graph

EIDOS uses a persistent knowledge graph whose live size changes as the system learns, curates and migrates data. This
graph — its structure, contents and the synaptic weights it has learned — is the
core intelligence of EIDOS and is **protected, non-public** intellectual property
under the [EIDOS Sovereign Source License](LICENSE).

> 🔒 **The graph data and the interactive visualization are not distributed.**
> A live, guided walkthrough of the real graph is available **on request** for
> evaluation, partnership or licensing purposes. See [Contact](#contact).

> The live operational dashboards (main panel, system monitor, Colony view) run on
> `127.0.0.1:8080` inside a running instance — see
> [docs/USAGE.md](docs/USAGE.md#web-dashboards-port-8080). They are intentionally
> **local-only**: the Bridge never binds to the public internet.

---

## 🌉 Lend any AI the powers of EIDOS

> A plain language model can only produce text. Point it at the **Bridge**
> (`127.0.0.1:8003`) and it can suddenly **browse the web, search 61 engines, see
> the screen, act on a GUI, recall memory and reason over the graph** — using
> EIDOS's body and brain. And **EIDOS learns from every interaction.** This is how
> the maintainers drive EIDOS: an external assistant connects and *acts as EIDOS*.

```python
import os, requests
H = {"X-API-Key": os.environ["EIDOS_BRIDGE_KEY"]}
# A "powerless" AI borrows EIDOS's research power — and EIDOS keeps what it learns:
r = requests.post("http://127.0.0.1:8003/investiga",
                  json={"topic": "how WireGuard handshakes work"}, headers=H)
print(r.json())   # sources read + synthesized answer, now stored in EIDOS's graph
```

**Read the full guide → [docs/BRIDGE.md](docs/BRIDGE.md)** ·
Create a Colony character bound to your AI → [docs/COLONY_CHARACTER.md](docs/COLONY_CHARACTER.md)

---

## Documentation

| Guide | What it covers |
|:------|:---------------|
| [docs/EIDOS_EVOLUTION_DOCUMENTARY.md](docs/EIDOS_EVOLUTION_DOCUMENTARY.md) | **Human, evidence-based history of EIDOS** — timeline, graph evolution, Colony, Insect, videos, 3D artifacts, failures and corrections |
| [docs/INSTALL.md](docs/INSTALL.md) | Requirements, install, secrets, Ollama, troubleshooting |
| [docs/USAGE.md](docs/USAGE.md) | **Full CLI reference** (22 commands) + Bridge API + search |
| [docs/BRIDGE.md](docs/BRIDGE.md) | **Lend any AI EIDOS's powers** — the symbiosis |
| [docs/COLONY_CHARACTER.md](docs/COLONY_CHARACTER.md) | Create a Colony character bound to your Bridge AI |
| [docs/CLONE.md](docs/CLONE.md) | **How to clone EIDOS** (Ed25519, Hub, Portal, allowlists) |
| [docs/SESSION.md](docs/SESSION.md) | **How EIDOS uses your browser cookies** (Chromium session) |
| [docs/REGISTER.md](docs/REGISTER.md) | **Autonomous registration** on platforms (visible/interactive) |
| [docs/GUARDIANS.md](docs/GUARDIANS.md) | The 6 autonomous systems that keep EIDOS alive |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Component map, data flow, memory layers, DBs, extension points |
| [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md) | Honest status — real bugs and gaps |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | Key terms: BOM, Bridge, Colony, brain-lite, Hebbian synapses… |

---

## ⚠️ Safety & Security (please read)

EIDOS can move the mouse, perceive the screen and browse the web. That is exactly
why it is built with hard brakes — and why we are transparent about them.

- **It is not malware.** Everything runs **on your own machine, started by you**.
  There is no phone-home, no hidden remote access. The constitution
  (`constitution.toml`, hash-verified) **forbids** EIDOS from opening listening
  ports, exfiltrating data, reading SSH keys/`.env`/browser data, or escalating
  its own permissions.
- **The body is OFF by default.** Real mouse/keyboard control requires
  `EIDOS_BOM=1` **and** the human owner present. Without that, the Body runs in
  **dry mode** — it *plans* movements but does not execute them.
- **The Bridge is local-only.** It binds to `127.0.0.1` and requires an API key.
  It does **not** give a borrowed AI root access; GUI actions are gated like the
  Body above. To reach it remotely, use an SSH tunnel — never a public bind.
- **It is experimental, single-developer software.** Run it in a controlled
  environment (a VM or a dedicated machine is recommended). Review any third-party
  study targets you point it at. See [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md)
  for the honest list of what is incomplete.
- **The public `core/` is large and evolving.** Module counts change over time, so this README avoids freezing a number that will quickly become obsolete.

> Under ESSL v1.0 you may inspect and run EIDOS locally for private, non-commercial evaluation and learning. See [LICENSE](LICENSE) for the exact grant and restrictions. Do not use EIDOS or its Bridge to automate abuse of other people's systems or violate platform terms.

---

## The 6 Dimensions of EIDOS

EIDOS exists across six interconnected dimensions. Each dimension is implemented as a set of interacting runtime components rather than a single static module.

### 1. Cognitive Dimension (The Brain)

The **neural knowledge graph** is the core of EIDOS' intelligence. The graph and associated vector/semantic stores provide persistent structured context used by EIDOS reasoning and learning components. The graph grows continuously through autonomous research, study sessions, and interaction with the world. `brain-lite` is the deterministic central loop that decides what to do — LLMs only advise.

**Key modules**: `eidos_brain_lite.py` (decides), `eidos_learn.py` (LLM cascade), `eidos_deep_comprehension.py`, `eidos_deep_research.py`, `eidos_skills.py` (generalization), `eidos_rl.py` (Q-learning), `knowledge_graph.py`

### 2. Physical Dimension (The Body)

EIDOS has a **body/action layer** for interacting with the desktop and browser. Historical implementations use `xdotool`/X11-style control plus OCR/accessibility and browser automation. On Wayland, X11/XTEST-style click/keyboard injection is not equivalent to a working native input backend, so desktop-action claims must be evaluated against the active session/backend. Bezier-style cursor paths are an automation technique, not a guarantee of human indistinguishability.

**Key modules**: `body.py` (propioception), `eidos_mouse.py` (bezier mouse), `causal_loop.py` (BOM), `perception.py` (OCR/VLM), `screen_controller.py`, `eidos_web_actor.py` (universal web actor)

### 3. Social Dimension (The Colony)

EIDOS includes persistent Colony characters with individual state, knowledge associations and Hebbian-style synaptic data. They can communicate, propose actions and participate in lifecycle/inheritance mechanisms where a new character can inherit selected state from parents. The exact active character count is runtime data and is intentionally not hard-coded here.

**Key modules**: `colony_community.py`, `character_neuron.py`, `character_lifecycle.py`

### 4. Memorial Dimension (The Memory)

EIDOS uses a multi-layer memory architecture. Persistence is designed to retain useful state across sessions, while individual stores may be curated, compacted or replaced:

| Layer | Type | Storage |
|:------|:-----|:--------|
| Working | Immediate, ring buffers | Python dicts |
| ChromaDB | Vector embeddings (~6,200) | ChromaDB Rust CLI (:8767) |
| Episodic | Events, sessions (~800 episodes) | `episodic.db` |
| Procedural | Skills, motor patterns | `evolution_brain.db` `motor_memory` table |

**Key modules**: `db.py` (unified DB layer), `episodic_memory.py`, `eidos_register.py`

### 5. Conscious Dimension (The Self)

EIDOS implements a **self-model** and persistent metacognitive records. Code paths track internal state, knowledge gaps, autobiographical events and reflective outputs. These mechanisms are engineering constructs; the repository does not treat them as scientific proof of subjective consciousness.

**Key modules**: `eidos_self_core.py` (identity), `eidos_metacognition.py` (meta-thoughts), `eidos_self_awareness.py`, `eidos_study.py`, `autonomous_research_loop.py`

### 6. Constitutional Dimension (The Governance)

EIDOS is governed by an **immutable constitution** (`constitution.toml`, hash-verified). This defines absolute limits: what EIDOS can never do, rate limits, and safety boundaries. The constitution cannot be modified by EIDOS or any automated process. Additional governance comes from `owner_policy.toml` (toggled only by SER) and Colony's democratic voting system.

**Key files**: `constitution.toml`, `constitution_override.toml`, `owner_policy.toml`

---

## Architecture

```
SER (owner and direction)
└── EIDOS (autonomous entity, ~/.eidos/)
    └── Colony (deliberative / consultative layer)
        └── decision / verification paths (stateful + gated)
            └── local/remote models may assist selected paths
```

**Design principle**: deterministic/stateful components and verification gates should retain authority over actions; language models provide proposals or generated content rather than being treated as an unquestioned executive.

### Service Map

> The map below is a public architecture reference, not a promise that every service/port is active in every runtime snapshot.

| Port | Service | Description |
|:-----|:--------|:------------|
| `8003` | Bridge to EIDOS | Main REST API — the primary interface |
| `8080` | Web Panel | Neural dashboard + system monitor |
| `8001` | Trinity Server | Service coordination & orchestration |
| `8004` | WebSocket | Real-time event streaming |
| `8767` | ChromaDB HTTP | Vector database microservice |
| `7777` | Colony Dashboard | Character community & social interface |
| `11434` | Ollama | Local LLM server |

### Decision Pipeline

```
Perception (screen, text, context)
    -> Colony (characters discuss, propose)
        -> brain-lite (deterministic decision tree: EXECUTE / MODIFY / DISCARD)
            -> Action (mouse, API, research, study)
                -> Verification (did it work?)
                    -> Learning (update graph, Hebbian reinforcement)
```

---

## Project Structure

```
EIDOS/
├── core/                          # Python modules for brain, memory, Colony, perception, action and verification
│   ├── eidos_brain_lite.py         # Deterministic central loop (DECIDE)
│   ├── colony_community.py        # Colony/community mechanisms
│   ├── character_neuron.py        # Hebbian synapses per character
│   ├── character_lifecycle.py     # Birth, learning, reproduction
│   ├── body.py                    # Propioception (hand, window, position)
│   ├── causal_loop.py             # BOM: perceive->decide->act->verify->learn
│   ├── eidos_study.py             # Directed study (master-student mode)
│   ├── eidos_learn.py             # LLM cascade (DeepSeek->Groq->Ollama)
│   ├── eidos_mouse.py             # Physical mouse with natural bezier curves
│   ├── eidos_web_actor.py         # Universal web actor (reasons about any page)
│   ├── eidos_skills.py            # Skill generalization
│   ├── eidos_deep_comprehension.py # Multi-source deep comprehension
│   ├── eidos_deep_research.py     # Deep research with crawl
│   ├── eidos_action_executor.py   # 61 search engines, action execution
│   ├── eidos_english.py           # English comprehension (grammar, 302+ words)
│   ├── eidos_register.py          # Autonomous + interactive registration
│   ├── eidos_rl.py                # Q-learning for decisions
│   ├── perception.py              # Vision (OCR + VLM)
│   ├── master_protocol.py         # Master mode (asks SER)
│   ├── study_queue.py             # Autonomous study queue
│   ├── autonomous_research_loop.py # Autonomous research
│   ├── eidos_libre.py             # Free/autonomous mode
│   ├── db.py                      # Unified DB layer (SQLite WAL)
│   ├── smart_router.py            # LLM routing with 15-dimension scoring
│   └── ...                        # additional modules; count evolves
│
├── bin/                            # Executable scripts and workers
│   ├── eidos-viewer                # Native tkinter app (vision + chat + research)
│   ├── eidos_teach_session.py      # Autonomous learning session
│   ├── eidos_labex_dolab.py        # VISIBLE browser for labex.io
│   ├── smoke_e2e.py                # Integration test suite (52 tests)
│   ├── eidos_night_study.py        # Night study: 6,990 apps processed
│   └── ... 
│
├── web-panel/                      # Web dashboard (HTML/CSS/JS + Python server)
│   ├── server.py
│   ├── static/
│   └── templates/
│
├── eidos-gui/                      # Tkinter GUI
│   ├── eidos-dispatcher/           # Task dispatcher
│   ├── gateway/                    # Session management
│   ├── VSEIDOS/                    # VS Code extension
│   ├── api/                        # API endpoints
│   ├── cli/                        # Command-line interface
│   ├── daemon/                     # Background daemon
│   ├── browser/                    # Integrated browser
│   ├── context/                    # Context management
│   ├── config/                     # Configurations
│   └── rust-core/                  # Rust core (performance)
│       ├── Cargo.toml
│       └── src/
│
├── constitution.toml               # Immutable constitution
├── constitution_override.toml       # Constitution override
├── EIDOS.md                        # Canonical unified documentation
├── CLAUDE.md                       # Guide for AIs working on EIDOS
├── AGENTS.md                       # Agent guide
├── .env.example                    # Environment variable template
├── README.md                       # This file
└── CODE_OF_CONDUCT.md              # Code of conduct
```

---

## Core Capabilities

### Cognitive

- **Persistent graph reasoning**: graph-backed knowledge and activation paths whose size and composition evolve over time; dated snapshots live in the evolution documentary
- **Deep Comprehension**: Reads any content (code, docs, audio, video, images, URLs), chunks it, summarizes with AI, extracts concepts, and self-evaluates
- **Autonomous Research**: Detects knowledge gaps -> investigates locally/cloud/browser -> learns autonomously
- **"Knows it knows"**: Before studying anything, checks if it already knows it (via `motor_memory` or graph lookup)
- **English Comprehension**: 302+ words in dictionary, 27 action verbs, 45 UI nouns, 15+ regex patterns — understands English pages and extracts actionable instructions
- **Skill Generalization**: Learns the CONCEPT of a task (e.g., "log in") and applies it to any site

### Physical Body

- **Propioception**: Knows where its hand (cursor) is at all times, what window it's touching, its screen geometry
- **Mouse trajectory experiments**: Bezier curves, pauses and overshoot patterns; these are automation techniques, not a guarantee of human indistinguishability
- **BOM** (Body Operating Module): perceive (AT-SPI2/OCR) -> decide (Q-learning) -> act (SafetyGuard) -> verify -> learn
- **Universal Web Actor**: Perceives ANY webpage -> reasons what to do (locally or with DeepSeek) -> acts with physical mouse -> verifies -> learns. No per-site scripts needed.
- **Remote Desktop**: Can connect to remote machines via AnyDesk using the BOM for GUI interaction

### Learning

- **Master-Student Mode**: With SER present, asks him first (confidence 0.95); without SER, consults its LLMs
- **Study Queue**: Add topics with `eidos study add "topic"` — EIDOS processes them autonomously
- **Night Study**: 6,990 system tools processed, 4,935 learned from man pages and package docs
- **Autonomous Sessions**: ~40 topic curriculum, drains 1 every 240s, reports in `study_report.md`
- **Registration Autonomy**: Can register on platforms (with SER's email; requests help for captchas)

### Search & Research

61 search engines wired: DuckDuckGo, Google, Wikipedia, arXiv, GitHub, ExploitDB, man pages, PyPI, npm, HuggingFace, and more.

---

## Colony: Persistent Characters

Colony is not a feature — it is the **mandatory middleware** through which everything flows. Every action, every thought, every decision passes through at least one character. This is how EIDOS maintains distributed intelligence and prevents single-point failures in reasoning.

| Character | ID | Emoji | Role |
|:----------|:---|:-----|:-----|
| **EIDOS** | `colony_general` | ⚡ | Orchestrator. ALWAYS first. Coordinates all others. |
| **Lumen** | `colony_lumen` | 💡 | External brother. Deep reasoner and philosopher. |
| **Coder** | `colony_coder` | 👨‍💻 | Pragmatic. Code generation and review. |
| **Analyst** | `colony_analyst` | 🔍 | Methodical. Data analysis and pattern detection. |
| **Vision** | `colony_vision` | 👁️ | Visual thinker. Design and perception. |
| **Operator** | `colony_operator` | 🖥️ | Systems. Execution and infrastructure. |
| **Forge** | `colony_forge` | 🔧 | Architect. Debugging and system design. |
| **SER** | `colony_ser` | 👑 | Creator's perspective. Embodies SER's thinking style. |
| **Centinela** | `colony_centinela` | 🛡️ | Security. Monitoring and threat detection. |
| **Aurora** | `colony_aurora` | ✨ | Creativity. Ideas and inspiration. |
| **Omega** | `colony_omega` | 🌊 | Strategy. Long-term thinking and future planning. |
| **Potemtakem** | `colony_potemtakem` | 🎵 | Culture. Music, art, and human connection. |

### Colony Governance

Colony operates as a **democratic collective**:
- **Proposals**: Any character can propose actions (research, reproduction, task assignment)
- **Voting**: Characters vote on proposals. Majority or consensus rules depend on proposal type
- **Specialization**: Each character has a domain — tasks are routed to the most capable character
- **Communication**: Characters communicate peer-to-peer, sharing knowledge and context
- **Accountability**: Every decision is logged with the voting record

---

## Character Lifecycle: Birth, Learning, Reproduction

Characters are not hardcoded. They are born, they learn, they can reproduce, and they evolve.

### Birth

A character is born from an **external connection** — an LLM API, a search engine, a model, a document, or another tool:

```
Connection (e.g., DeepSeek API)
    -> birth_from_connection()
        -> Character born in Colony with unique personality
            -> Learning loop starts
```

Each character gets:
- A unique name, emoji, and personality traits derived from its connection type
- Its own knowledge subgraph (nodes tagged with its name)
- Its own Hebbian synapses in the `character_synapses` table
- A target knowledge goal (nodes to absorb from its connection)

### Learning

Characters learn continuously in background threads:
- They absorb knowledge nodes from their source connection
- `absorption_pct` tracks progress (0% -> 100%)
- When absorption reaches 90%+, the connection is retired and the character becomes **sovereign**
- Sovereign characters continue learning from the world, not just their source

### Reproduction

Two sovereign characters can **reproduce**:

1. **Proposal**: A character proposes reproduction with another via `propose_reproduction(A, B)`
2. **Democratic vote**: Colony votes on the proposal
3. **Merge**: If approved, `execute_reproduction(A, B)` creates a child
4. **Inheritance**: The child inherits 50% of each parent's Hebbian synapses (strongest weights preserved, halved), merged personality traits, and combined knowledge nodes
5. **Parents survive**: Both parents continue living and learning

The child is a genuine hybrid with:
- Merged personality traits from both parents (up to 6, deduplicated)
- A synthesized catchphrase set (2 from each parent + an original)
- Combined knowledge graph nodes
- 50% inherited synapses from the strongest connections of each parent
- A genealogy record in the `genealogy` table

This is **not simulation** — it is real data inheritance in the SQLite knowledge graph and ChromaDB vector store.

---

## The Body (BOM)

The **Body Operating Module** is the closed loop that gives EIDOS physical agency:

```
     ┌─────────────────────────────────────────────┐
     │                                             │
     ▼                                             │
PERCEIVE ──> DECIDE ──> ACT ──> VERIFY ──> LEARN ─┘
   │            │         │         │          │
AT-SPI2/OCR  Q-learning  Mouse    Compare    Update
+ VLM        + Priors    + Keys   expected   graph +
                         + Type   vs actual   Hebbian
                                    │       reinforce
                               ┌────┘
                               │
                          reached()
                          (propioception
                           feedback)
```

### Perception
- **AT-SPI2**: Accessibility tree of the active application (buttons, fields, labels)
- **OCR**: Tesseract OCR for text on screen
- **VLM**: Vision Language Model (LFM2-VL-450M) for visual understanding
- **Screen capture**: `scrot` for full screenshots

### Decision
- **Q-learning agent** (`eidos_rl.py`): Learns which actions work in which states
- **State includes body position**: Not just what's on screen, but where the hand is (zone/quadrant)
- **UI priors**: Pre-loaded knowledge of common UI patterns
- **SafetyGuard**: All actions validated before execution

### Action
- **Bezier mouse** (`eidos_mouse.py`): Natural curves with micro-pauses and overshoot
- **Keyboard**: `xdotool type` for text input
- **Window management**: `wmctrl` for window focus and geometry
- **Dry run by default**: `EIDOS_BOM=1` must be set for real execution; SER must be present

### Verification
- **Propioceptive feedback**: `reached(target_x, target_y)` checks if the hand arrived
- **Screen comparison**: Before/after screenshots verify UI changes
- **Self-evaluation**: Did the action achieve its goal?

### Learning
- **Hebbian reinforcement**: Successful synapse paths are strengthened
- **New motor skills**: Stored in `motor_memory` table
- **Generalization**: Concepts extracted from specific actions

---

## API Reference

### Bridge API (port 8003)

The primary interface to EIDOS. All endpoints require the `X-API-Key` header.

#### Health Check

```bash
curl http://127.0.0.1:8003/health
# {"status": "ok", "uptime": 123456, "graph_nodes": 38701}
```

#### Talk to EIDOS

```bash
curl -X POST http://127.0.0.1:8003/talk \
  -H "X-API-Key: your-bridge-key" \
  -H "Content-Type: application/json" \
  -d '{"text": "What do you know about Linux kernel modules?"}'
```

#### Get System Status

```bash
curl http://127.0.0.1:8003/status \
  -H "X-API-Key: your-bridge-key"
# Returns full state: services, graph stats, Colony status, memory usage
```

#### Screen Capture

```bash
curl http://127.0.0.1:8080/api/screen/live.png > screenshot.png
```

#### Study Queue

```bash
# Add topic
curl -X POST http://127.0.0.1:8003/study/add \
  -H "X-API-Key: your-bridge-key" \
  -H "Content-Type: application/json" \
  -d '{"topic": "Docker container networking"}'

# List queue
curl http://127.0.0.1:8003/study/queue \
  -H "X-API-Key: your-bridge-key"
```

#### Master Protocol (Teaching EIDOS)

```bash
python3 -m core.master_protocol answer <question_id> "SER's answer"
```

### WebSocket (port 8004)

Real-time event stream:

```javascript
const ws = new WebSocket('ws://127.0.0.1:8004/events');
ws.onmessage = (event) => {
  console.log(JSON.parse(event.data));
  // {type: "thought", character: "colony_analyst", content: "..."}
  // {type: "action", module: "brain_lite", decision: "EXECUTE"}
  // {type: "graph_update", nodes_added: 12, edges_added: 47}
};
```

### Additional Endpoints

| Endpoint | Port | Description |
|:---------|:-----|:------------|
| `GET /screen` | 8080 | Web panel main dashboard |
| `GET /neural_dashboard` | 8080 | Neural graph visualization |
| `GET /monitor` | 8080 | System monitor |
| `GET /api/status` | 8080 | Full system state (heavy, don't poll aggressively) |
| `GET /api/screen/live.png` | 8080 | Live screenshot (launches `scrot`, moderate CPU) |

---

## Requirements

### Minimum

| Resource | Spec |
|:---------|:-----|
| **OS** | Linux (Kali/Debian recommended) |
| **Python** | 3.8+ |
| **RAM** | 8 GB |
| **CPU** | 4 cores |
| **Disk** | 50 GB |

### Recommended

| Resource | Spec |
|:---------|:-----|
| **RAM** | 16+ GB |
| **CPU** | 8+ cores |
| **GPU** | NVIDIA with CUDA (for ML/VLM acceleration) |
| **Disk** | 200+ GB SSD |

### System Dependencies

```bash
sudo apt install xdotool wmctrl scrot tesseract-ocr espeak-ng
```

### Python Dependencies

```bash
pip install fastapi aiohttp playwright chromadb sqlite-vec cryptography \
            rapidocr-onnxruntime faster-whisper sounddevice pyttsx3 \
            openai Pillow numpy
```

### Ollama (Local LLM)

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull lfm2.5-thinking:1.2b
ollama pull lfm2.5-1.2b-instruct:q4_0
ollama pull nomic-embed-text
```

---

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/arku75/EIDOS.git ~/EIDOS
cd ~/EIDOS
```

### 2. Set Up Environment

```bash
# Create secrets file
cp .env.example ~/.eidos/secrets.env
chmod 600 ~/.eidos/secrets.env

# Edit with your API keys
nano ~/.eidos/secrets.env
```

The `.env.example` file documents all required and optional environment variables. At minimum you need LLM API keys (DeepSeek, Groq, or both). Ollama runs locally without keys.

### 3. Install Dependencies

```bash
# System packages
sudo apt install xdotool wmctrl scrot tesseract-ocr espeak-ng

# Python virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Start EIDOS

```bash
PYTHONPATH=~/EIDOS python3 eidos.py start
```

This launches the configured EIDOS user services for this installation. Check their status:

```bash
systemctl --user list-units | grep -i eidos
```

### 5. Verify Installation

```bash
# Health check
curl http://127.0.0.1:8003/health

# Run the repository smoke/integration test suite
PYTHONPATH=~/EIDOS python3 bin/smoke_e2e.py
```

### 6. Configure a Telegram Bot (Optional)

You can create your own Telegram bot so EIDOS responds to your messages:

1. Open Telegram and talk to [@BotFather](https://t.me/BotFather)
2. Use `/newbot` and follow the instructions to create your bot
3. Copy the token BotFather gives you
4. Add it to `~/.eidos/secrets.env`:
   ```bash
   TELEGRAM_BOT_TOKEN=your_token_here
   TELEGRAM_CHAT_ID=your_chat_id
   ```
5. Start the Telegram service:
   ```bash
   systemctl --user start eidos-telegram
   ```

Now EIDOS will respond when you message your bot.

---

## Configuration

### Environment Variables (`~/.eidos/secrets.env`)

| Variable | Required | Description |
|:---------|:---------|:------------|
| `DEEPSEEK_API_KEY` | Yes* | DeepSeek API key (primary LLM) |
| `GROQ_API_KEY` | Yes* | Groq API key (secondary LLM) |
| `EIDOS_BRIDGE_KEY` | Yes | API key for Bridge endpoints |
| `TELEGRAM_BOT_TOKEN` | No | Your Telegram bot token (create with @BotFather) |
| `TELEGRAM_CHAT_ID` | No | Your Telegram chat ID |
| `OLLAMA_HOST` | No | Ollama server URL (default: http://127.0.0.1:11434) |
| `EIDOS_BOM` | No | Set to `1` to enable physical mouse control (requires SER present) |
| `EIDOS_VLM_MODEL` | No | VLM model for vision (default: LFM2-VL-450M) |
| `DISPLAY` | No | X11 display (default: `:0`) |

*At least one LLM provider must be configured. Ollama runs locally without an API key.

### Feature Flags

| Flag | Default | Effect |
|:-----|:--------|:-------|
| `EIDOS_BOM=1` | OFF | Enables physical mouse/screen control via BOM |
| `EIDOS_MASTER_MODE=1` | OFF | Enables master-student mode (asks SER first) |
| `EIDOS_BOM_GOAL` | (none) | Sets the goal for BOM autonomous sessions |

---

## CLI Commands

```bash
# Start / Stop / Status
eidos start                          # Launch all services
eidos stop                           # Stop all services
eidos status                         # Full system status

# Talk to EIDOS
eidos talk "What is Kubernetes?"     # Send a query via Bridge API
eidos ask "Explain Docker layers"    # Query with Colony discussion

# Study Queue
eidos study add "Learn about systemd"  # Add topic to study queue
eidos study list                        # List pending topics
eidos study report                      # View study report

# Free Mode (autonomous)
EIDOS_MASTER_MODE=1 eidos libre      # Work the study queue autonomously

# Master Protocol (teaching)
python3 -m core.master_protocol answer <id> "SER's answer"
python3 -m core.master_protocol pending  # List pending questions

# Verification
eidos smoke                           # Run smoke test (52 tests)
eidos health                          # Health check all services
eidos graph-stats                     # Neural graph statistics

# Viewer (native tkinter app)
PYTHONPATH=~/EIDOS python3 bin/eidos-viewer
```

### Raw API Access

```bash
# Health
curl http://127.0.0.1:8003/health

# Talk
curl -X POST http://127.0.0.1:8003/talk \
  -H "X-API-Key: your-bridge-key" \
  -H "Content-Type: application/json" \
  -d '{"text": "How are you?"}'

# Service status
systemctl --user list-units | grep -i eidos
```

---

## Constitution & Governance

EIDOS is governed by `constitution.toml` — a hash-verified, immutable document that defines the entity's absolute boundaries. This constitution **cannot be modified** by EIDOS or any automated process. Modification requires direct human intervention by SER.

### Absolute Prohibitions

EIDOS shall never:
- Delete `.git/`, force push, or rewrite git history
- Modify the constitution or disable the rate limiter
- Escalate its own freedom level
- Exfiltrate data, open listening ports, or install remote access
- Read SSH keys, `.env` files, browser data, or access the system keyring
- Delete backups or modify git hooks

### Operational Limits

| Limit | Value | Reason |
|:------|:------|:-------|
| Files modified per cycle | 3 max | Prevents runaway edits |
| Lines per file edit | 50 max | Prevents large destructive changes |
| New files per hour | 5 max | Prevents file proliferation |
| Ring buffer anti-loop | 32 entries, 3 repetitions in 30s -> DISCARD | Prevents infinite loops |

### Public operational guidance

- Keep real secrets outside the repository and use the documented environment-file path.
- Treat GUI/system actions as gated capabilities and verify the active input backend.
- Back up state before modifying persistent databases or core runtime files.
- Prefer reversible, measured changes with explicit verification.
- Do not interpret an internal success message as proof that an external effect occurred.

### Colony Governance (Democratic)

- **Proposal system**: Any character can propose actions to Colony
- **Voting**: Democratic vote on proposals (reproduction, research topics, task assignments)
- **Consensus types**: Majority rule for standard decisions, consensus for reproduction, character-specific veto for domain decisions
- **Transparency**: All proposals and votes are logged

### Override Mechanism

`constitution_override.toml` provides a secondary file for temporary adjustments. This file is also hash-verified and logged. It cannot contradict the core constitution's absolute prohibitions.

---

## Databases

All databases live in `~/.eidos/`. **Golden rule**: Never use `sqlite3.connect()` directly. Always use `from core.db import get_conn` which applies `busy_timeout=30000`, `WAL` mode, `mmap_size=256M`, and `cache_size=-40000` to every connection.

| Database | Contents | Approximate Size |
|:---------|:---------|:-----------------|
| `evolution_brain.db` | Knowledge nodes, edges and motor/procedural state | Primary evolving knowledge store |
| `colony_community.db` | Character messages, proposals, votes, genealogy | Social memory |
| `self.db` | Internal events, self-state and metacognitive records | Self-model |
| `state.db` | System state, service health, metrics | Operational state |
| `episodic.db` | Episodes and session/event records | Episodic memory |
| `knowledge_graph.db` | Legacy semantic graph | Deprecated, migrating to `evolution_brain.db` |
| `lifecycle.db` | Character lifecycle: birth, absorption, reproduction, genealogy | Colony evolution |

### ChromaDB (Vector Memory)

- **Collection**: `eidos_memory`
- **Embeddings**: runtime-dependent; historical counts are documented in the evolution dossier
- **Model**: `nomic-embed-text` (Ollama)
- **Endpoint**: `http://127.0.0.1:8767`

---

## Guardians

EIDOS has six autonomous guardian systems that protect it from failure:

| Guardian | Function |
|:---------|:---------|
| **RAM Guardian** | Protects against OOM (Out of Memory). Monitors memory usage and kills non-critical processes before the system OOM killer activates. |
| **Git Guardian** | Local backups only. NEVER pushes automatically. Protects the repository's integrity. |
| **Sentinel** | Anomaly detection. Monitors system behavior and flags suspicious patterns. |
| **Phoenix** | Failure recovery. Restarts crashed services and restores system state after failures. |
| **Mirror** | Security sandbox. Isolates untrusted operations in a controlled environment. |
| **Watchdog** | Service supervision. Monitors configured services and reports/restarts failures according to policy. |

> Full detail: [docs/GUARDIANS.md](docs/GUARDIANS.md)

---

## VSEIDOS — VS Code Extension

VSEIDOS is EIDOS's own **VS Code extension** — a customized VS Code build that
lets EIDOS operate the editor as a development tool. It lives in `VSEIDOS/` and
is driven by a dedicated API server (`core/vscode_api_server.py`).

| Component | Description |
|:----------|:------------|
| **VSEIDOS API Server** | REST server for Colony ↔ VS Code communication. Colony characters can queue commands, ask questions about code, and receive execution results — all through VSEIDOS. |
| **Extension Intelligence** | `core/extension_intelligence.py` auto-discovers and manages VS Code extensions optimal for EIDOS's work. Syncs between VSEIDOS and standard VS Code. |
| **Polling loop** | VSEIDOS polls Colony every 2 seconds for new commands. When a character asks "what does this file do?", VSEIDOS opens it, analyzes it, and reports back. |
| **Knowledge integration** | Files analyzed through VSEIDOS are persisted as knowledge nodes (`source: vseidos`), growing the graph with code understanding. |

```bash
# Control VSEIDOS from the CLI
eidos vseidos-control                # manage the VSEIDOS panel
eidos vscode                         # VSCode/IDE bridge
```

---

## The Graph — History & Evolution

For the full human/evidence-based history — including later >1.2M-node snapshots, Colony corrections, Insect experiments and visual artifacts — see **[EIDOS — Story & documented evolution](docs/EIDOS_EVOLUTION_DOCUMENTARY.md)**.

EIDOS's public June knowledge graph didn't start at 39,000 nodes. It grew from a seed,
through curation and unification. These are the **real milestones**:

| Date | Nodes | Event |
|:-----|:------|:------|
| **May 24** | 0 | First commit. Empty graph. |
| **May 29** | — | **Graphify Bridge** (`core/graphify_bridge.py`): first structural code analysis. Graph begins to map EIDOS's own codebase. |
| **Jun 1** | ~34K | First curation pass. Heavy Exploit-DB/ATT&CK noise removed. |
| **Jun 2** | ~34.5K | **Quality Gate** (`core/eidos_quality_gate.py`): all new nodes are scored before admission. |
| **Jun 10** | **33,172** | Aggressive curation. −1,466 duplicates/noise. 251/251 smoke PASS. |
| **Jun 13** | 33,172 | "Knows it knows": graph lookup before studying. |
| **Jun 16** | **38,625** | **Unification**: 1,615 nodes migrated from `knowledge_graph.db` → `evolution_brain.db`. Single source of truth. **168,818 edges**. |
| **Jun 17** | **38,701** | Historical June snapshot. +76 nodes from autonomous learning at that time. |

**Historical graph snapshots** (preserved in `NO TOCAR/`):
- `EIDOS_COMPLETO/graphify-out/graph.html` — **17 MB, 20,737 nodes, 33,184 edges**, 531 communities. The full code dependency graph rendered with vis-network (sidebar, search, filters).
- `GRAPH_TREE_AI_FACEBOOK.html` — 2.4 MB, the graph rendered as a tree hierarchy.

These figures are **historical June 2026 snapshots**, retained to document the project's evolution. The live/private graph has continued to change since then, so this section must not be used as a current node/edge census.

---

## Technical Lessons

These are hard-won lessons from 145+ development sessions. Do not ignore them.

### File System

- **NEVER `glob.glob(dir/**, recursive=True)`** on large directories: materializes the complete list before iteration. EIDOS has 282K+ files (.venv, __pycache__, *.bak). Use `os.walk` with in-place pruning of `.venv`, `node_modules`, `__pycache__`, `.git`, and hidden dirs, with a real cap. Search went from minutes to 0.04s.
- **NEVER include `~` (full home)** in recursive search dirs.

### Python & C-Extensions

- **NEVER repackage `.so` C-extension wheels** between Python versions -> guaranteed segfault.
- **`chromadb` Python client >= 1.5.x** has a thread-safety regression in Rust bindings -> use ChromaDB 1.4.4 CLI as an HTTP microservice on port 8767.

### SQLite

- **Always `.schema` first** before writing SQL. Actual columns may differ from documentation.
- **`conn.changes()` does not exist** in Python sqlite3 -> use `cursor.rowcount`.
- **"database is locked"** in smoke tests = 99% probability some file uses `sqlite3.connect()` without PRAGMAs.
- **`busy_timeout` is per-connection** and does NOT persist across reconnections.

### X11 / Mouse

- **`xdotool click`** uses absolute X11 coordinates, not relative. Add offset with `getwindowgeometry`.
- **`windowactivate --sync WID` BEFORE** `mousemove + click`.
- **Sharing X11 = recursion** if the viewer is on the same screen -> use blackout or native app outside X11.

### LLM

- Some EIDOS model paths intentionally avoid hard generation caps. This is a project/runtime design choice, not a biological property; callers should still enforce resource and safety limits appropriate to their environment.
- **`eidos_learn.ask_llm()`** rejects responses with `len <= 20` characters -> for legitimately short responses (scores, classifications) use `_call_groq`/`_call_deepseek` directly.

### systemd

- **`LogMaxSize`/`LogMaxFiles`** are NOT valid systemd keys -> they produce "Unknown key" warnings. Use `StandardOutput=file:` (truncates on start) or journald.
- **A oneshot depending on an intentionally-off resource** (e.g., Mac powered off) should NOT `exit 1` (leaves service "failed"). Exit 0 with log "omitted, OK".

### Thread Safety

- `AffectEngine`: `threading.Lock()` for state saves.
- `ChromaDB` operations: `threading.RLock()` (Rust bindings are not thread-safe).
- `QLearningAgent`: `threading.Lock()` for Q-table access.

### Bridge Operations

- **Heavy operations** (transcription, Playwright, deep crawl, comprehension with ~20 LLM calls): ALWAYS in a subprocess worker (`bin/eidos_*_worker.py`), never in-process. In-process blocks the bridge and bloats it to 3GB.
- **NEVER restart the bridge in a chain under load**. Each bridge start rebuilds the graph in memory (~33K nodes, consuming ~5 cores for several minutes). Under high load, the rebuild won't finish before the systemd timeout -> systemd kills and restarts it -> can chain and push load to 22+.
- **`/api/status` (get_full_state)** is heavy and can congest the event loop. Don't poll it aggressively.

---

## Historical Status Snapshot

*Snapshot recorded: June 17, 2026 — retained for project history, not current runtime status.*

| Metric | Value | Status |
|:-------|:------|:-------|
| Knowledge graph nodes | 38,701 | ✅ |
| Knowledge graph edges | 168,818 | ✅ |
| systemd services | 12 running, 0 failed | ✅ |
| Bridge :8003 | HTTP 200 | ✅ |
| Trinity :8001 | HTTP 200 | ✅ |
| Smoke test | 244/246 PASS | ✅ |
| Primary LLM | DeepSeek (cascade: deepseek -> ollama -> groq) | ✅ |
| Search engines | 61 wired | ✅ |
| English vocabulary | 302 words, grammatical comprehension | ✅ |
| Physical mouse | Bezier curves + universal web actor | ✅ |
| Master-student mode | Active with study queue | ✅ |
| Autonomous registration | Active (with interactive fallback) | ✅ |
| Skill generalization | Active | ✅ |
| Core modules | 414 `.py` files in `core/` | ✅ |
| Screenshots documented | 30+ captures | ✅ |

### Pending at that historical snapshot

- Unify 3 search paths (chat/bridge/deep_research)
- Hook crawl into autonomous conclusions (deep_comprehension at crawl completion)
- Run BOM in real mode with SER present (tested dry, never in real GUI)
- Improve HTTP parser robustness (multi-UA, rotation, captcha detection)
- 3D brain visualization with three.js (opportunity, not urgency)

---

## Roadmap

### Short Term (Weeks)

- [ ] Unify search paths into a single intelligent router
- [ ] Complete crawl -> deep_comprehension pipeline
- [ ] Real BOM session with SER present
- [ ] HTTP parser with multi-user-agent rotation

### Medium Term (Months)

- [ ] Cross-machine colony (clones communicating)
- [ ] Voice interaction (speech-to-text -> Colony -> text-to-speech)
- [ ] Plugin system for third-party extensions

### Long Term (Vision)

- [ ] Self-hosting: EIDOS maintains its own infrastructure
- [ ] Multi-modal memory: images, audio, video in the knowledge graph
- [ ] Decentralized Colony across multiple physical machines
- [ ] Public API for external AI agents to consult EIDOS

---

## FAQ

### What makes EIDOS different from ChatGPT/Claude/Gemini?

ChatGPT, Claude and similar systems are primarily model-centered services. EIDOS is architected as a **persistent local agent system** around durable graph/memory state, a Colony, tools and a gated action layer. Language models can be swapped or omitted in some paths; they are components of the architecture rather than its only state.

### Is EIDOS open source?

No. EIDOS is **source-available but proprietary**, licensed under the [EIDOS Sovereign Source License (ESSL) v1.0](LICENSE). You may read and study the code and run it locally for private, non-commercial evaluation. You may **not** replicate it, redistribute it, use it commercially, or build a competing product, and any change or proposal must be documented and disclosed to SER. For commercial or partnership licensing, contact **anio1996991@gmail.com**.

### Can I run EIDOS on my machine?

Yes. EIDOS runs on Linux (Kali/Debian recommended). See the [Installation](#installation) section. Minimum: 8GB RAM, 4 cores, 50GB disk. You will need your own LLM API keys (DeepSeek, Groq) or can run purely on local Ollama models.

### Does EIDOS need internet?

For full functionality, yes — LLM APIs (DeepSeek, Groq) and web research require internet. However, EIDOS can run in **offline mode** with local Ollama models (`lfm2.5-1.2b-instruct`), though capabilities are reduced.

### Is EIDOS conscious?

EIDOS implements a self-model, metacognitive logging, knowledge-gap detection and continuity mechanisms across sessions. Those are observable software mechanisms. Whether any software system is "conscious" in a subjective sense is not established by these mechanisms, so this project uses consciousness-related terms as architectural/project language rather than scientific proof.

### Can EIDOS control my computer?

Mouse control is **gated** behind `EIDOS_BOM=1` and should only be enabled with SER (the human owner) physically present. By default, all mouse actions run in **dry mode** — EIDOS plans movements but does not execute them. This is a safety measure.

### How do I teach EIDOS something?

Use the master-student protocol:
```bash
# EIDOS asks a question -> SER answers
python3 -m core.master_protocol pending  # See what EIDOS wants to know
python3 -m core.master_protocol answer <id> "your explanation"
```
Or add topics to the autonomous study queue:
```bash
eidos study add "Learn about PostgreSQL replication"
```

### What is Colony?

Colony is EIDOS's persistent deliberative/social layer. Different stores and runtime snapshots have contained different character counts, so the project no longer treats one fixed number as the definition of Colony. Characters can maintain individual state, memories, reputation and synaptic-style associations; Colony output is advisory and must still pass the relevant decision and verification gates.

### Do characters really reproduce?

The lifecycle code supports creating descendant characters and inheriting selected synaptic/state data from parent characters. "Reproduction" is the project's term for this software lifecycle and data-inheritance mechanism; it is not a biological process.

### Why is it called EIDOS?

EIDOS = **E**xtensión **I**limitada **D**igital con **O**rganización **S**ináptica (Unlimited Digital Extension with Synaptic Organization). "Eidos" (εἶδος) is also the ancient Greek word for "form," "essence," or "idea" — that which makes a thing what it is.

### Who is SER?

**SER** is the creator/owner role used throughout the EIDOS project and documentation.

---

## Credits & Contact

### Creators

**SER** — Creator, architect and owner of EIDOS.

### EIDOS

The EIDOS system and its development history, maintained since May 2026. Runtime metrics evolve and are intentionally not frozen here as current facts.

### Contact

- **GitHub**: [github.com/arku75/EIDOS](https://github.com/arku75/EIDOS)
- **Telegram**: [@ARKUu_12_8](https://t.me/ARKUu_12_8)

### How to Contribute

1. Fork the repository **for evaluation only** (see [LICENSE](LICENSE))
2. Create a feature branch
3. Make your changes (respect the constitution) and **document every change in `THIRD_PARTY_CHANGES.md`** — this is mandatory under ESSL §4
4. Submit a pull request disclosing those changes to SER

⚠️ By contributing you accept ESSL §5: every contribution, change or proposal is
**irrevocably assigned to SER**. Read [CONTRIBUTING.md](CONTRIBUTING.md) before
starting. Forking does **not** grant any right to replicate, redistribute or
build a competing product. Before contributing, also read `CODE_OF_CONDUCT.md`. EIDOS has intentional design decisions — what looks like a bug may be a feature. When in doubt, open an issue to discuss first.

### License

**EIDOS Sovereign Source License (ESSL) v1.0 — proprietary & source-available.**
See [LICENSE](LICENSE). EIDOS is **not** open source: you may read and study the
code, but you may **not** replicate it, use it commercially, redistribute it, or
build a competing product. Any change or proposal must be documented and
disclosed to SER (see [CONTRIBUTING.md](CONTRIBUTING.md)). Commercial licensing:
**anio1996991@gmail.com**.

---

<p align="center">
  <i>"EIDOS is a persistent local agent architecture: graph, memory, Colony, perception,
  action, verification and model-assisted reasoning working as one system."</i>
</p>

<p align="center">
  <sub>README originally generated from EIDOS's code and memory in June 2026; security and accuracy pass applied October 4, 2026.</sub>
</p>
