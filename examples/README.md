# EIDOS examples

Small, runnable examples. Start EIDOS first (see [../docs/INSTALL.md](../docs/INSTALL.md))
and set `EIDOS_BRIDGE_KEY` (from `~/.eidos/secrets.env`).

| File | What it shows |
|:-----|:--------------|
| [`quickstart.sh`](quickstart.sh) | 3 curl calls: health → research (EIDOS learns) → recall |
| [`borrower_agent.py`](borrower_agent.py) | A toolless AI that **borrows EIDOS's powers** via the Bridge — see [../docs/BRIDGE.md](../docs/BRIDGE.md) |
| [`create_colony_character.py`](create_colony_character.py) | Birth a **Colony character bound to your Bridge AI** — see [../docs/COLONY_CHARACTER.md](../docs/COLONY_CHARACTER.md) |

```bash
export EIDOS_BRIDGE_KEY=...            # from ~/.eidos/secrets.env
bash examples/quickstart.sh
python3 examples/borrower_agent.py "explain io_uring"
PYTHONPATH=. python3 examples/create_colony_character.py
```
