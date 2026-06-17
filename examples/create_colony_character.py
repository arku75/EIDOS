#!/usr/bin/env python3
"""
create_colony_character.py — Give birth to a Colony character bound to a Bridge AI.

The character is born from an "api_llm" connection (your Bridge AI). It then grows
its own knowledge subgraph and Hebbian synapses in the background. See
docs/COLONY_CHARACTER.md.

Run from the EIDOS repo root:

    PYTHONPATH=. python3 examples/create_colony_character.py
"""
import os
import sqlite3

from core.character_lifecycle import get_lifecycle

BRIDGE = os.environ.get("EIDOS_BRIDGE", "http://127.0.0.1:8003")


def main():
    lc = get_lifecycle()
    name = lc.birth_from_connection(
        name="claude_bridge",                  # → colony_claude_bridge
        connection_type="api_llm",             # learns from an LLM-style source
        connection_data={
            "provider": "claude-via-bridge",
            "endpoint": f"{BRIDGE}/talk",
            "note": "External assistant reached through the EIDOS Bridge",
        },
        emoji="🌉",
        target_nodes=60,
    )
    print("born:", name or "(already existed)")

    # Show current Colony characters straight from the lifecycle DB
    db = os.path.expanduser("~/.eidos/lifecycle.db")
    if os.path.exists(db):
        con = sqlite3.connect(db)
        print("\nColony characters (name, status, absorption%, nodes/target):")
        for row in con.execute(
            "SELECT name, status, absorption_pct, knowledge_nodes, target_nodes "
            "FROM characters ORDER BY birth_date DESC LIMIT 15"
        ):
            print("  ", row)
        con.close()


if __name__ == "__main__":
    main()
