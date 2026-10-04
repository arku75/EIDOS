#!/usr/bin/env python3
"""Compatibility entrypoint for the canonical EIDOS Chroma microservice.

Historical versions implemented a second HTTP server here and attempted to
instantiate core.colony_chroma.ChromaMemory in an "embedded" mode that the
current client no longer implements. Keep this path for old scripts, but route
all behavior to core.eidos_chroma_server so there is one server contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.eidos_chroma_server import ChromaHandler, main  # re-export compatibility


if __name__ == "__main__":
    main()
