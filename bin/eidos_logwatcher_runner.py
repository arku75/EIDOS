#!/usr/bin/env python3
"""
eidos_logwatcher_runner.py — Runner del LogWatcher como servicio [S123]
=======================================================================
El README de EIDOS promete: "Log Watcher — analiza logs en tiempo real,
detecta patrones de error y alerta a la Colony si algo huele mal".
El módulo core/log_watcher.py existía pero nadie lo arrancaba
(running=False). Este runner lo mantiene vivo: escanea cada 5 min y
enruta hallazgos high/critical por AlertManager → Colony.
"""
import sys
import time
import logging

sys.path.insert(0, "/home/ser/EIDOS")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [logwatcher] %(message)s",
)
log = logging.getLogger("logwatcher-runner")


def main() -> None:
    from core.log_watcher import get_log_watcher

    watcher = get_log_watcher()
    watcher.start(interval=300)  # escaneo cada 5 min (hilo de fondo)
    log.info("LogWatcher arrancado (intervalo 300s) — vigilando logs de EIDOS")

    # Mantener el proceso vivo; el hilo del watcher hace el trabajo.
    try:
        while True:
            time.sleep(3600)
            stats = watcher.stats
            log.info(
                "latido: findings=%s, last_24h=%s, running=%s",
                stats.get("total_findings"), stats.get("last_24h"),
                stats.get("running"),
            )
    except KeyboardInterrupt:
        watcher._running = False
        log.info("LogWatcher detenido")


if __name__ == "__main__":
    main()
