"""
core/lifecycle_daemon.py — Daemon que mantiene los learning loops de personajes activos.

Arrancado por `eidos start` como proceso persistente.
- Inicializa get_lifecycle() y arranca los learning loops en background.
- Revisa cada 5 minutos si algún thread murió y lo relanza.
- Persiste hasta recibir SIGTERM.
"""
from __future__ import annotations

import sys
import signal
import threading
import time
import logging

log = logging.getLogger("eidos.lifecycle_daemon")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

_running = True


def _screen_scan_loop():
    """Escanea ventanas abiertas cada 5 minutos y las indexa en brain.db. Sin Ollama."""
    time.sleep(60)  # espera inicial para que todo arranque
    while _running:
        try:
            from core.screen_scanner import scan_windows
            result = scan_windows(use_vision=False)
            log.info("Screen scan: %s", result.get("message", ""))
        except Exception as e:
            log.debug("Screen scan error: %s", e)
        # Esperar 5 minutos
        for _ in range(300):
            if not _running:
                return
            time.sleep(1)


def _handle_stop(sig, frame):
    global _running
    log.info("SIGTERM recibido, cerrando lifecycle daemon")
    _running = False


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT,  _handle_stop)

    log.info("Lifecycle daemon iniciando...")
    try:
        from core.character_lifecycle import get_lifecycle
        lc = get_lifecycle()
        lc._restart_learning_loops()
        log.info("Learning loops arrancados para personajes activos")
    except Exception as e:
        log.error("Error inicializando lifecycle: %s", e)
        sys.exit(1)

    # Arrancar screen scanner en background (sin Ollama, cada 5min)
    _scan_thread = threading.Thread(target=_screen_scan_loop, daemon=True, name="screen_scan")
    _scan_thread.start()
    log.info("Screen scanner activo — indexando ventanas cada 5 minutos")

    # Watchdog: revisar y relanzar loops muertos cada 5 minutos
    while _running:
        time.sleep(300)
        if not _running:
            break
        try:
            lc._restart_learning_loops()
            alive = sum(1 for t in lc._threads.values() if t.is_alive())
            log.info("Watchdog: %d learning loops activos", alive)
        except Exception as e:
            log.warning("Watchdog error: %s", e)

    log.info("Lifecycle daemon detenido")


if __name__ == "__main__":
    main()
