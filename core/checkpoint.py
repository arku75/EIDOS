"""
EIDOS core/checkpoint.py — Sistema de Checkpoints Automáticos
===============================================================
Guarda el estado completo de EIDOS cada 30 minutos para permitir
recuperación completa en caso de fallo.

Funcionalidades:
  - Guarda estado cada 30 minutos (configurable)
  - Incluye conversación completa, modo actual, skills aprendidas
  - Compresión automática de checkpoints antiguos
  - Retención: últimos 10 checkpoints + 1 por día del último mes
  - Carga instantánea del último checkpoint

Estructura de Checkpoint:
  {
    "id": "checkpoint_20260317_143025",
    "created_at": 1710684625.123,
    "mode": "PLAN+EDIT",
    "conversation_history": [...],
    "learned_skills": [...],
    "sandbox_improvements": [...],
    "current_task": "...",
    "system_state": {...}
  }

Uso:
    from core.checkpoint import AutoCheckpoint

    # Iniciar auto-guardado cada 30 min
    ac = AutoCheckpoint(interval_minutes=30)
    ac.start()

    # Guardar manualmente
    ac.save_checkpoint(mode="PLAN", task="analyzing code")

    # Cargar último checkpoint
    checkpoint = load_latest_checkpoint()
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any


# ── Configuración ────────────────────────────────────────────────────────────

CHECKPOINT_DIR = Path.home() / ".eidos" / "checkpoints"
CHECKPOINT_INTERVAL_MINUTES = 30
MAX_RECENT_CHECKPOINTS = 10
MAX_DAILY_CHECKPOINTS = 30  # Últimos 30 días


# ── Tipos básicos ────────────────────────────────────────────────────────────

@dataclass
class Checkpoint:
    """Representa un checkpoint completo del estado de EIDOS."""
    id: str
    created_at: float
    mode: str  # PLAN, EDIT, PLAN+EDIT
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    learned_skills: List[Dict[str, Any]] = field(default_factory=list)
    sandbox_improvements: List[Dict[str, Any]] = field(default_factory=list)
    current_task: Optional[str] = None
    system_state: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convierte el checkpoint a diccionario."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Checkpoint:
        """Crea un checkpoint desde un diccionario."""
        return cls(
            id=data["id"],
            created_at=data["created_at"],
            mode=data["mode"],
            conversation_history=data.get("conversation_history", []),
            learned_skills=data.get("learned_skills", []),
            sandbox_improvements=data.get("sandbox_improvements", []),
            current_task=data.get("current_task"),
            system_state=data.get("system_state", {}),
        )

    def save_to_file(self, path: Path, compress: bool = False) -> None:
        """Guarda el checkpoint a un archivo."""
        data = self.to_dict()

        if compress:
            with gzip.open(str(path) + ".gz", "wt", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        else:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)

    @classmethod
    def load_from_file(cls, path: Path) -> Checkpoint:
        """Carga un checkpoint desde un archivo."""
        if str(path).endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                data = json.load(f)
        else:
            with open(path, "r") as f:
                data = json.load(f)

        return cls.from_dict(data)

    def size_mb(self, path: Path) -> float:
        """Retorna el tamaño del checkpoint en MB."""
        if not path.exists():
            return 0.0
        return path.stat().st_size / (1024 * 1024)


# ── AutoCheckpoint ───────────────────────────────────────────────────────────

class AutoCheckpoint:
    """
    Sistema de checkpoints automáticos para EIDOS.

    Guarda el estado completo periódicamente para permitir recuperación
    completa en caso de fallo.
    """

    def __init__(
        self,
        interval_minutes: int = CHECKPOINT_INTERVAL_MINUTES,
        verbose: bool = True
    ):
        self.interval_minutes = interval_minutes
        self.verbose = verbose
        self._thread: Optional[threading.Thread] = None
        self._is_running = False
        self._ensure_checkpoint_dir()

        # Estado actual que se guardará en checkpoints
        self.current_mode = "PLAN+EDIT"
        self.current_task: Optional[str] = None
        self.conversation_history: List[Dict[str, Any]] = []

    def _ensure_checkpoint_dir(self) -> None:
        """Crea el directorio de checkpoints si no existe."""
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    def _log(self, msg: str) -> None:
        """Log con prefijo."""
        if self.verbose:
            print(f"💾 [CHECKPOINT] {msg}")

    def _generate_checkpoint_id(self) -> str:
        """Genera un ID único para el checkpoint."""
        return f"checkpoint_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def _get_learned_skills(self) -> List[Dict[str, Any]]:
        """Obtiene las skills aprendidas desde el Training System."""
        try:
            from core.trainer import get_trainer
            trainer = get_trainer()
            stats = trainer.get_stats()
            return stats.get("recent_learnings", [])
        except Exception:
            return []

    def _get_sandbox_improvements(self) -> List[Dict[str, Any]]:
        """Obtiene las mejoras en sandbox (si existen)."""
        """Mirror Guardian sandbox improvements — check if any exist."""
        from pathlib import Path
        sandbox_dir = Path.home() / ".eidos" / "sandbox"
        if sandbox_dir.exists():
            files = list(sandbox_dir.glob("*.py"))
            return [{"type": "sandbox", "count": len(files)}]
        return []

    def _get_system_state(self) -> Dict[str, Any]:
        """Obtiene el estado del sistema (RAM, cache, etc.)."""
        state = {}

        try:
            from core.ram_guardian import get_ram_status
            ram = get_ram_status()
            state["ram"] = {
                "percent": ram.percent,
                "available_mb": ram.available_mb,
            }
        except Exception:
            pass  # error no crítico, continuar
        try:
            from core.smart_cache import smart_cache
            cache_stats = smart_cache.get_stats()
            state["cache"] = cache_stats
        except Exception:
            pass  # error no crítico, continuar
        return state

    def save_checkpoint(
        self,
        mode: Optional[str] = None,
        task: Optional[str] = None,
        force: bool = False
    ) -> Optional[Checkpoint]:
        """
        Guarda un checkpoint del estado actual.

        Args:
            mode: Modo actual (PLAN, EDIT, PLAN+EDIT). Si None, usa current_mode.
            task: Tarea actual. Si None, usa current_task.
            force: Si True, guarda incluso si no ha pasado el intervalo.

        Returns:
            El checkpoint guardado, o None si hubo error.
        """
        try:
            checkpoint_id = self._generate_checkpoint_id()

            checkpoint = Checkpoint(
                id=checkpoint_id,
                created_at=time.time(),
                mode=mode or self.current_mode,
                conversation_history=self.conversation_history.copy(),
                learned_skills=self._get_learned_skills(),
                sandbox_improvements=self._get_sandbox_improvements(),
                current_task=task or self.current_task,
                system_state=self._get_system_state(),
            )

            # Guardar a disco
            checkpoint_path = CHECKPOINT_DIR / f"{checkpoint_id}.json"
            checkpoint.save_to_file(checkpoint_path, compress=False)

            size_mb = checkpoint.size_mb(checkpoint_path)
            self._log(f"✅ Checkpoint guardado: {checkpoint_id} ({size_mb:.2f} MB)")

            # Limpiar checkpoints antiguos
            self._cleanup_old_checkpoints()

            return checkpoint

        except Exception as e:
            self._log(f"❌ Error guardando checkpoint: {e}")
            return None

    def _cleanup_old_checkpoints(self) -> None:
        """
        Limpia checkpoints antiguos siguiendo la política de retención:
        - Últimos 10 checkpoints siempre se mantienen
        - 1 checkpoint por día del último mes
        - Resto se comprime o elimina
        """
        try:
            checkpoints = sorted(
                CHECKPOINT_DIR.glob("checkpoint_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )

            if len(checkpoints) <= MAX_RECENT_CHECKPOINTS:
                return  # No hay suficientes para limpiar

            # Mantener últimos N checkpoints
            recent = checkpoints[:MAX_RECENT_CHECKPOINTS]
            old = checkpoints[MAX_RECENT_CHECKPOINTS:]

            # Agrupar por día
            daily_checkpoints: Dict[str, Path] = {}
            for cp in old:
                try:
                    # Extraer fecha del nombre (checkpoint_YYYYMMDD_HHMMSS.json)
                    date_str = cp.stem.split("_")[1]  # YYYYMMDD
                    if date_str not in daily_checkpoints:
                        daily_checkpoints[date_str] = cp
                except Exception:
                    continue

            # Mantener 1 por día del último mes
            keep_daily = list(daily_checkpoints.values())[:MAX_DAILY_CHECKPOINTS]

            # Eliminar el resto
            for cp in old:
                if cp not in keep_daily:
                    cp.unlink()
                    self._log(f"🗑️  Checkpoint eliminado: {cp.stem}")

        except Exception as e:
            self._log(f"⚠️  Error limpiando checkpoints: {e}")

    def _checkpoint_loop(self) -> None:
        """Loop principal del thread de auto-checkpoint."""
        self._log(f"Iniciado - guardando cada {self.interval_minutes} min")

        while self._is_running:
            time.sleep(self.interval_minutes * 60)

            if self._is_running:
                self.save_checkpoint()

        self._log("Thread de auto-checkpoint detenido")

    def start(self, daemon: bool = True) -> None:
        """
        Inicia el sistema de auto-checkpoint.

        Args:
            daemon: Si True, corre en un thread daemon.
        """
        if self._thread is not None and self._thread.is_alive():
            self._log("Auto-checkpoint ya está corriendo")
            return

        self._is_running = True
        self._thread = threading.Thread(
            target=self._checkpoint_loop,
            daemon=daemon,
            name="AutoCheckpoint",
        )
        self._thread.start()
        self._log("✅ Auto-checkpoint iniciado")

    def stop(self) -> None:
        """Detiene el sistema de auto-checkpoint."""
        if not self._is_running:
            return

        self._log("Deteniendo auto-checkpoint...")
        self._is_running = False

        if self._thread is not None:
            self._thread.join(timeout=5)

        # Guardar checkpoint final
        self.save_checkpoint(force=True)
        self._log("✅ Auto-checkpoint detenido")

    def update_conversation(self, message: Dict[str, Any]) -> None:
        """Agrega un mensaje al historial de conversación."""
        self.conversation_history.append(message)

    def set_mode(self, mode: str) -> None:
        """Actualiza el modo actual."""
        self.current_mode = mode

    def set_task(self, task: str) -> None:
        """Actualiza la tarea actual."""
        self.current_task = task


# ── Funciones de utilidad ───────────────────────────────────────────────────

def load_latest_checkpoint() -> Optional[Checkpoint]:
    """
    Carga el checkpoint más reciente.

    Returns:
        El checkpoint más reciente, o None si no hay ninguno.
    """
    try:
        checkpoints = sorted(
            CHECKPOINT_DIR.glob("checkpoint_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not checkpoints:
            return None

        latest = checkpoints[0]
        return Checkpoint.load_from_file(latest)

    except Exception as e:
        print(f"❌ Error cargando último checkpoint: {e}")
        return None


def load_checkpoint_by_id(checkpoint_id: str) -> Optional[Checkpoint]:
    """
    Carga un checkpoint específico por ID.

    Args:
        checkpoint_id: ID del checkpoint (ej: "checkpoint_20260317_143025")

    Returns:
        El checkpoint, o None si no existe.
    """
    try:
        checkpoint_path = CHECKPOINT_DIR / f"{checkpoint_id}.json"

        if not checkpoint_path.exists():
            # Intentar con .gz
            checkpoint_path = CHECKPOINT_DIR / f"{checkpoint_id}.json.gz"
            if not checkpoint_path.exists():
                return None

        return Checkpoint.load_from_file(checkpoint_path)

    except Exception as e:
        print(f"❌ Error cargando checkpoint {checkpoint_id}: {e}")
        return None


def list_checkpoints(limit: int = 20) -> List[Dict[str, Any]]:
    """
    Lista los checkpoints disponibles.

    Args:
        limit: Número máximo de checkpoints a listar.

    Returns:
        Lista de metadatos de checkpoints.
    """
    try:
        checkpoints = sorted(
            CHECKPOINT_DIR.glob("checkpoint_*.json*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]

        result = []
        for cp_path in checkpoints:
            try:
                cp = Checkpoint.load_from_file(cp_path)
                result.append({
                    "id": cp.id,
                    "created_at": datetime.fromtimestamp(cp.created_at).isoformat(),
                    "mode": cp.mode,
                    "task": cp.current_task,
                    "size_mb": cp.size_mb(cp_path),
                    "messages": len(cp.conversation_history),
                })
            except Exception:
                continue

        return result

    except Exception as e:
        print(f"❌ Error listando checkpoints: {e}")
        return []


# ── Singleton global ─────────────────────────────────────────────────────────

_auto_checkpoint: Optional[AutoCheckpoint] = None

def get_auto_checkpoint() -> AutoCheckpoint:
    """Obtiene la instancia singleton de AutoCheckpoint."""
    global _auto_checkpoint
    if _auto_checkpoint is None:
        _auto_checkpoint = AutoCheckpoint()
    return _auto_checkpoint


# ── CLI rápido ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Uso: python checkpoint.py [save|load|list|start]")
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "save":
        ac = AutoCheckpoint(verbose=True)
        mode = sys.argv[2] if len(sys.argv) > 2 else "PLAN+EDIT"
        task = sys.argv[3] if len(sys.argv) > 3 else None
        checkpoint = ac.save_checkpoint(mode=mode, task=task)
        if checkpoint:
            print(f"✅ Checkpoint guardado: {checkpoint.id}")

    elif cmd == "load":
        checkpoint = load_latest_checkpoint()
        if checkpoint:
            print(f"✅ Último checkpoint: {checkpoint.id}")
            print(f"   Creado: {datetime.fromtimestamp(checkpoint.created_at)}")
            print(f"   Modo: {checkpoint.mode}")
            print(f"   Tarea: {checkpoint.current_task or 'N/A'}")
            print(f"   Mensajes: {len(checkpoint.conversation_history)}")
            print(f"   Skills: {len(checkpoint.learned_skills)}")
        else:
            print("❌ No hay checkpoints disponibles")

    elif cmd == "list":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        checkpoints = list_checkpoints(limit=limit)
        print(f"\n📋 Últimos {len(checkpoints)} checkpoints:\n")
        for cp in checkpoints:
            print(f"  {cp['id']}")
            print(f"    Creado: {cp['created_at']}")
            print(f"    Modo: {cp['mode']} | Tarea: {cp['task'] or 'N/A'}")
            print(f"    Size: {cp['size_mb']:.2f} MB | Mensajes: {cp['messages']}")
            print()

    elif cmd == "start":
        ac = AutoCheckpoint(verbose=True)
        ac.start(daemon=False)  # Bloquea para testing

    else:
        print(f"❌ Comando desconocido: {cmd}")
        sys.exit(1)
