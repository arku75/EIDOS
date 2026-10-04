"""
EIDOS core/octoclaw_bridge.py — Puente OctoClaw ↔ EIDOS + HybridRouter
=======================================================================
v2.0 — integración completa: HybridRouter = SmartRouter (15D) + OctoClaw features

OctoClaw vive en $EIDOS_SOURCE_ROOT/external/octoclaw/lib/.
Este bridge:
- Configura WORKSPACE=EIDOS_DIR antes de importar
- Expone HybridRouter: decide el modelo Ollama óptimo para cada tarea
- Implementa patrol_once() real: salud de Ollama + agentes Colony
- Feedback loop persistente en ~/.eidos/router.db
- Mapea nombres de modelos OctoClaw → modelos Ollama de EIDOS

Uso:
    from core.octoclaw_bridge import get_hybrid_router
    model = get_hybrid_router().decide(message, default_model, agent_id)
"""
from __future__ import annotations

import os
import sys
import sqlite3
import time
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from core.db import get_conn

log = logging.getLogger("eidos.octoclaw_bridge")

EIDOS_ROOT    = Path(__file__).resolve().parent.parent
OCTOCLAW_ROOT = EIDOS_ROOT / "external" / "octoclaw"
OCTOCLAW_LIB  = OCTOCLAW_ROOT / "lib"
DB_PATH       = Path.home() / ".eidos" / "router.db"

os.environ.setdefault("WORKSPACE",             str(EIDOS_ROOT))
os.environ.setdefault("OCTOCLAW_WORKSPACE",    str(EIDOS_ROOT))
os.environ.setdefault("OCTOCLAW_SKILL_ROOT",   str(OCTOCLAW_ROOT))
os.environ.setdefault("OCTOCLAW_NOTIFIER",     "none")
os.environ.setdefault("FEISHU_WEBHOOK",        "")

if str(OCTOCLAW_LIB) not in sys.path:
    sys.path.insert(0, str(OCTOCLAW_LIB))

_octoclaw_route = None
_auto_router    = None
_octopus_config = None
_import_error: Optional[str] = None

try:
    import octoclaw_route as _octoclaw_route
    import auto_router    as _auto_router
    import octopus_config as _octopus_config
    log.info("OctoClaw modules loaded from %s", OCTOCLAW_LIB)
except Exception as e:
    _import_error = f"{type(e).__name__}: {e}"
    log.warning("OctoClaw bridge: import failed (%s) — modo degradado", _import_error)

_OCTOCLAW_TO_OLLAMA: Dict[str, str] = {
    "fast":   "lfm2.5-thinking:1.2b",
    "normal": "lfm2.5-thinking:1.2b",                    # análisis y conversación sustancial
    "heavy":  "deepseek-r1:14b",   # análisis muy profundo
    "vision": "moondream:latest",
    "embed":  "nomic-embed-text",
}


# ─────────────────────────────────────────────────────────────────────────────
# Funciones públicas básicas (compatibilidad hacia atrás)
# ─────────────────────────────────────────────────────────────────────────────

def is_available() -> bool:
    return _octoclaw_route is not None and _auto_router is not None


def _ollama_for_band(band: str) -> str:
    return _OCTOCLAW_TO_OLLAMA.get((band or "normal").lower(), "lfm2.5-thinking:1.2b")


def route_advice(task: str, command: str = "") -> Dict[str, Any]:
    """Devuelve sugerencias de routing desde OctoClaw para una tarea."""
    if not is_available():
        return {
            "available": False,
            "reason": _import_error or "octoclaw not loaded",
            "route": "direct", "model_band": "normal",
            "cost_band": "normal", "latency_ms": 5000,
            "suggested_ollama_model": "lfm2.5-thinking:1.2b",
        }
    try:
        features      = _octoclaw_route.extract_features(task, command)
        is_direct     = _octoclaw_route.direct_contract_candidate(features)
        route         = "direct" if is_direct else "runner"
        work_contract = _octoclaw_route.infer_work_contract_hint(features, route)
        work_type     = _octoclaw_route.infer_work_type_hint(features, route, work_contract)
        phase         = _octoclaw_route.infer_phase_hint(features, route, work_type, work_contract)
        model_band    = _octoclaw_route.infer_model_band_hint(features, route, work_type)
        cost_band     = _octoclaw_route.expected_cost_band(route, features)
        latency_ms    = _octoclaw_route.expected_latency_ms(route, features)
        return {
            "available": True, "route": route,
            "work_contract_hint": work_contract, "work_type": work_type,
            "phase": phase, "model_band": model_band,
            "cost_band": cost_band, "latency_ms": latency_ms,
            "suggested_ollama_model": _ollama_for_band(model_band),
        }
    except Exception as e:
        log.exception("octoclaw route_advice failed")
        return {
            "available": False, "reason": f"{type(e).__name__}: {e}",
            "route": "direct", "model_band": "normal",
            "cost_band": "normal", "latency_ms": 5000,
            "suggested_ollama_model": "lfm2.5-thinking:1.2b",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Patrol real: salud de Ollama + agentes Colony
# ─────────────────────────────────────────────────────────────────────────────

def patrol_once() -> Dict[str, Any]:
    """
    Pasada de salud del sistema EIDOS:
    - Verifica que Ollama responde
    - Verifica que Colony tiene agentes activos
    - Detecta DBs bloqueadas
    Integrado con alert_manager si está disponible.
    """
    results: Dict[str, Any] = {"timestamp": time.time(), "checks": {}}

    # 1. Ollama health
    try:
        import requests as _req
        r = _req.get("http://localhost:11434/api/tags", timeout=5)
        models = [m.get("name", "") for m in r.json().get("models", [])]
        results["checks"]["ollama"] = {"ok": True, "models": len(models)}
    except Exception as e:
        results["checks"]["ollama"] = {"ok": False, "error": str(e)}
        _alert("patrol.ollama_down", f"Ollama no responde: {e}")

    # 2. Colony community DB
    colony_db = Path.home() / ".eidos" / "colony_community.db"
    if colony_db.exists():
        try:
            conn = get_conn(colony_db, timeout=3)
            conn.execute("SELECT 1")
            pass  # S109: get_conn no necesita close()
            results["checks"]["colony_db"] = {"ok": True}
        except Exception as e:
            results["checks"]["colony_db"] = {"ok": False, "error": str(e)}
            _alert("patrol.colony_db_locked", f"colony_community.db bloqueada: {e}")
    else:
        results["checks"]["colony_db"] = {"ok": None, "note": "db not created yet"}

    # 3. Evolution brain DB
    brain_db = Path.home() / ".eidos" / "evolution_brain.db"
    if brain_db.exists():
        try:
            conn = get_conn(brain_db, timeout=3)
            row = conn.execute("SELECT COUNT(*) FROM thoughts").fetchone()
            pass  # S109: get_conn no necesita close()
            results["checks"]["brain"] = {"ok": True, "thoughts": row[0] if row else 0}
        except Exception as e:
            results["checks"]["brain"] = {"ok": False, "error": str(e)}
            _alert("patrol.brain_db_locked", f"evolution_brain.db bloqueada: {e}")

    # 4. OctoClaw patrol nativo (si disponible)
    if is_available():
        try:
            import patrol as _patrol
            if hasattr(_patrol, "patrol_once"):
                native = _patrol.patrol_once()
                results["checks"]["octoclaw_native"] = {"ok": True, "result": str(native)[:200]}
            else:
                results["checks"]["octoclaw_native"] = {"ok": True, "note": "no patrol_once entrypoint"}
        except Exception as e:
            results["checks"]["octoclaw_native"] = {"ok": False, "error": str(e)}

    results["healthy"] = all(
        v.get("ok") is not False
        for v in results["checks"].values()
    )
    log.info("patrol_once: %s", "OK" if results["healthy"] else "ISSUES DETECTED")
    return results


def _alert(event: str, message: str) -> None:
    try:
        from core.alert_manager import get_alert_manager
        get_alert_manager().publish(event, {"message": message})
    except Exception:
        log.warning("PATROL ALERT [%s]: %s", event, message)


# ─────────────────────────────────────────────────────────────────────────────
# HybridRouter — combina SmartRouter (15D) + OctoClaw features
# ─────────────────────────────────────────────────────────────────────────────

class HybridRouter:
    """
    Router híbrido que combina SmartRouter (análisis semántico 15D) con
    OctoClaw (detección de patrones de ejecución) para seleccionar el
    modelo Ollama óptimo en cada tarea de Colony.

    Lógica de decisión (por prioridad):
    1. Si SmartRouter detecta multimodal → vision model siempre
    2. Si OctoClaw detecta runner task (shell/logs) → modelo fast
    3. Si SmartRouter confidence > 0.85 → confía en SmartRouter
    4. Si OctoClaw disponible y SmartRouter confidence < 0.6 → OctoClaw decide
    5. Fallback: SmartRouter decide
    """

    def __init__(self):
        self._smart: Optional[Any] = None
        self._lock  = threading.Lock()
        self._init_db()
        self._load_smart_router()

    def _load_smart_router(self) -> None:
        try:
            from core.smart_router import SmartRouter
            self._smart = SmartRouter()
            log.info("HybridRouter: SmartRouter cargado")
        except Exception as e:
            log.warning("HybridRouter: SmartRouter no disponible (%s)", e)

    def _init_db(self) -> None:
        try:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            conn = get_conn(DB_PATH)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS routing_feedback (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_hash       TEXT,
                    agent_id        TEXT,
                    default_model   TEXT,
                    smart_model     TEXT,
                    octo_model      TEXT,
                    final_model     TEXT,
                    decision_reason TEXT,
                    smart_confidence REAL,
                    success         INTEGER DEFAULT NULL,
                    exec_time_ms    REAL    DEFAULT NULL,
                    timestamp       REAL
                )
            """)
            conn.commit()
            pass  # S109: get_conn no necesita close()
        except Exception as e:
            log.warning("HybridRouter DB init failed: %s", e)

    def decide(self, task: str, default_model: str, agent_id: str = "colony") -> str:
        """
        Decide el modelo Ollama óptimo para una tarea.

        Args:
            task:          texto de la tarea/mensaje del usuario
            default_model: modelo que Colony usaría sin routing
            agent_id:      identificador del agente que pregunta

        Returns:
            Nombre del modelo Ollama a usar
        """
        with self._lock:
            smart_model   = default_model
            smart_conf    = 0.5
            octo_model    = default_model
            reason        = "default"

            # ── SmartRouter analysis ──────────────────────────────────────
            if self._smart is not None:
                try:
                    result     = self._smart.route(task, profile="auto")
                    smart_model = result.model
                    smart_conf  = result.confidence

                    # Regla 1: visión siempre gana
                    if result.tier == "VISION":
                        self._record(task, agent_id, default_model,
                                     smart_model, octo_model, smart_model,
                                     "vision_override", smart_conf)
                        return smart_model
                except Exception as e:
                    log.debug("SmartRouter failed: %s", e)

            # ── OctoClaw analysis ─────────────────────────────────────────
            if is_available():
                try:
                    advice    = route_advice(task)
                    band      = advice.get("model_band", "normal")
                    octo_model = _ollama_for_band(band)
                    route      = advice.get("route", "direct")

                    # Regla 2: runner task → fast model siempre
                    if route == "runner":
                        final = _ollama_for_band("fast")
                        self._record(task, agent_id, default_model,
                                     smart_model, octo_model, final,
                                     "runner_fast", smart_conf)
                        return final
                except Exception as e:
                    log.debug("OctoClaw advice failed: %s", e)

            # ── Merge logic ───────────────────────────────────────────────
            if smart_conf >= 0.85:
                # Regla 3: SmartRouter muy confiado
                final  = smart_model
                reason = f"smart_high_conf({smart_conf:.2f})"
            elif smart_conf < 0.6 and is_available():
                # Regla 4: SmartRouter inseguro + OctoClaw disponible
                final  = octo_model
                reason = f"octo_low_smart_conf({smart_conf:.2f})"
            else:
                # Regla 5: SmartRouter por defecto
                final  = smart_model
                reason = f"smart_default({smart_conf:.2f})"

            self._record(task, agent_id, default_model,
                         smart_model, octo_model, final, reason, smart_conf)
            return final

    def record_outcome(self, task_hash: str, success: bool, exec_time_ms: float) -> None:
        """Registra el resultado de una tarea para mejorar el routing futuro."""
        try:
            conn = get_conn(DB_PATH)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "UPDATE routing_feedback SET success=?, exec_time_ms=? "
                "WHERE task_hash=? ORDER BY timestamp DESC LIMIT 1",
                (1 if success else 0, exec_time_ms, task_hash)
            )
            conn.commit()
            pass  # S109: get_conn no necesita close()
        except Exception:
            pass  # error no crítico, continuar
    def _record(self, task: str, agent_id: str, default_model: str,
                smart_model: str, octo_model: str, final: str,
                reason: str, confidence: float) -> None:
        try:
            import hashlib
            task_hash = hashlib.sha256(task.encode()).hexdigest()[:16]
            conn = get_conn(DB_PATH)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT INTO routing_feedback "
                "(task_hash,agent_id,default_model,smart_model,octo_model,"
                "final_model,decision_reason,smart_confidence,timestamp) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (task_hash, agent_id, default_model, smart_model, octo_model,
                 final, reason, confidence, time.time())
            )
            conn.commit()
            pass  # S109: get_conn no necesita close()
            if final != default_model:
                log.debug("HybridRouter [%s]: %s → %s (%s)", agent_id, default_model, final, reason)
        except Exception:
            pass  # error no crítico, continuar
    def get_stats(self) -> Dict[str, Any]:
        """Estadísticas de routing para el dashboard."""
        try:
            conn = get_conn(DB_PATH)
            conn.execute("PRAGMA journal_mode=WAL")
            total   = conn.execute("SELECT COUNT(*) FROM routing_feedback").fetchone()[0]
            changed = conn.execute(
                "SELECT COUNT(*) FROM routing_feedback WHERE final_model != default_model"
            ).fetchone()[0]
            by_model = conn.execute(
                "SELECT final_model, COUNT(*) as n FROM routing_feedback "
                "GROUP BY final_model ORDER BY n DESC"
            ).fetchall()
            pass  # S109: get_conn no necesita close()
            return {
                "total_decisions": total,
                "model_overrides": changed,
                "override_rate": f"{changed/max(total,1)*100:.1f}%",
                "by_model": {r[0]: r[1] for r in by_model},
            }
        except Exception:
            return {"total_decisions": 0, "model_overrides": 0}


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_hybrid_router: Optional[HybridRouter] = None
_hr_lock = threading.Lock()


def get_hybrid_router() -> HybridRouter:
    global _hybrid_router
    if _hybrid_router is None:
        with _hr_lock:
            if _hybrid_router is None:
                _hybrid_router = HybridRouter()
    return _hybrid_router


def get_workspace() -> str:
    if _octopus_config is not None:
        try:
            return _octopus_config.resolve_workspace()
        except Exception:
            pass  # error no crítico, continuar
    return str(EIDOS_ROOT)


def get_status() -> Dict[str, Any]:
    return {
        "available":    is_available(),
        "import_error": _import_error,
        "workspace":    get_workspace(),
        "lib_path":     str(OCTOCLAW_LIB),
        "model_mapping": _OCTOCLAW_TO_OLLAMA,
        "hybrid_router": get_hybrid_router().get_stats() if True else {},
    }


if __name__ == "__main__":
    import json
    print("=== OctoClaw Bridge Status ===")
    print(json.dumps(get_status(), indent=2))
    print()
    print("=== HybridRouter samples ===")
    router = get_hybrid_router()
    samples = [
        ("Mira los logs de error del daemon", "lfm2.5-thinking:1.2b", "colony_operator"),
        ("Refactoriza esta función Python con tests", "lfm2.5-thinking:1.2b", "colony_coder"),
        ("Analiza esta imagen y dime qué ves", "lfm2.5-thinking:1.2b", "colony_vision"),
        ("¿Cuál es la capital de Francia?", "lfm2.5-thinking:1.2b", "colony_general"),
        ("ps aux | grep eidos", "lfm2.5-thinking:1.2b", "colony_operator"),
    ]
    for task, default, agent in samples:
        chosen = router.decide(task, default, agent)
        changed = "→ OVERRIDE" if chosen != default else "→ same"
        print(f"[{agent}] {task[:55]}")
        print(f"  default={default}  final={chosen}  {changed}")
        print()
