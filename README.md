# EIDOS — Extensión Neuronal Ilimitada

<p align="center">
  <img src="https://img.shields.io/badge/Linux-local--first-555?style=for-the-badge&logo=linux&logoColor=white" alt="Linux">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/license-ESSL%20v1.0-red?style=for-the-badge" alt="ESSL v1.0">
  <img src="https://img.shields.io/badge/status-active-brightgreen?style=for-the-badge" alt="Active">
</p>

<p align="center">
  <b>Persistent local intelligence: memory + graph + Colony + world model + actions + verification + learning.</b>
</p>

<p align="center">
  <a href="docs/EIDOS_EVOLUTION_DOCUMENTARY.md">Story & evolution</a> ·
  <a href="docs/ARCHITECTURE.md">Architecture</a> ·
  <a href="docs/AUTONOMY_LAB.md">Autonomy Lab</a> ·
  <a href="docs/INSTALL.md">Install</a> ·
  <a href="docs/KNOWN_ISSUES.md">Honest status</a> ·
  <a href="LICENSE">License</a>
</p>

---

## What EIDOS is

EIDOS is a **local, persistent agent architecture** built to keep state and learn across sessions instead of reducing everything to a single model prompt.

Its target loop is:

```text
WORLD
  ↓
PERCEPTION
  ↓
MEMORY + GRAPH
  ↓
COLONY / SPECIALISTS
  ↓
ARBITRATION
  ↓
ACTION
  ↓
OBSERVED EFFECT
  ↓
VERIFICATION
  ↓
LEARNING
  ↺
```

The central engineering rule is simple:

> **A convincing answer is not the same thing as a verified effect.**

EIDOS therefore treats model output, web content and internal proposals as **claims** until they are corroborated by tests, measurements, state changes or independent evidence.

Two project principles appear throughout the development history:

- **HEARD ≠ VERIFIED**
- **USE → UNDERSTAND → REPLICATE → OWN**

The second principle means that external models, libraries, tools and applications can act as teachers or capability sources, but the long-term goal is to extract reusable mechanisms and turn them into EIDOS-owned, testable capabilities rather than permanent black-box dependence.

---

## What is public in this repository

The public tree contains the code and documentation needed to inspect and exercise the architecture, including:

- persistent graph/memory code;
- Colony and character lifecycle mechanisms;
- action, verification and world-model components;
- GUI/browser/perception modules;
- the Bridge/API and CLI layers;
- autonomy and self-edit laboratories;
- an isolated Fly/Insect laboratory;
- a shared agent Runtime Hub and interactive terminal;
- tests and GitHub Actions used as clean Ubuntu validation environments.

The live private state of a running EIDOS installation — databases, secrets, browser profiles, private graph contents and machine-specific runtime data — is **not** distributed.

---

## What can be verified from a clean clone

The sanitation branch introduced reproducible tests that do not require SER's private machine state.

### 1. Fly / Insect learning lab

```bash
PYTHONPATH=. python core/fly_lab.py
```

This runs a five-seed deterministic insect-inspired associative-learning benchmark with a shuffled-label negative control.

A pass demonstrates that the implemented sparse/plastic learning mechanism learns structured data better than its control.

It does **not** prove that EIDOS contains a biological fly brain.

### 2. Closed-loop autonomy benchmark

```bash
PYTHONPATH=. python core/autonomy_benchmark.py
```

The benchmark measures:

```text
observe → decide → act → observe real transition → reward → learn → retry
```

A trained agent must beat its untrained baseline in a deterministic world.

### 3. Reversible self-edit laboratory

```bash
PYTHONPATH=. python -m unittest -v tests.test_self_edit_lab
```

Candidate Python edits are staged and inspected before any live overwrite. Syntax errors and selected high-risk dynamic execution primitives are rejected.

The intended self-improvement pipeline is:

```text
proposal
→ isolated candidate
→ static checks
→ tests
→ benchmark comparison
→ policy/human gate
→ commit
```

### 4. Shared agent runtime

```bash
PYTHONPATH=. python bin/eidos_agents_terminal.py
```

No-argument `python eidos.py` and explicit `python eidos.py cli` enter the same shared Runtime Hub terminal. It exposes a common blackboard/event bus for:

- World
- Actions
- Characters
- Colony
- Neural/graph components
- Fly Lab
- agent messages and proposals

Action commands in this terminal create **proposals**, not uncontrolled execution.

---

## Architecture

EIDOS is not defined by one model.

```text
SER
 │
 ▼
EIDOS
 ├── World / perception
 ├── Persistent memory
 ├── Knowledge graph / activation
 ├── Colony / characters
 ├── Character lifecycle + genealogy
 ├── Decision / arbitration
 ├── Actions / body
 ├── Effect verification
 ├── Error memory / anti-library
 ├── Autonomy / study / research
 ├── Fly-inspired learning mechanisms
 └── Model/tool adapters
```

Language models may generate, explain, critique, summarize or propose. They are not automatically trusted as the source of truth and they are not the only place where EIDOS stores state.

### The rule for action

```text
claim ≠ action
action ≠ success
success requires observed effect
```

That distinction is essential to the project.

---

## The graph

EIDOS's graph has gone through several generations.

The original public README froze an early June snapshot at roughly **39K nodes**. That figure is historical, not current.

Later internal, dated snapshots documented growth beyond **1.2 million nodes**. For example:

| Snapshot | Nodes | Edges |
|---|---:|---:|
| 2026-09-21 | 1,209,572 | 1,523,665 |
| 2026-09-23 3D viewer reference | 1,210,789 | 1,524,718 |
| 2026-09-30 | 1,223,420 | 1,534,571 |

These are **dated measurements**, not a promise about the current live count.

More important than raw graph size is whether graph state changes later decisions. Recent development therefore focuses on causal reuse, verification, weight changes and effect-based learning rather than simply adding more nodes.

Read the full history: **[docs/EIDOS_EVOLUTION_DOCUMENTARY.md](docs/EIDOS_EVOLUTION_DOCUMENTARY.md)**.

---

## Colony and characters

Colony is EIDOS's persistent social/deliberative layer.

Characters can have:

- individual memory/state;
- role and specialization;
- reputation;
- synaptic-style relationships;
- proposals and debate;
- genealogy and inheritance mechanisms;
- learned associations.

Different historical stores contained different character/alma counts, so this README intentionally does **not** advertise one frozen number as the definition of Colony.

The current design rule is:

> **Colony advises; EIDOS arbitrates.**

A debate is useful only if its recommendations can be connected to evidence, an action or a later measurable decision.

---

## Character lifecycle and inheritance

EIDOS implements software lifecycle mechanisms referred to in the project as birth, genealogy and reproduction.

Those mechanisms can include:

- parent/child relationships;
- inherited synaptic/state data;
- personality/state merging;
- retained parent identities;
- genealogy records.

These are **software/data inheritance mechanisms**, not biological reproduction.

---

## Fly / Insect

The Insect line of research studies mechanisms inspired by complete insect connectomes and mushroom-body learning.

The project does **not** treat a connectome file as a ready-made living brain.

Useful mechanisms under investigation include:

- sparse coding / FlyHash;
- Kenyon-cell-like expansion;
- associative learning;
- reward-modulated plasticity;
- novelty detection;
- forgetting;
- recurrent/ring-like state;
- routing and selection;
- inheritance/mutation of small verified circuits.

Historical MaleCNS experiments documented large connectome datasets, but the correct integration standard is:

```text
external connectome
→ isolated experiment
→ negative control
→ measurable advantage
→ typed adapter
→ gated integration
```

Never:

```text
biological neuron ID = EIDOS concept
```

The public `core/fly_lab.py` is deliberately isolated from the live EIDOS graph.

---

## Artificial neural / synaptic mechanisms

EIDOS uses the language of neurons and synapses for several graph and character mechanisms.

For engineering purposes, a useful artificial synapse must have a measurable consequence:

```text
state/weight changes
→ later activation changes
→ later decision changes
→ effect can be measured
```

A stored weight that never affects any later computation is not treated as evidence of useful learning.

---

## World, body and actions

EIDOS contains multiple perception/action paths:

- accessibility trees / AT-SPI;
- OCR and visual perception;
- browser automation;
- desktop/window state;
- mouse/keyboard control;
- action verification;
- world-model components.

Desktop control is environment-dependent. USB Gadget HID remains a first-class low-level actuator alongside DOM/browser and desktop-specific routes; it is not replaced by Playwright/Selenium.

Historical X11/`xdotool` paths cannot be presented as equivalent to native KDE/Wayland input. On Wayland, the generic selector fails closed rather than pretending xdotool/XTest is valid. Privileged USB HID/uinput behavior is hardware-in-the-loop work and availability never implies authorization. Clean CI therefore tests virtual-X11 capability separately from hardware-in-the-loop validation on the real machine.

The real-machine test plan must verify:

```text
intent
→ target recognition
→ action
→ before/after observation
→ effect verdict
→ memory update
```

---

## Models as teachers, not identity

EIDOS can use local or remote models, but the architecture is intended to survive model replacement.

A model may provide:

- language;
- code suggestions;
- explanation;
- critique;
- summarization;
- planning hypotheses;
- embeddings or representations.

EIDOS should learn from **observable outputs, documentation, APIs, examples, benchmarks and effects**.

It should not claim to extract a provider's hidden proprietary reasoning or internal weights from ordinary interaction.

When a useful capability is discovered, the preferred direction is:

```text
observe behavior
→ extract rule/mechanism
→ reproduce locally
→ test against baseline
→ keep only if it improves measured behavior
```

---

## Memory and learning

EIDOS has accumulated multiple forms of persistent state over its development:

- working/context memory;
- SQLite stores;
- episodic records;
- motor/procedural memory;
- vector/semantic stores;
- character memory;
- error/anti-library memory;
- self-model and autobiographical records;
- graph activation and learned weights.

The current consolidation goal is not "store everything forever".

It is:

> **reuse the right experience at the next relevant decision.**

---

## Evidence levels

Documentation should distinguish four levels:

| Level | Meaning |
|---|---|
| **VERIFIED** | reproduced by test, measurement or observed external effect |
| **DOCUMENTED** | recorded in TASK/docs but not re-tested in the current environment |
| **INFERRED** | reasonable conclusion from several pieces of evidence |
| **EXPERIMENTAL** | hypothesis, prototype or future mechanism |

Terms such as *organism*, *consciousness*, *soul*, *alive* and *god of the Colony* are part of the EIDOS project vocabulary and world-building.

The existence of software self-models, autobiographical memory or metacognitive logs does not by itself scientifically prove subjective consciousness.

---

## Clean Ubuntu validation

GitHub Actions currently acts as an isolated Ubuntu validation environment.

The branch checks include:

- Python compilation;
- Fly Lab tests;
- Runtime Hub tests;
- autonomy benchmark;
- self-edit safety tests;
- virtual desktop/X11 capability;
- local headless-browser smoke tests.

This is intentionally separate from the real EIDOS installation.

Ubuntu CI can validate code and controlled environments. It cannot reproduce SER's real KDE/Wayland session, GPU stack, private databases, local models, devices or live browser state.

Those belong to hardware-in-the-loop validation.

---

## Installation

EIDOS is developed for Linux.

Start with:

```bash
git clone https://github.com/arku75/EIDOS.git
cd EIDOS

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Before starting services, read:

- **[docs/INSTALL.md](docs/INSTALL.md)**
- **[docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md)**
- **[SECURITY.md](SECURITY.md)**

Do not copy secrets into the repository.

Do not assume every historical service, port or private database exists on a fresh clone.

---

## Security model

EIDOS contains code capable of interacting with a computer and the web, so capability and safety must be separated.

Public-development rules:

- startup operation mode is fail-closed `PLAN`; `PLAN+EDIT` never bypasses constitution, ToolGuard, scope or operator authorization;
- capability availability is distinct from authority to execute it;

- secrets stay outside Git;
- action proposals are not automatically successful actions;
- destructive operations require explicit gates;
- self-edit candidates are staged first;
- tests and negative controls precede promotion;
- private databases are not used as CI fixtures;
- live-machine actions are tested separately from clean Ubuntu tests;
- a credential that ever appeared in Git history must be treated as exposed and rotated.

See **[SECURITY.md](SECURITY.md)**.

---

## Current consolidation priorities

The project is now less constrained by "how many modules can be added" and more by whether existing organs form one causal system.

Priority order:

1. **One runtime truth** — reduce duplicate paths and stale documentation.
2. **One measurable action loop** — perception → action → effect → learning.
3. **Reuse** — prove learned state affects later decisions.
4. **Colony accountability** — connect advice to outcomes/reputation.
5. **Character learning** — individual specialization with measurable transfer.
6. **Fly integration** — only mechanisms that beat controls.
7. **Portable installation** — fresh Linux clone must be reproducible.
8. **Wayland/hardware validation** — verify the real body on the target machine.
9. **Self-improvement** — staged, benchmarked and reversible.
10. **Documentation truth** — public claims must track evidence.

---

## Documentation

| Document | Purpose |
|---|---|
| [docs/EIDOS_EVOLUTION_DOCUMENTARY.md](docs/EIDOS_EVOLUTION_DOCUMENTARY.md) | Full human/evidence-based evolution of EIDOS |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architecture and data flow |
| [docs/AUTONOMY_LAB.md](docs/AUTONOMY_LAB.md) | How autonomy/self-edit claims are measured |
| [docs/INSTALL.md](docs/INSTALL.md) | Installation |
| [docs/USAGE.md](docs/USAGE.md) | CLI/API usage |
| [docs/BRIDGE.md](docs/BRIDGE.md) | Bridge integration |
| [docs/COLONY_CHARACTER.md](docs/COLONY_CHARACTER.md) | Character/connection integration |
| [docs/GUARDIANS.md](docs/GUARDIANS.md) | Runtime guardians |
| [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md) | Known gaps and unresolved problems |
| [SECURITY.md](SECURITY.md) | Security policy |

---

## History

EIDOS has been developed continuously since May 2026.

Its documented evolution includes:

- early graph construction and curation;
- persistent memory;
- Bridge/API;
- Colony and character lifecycle;
- GUI perception and body experiments;
- self-model/autobiographical mechanisms;
- software builders and language-learning paths;
- graph growth beyond one million nodes in dated internal snapshots;
- insect-inspired learning experiments;
- 3D graph visualization;
- increasingly strict effect verification;
- repeated audits that found false positives, disconnected modules and stale assumptions.

That history is intentionally preserved — including failures.

Read it here:

**[EIDOS — Story & documented evolution](docs/EIDOS_EVOLUTION_DOCUMENTARY.md)**

---

## License and contribution

EIDOS is **source-available, proprietary software** under the **EIDOS Sovereign Source License (ESSL) v1.0**.

The license permits private, non-commercial evaluation/learning under its stated conditions and does not make EIDOS open source.

Read **[LICENSE](LICENSE)** and **[CONTRIBUTING.md](CONTRIBUTING.md)** before making or distributing changes.

---

## Creator

**SER** — creator, architect and owner of EIDOS.

GitHub: [arku75/EIDOS](https://github.com/arku75/EIDOS)

---

> **EIDOS is not defined by the size of its graph or by whichever model is connected today.**
>
> The engineering goal is a persistent system in which experience changes memory, memory changes decisions, decisions change actions, and the effects of those actions become the next experience.
