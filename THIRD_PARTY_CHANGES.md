# THIRD_PARTY_CHANGES

This file is **mandatory** under the EIDOS Sovereign Source License (ESSL) v1.0,
Section 4. Anyone other than SER who modifies, extends, derives from, or proposes
any change to EIDOS **must** record every change here and disclose it to SER
before any use beyond local evaluation.

Each entry must include: author/identity, date, files/areas affected, a full
description, and the purpose of the change. Removing or falsifying entries
terminates the license automatically.

| Date | Author / Identity | Files / Area affected | Description | Purpose |
|------|-------------------|-----------------------|-------------|---------|
| 2026-10-04 | OpenAI ChatGPT, authorized by SER | `requirements.txt`, `.github/workflows/ci.yml`, `.gitignore`, `SECURITY.md`, `README.md` | Security hardening: update vulnerable dependency pins conservatively, stop CI from masking byte-compile failures, expand credential ignore rules, clarify secret-rotation policy, and sanitize README claims/obsolete runtime metrics. | Reduce public-repository security risk and improve technical accuracy without changing EIDOS runtime logic. |
| 2026-10-04 | OpenAI ChatGPT, authorized by SER | `docs/EIDOS_EVOLUTION_DOCUMENTARY.md`, `README.md` | Added an evidence-based human-readable history of EIDOS built from TASK snapshots, project dossiers, mobile restoration notes and preserved visual artifacts; linked it from the README and removed remaining stale/absolute claims. | Preserve the project's real evolution while keeping the public README concise, current and technically credible. |

| 2026-10-04 | OpenAI ChatGPT, authorized by SER | `AGENTS.md`, `.github/workflows/sanitation-ci.yml` | Removed a hard-coded Bridge API key from documentation, retired a stale restoration snapshot, and added isolated Ubuntu CI for syntax, packaging and safe tests. | Prevent credential reuse and make public-repo claims reproducible on a clean machine. |

---
© 2026 SER. All Rights Reserved. EIDOS is proprietary & source-available (ESSL v1.0).
