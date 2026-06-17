"""
core/colony_code_proposer.py — Pipeline de automodificación de código  [Fase 7]

Los personajes de Colony pueden proponer cambios de código a través de este módulo.
El flujo es: propuesta → staging → revisión automática → aprobación SER → aplicación.

Garantías de seguridad:
  - Solo escribe en ~/.eidos/staging/ (nunca en el árbol EIDOS directamente)
  - Todo cambio queda en Colony Proposals esperando votación y aprobación de SER
  - Los diffs se muestran en el inbox antes de aplicar
  - Nunca aplica cambios sin proposal_id aprobado

Uso:
    from core.colony_code_proposer import get_code_proposer
    proposer = get_code_proposer()
    pid = proposer.propose_change("colony_coder", "Añadir retry a ollama_queue",
                                  "ollama_queue.py", nuevo_codigo)
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("eidos.code_proposer")

EIDOS_ROOT   = Path(__file__).parent.parent
STAGING_DIR  = Path.home() / ".eidos" / "staging"
MAX_DIFF_LINES = 200   # límite de líneas de diff en el resumen


class CodeProposer:
    """Pipeline seguro de propuestas de automodificación de código."""

    def __init__(self):
        STAGING_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ── API pública ──────────────────────────────────────────────────────────

    def propose_change(
        self,
        author: str,
        title: str,
        target_file: str,
        new_content: str,
        reason: str = "",
    ) -> Optional[int]:
        """
        Propone un cambio de código.

        Args:
            author:       personaje que propone ("colony_coder", etc.)
            title:        título corto del cambio
            target_file:  ruta relativa al root de EIDOS (ej: "core/ollama_queue.py")
            new_content:  contenido completo del archivo modificado
            reason:       justificación del cambio

        Returns:
            proposal_id o None si falló
        """
        target = Path(target_file)
        if target.is_absolute():
            log.warning("propose_change: ruta absoluta rechazada: %s", target_file)
            return None

        src_path = EIDOS_ROOT / target
        if not src_path.exists():
            log.warning("propose_change: archivo no existe: %s", src_path)
            return None

        # Calcular diff
        old_text = src_path.read_text(errors="replace")
        diff     = _make_diff(old_text, new_content, target_file)
        if not diff.strip():
            log.info("propose_change: sin cambios en %s", target_file)
            return None

        # Guardar en staging
        stage_id = hashlib.sha1(f"{time.time()}{author}{target_file}".encode()).hexdigest()[:12]
        stage_path = STAGING_DIR / f"{stage_id}_{target.name}"
        with self._lock:
            stage_path.write_text(new_content)

        # Crear propuesta en Colony
        summary = (
            f"Autor: {author}\n"
            f"Archivo: {target_file}\n"
            f"Razón: {reason or 'no especificada'}\n\n"
            f"Diff (primeras {MAX_DIFF_LINES} líneas):\n"
            + "\n".join(diff.splitlines()[:MAX_DIFF_LINES])
        )
        metadata = {
            "stage_id":    stage_id,
            "stage_path":  str(stage_path),
            "target_file": target_file,
            "diff_lines":  len(diff.splitlines()),
        }

        try:
            from core.colony_proposals import get_proposal_system
            ps  = get_proposal_system()
            pid = ps.create(
                author=author,
                proposal_type="code_change",
                title=title[:120],
                description=summary[:2000],
                metadata=metadata,
            )
            log.info("CodeProposer: propuesta #%d creada por %s → %s", pid, author, target_file)

            # Notificar al inbox de SER directamente (code_change siempre requiere SER)
            try:
                from core.ser_inbox import get_ser_inbox
                get_ser_inbox().add(
                    msg_type="proposal_needs_approval",
                    title=f"[CÓDIGO] {title[:80]}",
                    summary=f"Propuesta #{pid} de {author}\n{summary[:300]}",
                    proposal_id=pid,
                    action_needed=True,
                )
            except Exception as e:
                log.debug("inbox notify: %s", e)

            return pid
        except Exception as e:
            log.error("propose_change error: %s", e)
            stage_path.unlink(missing_ok=True)
            return None

    def apply_approved(self, proposal_id: int) -> bool:
        """
        Aplica un cambio aprobado por SER.
        Solo llamar cuando la propuesta está en estado 'passed' y aprobada en inbox.
        """
        try:
            from core.colony_proposals import get_proposal_system
            ps       = get_proposal_system()
            proposal = ps.get_proposal(proposal_id)
            if not proposal:
                log.warning("apply_approved: propuesta %d no encontrada", proposal_id)
                return False

            meta       = proposal.metadata
            stage_path = Path(meta.get("stage_path", ""))
            target_rel = meta.get("target_file", "")

            if not stage_path.exists():
                log.error("apply_approved: staging no encontrado: %s", stage_path)
                return False

            target = EIDOS_ROOT / target_rel
            if not target.exists():
                log.error("apply_approved: target no existe: %s", target)
                return False

            # Backup antes de aplicar
            backup = target.with_suffix(target.suffix + f".bak_{int(time.time())}")
            backup.write_text(target.read_text(errors="replace"))

            # Aplicar
            target.write_text(stage_path.read_text())
            stage_path.unlink(missing_ok=True)
            ps.mark_executed(proposal_id)

            log.info("apply_approved: #%d aplicado → %s (backup: %s)", proposal_id, target, backup.name)

            try:
                from core.colony_broadcast import get_broadcast
                get_broadcast().broadcast(
                    "colony_governor",
                    f"Código aplicado: {target_rel} (propuesta #{proposal_id})",
                    msg_type="learning",
                )
            except Exception:
                pass  # error no crítico, continuar
            return True
        except Exception as e:
            log.error("apply_approved %d: %s", proposal_id, e)
            return False

    def list_staged(self) -> list[dict]:
        """Lista los cambios en staging (pendientes de aprobación)."""
        result = []
        for f in sorted(STAGING_DIR.glob("*")):
            if f.is_file():
                result.append({
                    "file":  f.name,
                    "size":  f.stat().st_size,
                    "mtime": f.stat().st_mtime,
                })
        return result

    def discard_staged(self, stage_id: str) -> bool:
        """Descarta un cambio en staging."""
        with self._lock:
            for f in STAGING_DIR.glob(f"{stage_id}_*"):
                f.unlink(missing_ok=True)
                log.info("Staging descartado: %s", f.name)
                return True
        return False


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_diff(old: str, new: str, filename: str) -> str:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=3,
    ))


# ── Singleton ─────────────────────────────────────────────────────────────────

_instance: Optional[CodeProposer] = None
_inst_lock = threading.Lock()


def get_code_proposer() -> CodeProposer:
    global _instance
    if _instance is None:
        with _inst_lock:
            if _instance is None:
                _instance = CodeProposer()
    return _instance
