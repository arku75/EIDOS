## What this PR does

## Why

## Checklist
- [ ] Respects `constitution.toml` (no escalation of freedom, no secrets committed)
- [ ] New feature → new module (don't rewrite core except delimited extensions)
- [ ] No `max_tokens` added to LLM calls; no direct `sqlite3.connect()` (use `core.db.get_conn`)
- [ ] Docs updated if behavior changed
