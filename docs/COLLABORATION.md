# Collaborating on EIDOS

EIDOS is an active, source-available research and product project created by **Luka Sorta**. Contributions are welcome when they improve evidence, portability, safety, integration quality or reproducibility.

## Where help is most valuable

The project currently benefits most from contributors with experience in:

- Linux desktop automation, KDE/Wayland and accessibility APIs;
- browser automation, DOM/CDP, Playwright and pixel/OCR fallback;
- local LLM runtimes and model orchestration;
- agent evaluation, action-effect verification and reproducible benchmarks;
- persistent memory, knowledge graphs and retrieval systems;
- multi-agent coordination and arbitration;
- packaging, clean installation and CI on fresh Linux environments;
- security boundaries, sandboxing, rollback and capability authorization;
- human-computer interaction for a dedicated EIDOS workspace / Desktop 2.

## Contribution standard

A contribution should distinguish among:

1. **implemented** — code exists;
2. **wired** — the runtime actually reaches it;
3. **executed** — the path has been run;
4. **measured** — evidence shows what changed;
5. **reused** — the result affects a later task or decision.

A convincing explanation is not a test result.

## Before opening a pull request

- start from the current public branch;
- keep changes scoped and reversible;
- do not add secrets, private runtime state or machine-specific data;
- include tests or a reproducible verification procedure;
- document limitations and negative results;
- do not describe experimental mechanisms as production-ready;
- do not make claims of AGI, consciousness or biological equivalence.

## Good first contribution areas

- clean-clone installation fixes;
- documentation/reproducibility gaps;
- isolated benchmark improvements;
- Browser DOM/CDP + pixel integration tests;
- Wayland portability;
- deterministic capability-reuse tests;
- packaging and release automation.

## Research / product collaboration

For research, technical evaluation, deployment pilots or integration partnerships, open a GitHub Discussion with a bounded use case and desired acceptance criteria.

The commercial principle is simple:

> **Sell and evaluate verified outcomes, not mythology.**

See [PORTFOLIO.md](PORTFOLIO.md), [PRODUCT.md](PRODUCT.md) and [STATUS_2026-10-07.md](STATUS_2026-10-07.md).
