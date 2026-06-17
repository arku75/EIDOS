"""
core/colony_healer_loop.py — Daemon de autorreparación de Colony  [Fase 5]

Escucha broadcasts de tipo "alert" y "error" de cualquier personaje.
Llama a SelfHealer para diagnosticar y reparar automáticamente.
Si el fix requiere acción privilegiada, crea una propuesta en colony_proposals.

Arranca como thread daemon desde eidos start (via colony_conductor).

Uso:
    from core.colony_healer_loop import get_healer_loop
    loop = get_healer_loop()
    loop.start()
"""
from __future__ import annotations

import time
import threading
import logging
from pathlib import Path
from typing import Optional
from core.db import get_conn

log = logging.getLogger("eidos.healer_loop")

BRAIN_DB    = Path.home() / ".eidos" / "evolution_brain.db"
POLL_SECS   = 30   # revisar broadcasts cada 30s


class HealerLoop:
    """Daemon que monitoriza errores y los repara automáticamente."""

    def __init__(self):
        self._stop   = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_checked: float = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="colony-healer"
        )
        self._thread.start()
        log.info("HealerLoop iniciado")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── Loop principal ───────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                log.debug("HealerLoop tick error: %s", e)
            self._stop.wait(timeout=POLL_SECS)

    def _tick(self) -> None:
        """Revisa broadcasts de tipo alert/error y los repara."""
        try:
            import sqlite3
            conn = get_conn(BRAIN_DB, timeout=3)
            conn.execute("PRAGMA journal_mode=WAL")
            # Leer alertas desde la última revisión
            rows = conn.execute(
                "SELECT id, from_char, message, ts FROM colony_broadcasts "
                "WHERE msg_type IN ('alert','error') AND ts > ? ORDER BY ts ASC LIMIT 20",
                (self._last_checked,)
            ).fetchall()
            pass  # S109: get_conn no necesita close()
        except Exception:
            return

        for row in rows:
            bid, from_char, message, ts = row
            self._last_checked = max(self._last_checked, ts)
            self._handle_alert(from_char, message)

    def _handle_alert(self, source: str, message: str) -> None:
        """Intenta reparar el error descrito en el mensaje."""
        log.info("Alerta de [%s]: %s", source, message[:80])

        # 1. Detectar categoría del error
        category = self._categorize(message)

        # 2. Intentar reparación automática
        fixed = False
        action = ""

        if category == "import_error":
            fixed, action = self._fix_import(message)
        elif category == "timeout":
            fixed, action = self._fix_timeout(source)
        elif category == "disk_full":
            fixed, action = self._fix_disk()
        elif category == "ollama_down":
            fixed, action = self._fix_ollama()

        # 3. Broadcastear resultado
        result_msg = (
            f"[HEALER] {'Reparado' if fixed else 'No reparado'}: {action or message[:60]}"
        )
        try:
            from core.colony_broadcast import get_broadcast
            msg_type = "learning" if fixed else "alert"
            get_broadcast().broadcast("colony_healer", result_msg, msg_type=msg_type)
        except Exception:
            pass  # error no crítico, continuar
        # 4. Si no se pudo reparar → crear propuesta
        if not fixed:
            self._propose_fix(source, message, category)

    def _categorize(self, message: str) -> str:
        m = message.lower()
        if "import" in m or "modulenotfounderror" in m or "no module" in m:
            return "import_error"
        if "timeout" in m:
            return "timeout"
        if "disk" in m or "no space" in m or "enospc" in m:
            return "disk_full"
        if "ollama" in m and ("down" in m or "refused" in m or "connect" in m):
            return "ollama_down"
        return "unknown"

    def _fix_import(self, message: str) -> tuple[bool, str]:
        """Intenta instalar el módulo faltante con pip."""
        import re, subprocess
        m = re.search(r"No module named ['\"]([a-zA-Z0-9_\-]+)['\"]", message)
        if not m:
            return False, ""
        pkg = m.group(1)
        try:
            r = subprocess.run(
                ["pip", "install", "--quiet", pkg],
                capture_output=True, text=True, timeout=30
            )
            if r.returncode == 0:
                return True, f"pip install {pkg} OK"
            return False, f"pip install {pkg} falló: {r.stderr[:100]}"
        except Exception as e:
            return False, str(e)

    def _fix_timeout(self, source: str) -> tuple[bool, str]:
        """Registra el timeout como conocimiento para ajuste futuro."""
        log.info("Timeout detectado en %s — registrado para ajuste", source)
        return True, f"timeout en {source} registrado"

    def _fix_disk(self) -> tuple[bool, str]:
        """Activa el sistema de limpieza."""
        try:
            from core.cleanup_system import CleanupSystem
            cs = CleanupSystem()
            cs.cleanup()
            return True, "CleanupSystem ejecutado"
        except Exception as e:
            return False, str(e)

    def _fix_ollama(self) -> tuple[bool, str]:
        """Intenta reiniciar ollama si está caído."""
        import subprocess
        try:
            r = subprocess.run(
                ["systemctl", "--user", "restart", "ollama"],
                capture_output=True, timeout=10
            )
            return r.returncode == 0, "ollama restarted" if r.returncode == 0 else "restart failed"
        except Exception:
            pass  # error no crítico, continuar
        return False, "no se pudo reiniciar ollama"

    def _propose_fix(self, source: str, message: str, category: str) -> None:
        """Crea propuesta democrática para fallos que no se pueden reparar solos."""
        try:
            from core.colony_proposals import get_proposal_system
            ps = get_proposal_system()
            ps.create(
                author="colony_healer",
                proposal_type="experiment",
                title=f"Reparar: {category} en {source}",
                description=(
                    f"HealerLoop no pudo reparar automáticamente:\n"
                    f"Fuente: {source}\nCategoría: {category}\n"
                    f"Error: {message[:200]}\n\n"
                    f"Propongo que colony_coder investigue y genere un fix."
                )
            )
        except Exception as e:
            log.debug("propose_fix error: %s", e)


_instance: Optional[HealerLoop] = None
_lock      = threading.Lock()


def get_healer_loop() -> HealerLoop:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = HealerLoop()
    return _instance
