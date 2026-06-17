"""
core/colony_conductor.py — Coordinador de bucles autónomos  [Fase 2]

Evita que libre, curiosity y el autonomous loop se pisen entre sí.
Comparte estado entre procesos via JSON en ~/.eidos/conductor_state.json.

Uso:
    from core.colony_conductor import get_conductor
    c = get_conductor()
    if c.request_slot("libre"):
        ...hacer trabajo...
        c.release_slot("libre")
"""
from __future__ import annotations

import json
import os
import time
import threading
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("eidos.conductor")

STATE_FILE      = Path.home() / ".eidos" / "conductor_state.json"
MAX_RPM         = 4          # máximo peticiones/minuto a Ollama (todas las fuentes)
SLOT_TIMEOUT    = 90         # segundos máximo en posesión de un slot
KNOWN_LOOPS     = ("libre", "curiosity", "autonomous", "healer", "governor")


class ColonyConductor:
    """Director de tráfico de los bucles autónomos de Colony."""

    def __init__(self):
        self._lock       = threading.Lock()
        self._local_ts:  list[float] = []  # timestamps de peticiones locales (token bucket)
        self._slots:     dict = {}          # {loop_id: ts_acquired}
        self._state_lock = threading.Lock()
        self._load_state()

    # ── API ─────────────────────────────────────────────────────────────────

    def request_slot(self, loop_id: str, priority: int = 1) -> bool:
        """
        Solicita permiso para hacer trabajo (llamar a Ollama).
        Devuelve True si puede proceder ahora, False si debe esperar.
        priority: 0=URGENT, 1=NORMAL, 2=BACKGROUND
        """
        with self._lock:
            self._cleanup_expired_slots()
            self._load_state()

            # URGENT siempre pasa
            if priority == 0:
                self._slots[loop_id] = time.time()
                self._save_state()
                return True

            # Token bucket: máximo MAX_RPM por minuto
            now    = time.time()
            cutoff = now - 60.0
            self._local_ts = [t for t in self._local_ts if t > cutoff]

            # Para BACKGROUND: solo si hay menos de 2 peticiones en el último minuto
            limit = MAX_RPM if priority == 1 else 2
            if len(self._local_ts) >= limit:
                log.debug("[%s] rate limit (%d/%d rpm)", loop_id, len(self._local_ts), limit)
                return False

            self._local_ts.append(now)
            self._slots[loop_id] = now
            self._save_state()
            return True

    def release_slot(self, loop_id: str) -> None:
        """Libera el slot tras completar el trabajo."""
        with self._lock:
            self._slots.pop(loop_id, None)
            self._save_state()

    def yield_turn(self, loop_id: str) -> None:
        """El loop cede voluntariamente su turno."""
        self.release_slot(loop_id)
        log.debug("[%s] cedió turno", loop_id)

    def get_status(self) -> dict:
        """Estado actual del conductor."""
        with self._lock:
            self._cleanup_expired_slots()
            now    = time.time()
            cutoff = now - 60.0
            rpm    = len([t for t in self._local_ts if t > cutoff])
            return {
                "active_loops": list(self._slots.keys()),
                "rpm_last_minute": rpm,
                "max_rpm": MAX_RPM,
                "healthy": rpm < MAX_RPM,
            }

    # ── Internal ────────────────────────────────────────────────────────────

    def _cleanup_expired_slots(self) -> None:
        expired_cutoff = time.time() - SLOT_TIMEOUT
        expired = [lid for lid, ts in self._slots.items() if ts < expired_cutoff]
        for lid in expired:
            log.warning("Slot expirado limpiado: %s", lid)
            del self._slots[lid]

    def _load_state(self) -> None:
        try:
            if STATE_FILE.exists():
                data = json.loads(STATE_FILE.read_text())
                # Merge: conservar slots de otros procesos
                for lid, ts in data.get("slots", {}).items():
                    if lid not in self._slots:
                        self._slots[lid] = ts
        except Exception:
            pass  # error no crítico, continuar
    def _save_state(self) -> None:
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps({
                "slots": self._slots,
                "ts":    time.time(),
            }))
        except Exception:
            pass  # error no crítico, continuar
_instance: Optional[ColonyConductor] = None
_inst_lock = threading.Lock()


def get_conductor() -> ColonyConductor:
    global _instance
    if _instance is None:
        with _inst_lock:
            if _instance is None:
                _instance = ColonyConductor()
    return _instance
