"""
core/eidos_goals.py — GoalManager: planificación y descomposición de metas (S76 Fase 3)

Sistema de metas autónomas para EIDOS. Sin LLM: usa descomposición
heurística + grafo de conocimiento para dividir metas complejas en
sub-metas accionables.

Inspirado en:
- Goal-Oriented Action Planning (GOAP)
- Hierarchical Task Networks (HTN)
- Sub-goal decomposition por keyword matching contra skills conocidas

API:
    gm = GoalManager()
    gm.add_goal("Instalar y configurar un servidor web con HTTPS")
    # → descompone en sub-metas automáticamente
    next_action = gm.get_next_action()
    # → "Abrir Firefox ESR en la URL de búsqueda para 'nginx let's encrypt tutorial'"

Persistencia: SQLite (~/.eidos/evolution_brain.db, tabla goals)
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from core.db import get_conn

log = logging.getLogger("eidos.goals")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
GOALS_CACHE_TTL = 300  # 5 minutos


class GoalStatus(Enum):
    PENDING = "pending"
    ACTIVE = "active"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Goal:
    """Una meta con posible descomposición en sub-metas."""
    id: str
    description: str
    priority: float = 0.5       # 0.0 - 1.0
    status: GoalStatus = GoalStatus.PENDING
    parent_id: Optional[str] = None
    subgoals: List[str] = field(default_factory=list)
    skills_used: List[str] = field(default_factory=list)
    attempts: int = 0
    max_attempts: int = 5
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    notes: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in (GoalStatus.COMPLETED, GoalStatus.FAILED,
                               GoalStatus.CANCELLED)

    @property
    def is_ready(self) -> bool:
        return self.status == GoalStatus.PENDING and self.attempts < self.max_attempts

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "description": self.description,
            "priority": self.priority, "status": self.status.value,
            "parent_id": self.parent_id, "subgoals": self.subgoals,
            "attempts": self.attempts, "notes": self.notes
        }


# ── Heurísticas de descomposición ────────────────────────────────────────────

# Patrones: (regex, sub_metas_generadas)
_DECOMPOSE_RULES = [
    # Instalar + configurar → 3 pasos
    (r"\b(instalar?|install)\s+y\s+(configurar?|configure)\s+(.+)",
     lambda m: [
         f"Investigar requisitos de {m.group(3)}",
         f"Instalar {m.group(3)}",
         f"Configurar {m.group(3)}",
     ]),
    # "crear un X que Y" → planificar + implementar + probar
    (r"\b(crear?|hacer?|generar?|develop)\s+un\s+(.+)\s+que\s+(.+)",
     lambda m: [
         f"Planificar estructura de {m.group(2)}",
         f"Implementar {m.group(2)} que {m.group(3)}",
         f"Probar {m.group(2)}",
     ]),
    # "aprender X" → buscar info + practicar + documentar
    (r"\b(aprender?|estudiar?|learn)\s+(.+)",
     lambda m: [
         f"Buscar información sobre {m.group(2)}",
         f"Practicar conceptos de {m.group(2)}",
         f"Documentar aprendizaje de {m.group(2)}",
     ]),
    # "automatizar X" → analizar + script + test
    (r"\b(automatizar?|automate)\s+(.+)",
     lambda m: [
         f"Analizar proceso manual de {m.group(2)}",
         f"Crear script de automatización para {m.group(2)}",
         f"Probar y ajustar automatización de {m.group(2)}",
     ]),
    # "migrar X a Y" → backup + preparar + migrar + verificar
    (r"\b(migrar?|migrate)\s+(.+)\s+a\s+(.+)",
     lambda m: [
         f"Hacer backup de {m.group(2)} antes de migrar",
         f"Preparar destino {m.group(3)} para migración",
         f"Ejecutar migración de {m.group(2)} a {m.group(3)}",
         f"Verificar integridad tras migración",
     ]),
    # "limpiar"/"optimizar" X → analizar + limpiar + verificar
    (r"\b(limpiar?|optimizar?|clean|optimize)\s+(.+)",
     lambda m: [
         f"Analizar estado actual de {m.group(2)}",
         f"Ejecutar limpieza/optimización de {m.group(2)}",
         f"Verificar estado tras limpieza de {m.group(2)}",
     ]),
    # "monitorizar"/"monitorear" X → configurar + alertas + dashboard
    (r"\b(monitorizar?|monitorear?|monitor)\s+(.+)",
     lambda m: [
         f"Configurar recolección de datos de {m.group(2)}",
         f"Crear reglas de alerta para {m.group(2)}",
         f"Crear dashboard de {m.group(2)}",
     ]),
    # Default: cualquier verbo + complemento → investigar + ejecutar + verificar
    (r"\b(\w+ar|\w+er|\w+ir)\s+(.+)",
     lambda m: [
         f"Investigar cómo {m.group(1)} {m.group(2)}",
         f"Ejecutar: {m.group(1)} {m.group(2)}",
         f"Verificar resultado de {m.group(1)} {m.group(2)}",
     ]),
]


class GoalManager:
    """Gestiona metas, su descomposición y ejecución.

    Uso:
        gm = GoalManager()
        gid = gm.add_goal("Instalar y configurar nginx con HTTPS")
        while True:
            action = gm.get_next_action()
            if action is None:
                break
            result = execute(action)
            gm.update(action["goal_id"], result)
    """

    def __init__(self):
        self._init_db()
        self._cache: Dict[str, List[Dict]] = {}
        self._cache_ts: float = 0

    def _init_db(self):
        """Crea/migra tabla goals al schema correcto."""
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")

            # Verificar si la tabla existe y qué columnas tiene
            existing = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='goals'"
            ).fetchone()

            if existing:
                # Verificar schema actual
                cols = {row[1] for row in conn.execute("PRAGMA table_info(goals)")}
                required = {"id", "description", "priority", "status", "parent_id",
                           "subgoals", "skills_used", "attempts", "max_attempts",
                           "created_at", "completed_at", "notes"}
                if not required.issubset(cols):
                    # Schema antiguo → migrar (backup + recrear)
                    log.info("Migrando tabla goals a nuevo schema...")
                    conn.execute("ALTER TABLE goals RENAME TO goals_old")
                    conn.execute("""
                        CREATE TABLE goals (
                            id TEXT PRIMARY KEY,
                            description TEXT NOT NULL,
                            priority REAL DEFAULT 0.5,
                            status TEXT DEFAULT 'pending',
                            parent_id TEXT,
                            subgoals TEXT DEFAULT '[]',
                            skills_used TEXT DEFAULT '[]',
                            attempts INTEGER DEFAULT 0,
                            max_attempts INTEGER DEFAULT 5,
                            created_at REAL,
                            completed_at REAL,
                            notes TEXT DEFAULT ''
                        )
                    """)
                    # Migrar datos si hay: mapear columnas comunes
                    try:
                        conn.execute(
                            "INSERT INTO goals (id, description, status, priority, "
                            "created_at, completed_at) "
                            "SELECT id, COALESCE(title, description, ''), status, "
                            "CAST(priority AS REAL), created_at, completed_at "
                            "FROM goals_old"
                        )
                    except Exception:
                        pass
                    conn.execute("DROP TABLE IF EXISTS goals_old")
            else:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS goals (
                        id TEXT PRIMARY KEY,
                        description TEXT NOT NULL,
                        priority REAL DEFAULT 0.5,
                        status TEXT DEFAULT 'pending',
                        parent_id TEXT,
                        subgoals TEXT DEFAULT '[]',
                        skills_used TEXT DEFAULT '[]',
                        attempts INTEGER DEFAULT 0,
                        max_attempts INTEGER DEFAULT 5,
                        created_at REAL,
                        completed_at REAL,
                        notes TEXT DEFAULT ''
                    )
                """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_goals_status
                ON goals(status, priority DESC)
            """)
            conn.commit()

        except Exception as e:
            log.warning("GoalManager DB init: %s", e)

    # ── API ──────────────────────────────────────────────────────────────────

    def add_goal(self, description: str, *,
                 priority: float = 0.5,
                 parent_id: Optional[str] = None,
                 auto_decompose: bool = True) -> str:
        """Añade una meta y opcionalmente la descompone en sub-metas.

        Returns:
            goal_id de la meta creada.
        """
        gid = uuid.uuid4().hex[:12] + "_goal"
        goal = Goal(
            id=gid, description=description,
            priority=priority,
            parent_id=parent_id
        )

        self._persist_goal(goal)
        log.info("Goal añadida [%s]: %s (pri=%.2f)", gid, description[:60], priority)

        if auto_decompose:
            subgoals = self._decompose(description)
            if subgoals:
                for i, sg_desc in enumerate(subgoals):
                    sg_priority = priority - (i * 0.05)  # sub-metas menor prioridad
                    sg_id = self.add_goal(
                        sg_desc,
                        priority=max(0.1, sg_priority),
                        parent_id=gid,
                        auto_decompose=False  # solo un nivel
                    )
                    goal.subgoals.append(sg_id)
                self._persist_goal(goal)  # actualizar subgoals

        return gid

    def get_next_action(self) -> Optional[Dict[str, Any]]:
        """Retorna la siguiente acción accionable (meta hoja pendiente).

        Prioriza: mayor priority, luego más antigua (FIFO por nivel).
        """
        goals = self._load_pending()
        if not goals:
            return None

        # Filtrar metas hoja (sin sub-metas pendientes)
        for g in sorted(goals, key=lambda g: (-g.priority, g.created_at)):
            if g.is_terminal:
                continue
            # ¿Es hoja? (sin sub-metas activas)
            if not g.subgoals or all(
                self._get_status(sg) in (
                    GoalStatus.COMPLETED.value,
                    GoalStatus.FAILED.value,
                    GoalStatus.CANCELLED.value
                )
                for sg in g.subgoals
            ):
                g.attempts += 1
                g.status = GoalStatus.ACTIVE
                self._persist_goal(g)
                return {
                    "goal_id": g.id,
                    "description": g.description,
                    "priority": g.priority,
                    "attempt": g.attempts,
                }

        return None  # Sin acciones hoja pendientes

    def update(self, goal_id: str, result: Dict[str, Any]):
        """Actualiza el estado de una meta tras ejecución."""
        goal = self._load_goal(goal_id)
        if goal is None:
            return

        success = result.get("success", False)
        output = result.get("output", "")

        if success:
            goal.status = GoalStatus.COMPLETED
            goal.completed_at = time.time()
            goal.notes = output[:200]
        else:
            if goal.attempts >= goal.max_attempts:
                goal.status = GoalStatus.FAILED
                goal.notes = f"Falló tras {goal.attempts} intentos: {output[:200]}"
            else:
                goal.status = GoalStatus.PENDING  # reintentar luego
                goal.notes = f"Intento {goal.attempts}: {output[:200]}"

        self._persist_goal(goal)

        # Si es sub-meta completada, verificar si la meta padre se completa
        if goal.parent_id:
            self._check_parent_completion(goal.parent_id)

    def cancel(self, goal_id: str):
        """Cancela una meta y sus sub-metas."""
        goal = self._load_goal(goal_id)
        if goal:
            goal.status = GoalStatus.CANCELLED
            self._persist_goal(goal)
            for sg_id in goal.subgoals:
                self.cancel(sg_id)

    def status_report(self) -> Dict[str, Any]:
        """Reporte de estado de todas las metas."""
        conn = get_conn(BRAIN_DB, timeout=5)
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM goals GROUP BY status"
        ).fetchall()

        counts = {row[0]: row[1] for row in rows}
        total = sum(counts.values())
        return {
            "total": total,
            "pending": counts.get("pending", 0),
            "active": counts.get("active", 0),
            "blocked": counts.get("blocked", 0),
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
            "cancelled": counts.get("cancelled", 0),
        }

    def list_active(self) -> List[Dict[str, Any]]:
        """Lista metas activas/ pendientes."""
        goals = self._load_pending()
        return [g.to_dict() for g in goals[:20]]

    # ── Descomposición heurística ─────────────────────────────────────────────

    def _decompose(self, description: str) -> List[str]:
        """Descompone una meta en sub-metas usando reglas heurísticas."""
        desc_lower = description.lower().strip()

        for pattern, generator in _DECOMPOSE_RULES:
            m = re.search(pattern, desc_lower)
            if m:
                try:
                    subgoals = generator(m)
                    if subgoals:
                        log.debug("Descomposición: '%s' → %d sub-metas",
                                  description[:50], len(subgoals))
                        return subgoals
                except Exception:
                    continue

        # Sin regla aplicable → meta atómica (no se descompone)
        return []

    # ── Persistencia ──────────────────────────────────────────────────────────

    def _persist_goal(self, goal: Goal):
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute(
                """INSERT OR REPLACE INTO goals
                   (id, description, priority, status, parent_id, subgoals,
                    skills_used, attempts, max_attempts, created_at,
                    completed_at, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (goal.id, goal.description, goal.priority,
                 goal.status.value, goal.parent_id,
                 _json_dumps(goal.subgoals),
                 _json_dumps(goal.skills_used),
                 goal.attempts, goal.max_attempts,
                 goal.created_at, goal.completed_at,
                 goal.notes)
            )
            conn.commit()

        except Exception as e:
            log.debug("_persist_goal error: %s", e)

    def _load_goal(self, goal_id: str) -> Optional[Goal]:
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            row = conn.execute(
                "SELECT * FROM goals WHERE id=?", (goal_id,)
            ).fetchone()

            if row:
                return self._row_to_goal(row)
        except Exception as e:
            log.debug("_load_goal error: %s", e)
        return None

    def _load_pending(self) -> List[Goal]:
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            rows = conn.execute(
                "SELECT * FROM goals WHERE status IN ('pending','active') "
                "ORDER BY priority DESC, created_at ASC LIMIT 50"
            ).fetchall()

            return [self._row_to_goal(r) for r in rows]
        except Exception as e:
            log.debug("_load_pending error: %s", e)
            return []

    def _get_status(self, goal_id: str) -> str:
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            row = conn.execute(
                "SELECT status FROM goals WHERE id=?", (goal_id,)
            ).fetchone()

            return row[0] if row else "unknown"
        except Exception:
            return "unknown"

    def _check_parent_completion(self, parent_id: str):
        """Verifica si todas las sub-metas de un padre están completas."""
        parent = self._load_goal(parent_id)
        if not parent or not parent.subgoals:
            return

        all_done = all(
            self._get_status(sg) in (
                GoalStatus.COMPLETED.value,
                GoalStatus.FAILED.value,
                GoalStatus.CANCELLED.value,
            )
            for sg in parent.subgoals
        )

        if all_done:
            # Verificar si alguna falló
            any_failed = any(
                self._get_status(sg) == GoalStatus.FAILED.value
                for sg in parent.subgoals
            )
            parent.status = GoalStatus.FAILED if any_failed else GoalStatus.COMPLETED
            parent.completed_at = time.time()
            self._persist_goal(parent)
            log.info("Meta padre completada: %s → %s",
                     parent.description[:60], parent.status.value)

            # Propagar hacia arriba
            if parent.parent_id:
                self._check_parent_completion(parent.parent_id)

    @staticmethod
    def _row_to_goal(row: tuple) -> Goal:
        cols = ["id", "description", "priority", "status", "parent_id",
                "subgoals", "skills_used", "attempts", "max_attempts",
                "created_at", "completed_at", "notes"]
        d = dict(zip(cols, row))
        return Goal(
            id=d["id"], description=d["description"] or "",
            priority=d["priority"] or 0.5,
            status=GoalStatus(d["status"] or "pending"),
            parent_id=d.get("parent_id"),
            subgoals=_json_loads(d.get("subgoals", "[]")),
            skills_used=_json_loads(d.get("skills_used", "[]")),
            attempts=d.get("attempts", 0) or 0,
            max_attempts=d.get("max_attempts", 5) or 5,
            created_at=d.get("created_at") or time.time(),
            completed_at=d.get("completed_at"),
            notes=d.get("notes", "") or ""
        )


def _json_dumps(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def _json_loads(s: str) -> Any:
    import json
    try:
        return json.loads(s) if s else []
    except (json.JSONDecodeError, TypeError):
        return [] if s.startswith("[") else {}


# ── Singleton ──────────────────────────────────────────────────────────────────

_goal_manager: Optional[GoalManager] = None


def get_goal_manager() -> GoalManager:
    global _goal_manager
    if _goal_manager is None:
        _goal_manager = GoalManager()
    return _goal_manager


# ── CLI ────────────────────────────────────────────────────────────────────────


def get_active_goals() -> list:
    """Shim para compatibilidad con código que importaba desde core.goals (ahora .dead).
    Retorna las metas activas desde el GoalManager."""
    try:
        gm = get_goal_manager()
        return gm.list_active()
    except Exception:
        return []

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(description="EIDOS Goal Manager")
    p.add_argument("--add", type=str, help="Añadir meta")
    p.add_argument("--list", action="store_true", help="Listar metas activas")
    p.add_argument("--next", action="store_true", help="Siguiente acción")
    p.add_argument("--status", action="store_true", help="Reporte de estado")
    args = p.parse_args()

    gm = GoalManager()

    if args.add:
        gid = gm.add_goal(args.add)
        print(f"Meta creada: {gid}")

    if args.list:
        for g in gm.list_active():
            print(f"  [{g['status']}] pri={g['priority']:.2f} {g['description'][:80]}")

    if args.next:
        action = gm.get_next_action()
        if action:
            print(f"Siguiente acción: {action['description']}")
        else:
            print("Sin acciones pendientes.")

    if args.status:
        report = gm.status_report()
        print(f"Metas: {report['total']} total | "
              f"{report['pending']} pendientes | "
              f"{report['active']} activas | "
              f"{report['completed']} completadas | "
              f"{report['failed']} fallidas")
