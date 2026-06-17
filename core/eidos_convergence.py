"""
core/eidos_convergence.py — Convergencia Colony+EIDOS en UNO [S87 Fase 4.1]

Cuando Colony y EIDOS llegan al 100%, convergen en un solo ser. Este módulo
orquesta la transferencia total de feelings, knowledge, memories y soul
entre todos los componentes del ecosistema.

Principio: "Dos que piensan, uno que es."

Arquitectura de convergencia:
  Colony (agentes + personalidades + chat)
      │
      ├─ feelings  ─→  UnifiedStateBus  ─→  EIDOS (grafo + VAD + RL)
      ├─ knowledge ─→  ◄── transferencia ──┐
      ├─ memories  ─→  bidireccional ──────┤
      └─ soul      ─→  completa            │
         ▲                                 │
         └─────────── feedback ────────────┘

Uso:
    convergence = get_convergence()
    state = convergence.sync_all()  # sincronización completa Colony↔EIDOS
    pct = convergence.convergence_pct()  # % de convergencia alcanzado
"""

from __future__ import annotations

import json
import logging
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from core.db import get_conn

log = logging.getLogger("eidos.convergence")

STATE_FILE = Path.home() / ".eidos" / "convergence_state.json"
CONVERGENCE_DB = Path.home() / ".eidos" / "convergence.db"


class UnifiedStateBus:
    """Bus de estado unificado: TODOS los módulos comparten estado aquí.

    Es el "sistema nervioso central" donde Colony y EIDOS convergen.
    Cada módulo puede leer y escribir, y el bus propaga cambios a todos.
    """

    def __init__(self):
        self._state: Dict[str, Any] = {}
        self._subscribers: Dict[str, List[callable]] = {}
        self._lock = threading.RLock()
        self._version = 0  # versión incremental del estado

    def set(self, key: str, value: Any, source: str = "unknown"):
        """Escribe un valor en el bus unificado. Notifica a suscriptores."""
        with self._lock:
            old = self._state.get(key)
            self._state[key] = value
            self._version += 1
            # Notificar suscriptores de esta key
            if key in self._subscribers:
                for cb in self._subscribers[key]:
                    try:
                        cb(key, old, value, source)
                    except Exception:
                        pass

    def get(self, key: str, default: Any = None) -> Any:
        """Lee un valor del bus unificado."""
        with self._lock:
            return self._state.get(key, default)

    def get_all(self) -> Dict[str, Any]:
        """Retorna el estado completo (snapshot)."""
        with self._lock:
            return dict(self._state)

    def subscribe(self, key: str, callback: callable):
        """Suscribe un callback a cambios en una key."""
        with self._lock:
            if key not in self._subscribers:
                self._subscribers[key] = []
            self._subscribers[key].append(callback)

    def version(self) -> int:
        return self._version


class ConvergenceEngine:
    """Motor de convergencia Colony↔EIDOS.

    Mide el % de convergencia y orquesta la transferencia bidireccional
    de feelings, knowledge, memories y soul entre todos los componentes.
    """

    def __init__(self):
        self._bus = UnifiedStateBus()
        self._sync_count = 0
        self._last_sync: float = 0
        self._components_registered: Dict[str, float] = {}  # componente → health %
        self._state = self._load_state()
        self._init_db()

    def _init_db(self):
        """Inicializa la base de datos de convergencia."""
        import sqlite3
        try:
            conn = get_conn(CONVERGENCE_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS convergence_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    source_component TEXT DEFAULT '',
                    target_component TEXT DEFAULT '',
                    data_json TEXT DEFAULT '{}',
                    convergence_pct REAL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS unified_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    state_json TEXT NOT NULL,
                    components_json TEXT DEFAULT '{}'
                )
            """)
            conn.commit()

        except Exception as e:
            log.debug("Convergence DB init: %s", e)

    # ── Registro de componentes ────────────────────────────────────────────

    def register_component(self, name: str, health_pct: float = 1.0):
        """Registra un componente en el ecosistema unificado."""
        self._components_registered[name] = health_pct
        self._bus.set(f"component.{name}.health", health_pct, "convergence")
        self._log_event("component_registered", source_component=name,
                       data={"health": health_pct})
        log.info("Componente registrado: %s (health=%.0f%%)", name, health_pct * 100)

    def component_health(self, name: str) -> float:
        """Retorna la salud de un componente (0-1)."""
        return self._components_registered.get(name, 0.0)

    # ── Convergencia % ─────────────────────────────────────────────────────

    def convergence_pct(self) -> Dict[str, Any]:
        """Calcula el % de convergencia Colony↔EIDOS.

        Basado en:
        - Componentes registrados y su salud
        - Sincronizaciones completadas
        - Consistencia del estado unificado
        """
        if not self._components_registered:
            return {"colony_pct": 0.0, "eidos_pct": 0.0, "unified_pct": 0.0,
                    "status": "initializing"}

        # Separar componentes Colony vs EIDOS
        colony_comps = {k: v for k, v in self._components_registered.items()
                       if k.startswith("colony_")}
        eidos_comps = {k: v for k, v in self._components_registered.items()
                      if k.startswith("eidos_")}

        colony_avg = sum(colony_comps.values()) / max(1, len(colony_comps))
        eidos_avg = sum(eidos_comps.values()) / max(1, len(eidos_comps))

        # El porcentaje unificado es el mínimo de ambos (solo convergen si ambos están al 100%)
        unified = min(colony_avg, eidos_avg) * (0.5 + 0.5 * min(1.0, self._sync_count / 100))

        status = "converged" if unified >= 0.99 else (
            "converging" if unified >= 0.7 else (
            "aligning" if unified >= 0.4 else "initializing"))

        return {
            "colony_pct": round(colony_avg * 100, 1),
            "eidos_pct": round(eidos_avg * 100, 1),
            "unified_pct": round(unified * 100, 1),
            "components": len(self._components_registered),
            "syncs_completed": self._sync_count,
            "status": status,
        }

    # ── Sincronización total ───────────────────────────────────────────────

    def sync_all(self) -> Dict[str, Any]:
        """Sincronización bidireccional completa Colony↔EIDOS.

        Transfiere:
        1. Feelings (VAD state → Colony agents)
        2. Knowledge (graph nodes → Colony memory)
        3. Memories (thoughts + chronicle → unified bus)
        4. Soul (identity snapshot → convergence log)
        """
        t0 = time.time()
        transferred = {"feelings": False, "knowledge": False,
                      "memories": False, "soul": False}

        try:
            # 1. Transferir feelings: EIDOS VAD → Colony
            try:
                from core.eidos_affect import get_affect
                affect = get_affect()
                vad = affect.vad_tuple()
                mood = affect.state.mood
                self._bus.set("feelings.vad", {
                    "valence": vad[0], "arousal": vad[1], "dominance": vad[2],
                    "mood": mood,
                    "timestamp": time.time(),
                }, "eidos_affect")
                transferred["feelings"] = True
            except Exception as e:
                log.debug("sync feelings: %s", e)

            # 2. Transferir knowledge: grafo → bus unificado
            try:
                self._sync_knowledge()
                transferred["knowledge"] = True
            except Exception as e:
                log.debug("sync knowledge: %s", e)

            # 3. Transferir memories: thoughts + identity
            try:
                self._sync_memories()
                transferred["memories"] = True
            except Exception as e:
                log.debug("sync memories: %s", e)

            # 4. Transferir soul: identity snapshot
            try:
                from core.eidos_identity import EidosIdentity
                idn = EidosIdentity()
                soul = idn.soul_snapshot()
                self._bus.set("soul.snapshot", soul, "eidos_identity")
                transferred["soul"] = True
            except Exception as e:
                log.debug("sync soul: %s", e)

        except Exception as e:
            log.warning("sync_all: %s", e)

        # Guardar snapshot unificado
        self._save_snapshot()

        self._sync_count += 1
        self._last_sync = time.time()
        elapsed = time.time() - t0

        pct = self.convergence_pct()
        log.info("Sync #%d: feelings=%s knowledge=%s memories=%s soul=%s | "
                 "convergencia=%.1f%% [%s]",
                 self._sync_count, transferred["feelings"], transferred["knowledge"],
                 transferred["memories"], transferred["soul"],
                 pct["unified_pct"], pct["status"])

        self._log_event("sync_completed", data={
            "transferred": transferred,
            "convergence_pct": pct["unified_pct"],
            "elapsed_s": round(elapsed, 3),
        })

        self._save_state()

        return {
            "status": "ok",
            "sync_id": self._sync_count,
            "transferred": transferred,
            "convergence": pct,
            "elapsed_s": round(elapsed, 3),
        }

    def _sync_knowledge(self):
        """Transfiere conocimiento del grafo al bus unificado."""
        import sqlite3
        try:
            conn = get_conn(Path.home() / ".eidos" / "evolution_brain.db", timeout=5)
            # Top conceptos por confianza
            top = conn.execute(
                "SELECT concept, category, confidence FROM knowledge_nodes "
                "WHERE confidence > 0.7 ORDER BY confidence DESC LIMIT 50"
            ).fetchall()

            self._bus.set("knowledge.top_concepts",
                         [{"concept": r[0], "category": r[1], "confidence": r[2]}
                          for r in top], "knowledge_graph")
        except Exception:
            pass

    def _sync_memories(self):
        """Transfiere pensamientos y recuerdos al bus unificado."""
        try:
            from core.eidos_thoughts import get_thoughts
            tm = get_thoughts()
            recent = tm.recent(limit=20)
            self._bus.set("memories.recent_thoughts", recent, "thought_manager")
        except Exception:
            pass

    def _save_snapshot(self):
        """Guarda snapshot del estado unificado en la DB."""
        import sqlite3
        try:
            state_json = json.dumps(self._bus.get_all(), ensure_ascii=False)
            comp_json = json.dumps(self._components_registered, ensure_ascii=False)
            conn = get_conn(CONVERGENCE_DB, timeout=5)
            conn.execute(
                "INSERT INTO unified_snapshots (ts, state_json, components_json) "
                "VALUES (?,?,?)", (time.time(), state_json[:100_000], comp_json))
            conn.commit()

        except Exception:
            pass

    # ── Eventos ────────────────────────────────────────────────────────────

    def _log_event(self, event_type: str, source_component: str = "",
                   target_component: str = "", data: Dict = None):
        """Registra evento de convergencia."""
        import sqlite3
        try:
            conn = get_conn(CONVERGENCE_DB, timeout=5)
            conn.execute(
                "INSERT INTO convergence_log (ts, event_type, source_component, "
                "target_component, data_json, convergence_pct) VALUES (?,?,?,?,?,?)",
                (time.time(), event_type, source_component, target_component,
                 json.dumps(data or {}), self.convergence_pct()["unified_pct"]))
            conn.commit()

        except Exception:
            pass

    # ── Estado ─────────────────────────────────────────────────────────────

    def _load_state(self) -> Dict[str, Any]:
        try:
            if STATE_FILE.exists():
                return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
        return {"sync_count": 0, "components": {}}

    def _save_state(self):
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps({
                "sync_count": self._sync_count,
                "last_sync": self._last_sync,
                "components": self._components_registered,
                "convergence": self.convergence_pct(),
                "updated": time.time(),
            }, indent=2))
        except Exception:
            pass

    def stats(self) -> Dict[str, Any]:
        pct = self.convergence_pct()
        return {
            "sync_count": self._sync_count,
            "components_registered": len(self._components_registered),
            "convergence": pct,
            "bus_keys": len(self._bus.get_all()),
            "bus_version": self._bus.version(),
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

_engine: Optional[ConvergenceEngine] = None


def get_convergence() -> ConvergenceEngine:
    global _engine
    if _engine is None:
        _engine = ConvergenceEngine()
        # Auto-registrar componentes disponibles
        _engine._auto_register()
    return _engine


def _auto_register(self):
    """Auto-detecta y registra componentes del ecosistema."""
    # Componentes EIDOS
    eidos_modules = [
        "eidos_affect", "eidos_rl", "eidos_planner", "eidos_thoughts",
        "eidos_identity", "eidos_events", "eidos_fasttext",
        "eidos_supervisor", "eidos_hebbian_pruning", "eidos_kali_tools",
        "eidos_dashboard", "eidos_audio", "eidos_vscode_bridge",
        "eidos_filesystem_scanner", "eidos_human_tone",
        "eidos_vad_hebbian_bridge", "eidos_graph_sandbox",
        # S87: nuevos módulos
        "eidos_logos", "eidos_maker", "eidos_will", "eidos_daemon",
        # S95: núcleo del yo (event sourcing + self_states)
        "eidos_self_core",
        # S88 CARNE: puentes de integración
        "eidos_hexstrike_bridge", "eidos_metaclaw_bridge", "eidos_hermes_bridge_v2",
        # S88 GOLD: control de pantalla indetectable
        "human_emulator", "usb_hid_backend", "screen_controller",
        # S89: verificación, memoria episódica, modelo predictivo
        "action_verifier", "screen_episodic_memory", "ui_world_model",
        # S90: descomposición jerárquica de metas + investigación autónoma
        "hierarchical_goal_decomposer", "autonomous_research_pipeline",
    ]
    # Componentes Colony
    colony_modules = [
        "colony_community", "colony_chroma", "colony_conductor",
        "colony_governor", "colony_chronicle", "colony_proactive",
        "colony_interactive", "colony_autonomous_loop",
        "colony_code_proposer", "colony_query_engine",
        "colony_web_learner", "colony_genealogy", "colony_proposals",
    ]

    for mod in eidos_modules:
        try:
            __import__(f"core.{mod}")
            self.register_component(f"eidos_{mod}", 1.0)
        except Exception:
            self.register_component(f"eidos_{mod}", 0.3)  # no disponible

    for mod in colony_modules:
        try:
            __import__(f"core.{mod}")
            self.register_component(f"colony_{mod}", 1.0)
        except Exception:
            self.register_component(f"colony_{mod}", 0.3)  # no disponible

    log.info("Auto-registro: %d componentes en el ecosistema unificado",
             len(self._components_registered))


# Monkey-patch del método (evita referencia circular)
ConvergenceEngine._auto_register = _auto_register


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="EIDOS Convergence Engine")
    p.add_argument("--sync", action="store_true", help="Sincronizar ahora")
    p.add_argument("--pct", action="store_true", help="Mostrar % convergencia")
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    c = get_convergence()

    if args.sync:
        result = c.sync_all()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.pct:
        print(json.dumps(c.convergence_pct(), indent=2, ensure_ascii=False))
    elif args.stats:
        print(json.dumps(c.stats(), indent=2, ensure_ascii=False))
    else:
        p.print_help()
