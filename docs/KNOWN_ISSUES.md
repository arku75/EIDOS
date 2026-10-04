# Known Issues & Honest Status

> Updated 2026-10-04.
>
> This file separates current verified gaps from historical limitations. Old metrics are
> not automatically treated as current truth.

## 1. Full clean-clone installation is not yet proven end-to-end

**Status: OPEN**

The code now compiles and the focused sanitation/autonomy tests run in clean Ubuntu CI.

However, "the tests pass" is not the same as "a completely new user can install and start every EIDOS subsystem".

A dependency-resolution CI job has been added. Full runtime/service startup remains a separate acceptance target.

## 2. ChromaDB strategy is internally inconsistent

**Status: OPEN — GitHub issue #8**

The public tree currently mixes:

- port 8766 and port 8767;
- external CLI service and custom Python server paths;
- raw-HTTP assumptions and `chromadb.HttpClient`;
- historical client/server version constraints.

This must be resolved with an isolated end-to-end vector-store test, not a documentation-only change.

## 3. Wayland body is not equivalent to the historical X11 body

**Status: OPEN / HARDWARE-IN-THE-LOOP**

Historical desktop-control code uses X11-era tools and assumptions.

Clean CI can prove virtual X11 primitives. It cannot prove native control of SER's KDE/Wayland session.

The real body needs target-hardware tests for:

- pointer movement;
- click;
- keyboard input;
- focus/window targeting;
- before/after screen effect;
- failure diagnosis.

## 4. Graph size is not the same thing as useful reasoning

**Status: ACTIVE RESEARCH**

Historical snapshots exceeded 1.2M nodes, but the important question is how much state is reused causally.

The current direction is to measure:

- which nodes/edges are activated;
- whether learned weights change later choices;
- retrieval/reuse rate;
- effect-verifiable decisions.

Old claims such as "~89% usage_count=0" are retained as historical diagnostics unless re-measured on the current private graph.

## 5. Many organs historically existed before they were wired together

**Status: ACTIVE CONSOLIDATION**

EIDOS accumulated research engines, memory systems, Colony paths, self-models, body components and builders across many sessions.

The current Runtime Hub is an integration/inspection facade, not proof that every old subsystem already participates in one causal loop.

The acceptance loop is:

```text
intent → mechanism → action → observed effect → verifier → memory/weights → later changed decision
```

## 6. Colony volume is not the same as deliberation quality

**Status: ACTIVE RESEARCH**

Historical audits found many messages/elevations that did not reliably map to effect-verifiable actions.

Desired end state:

- claims are attributable to characters;
- recommendations are scored against later outcomes;
- reputation changes from evidence;
- character specialization is measurable.

## 7. Character counts vary by store/snapshot

**Status: DOCUMENTATION FIXED, RUNTIME CENSUS PENDING**

Historical documentation used fixed numbers such as 12 characters.

Later audits found different counts for base characters, souls/identities and descendants.

The README now avoids presenting one frozen number as "the Colony".

## 8. Fly/Insect is not yet a live biological-connectome brain

**Status: EXPERIMENTAL**

The public Fly Lab proves only isolated sparse/plastic learning experiments.

Real connectome integration requires:

- dataset provenance/checksum/license;
- typed neuron/circuit mapping;
- negative controls;
- measurable improvement;
- explicit isolation from the live graph until verified.

## 9. Self-editing is staged, not autonomous live overwrite

**Status: SAFE PROTOTYPE**

`core/self_edit_lab.py` can validate/stage candidates, but EIDOS does not yet have authority to replace arbitrary live core files based solely on its own judgment.

That is intentional.

## 10. Model "distillation" needs precise language

**Status: ACTIVE RESEARCH**

EIDOS may learn from model outputs, examples, APIs, documentation and benchmarks.

Ordinary interaction does not expose another model's hidden proprietary chain-of-thought or internal weights.

Future distillation work must define:

- source dataset;
- teacher outputs that may legally/technically be used;
- student model/mechanism;
- held-out benchmark;
- baseline and regression criteria.

## 11. Secret history requires rotation, not only deletion

**Status: MANUAL SECURITY TASK**

Removing a credential from the current tree does not erase it from Git history.

Any previously valid credential must be revoked/rotated and then reviewed with secret scanning.

## 12. Repository homepage metadata is stale

**Status: OPEN — GitHub issue #7**

The repository description still advertises an early ~39K-node snapshot and old wording.

The code connector used for the sanitation work does not expose repository-description/branch-protection settings, so this remains a GitHub settings task.

## 13. GitHub "Code scanning AI findings" currently fails for quota, not code

**Status: EXTERNAL**

The GitHub-generated AI review workflow returned HTTP 402 because its monthly model quota was exhausted.

This should not be interpreted as a failing EIDOS security test.

The project-owned `sanity` and `sanitation-ci` checks remain the relevant reproducible CI signals.

## 14. Third-party vendored tools contain provider names

**Status: EXPECTED**

Some vendored/upstream tool documentation may mention supported clients such as Claude Desktop.

Those names are not treated as EIDOS authorship or branding and should not be mass-edited unless the vendored component itself is forked/maintained.

First-party unused Claude-specific adapter code has been removed from the sanitation branch.

---

## Historical findings worth re-testing on the private live system

The internal TASK history documented, at different points:

- stalled learning loops;
- weak reuse of stored graph knowledge;
- disconnected action/verifier paths;
- false-positive autonomy tests;
- Colony advice not reaching effect verification;
- Chroma/threading problems;
- X11/HiDPI targeting errors;
- service/unit drift;
- synthetic success that failed harder language/task banks.

These are valuable regression targets, not automatically current bugs.

---

## Definition of "fixed"

A problem is not closed because code was written.

Prefer this evidence chain:

```text
reproduce failure
→ make minimal change
→ test positive case
→ test negative/control case
→ verify no regression
→ record result
```
