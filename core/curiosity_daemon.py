#!/usr/bin/env python3
"""Daemon de curiosidad — iniciado por `eidos start`, corre en background.

Arranca el sistema de curiosidad propia de EIDOS (eidos_curiosity.py)
de forma independiente al CLI, para que funcione aunque no esté el CLI abierto.
"""
import sys
import os
import time
import signal
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("EIDOS_NO_VISION_WARMUP", "1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
log = logging.getLogger("eidos.curiosity_daemon")


def main():
    interval = float(os.environ.get("EIDOS_CURIOSITY_INTERVAL_MIN", "10"))

    from core.eidos_curiosity import get_curiosity
    curiosity = get_curiosity()
    curiosity.start(interval_minutes=interval)
    log.info("Curiosidad activa — tick cada %.0f min", interval)

    def _stop(sig, frame):
        curiosity.stop()
        log.info("Curiosidad detenida")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while True:
        time.sleep(30)


if __name__ == "__main__":
    main()
