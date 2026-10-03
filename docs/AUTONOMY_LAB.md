# EIDOS Autonomy Lab

This document defines what the clean Ubuntu runner can and cannot prove.

## Closed-loop benchmark

`core/autonomy_benchmark.py` measures a small world where an agent must:

1. observe state;
2. choose an action;
3. receive the actual transition and reward;
4. update its policy;
5. repeat;
6. beat an untrained negative control.

A pass demonstrates **measurable adaptation in a synthetic task**. It does not demonstrate general intelligence.

## Self-edit lab

`core/self_edit_lab.py` stages candidate Python edits in a temporary directory and rejects syntax errors and high-risk dynamic execution. It does **not** overwrite the live checkout.

A future self-improvement loop should be:

```
proposal -> stage -> static checks -> tests -> benchmark delta -> human/policy gate -> commit
```

Never:

```
model says "better" -> overwrite live code
```

## Desktop and browser

GitHub Actions is a headless VM. It can test:

- a virtual X11 display with Xvfb;
- keyboard/mouse primitives with xdotool;
- a local deterministic browser page;
- headless Chromium/Chrome DOM/screenshot behavior.

It cannot reproduce SER's real KDE/Wayland desktop, GPU stack, personal browser profile, devices or live EIDOS databases. Those require a second hardware-in-the-loop suite on the Dell.

## Learning and "training"

EIDOS can be tested for several forms of adaptation without retraining a foundation model:

- graph weight updates;
- reinforcement/policy learning;
- character reputation and synaptic-style updates;
- Fly Lab associative plasticity;
- memory retrieval improvement;
- code-patch selection by tests/benchmarks.

Foundation-model fine-tuning or distillation is a separate experiment and should only be added when a concrete dataset, baseline, compute budget and evaluation set exist.

## Evidence standard

Every capability should expose:

- baseline;
- intervention/learning;
- held-out evaluation;
- negative control;
- effect verification;
- rollback path.

Claims such as "better than a human", "conscious", "alive" or "better than every model" are not test results unless a defined benchmark actually measures them.
