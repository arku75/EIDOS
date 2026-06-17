#!/usr/bin/env python3
"""
EIDOS core/patrol_loop.py — Loop periódico de salud del sistema
===============================================================
Ejecuta patrol_once() de octoclaw_bridge en un thread daemon cada N minutos.
Se integra con alert_manager para notificaciones cuando hay problemas.

Uso:
    from core.patrol_loop import get_patrol_loop
    get_patrol_loop().start(interval_minutes=5)
"""
from __future__ import annotations

import threading
import time
import logging
from typing import Optional, Dict, Any

log = logging.getLogger("eidos.patrol_loop")


class PatrolLoop:
    def __init__(self):
        self.running      = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event  = threading.Event()
        self._last_result: Dict[str, Any] = {}
        self.checks: list = []

    def start(self, interval_minutes: float = 5.0) -> None:
        if self._thread is not None and self._thread.is_alive():
            log.debug("PatrolLoop ya está corriendo")
            return
        self._stop_event.clear()
        self.running  = True
        self._thread  = threading.Thread(
            target=self._loop,
            args=(interval_minutes,),
            daemon=True,
            name="eidos-patrol",
        )
        self._thread.start()
        log.info("PatrolLoop iniciado — intervalo %g min", interval_minutes)

    def stop(self) -> None:
        self.running = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        log.info("PatrolLoop detenido")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_last_result(self) -> Dict[str, Any]:
        return dict(self._last_result)

    def _loop(self, interval_minutes: float) -> None:
        interval_sec = interval_minutes * 60
        log.info("PatrolLoop worker arrancado (intervalo=%gs)", interval_sec)

        while not self._stop_event.is_set():
            try:
                from core.octoclaw_bridge import patrol_once
                result = patrol_once()
                self._last_result.update(result)
                self.checks = list(result.get("checks", {}).keys())

                if not result.get("healthy", True):
                    issues = [k for k, v in result.get("checks", {}).items()
                              if v.get("ok") is False]
                    log.warning("PatrolLoop: issues detectados: %s", issues)
                    # Notificar via alert_manager
                    try:
                        from core.alert_manager import get_alert_manager
                        get_alert_manager().publish("patrol.system_unhealthy", {
                            "issues": issues,
                            "checks": result.get("checks", {}),
                        })
                    except Exception:
                        pass  # error no crítico, continuar
                else:
                    log.debug("PatrolLoop: sistema saludable ✓")

            except Exception as e:
                log.exception("PatrolLoop error en ciclo: %s", e)

            self._stop_event.wait(timeout=interval_sec)

        log.info("PatrolLoop worker terminado")


_instance: Optional[PatrolLoop] = None
_lock = threading.Lock()


def get_patrol_loop() -> PatrolLoop:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = PatrolLoop()
    return _instance


def start_patrol(interval_minutes: float = 5.0) -> None:
    get_patrol_loop().start(interval_minutes)


def stop_patrol() -> None:
    get_patrol_loop().stop()
