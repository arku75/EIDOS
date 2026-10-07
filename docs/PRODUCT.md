# EIDOS — Product & Commercialization

## Product thesis

EIDOS should not be sold as "another AI chat app".

It should be sold as a **persistent local-agent runtime** for organizations and advanced users who need:

- memory that survives sessions and model changes;
- local/private state;
- multiple specialist agents without fragmented authority;
- controlled access to tools and computer-use interfaces;
- observed-effect verification;
- reusable capability learning;
- a local Bridge that can expose selected EIDOS capabilities to other AI systems.

The commercial value is the **runtime, integration, verification and persistence layer**, not the size of one neural graph snapshot.

---

## Product packaging

### EIDOS Evaluation

**Purpose:** technical evaluation and research.

- source-available under ESSL v1.0;
- public architecture and testable laboratories;
- clean-clone validation target;
- no private runtime state, customer data or proprietary live graph distribution;
- non-commercial rights only as defined by the license.

**Goal:** make it easy for a serious evaluator to understand what is real.

### EIDOS Private

**Purpose:** single-customer local deployment.

- deployment on customer-controlled Linux hardware;
- private memory / graph state;
- model adapters;
- Bridge/API integration;
- policy and authorization configuration;
- installation and operational support;
- customer-specific capability validation.

**Revenue:** deployment fee + annual commercial license + support.

### EIDOS Integration / Bridge

**Purpose:** let an existing AI product borrow EIDOS persistence, tools, memory and verified action interfaces.

- local Bridge/API;
- scoped capabilities;
- audit/effect records;
- adapters for the customer's existing model stack;
- optional desktop/browser integration.

**Revenue:** integration project + annual SDK/runtime license.

### EIDOS Enterprise / OEM

**Purpose:** private deployment inside a company product or infrastructure.

- custom packaging;
- enterprise security controls;
- connectors and policy integration;
- support/SLA;
- OEM or redistribution rights under a negotiated license.

**Revenue:** negotiated annual or multi-year license.

---

## Who should buy first

Do not begin with mass consumer distribution. The first customers should have a concrete reason to pay for local persistence and controlled action.

Best early profiles:

1. **AI engineering teams** building local/on-prem agents.
2. **Privacy-sensitive organizations** that cannot place long-term memory in third-party cloud systems.
3. **Automation/integration teams** that need an agent to operate several local tools while preserving state.
4. **Research groups** working on persistent agents, computer use or multi-agent coordination.
5. **Specialized integrators** that already sell private AI deployments and need a differentiating runtime.

---

## Go-to-market

### Phase 1 — Proof package

A buyer-facing proof package should contain:

- a 3–5 minute demo;
- one architecture page;
- one current-status/evidence page;
- reproducible public tests;
- security model;
- known limitations;
- commercial contact path.

The demo should show one causal loop, not twenty claims:

```text
goal
→ perception
→ decision
→ action
→ observed effect
→ verification
→ persisted learning
→ reuse
```

### Phase 2 — Guided evaluation

Run a guided technical session using a clean environment and a bounded live-machine demo.

Deliverables:

- deployment requirements;
- customer's use case;
- one acceptance test;
- security boundary;
- success metric;
- written pilot scope.

### Phase 3 — Paid pilot

A pilot should solve one real workflow and have a fixed definition of done.

Examples:

- operate a local application and verify state changes;
- retain and reuse organization-specific procedural knowledge;
- expose selected local capabilities to an existing AI via Bridge;
- perform a multi-step research/action workflow with auditable effects.

### Phase 4 — License and support

Convert successful pilots into:

- annual runtime license;
- maintenance/support;
- integration work;
- optional OEM/redistribution rights.

---

## What not to sell

Avoid making the commercial pitch depend on claims that are hard to prove or scientifically loaded:

- "conscious";
- "alive" as a factual scientific claim;
- AGI;
- a fixed Colony count as a proxy for quality;
- a raw node count as proof of intelligence;
- "self-improving" without rollback and benchmark evidence.

Use those ideas as project language or long-term research themes, while the product pitch stays grounded in observable behavior.

---

## Product moat

The defensible moat is a combination of:

- accumulated architecture and engineering history;
- persistent memory/graph integration;
- effect-verification discipline;
- local computer-use interfaces;
- Colony/arbitration design;
- capability-learning records;
- private runtime state and deployment know-how;
- ESSL commercial licensing;
- continued integration of the components into one causal system.

The moat is **not** the public source code alone.

---

## GitHub implementation

The GitHub presence should have one clear funnel:

```text
Luka Sorta / GitHub profile
→ arku75/EIDOS
→ README
→ Portfolio
→ Product
→ Verified status
→ Demo / Discussions
```

### Canonical repository

**Canonical:** `arku75/EIDOS`

`EIDOS-OFFICIAL` should become a redirect/archive surface rather than a second "official" source of truth.

### Branch discipline

- `main`: stable public baseline.
- `product/*`: portfolio/product documentation.
- `feature/*`: isolated implementation work.
- PR required before merging product or feature branches.
- protect `main` against force-push/deletion.
- require core CI checks before merge.

### Public releases and versioning

The June 2026 `v1.0.0` label is preserved as a historical publication label; it is **not** treated as proof that the current product reached 1.0 maturity.

The active reconstruction uses a **pre-1.0 `0.x` capability-history model**. Exact retrospective milestones must be backed by dated repository or machine evidence before tags are created. Until that genealogy is closed, do not manufacture version tags simply to make the repository look mature.

The next public release should be a `0.x` preview only after clean installation and core public acceptance tests are reproducible. See [ROADMAP_0X.md](ROADMAP_0X.md).

---

## Demo strategy

A professional demo should be recorded around **one measurable story**.

Recommended first demo:

1. Start with a clean task.
2. Show what EIDOS already remembers.
3. Let it plan with Colony/specialists.
4. Authorize one action.
5. Show the before/after state.
6. Show the verification verdict.
7. Restart/reload the relevant runtime boundary.
8. Repeat a related task and show reuse.

A successful demo makes persistence and verification visible without exposing private data.

---

## Buyer-facing checklist

Before active selling:

- [ ] canonical repo and duplicate redirect;
- [ ] main branch protection;
- [ ] clean install test from public clone;
- [ ] one current product-preview release;
- [ ] Pages/landing enabled;
- [ ] 3–5 minute demo video;
- [ ] no stale metrics in repository metadata;
- [ ] secret-rotation issue closed;
- [ ] security and known limitations linked from the first screen;
- [ ] one paid-pilot template and acceptance test.

---

## Commercial principle

> **Sell verified outcomes, not mythology.**

EIDOS becomes commercially strong when a buyer can see that persistent experience changes future decisions and that the resulting actions are measurable, controllable and private.
