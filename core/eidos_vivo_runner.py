#!/usr/bin/env python3
"""
core/eidos_vivo_runner.py — Daemon entry point para eidos-vivo.service (S67)

Arranca eidos_vivo.EidosVivo en main thread + registra SIGTERM/SIGINT handlers
para shutdown limpio. Se ejecuta como servicio systemd-user persistente.

systemd: /home/ser/.config/systemd/user/eidos-vivo.service
Logs:    /home/ser/.eidos/logs/vivo.log
"""
from __future__ import annotations

import faulthandler
import logging
import os
import signal
import sys
import time
from pathlib import Path

# S68-A · Capturar SIGSEGV con traceback Python antes de morir
# Volcar a /home/ser/.eidos/logs/vivo_faulthandler.log
_FH_LOG = Path.home() / ".eidos" / "logs" / "vivo_faulthandler.log"
_FH_LOG.parent.mkdir(parents=True, exist_ok=True)
_fh_file = open(_FH_LOG, "a", buffering=1)
faulthandler.enable(file=_fh_file, all_threads=True)
try:
    faulthandler.register(signal.SIGSEGV, file=_fh_file, all_threads=True, chain=True)
    faulthandler.register(signal.SIGABRT, file=_fh_file, all_threads=True, chain=True)
    faulthandler.register(signal.SIGFPE, file=_fh_file, all_threads=True, chain=True)
    faulthandler.register(signal.SIGBUS, file=_fh_file, all_threads=True, chain=True)
except Exception:
    pass

# Path setup (standalone)
EIDOS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EIDOS_ROOT))

# Logging
LOG_DIR = Path.home() / ".eidos" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "vivo.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),  # journalctl lo capta
    ],
)
log = logging.getLogger("eidos.vivo_runner")

# Evitar warmups pesados en arranque
os.environ.setdefault("EIDOS_NO_VISION_WARMUP", "1")


def main():
    log.info("=" * 60)
    log.info("EIDOS Vivo Runner — arrancando daemon")
    log.info("=" * 60)

    from core.eidos_vivo import get_vivo, KILL_SWITCH

    # Si quedó kill switch de sesión anterior, lo limpiamos al arrancar
    if KILL_SWITCH.exists():
        try:
            KILL_SWITCH.unlink()
            log.info("Kill switch previo limpiado: %s", KILL_SWITCH)
        except Exception:
            pass

    vivo = get_vivo()
    vivo.start()  # registra SIGTERM/SIGINT internamente (main thread OK)

    log.info("Vivo arrancado. PID=%s. Main thread esperando señales.", os.getpid())

    # Main thread se queda dormido esperando señales
    # El loop real corre en thread daemon. SIGTERM activa _graceful_shutdown.
    try:
        while vivo._running:
            time.sleep(5)
        log.info("Vivo._running=False, runner saliendo")
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — graceful shutdown")
        vivo.stop()
    except Exception as e:
        log.error("Runner main error: %s", e)
        vivo.stop()
        sys.exit(1)

    log.info("EIDOS Vivo Runner — terminado limpiamente")
    sys.exit(0)


if __name__ == "__main__":
    main()
