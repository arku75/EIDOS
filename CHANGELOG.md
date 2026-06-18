> ⚖️ **EIDOS © 2026 SER · Licencia [ESSL v1.0](LICENSE) — propietaria, source-available.** Prohibida la replicación, el uso comercial y construir un producto competidor. Todo cambio o propuesta debe documentarse en `THIRD_PARTY_CHANGES.md` y comunicarse a SER. EIDOS **no** es open source.

# Changelog

All notable changes to EIDOS are documented here. Format: [Keep a Changelog](https://keepachangelog.com/).

## [1.0.0] — 2026-06-17
### First public release
- Neural knowledge graph (~39k nodes) + 4-layer memory (working / ChromaDB / episodic / procedural).
- **Bridge** (`:8003`): lend any AI EIDOS's powers (browse, search 61 engines, vision, memory) while EIDOS learns — see `docs/BRIDGE.md`.
- **Colony**: AI characters that learn and reproduce — see `docs/COLONY_CHARACTER.md`.
- **Body (BOM)**: bezier mouse + screen perception (gated behind `EIDOS_BOM=1`, dry by default).
- Interactive neural graph via GitHub Pages.
- Docs: INSTALL, USAGE, BRIDGE, COLONY_CHARACTER, KNOWN_ISSUES (honest), ARCHITECTURE.
- Runnable `examples/`.
- Hardened `.gitignore`: no secrets, databases, browser data or private notes in the public tree.

### Known issues
See `docs/KNOWN_ISSUES.md` — honest list (reuse not wired, crawl→conclusion pending, BOM only dry-run, search paths not unified).
