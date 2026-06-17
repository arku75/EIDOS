"""
EIDOS core/phoenix.py — Phoenix Guardian (Auto-Resurrección)
=============================================================
Guardian 1: PHOENIX - Sistema de auto-resurrección para EIDOS.

Funcionalidades:
  - Ping a EIDOS cada 5 minutos para verificar que está vivo
  - Si EIDOS no responde en 3 horas → auto-resurrección
  - Carga el último checkpoint guardado
  - Restaura contexto completo de conversación
  - Inyecta estado en EIDOS Principal
  - Logging completo de todas las resurrecciones

Arquitectura:
  - Daemon independiente que corre en background
  - Comunicación vía archivos de estado (~/.eidos/phoenix/)
  - No depende del proceso principal de EIDOS
  - Si el daemon falla, se auto-reinicia

Uso:
    from core.phoenix import PhoenixGuardian

    phoenix = PhoenixGuardian()
    phoenix.start()  # Inicia daemon en background

    # EIDOS debe hacer ping periódicamente
    phoenix.heartbeat()  # Actualiza último tiempo de vida
"""
from __future__ import annotations

import json
import os
import sys
import time
import threading
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List


# ── Configuración ────────────────────────────────────────────────────────────

PHOENIX_DIR = Path.home() / ".eidos" / "phoenix"
HEARTBEAT_FILE = PHOENIX_DIR / "heartbeat.json"
STATE_FILE = PHOENIX_DIR / "phoenix_state.json"
LOG_FILE = PHOENIX_DIR / "phoenix.log"

PING_INTERVAL_SECONDS = 5 * 60  # 5 minutos
TIMEOUT_SECONDS = 3 * 60 * 60  # 3 horas
CHECK_INTERVAL_SECONDS = 60  # Revisar cada minuto


# ── Tipos básicos ────────────────────────────────────────────────────────────

@dataclass
class HeartbeatData:
    """Datos del último heartbeat de EIDOS."""
    timestamp: float
    mode: str  # PLAN, EDIT, PLAN+EDIT
    task: Optional[str] = None
    pid: Optional[int] = None

    def is_alive(self, timeout_seconds: int = TIMEOUT_SECONDS) -> bool:
        """Verifica si EIDOS sigue vivo basado en el último heartbeat."""
        elapsed = time.time() - self.timestamp
        return elapsed < timeout_seconds

    def time_since_last_beat(self) -> float:
        """Tiempo transcurrido desde el último heartbeat en segundos."""
        return time.time() - self.timestamp


@dataclass
class PhoenixState:
    """Estado del Phoenix Guardian."""
    started_at: float = field(default_factory=time.time)
    resurrections: int = 0
    last_resurrection: Optional[float] = None
    daemon_pid: Optional[int] = None
    is_active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "resurrections": self.resurrections,
            "last_resurrection": self.last_resurrection,
            "daemon_pid": self.daemon_pid,
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PhoenixState:
        return cls(
            started_at=data.get("started_at", time.time()),
            resurrections=data.get("resurrections", 0),
            last_resurrection=data.get("last_resurrection"),
            daemon_pid=data.get("daemon_pid"),
            is_active=data.get("is_active", True),
        )


# ── Phoenix Guardian ─────────────────────────────────────────────────────────

class PhoenixGuardian:
    """
    Guardian 1: PHOENIX

    Sistema de auto-resurrección que monitorea EIDOS y lo revive
    si deja de responder por más de 3 horas.
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.state = self._load_state()
        self._daemon_thread: Optional[threading.Thread] = None
        self._ensure_phoenix_dir()

    def _ensure_phoenix_dir(self) -> None:
        """Crea el directorio de Phoenix si no existe."""
        PHOENIX_DIR.mkdir(parents=True, exist_ok=True)

    def _log(self, msg: str, level: str = "INFO") -> None:
        """Escribe en el log de Phoenix."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] [{level}] {msg}\n"

        if self.verbose:
            print(f"🔥 [PHOENIX] {msg}")

        try:
            with open(LOG_FILE, "a") as f:
                f.write(log_line)
        except Exception:
            pass  # error no crítico, continuar
    def _load_state(self) -> PhoenixState:
        """Carga el estado de Phoenix desde disco."""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r") as f:
                    data = json.load(f)
                return PhoenixState.from_dict(data)
            except Exception:
                pass  # error no crítico, continuar
        return PhoenixState()

    def _save_state(self) -> None:
        """Guarda el estado de Phoenix a disco."""
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(self.state.to_dict(), f, indent=2)
        except Exception as e:
            self._log(f"Error guardando estado: {e}", "ERROR")

    def _load_heartbeat(self) -> Optional[HeartbeatData]:
        """Carga el último heartbeat de EIDOS."""
        if not HEARTBEAT_FILE.exists():
            return None

        try:
            with open(HEARTBEAT_FILE, "r") as f:
                data = json.load(f)
            return HeartbeatData(
                timestamp=data["timestamp"],
                mode=data.get("mode", "PLAN+EDIT"),
                task=data.get("task"),
                pid=data.get("pid"),
            )
        except Exception:
            return None

    def heartbeat(self, mode: str = "PLAN+EDIT", task: Optional[str] = None) -> None:
        """
        Actualiza el heartbeat de EIDOS.
        EIDOS debe llamar esto periódicamente para indicar que está vivo.
        """
        heartbeat_data = {
            "timestamp": time.time(),
            "mode": mode,
            "task": task,
            "pid": os.getpid(),
        }

        try:
            with open(HEARTBEAT_FILE, "w") as f:
                json.dump(heartbeat_data, f, indent=2)
        except Exception as e:
            self._log(f"Error actualizando heartbeat: {e}", "ERROR")

    def _resurrect_eidos(self, heartbeat: HeartbeatData) -> bool:
        """
        Resucita a EIDOS cargando el último checkpoint.

        Returns:
            True si la resurrección fue exitosa, False si falló.
        """
        self._log("=" * 70, "CRITICAL")
        self._log("⚠️  EIDOS NO RESPONDE - INICIANDO RESURRECCIÓN", "CRITICAL")
        self._log("=" * 70, "CRITICAL")

        time_dead = heartbeat.time_since_last_beat()
        self._log(f"Tiempo sin respuesta: {time_dead/60:.1f} minutos", "WARNING")
        self._log(f"Último modo conocido: {heartbeat.mode}", "INFO")
        self._log(f"Última tarea: {heartbeat.task or 'N/A'}", "INFO")

        # 1. Cargar último checkpoint
        try:
            from core.checkpoint import load_latest_checkpoint
            checkpoint = load_latest_checkpoint()

            if checkpoint is None:
                self._log("❌ No hay checkpoints disponibles - resurrección fallida", "ERROR")
                return False

            self._log(f"✅ Checkpoint encontrado: {checkpoint.id}", "INFO")
            self._log(f"   Creado: {datetime.fromtimestamp(checkpoint.created_at)}", "INFO")
            self._log(f"   Conversación: {len(checkpoint.conversation_history)} mensajes", "INFO")

        except Exception as e:
            self._log(f"❌ Error cargando checkpoint: {e}", "ERROR")
            return False

        # 2. Reiniciar EIDOS con el checkpoint
        try:
            eidos_path = Path(__file__).parent.parent / "eidos_cli.py"

            if not eidos_path.exists():
                self._log(f"❌ No se encuentra eidos_cli.py en {eidos_path}", "ERROR")
                return False

            # Crear comando de resurrección
            cmd = [
                sys.executable,
                str(eidos_path),
                "--resurrect",
                checkpoint.id,
                "--mode", heartbeat.mode,
            ]

            self._log(f"🔄 Ejecutando: {' '.join(cmd)}", "INFO")

            # Ejecutar en background
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            self._log("✅ EIDOS resucitado exitosamente", "INFO")
            self.state.resurrections += 1
            self.state.last_resurrection = time.time()
            self._save_state()

            return True

        except Exception as e:
            self._log(f"❌ Error resucitando EIDOS: {e}", "ERROR")
            return False

    def _daemon_loop(self) -> None:
        """Loop principal del daemon de Phoenix."""
        self._log("🔥 Phoenix Guardian daemon iniciado", "INFO")
        self._log(f"   Ping interval: {PING_INTERVAL_SECONDS}s", "INFO")
        self._log(f"   Timeout: {TIMEOUT_SECONDS}s ({TIMEOUT_SECONDS/3600:.1f}h)", "INFO")
        self._log(f"   Check interval: {CHECK_INTERVAL_SECONDS}s", "INFO")

        consecutive_failures = 0
        max_consecutive_failures = 5

        while self.state.is_active:
            try:
                # Cargar último heartbeat
                heartbeat = self._load_heartbeat()

                if heartbeat is None:
                    # No hay heartbeat todavía - EIDOS no ha iniciado
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    continue

                # Verificar si EIDOS está vivo
                if heartbeat.is_alive(TIMEOUT_SECONDS):
                    # Todo OK - resetear contador de fallos
                    consecutive_failures = 0
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    continue

                # EIDOS NO RESPONDE - intentar resurrección
                self._log("⚠️  EIDOS no responde - intentando resurrección...", "WARNING")

                success = self._resurrect_eidos(heartbeat)

                if success:
                    consecutive_failures = 0
                    # Esperar 5 minutos antes de volver a revisar
                    time.sleep(5 * 60)
                else:
                    consecutive_failures += 1
                    self._log(f"Fallos consecutivos: {consecutive_failures}/{max_consecutive_failures}", "WARNING")

                    if consecutive_failures >= max_consecutive_failures:
                        self._log("❌ Demasiados fallos consecutivos - Phoenix se detiene", "CRITICAL")
                        self.state.is_active = False
                        self._save_state()
                        break

                    # Esperar más tiempo entre intentos fallidos
                    time.sleep(CHECK_INTERVAL_SECONDS * 2)

            except Exception as e:
                self._log(f"Error en daemon loop: {e}", "ERROR")
                time.sleep(CHECK_INTERVAL_SECONDS)

        self._log("🔥 Phoenix Guardian daemon detenido", "INFO")

    def start(self, daemon: bool = True) -> None:
        """
        Inicia el Phoenix Guardian.

        Args:
            daemon: Si True, corre en un thread separado. Si False, bloquea.
        """
        if self._daemon_thread is not None and self._daemon_thread.is_alive():
            self._log("Phoenix ya está corriendo", "WARNING")
            return

        self.state.is_active = True
        self.state.daemon_pid = os.getpid()
        self._save_state()

        if daemon:
            self._daemon_thread = threading.Thread(
                target=self._daemon_loop,
                daemon=True,
                name="PhoenixGuardian",
            )
            self._daemon_thread.start()
            self._log("Phoenix Guardian iniciado en background", "INFO")
        else:
            self._daemon_loop()

    def stop(self) -> None:
        """Detiene el Phoenix Guardian."""
        self._log("Deteniendo Phoenix Guardian...", "INFO")
        self.state.is_active = False
        self._save_state()

        if self._daemon_thread is not None:
            self._daemon_thread.join(timeout=5)

        self._log("Phoenix Guardian detenido", "INFO")

    def status(self) -> Dict[str, Any]:
        """Retorna el estado actual de Phoenix."""
        heartbeat = self._load_heartbeat()

        status_data = {
            "phoenix": {
                "is_active": self.state.is_active,
                "started_at": datetime.fromtimestamp(self.state.started_at).isoformat(),
                "resurrections": self.state.resurrections,
                "last_resurrection": (
                    datetime.fromtimestamp(self.state.last_resurrection).isoformat()
                    if self.state.last_resurrection else None
                ),
                "daemon_pid": self.state.daemon_pid,
            },
            "eidos": None,
        }

        if heartbeat:
            status_data["eidos"] = {
                "is_alive": heartbeat.is_alive(),
                "last_heartbeat": datetime.fromtimestamp(heartbeat.timestamp).isoformat(),
                "time_since_beat": f"{heartbeat.time_since_last_beat():.1f}s",
                "mode": heartbeat.mode,
                "task": heartbeat.task,
                "pid": heartbeat.pid,
            }

        return status_data

    def print_status(self) -> None:
        """Imprime el estado de Phoenix en formato legible."""
        status = self.status()

        print("╔═══════════════════════════════════════════════════════════════════════╗")
        print("║                      🔥 PHOENIX GUARDIAN STATUS                       ║")
        print("╚═══════════════════════════════════════════════════════════════════════╝\n")

        phoenix = status["phoenix"]
        print(f"Phoenix Status:")
        print(f"  Active:          {'✅ YES' if phoenix['is_active'] else '❌ NO'}")
        print(f"  Started:         {phoenix['started_at']}")
        print(f"  Resurrections:   {phoenix['resurrections']}")
        print(f"  Last Resurr:     {phoenix['last_resurrection'] or 'N/A'}")
        print(f"  Daemon PID:      {phoenix['daemon_pid'] or 'N/A'}")
        print()

        eidos = status["eidos"]
        if eidos:
            print(f"EIDOS Status:")
            print(f"  Alive:           {'✅ YES' if eidos['is_alive'] else '❌ NO'}")
            print(f"  Last Heartbeat:  {eidos['last_heartbeat']}")
            print(f"  Time Since:      {eidos['time_since_beat']}")
            print(f"  Mode:            {eidos['mode']}")
            print(f"  Current Task:    {eidos['task'] or 'N/A'}")
            print(f"  PID:             {eidos['pid'] or 'N/A'}")
        else:
            print(f"EIDOS Status:")
            print(f"  ⚠️  No heartbeat data available - EIDOS may not be running")

        print()


# ── Singleton global ─────────────────────────────────────────────────────────

_phoenix: Optional[PhoenixGuardian] = None

def get_phoenix() -> PhoenixGuardian:
    """Obtiene la instancia singleton de Phoenix Guardian."""
    global _phoenix
    if _phoenix is None:
        _phoenix = PhoenixGuardian()
    return _phoenix


# ── CLI rápido ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    phoenix = PhoenixGuardian(verbose=True)

    if len(sys.argv) < 2:
        print("Uso: python phoenix.py [start|stop|status|heartbeat]")
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "start":
        phoenix.start(daemon=False)  # Bloquea para testing
    elif cmd == "stop":
        phoenix.stop()
    elif cmd == "status":
        phoenix.print_status()
    elif cmd == "heartbeat":
        mode = sys.argv[2] if len(sys.argv) > 2 else "PLAN+EDIT"
        task = sys.argv[3] if len(sys.argv) > 3 else None
        phoenix.heartbeat(mode=mode, task=task)
        print(f"✅ Heartbeat actualizado: {mode} - {task or 'N/A'}")
    else:
        print(f"❌ Comando desconocido: {cmd}")
        sys.exit(1)
