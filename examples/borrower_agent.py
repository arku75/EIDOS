#!/usr/bin/env python3
"""
borrower_agent.py — A "powerless" AI that borrows EIDOS's powers via the Bridge.

The external AI here has NO tools of its own. By talking JSON to the EIDOS Bridge
(port 8003) it gains the power to recall memory, research the live web and read
pages — using EIDOS's body and brain. Every call also teaches EIDOS.

Run EIDOS first (see docs/INSTALL.md) and export EIDOS_BRIDGE_KEY.

    export EIDOS_BRIDGE_KEY=...        # from ~/.eidos/secrets.env
    python3 examples/borrower_agent.py "how does WireGuard handshake work?"

See docs/BRIDGE.md for the full guide.
"""
import os
import sys
import requests

BRIDGE = os.environ.get("EIDOS_BRIDGE", "http://127.0.0.1:8003")
KEY = os.environ.get("EIDOS_BRIDGE_KEY")
H = {"X-API-Key": KEY or "", "Content-Type": "application/json"}


def power(endpoint: str, **payload):
    """Invoke one of EIDOS's powers over the Bridge."""
    r = requests.post(f"{BRIDGE}{endpoint}", json=payload, headers=H, timeout=120)
    r.raise_for_status()
    return r.json()


def handle(goal: str) -> dict:
    # 1) Does EIDOS already know it? (free, instant — reuse its memory)
    known = power("/semantic", query=goal)
    if known.get("answer"):
        return {"from": "memory", "answer": known["answer"]}

    # 2) Unknown → borrow EIDOS's research power (it reads sources AND learns)
    res = power("/investiga", topic=goal)

    # 3) Optionally open the first source and read it
    if res.get("sources"):
        try:
            page = power("/browse", url=res["sources"][0])
            res["first_source_excerpt"] = (page.get("text") or "")[:1500]
        except Exception:
            pass
    return {"from": "research", **res}


if __name__ == "__main__":
    if not KEY:
        sys.exit("Set EIDOS_BRIDGE_KEY first (see docs/INSTALL.md).")
    goal = " ".join(sys.argv[1:]) or "explain Linux cgroups v2"
    out = handle(goal)
    print(f"[{out.get('from')}] {out.get('answer') or out.get('definition') or out}")
