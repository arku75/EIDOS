# EIDOS — Professional Portfolio

> **Persistent local intelligence that can remember, arbitrate, act, verify effects, learn from them, and reuse what it learned.**

EIDOS is a single-developer research and product project created by **Luka Sorta** in Slovenia. Historical and internal project material also uses **SER** as the operator/owner identifier. EIDOS is designed as a **local-first persistent agent architecture**, not as a thin chat wrapper around one model.

The core product idea is simple:

```text
experience
→ persistent memory
→ graph activation
→ specialist / Colony advice
→ EIDOS arbitration
→ action
→ observed effect
→ verification
→ learning
→ later reuse
```

The model can change. The identity, state, memory, learned capabilities, policies and evidence trail are intended to remain.

---

## Creator

**Luka Sorta** is an independent AI-native builder focused on agentic systems, local AI, automation and rapid prototyping. The project is developed by defining system behavior, architecture, verification criteria and product direction, then using AI coding agents and local/cloud tooling as implementation collaborators.

The public positioning is intentionally evidence-based: no traditional software-engineering credentials, employer history or scientific claims are implied by the project unless separately verified.

---

## Why EIDOS exists

Most AI assistants are session-centric: useful context is compressed into prompts, tool use is often treated as success when a command merely returned, and replacing the model can mean replacing the "mind".

EIDOS explores a different architecture:

- **persistent state outside the LLM**;
- **local/private operation** as a first-class deployment mode;
- **effect-based verification** instead of trusting confident output;
- **reusable learned capabilities** rather than one-off tool calls;
- **Colony / specialist deliberation** with one final arbiter;
- **desktop, browser and tool interfaces** as a body, gated by policy;
- **reversible self-improvement** through isolated candidates, tests and rollback;
- **clear evidence levels** separating verified behavior from research vision.

Two engineering rules define the project:

> **HEARD ≠ VERIFIED**

> **claim ≠ action ≠ success**

---

## Architecture

```text
WORLD / COMPUTER
      │
      ▼
PERCEPTION
      │
      ▼
MEMORY + KNOWLEDGE GRAPH
      │
      ▼
COLONY / SPECIALISTS
      │
      ▼
EIDOS ARBITRATION
      │
      ▼
PLAN → ACTION / BODY / BRIDGE
      │
      ▼
OBSERVE REAL EFFECT
      │
      ▼
VERIFY → LEARN → GENERALIZE → REUSE
      └──────────────────────────────↺
```

The intended invariant is that a stored memory or learned weight matters only when it changes a later decision or measurable outcome.

---

## What is already demonstrable

The public repository contains reproducible components for:

- persistent graph and memory layers;
- Colony / character mechanisms;
- a shared Runtime Hub for agents and proposals;
- a local Bridge/API for external tools and AI systems;
- closed-loop autonomy experiments;
- reversible self-edit experiments;
- isolated insect-inspired associative-learning experiments;
- desktop/browser/perception modules;
- clean Ubuntu CI for public-clone validation;
- explicit security and evidence rules.

Live-machine work has additionally documented desktop-body certification, OCR/perception checks and generic tool-learning/reuse experiments. Those live results are kept distinct from what a clean public clone can reproduce.

See [STATUS_2026-10-06.md](STATUS_2026-10-06.md) for the current evidence boundary.

---

## What makes the project different

### 1. Model-independent identity

Models can be replaced or combined. EIDOS is intended to keep identity, memory, policy, graph state, capability records and accumulated evidence outside any single provider.

### 2. Effect verification

A model saying "done" is not enough. EIDOS is designed to inspect the world before and after an action and decide whether the intended effect actually occurred.

### 3. Learning that must survive

Learning is not defined as saving text. A capability is valuable only when persisted state is recovered later and changes behavior.

### 4. Colony advises; EIDOS arbitrates

Multiple characters/specialists may debate, critique and propose. They do not become independent authorities. One arbitration layer owns the final decision under the operator's constitution.

### 5. Local-first and private

The architecture is built around local state and on-premise execution. Remote models may assist, but they are optional organs, not the sole repository of identity or memory.

### 6. A gated body

Browser, desktop, mouse/keyboard, shell and other actuators are treated as capabilities with explicit authorization boundaries. Availability never equals permission.

---

## Current product position

EIDOS should be presented as an **advanced local-agent platform in active productization**, not as proven AGI, biological life or consciousness.

The strongest credible positioning is:

> **A persistent, local-first agent runtime that connects memory, graph state, multi-agent deliberation, computer-use interfaces, effect verification and learning into one measurable loop.**

This is stronger and more defensible than advertising raw graph size or calling every experimental mechanism complete.

---

## Commercial use cases

EIDOS is most naturally sold where privacy, persistence and controlled computer use matter:

- private on-prem AI assistants;
- AI operator / desktop automation systems;
- research environments for persistent agents;
- local knowledge and memory systems;
- enterprise agent runtimes that need auditable actions;
- AI-tool integration through the Bridge;
- custom local deployments for teams that cannot send state to a cloud service.

---

## Commercial path

The recommended commercial funnel is:

```text
public technical proof
→ guided demo
→ scoped paid pilot
→ private deployment
→ annual license + support
→ enterprise/OEM integration
```

A buyer should see evidence before architecture mythology: a short demo, a reproducible test pack, a security model, a bounded pilot and an explicit list of what is and is not verified.

See [PRODUCT.md](PRODUCT.md) for packaging and go-to-market.

---

## Intellectual property

EIDOS is **proprietary and source-available** under the **EIDOS Sovereign Source License (ESSL) v1.0**. The public repository exposes inspectable architecture and reproducible experiments while private runtime state, secrets and machine-specific data remain outside the repository.

Commercial licensing, redistribution and competing-product rights are governed by the license and written agreements.

---

## Current priorities

1. One canonical repository and one public product story.
2. Reproducible clean-clone installation.
3. One unified CLI/runtime entry point.
4. Held-out proof of learned-capability transfer and reuse.
5. Browser DOM/CDP + pixel perception as one verified loop.
6. Colony advice connected to measurable outcomes and reputation.
7. Continuous behavioral distillation with negative controls.
8. Calibrated plasticity and graph consolidation.
9. Full self-repair E2E with rollback evidence.
10. Professional demo, landing page, releases and buyer-facing documentation.

---

## Links

- **Canonical repository:** https://github.com/arku75/EIDOS
- **Architecture:** [ARCHITECTURE.md](ARCHITECTURE.md)
- **Evolution / engineering history:** [EIDOS_EVOLUTION_DOCUMENTARY.md](EIDOS_EVOLUTION_DOCUMENTARY.md)
- **Install:** [INSTALL.md](INSTALL.md)
- **Known issues:** [KNOWN_ISSUES.md](KNOWN_ISSUES.md)
- **Security:** ../SECURITY.md
- **Product & commercialization:** [PRODUCT.md](PRODUCT.md)
- **Current evidence boundary:** [STATUS_2026-10-06.md](STATUS_2026-10-06.md)

For evaluation, partnership or licensing, use the repository's **GitHub Discussions** channel so the first contact remains public, auditable and separate from private deployment details.
