# EIDOS Architecture

> Current public architecture reference — 2026-10-04.
>
> This document describes the **shape of the system**, not a frozen runtime snapshot.
> Service counts, graph size, character counts and enabled models vary by installation.

## Core loop

```text
WORLD / INPUT
   ↓
PERCEPTION
   ↓
MEMORY + GRAPH
   ↓
COLONY / SPECIALISTS
   ↓
ARBITRATION
   ↓
ACTION PROPOSAL
   ↓
SAFETY / POLICY GATES
   ↓
ACTION
   ↓
OBSERVED EFFECT
   ↓
VERIFICATION
   ↓
LEARNING / REPUTATION / WEIGHTS / MEMORY
   ↺
```

The key rule is that **a generated claim is not evidence of an external effect**.

## Main subsystems

| Area | Public components | Role |
|---|---|---|
| World / perception | `ui_world_model.py`, `eidos_world_engine.py`, perception/screen modules | Represent and observe environment state |
| Memory / graph | SQLite stores, semantic/vector paths, knowledge reasoners | Persistent state and retrieval |
| Colony | `colony_community.py`, lifecycle and character modules | Deliberation, specialization, social memory |
| Characters | `character_neuron.py`, `character_lifecycle.py` | Individual state, synaptic-style links, genealogy/inheritance |
| Arbitration | YO/reasoning/decision paths | Turn proposals into gated decisions |
| Actions | action system/executor, body/browser/UI paths | Produce external effects |
| Verification | action verifier, effect judges, negative controls | Check whether the intended effect occurred |
| Learning | RL, error memory, graph updates, reputation/weights | Change future behavior from evidence |
| Fly Lab | `fly_lab.py` | Isolated insect-inspired sparsity/plasticity experiments |
| Runtime Hub | `runtime_hub.py` | Shared inspection/event/blackboard facade |
| Self-edit Lab | `self_edit_lab.py` | Stage and inspect candidate code changes |
| Autonomy Lab | `autonomy_benchmark.py` | Reproducible learn-from-effects benchmark |

## Runtime Hub

`core/runtime_hub.py` is deliberately conservative.

Importing it must not:

- start GUI automation;
- start network services;
- execute a proposed system action;
- write Fly experiments into the live graph.

It exposes component availability, a shared blackboard/event bus and action **proposals**.

The intended direction is one observable runtime spine rather than many disconnected organs.

## Colony

Colony is a deliberative/social layer, not a fixed number of prompts.

Historical stores contained different censuses of base characters, souls/identities and descendants. Therefore the architecture does not freeze one number as "the Colony".

The rule is:

> **Colony advises; EIDOS arbitrates.**

Useful Colony output should eventually be linked to:

- the claim it made;
- the action chosen;
- the observed result;
- later reputation/credit.

## Characters and artificial synapses

Character lifecycle code can represent identity, parentage, inheritance and per-character associations.

For a synaptic-style mechanism to count as useful learning, the project expects a causal chain:

```text
weight/state changes
→ later activation changes
→ later decision changes
→ effect can be measured
```

A stored number that never changes downstream computation is not evidence of useful plasticity.

## Fly / Insect architecture

The Fly line transfers **mechanisms**, not biological identity.

Target pipeline:

```text
external connectome/dataset
→ isolated loader
→ sparse circuit experiment
→ negative/shuffled control
→ measurable advantage
→ typed adapter
→ gated integration
```

Candidate mechanisms include sparse coding, Kenyon-cell-like expansion, novelty, reward-modulated plasticity, forgetting, routing and recurrent state.

The public Fly Lab is isolated from the live EIDOS graph.

## Models and tools

Models are adapters/teachers/components, not EIDOS's persistent identity.

They may provide:

- language;
- code proposals;
- critique;
- summarization;
- planning hypotheses;
- embeddings/representations.

Outputs remain claims until verified where verification is possible.

EIDOS should learn from observable behavior, documentation, APIs, examples and benchmarks. It must not pretend ordinary interaction reveals a provider's hidden proprietary chain-of-thought or internal weights.

## Body / desktop

EIDOS has several action/perception paths:

- accessibility APIs;
- OCR/vision;
- browser automation;
- X11-style input;
- window state;
- desktop actions;
- effect verification.

X11 and Wayland are not interchangeable.

The clean Ubuntu CI can validate virtual X11 primitives and headless browser behavior. The real KDE/Wayland body must be validated separately on target hardware.

## Persistent stores

Runtime databases live outside the public source tree, typically under `~/.eidos`.

The public repo must not rely on private databases being present for syntax/unit tests.

Database access should prefer the project's unified DB layer where applicable and preserve rollback/backups for migrations.

## Vector memory / ChromaDB

The first-party topology now has one canonical server implementation,
`core/eidos_chroma_server.py`, on port **8767**. The historical bin entrypoint
is retained only as a compatibility wrapper.

GitHub issue #8 remains open because topology is not the same as runtime
compatibility. The remaining acceptance gate is an isolated integration test
covering heartbeat, collection creation, upsert/query/count, restart persistence
and fallback behavior with the supported Chroma dependency.

## Services and ports

Historical documents listed fixed service counts. That is no longer treated as architecture truth.

A service/port is only "active" when measured in a specific runtime snapshot.

Common historical endpoints include Bridge/web/vector services, but a clean clone must discover its configured services rather than assume the private machine's topology.

## Self-improvement

Target code-improvement flow:

```text
proposal
→ stage in isolation
→ static validation
→ tests
→ benchmark delta
→ policy/human gate
→ commit
→ later rollback if regression appears
```

Self-modification must never use its own prose as the sole proof that the change improved the system.

## Evidence labels

Architecture and documentation should use:

- **VERIFIED** — reproduced by test/measurement/effect;
- **DOCUMENTED** — recorded historically but not re-tested now;
- **INFERRED** — conclusion supported by multiple observations;
- **EXPERIMENTAL** — hypothesis/prototype/future mechanism.

## Validation layers

EIDOS now distinguishes three environments:

1. **Clean Ubuntu CI** — public checkout, controlled tests.
2. **Sanitized hardware worktree** — real machine, isolated from live EIDOS.
3. **Live EIDOS** — private data/services/hardware.

A capability should move inward only after it passes the previous layer.

See also:

- [AUTONOMY_LAB.md](AUTONOMY_LAB.md)
- [KNOWN_ISSUES.md](KNOWN_ISSUES.md)
- [EIDOS_EVOLUTION_DOCUMENTARY.md](EIDOS_EVOLUTION_DOCUMENTARY.md)
