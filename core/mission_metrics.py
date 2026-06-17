"""
core/mission_metrics.py — Métricas de misión real [S91]

Base de datos SQLite para tracking de misiones empíricas.
Mide tasas de éxito, falsos positivos OCR, efectividad de recovery,
latencia por paso, y progreso hacia autonomía real.

"No se mejora lo que no se mide." — DeepSeek

Tablas:
  mission_runs      — Una fila por misión ejecutada
  mission_steps     — Una fila por paso dentro de cada misión
  ocr_accuracy      — Falsos positivos/negativos del OCR
  recovery_effectiveness — Qué recoveries funcionan y cuáles no
  daily_summary     — Agregación diaria de métricas

Uso:
    mm = get_mission_metrics()
    mm.start_mission("buscar n8n en Google")
    mm.record_step(1, "click", True, 0.45, scene_before, scene_after, decision)
    mm.end_mission(success=True, total_steps=5)
    report = mm.daily_report()
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from core.db import get_conn

log = logging.getLogger("eidos.mission_metrics")

METRICS_DB = Path.home() / ".eidos" / "mission_metrics.db"


# ── Dataclasses ─────────────────────────────────────────────────────────────────

@dataclass
class StepMetric:
    """Métrica de un paso individual."""
    step_number: int
    action_type: str           # click, type, scroll, navigate, extract, wait
    success: bool
    elapsed_s: float
    ocr_chars_before: int
    ocr_chars_after: int
    ocr_similarity: float      # Jaccard entre before/after
    verdict: str = ""          # verified, stuck, unexpected, danger, slow
    recovery_used: str = ""    # retry, wait_retry, etc.
    danger_words_found: int = 0
    new_elements_found: int = 0
    lost_elements_found: int = 0
    risk_score: float = 0.0    # del UIWorldModel
    was_filtered: bool = False # acción bloqueada por WorldModel


@dataclass
class MissionReport:
    """Reporte completo de una misión."""
    mission_id: str
    goal: str
    started_at: float
    ended_at: float = 0.0
    total_steps: int = 0
    successful_steps: int = 0
    failed_steps: int = 0
    recovered_steps: int = 0
    verdicts: Dict[str, int] = field(default_factory=dict)
    recoveries: Dict[str, int] = field(default_factory=dict)
    ocr_false_positives: int = 0
    avg_step_time: float = 0.0
    avg_ocr_similarity: float = 0.0
    actions_filtered: int = 0
    hgd_used: bool = False
    hgd_nodes: int = 0
    hgd_completed: int = 0
    success: bool = False
    error: str = ""


# ── MissionMetrics ──────────────────────────────────────────────────────────────

class MissionMetrics:
    """Sistema de métricas para validación empírica de misiones.

    Persiste cada misión, paso, y evento en SQLite para análisis posterior.
    """

    def __init__(self, db_path: Path = METRICS_DB):
        self._db_path = Path(db_path)
        self._lock = threading.RLock()
        self._current_mission: Optional[str] = None
        self._current_steps: List[StepMetric] = []
        self._init_db()

    def _init_db(self):
        """Crea las tablas de métricas."""
        with self._lock:
            conn = get_conn(self._db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS mission_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT UNIQUE NOT NULL,
                    goal TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    total_steps INTEGER DEFAULT 0,
                    successful_steps INTEGER DEFAULT 0,
                    failed_steps INTEGER DEFAULT 0,
                    recovered_steps INTEGER DEFAULT 0,
                    avg_step_time REAL DEFAULT 0,
                    avg_ocr_similarity REAL DEFAULT 0,
                    actions_filtered INTEGER DEFAULT 0,
                    ocr_false_positives INTEGER DEFAULT 0,
                    hgd_used INTEGER DEFAULT 0,
                    hgd_nodes INTEGER DEFAULT 0,
                    hgd_completed INTEGER DEFAULT 0,
                    s89_enabled INTEGER DEFAULT 0,
                    s90_enabled INTEGER DEFAULT 0,
                    success INTEGER DEFAULT 0,
                    error TEXT DEFAULT '',
                    verdicts_json TEXT DEFAULT '{}',
                    recoveries_json TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS mission_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    step_number INTEGER NOT NULL,
                    action_type TEXT NOT NULL,
                    success INTEGER DEFAULT 0,
                    elapsed_s REAL DEFAULT 0,
                    ocr_chars_before INTEGER DEFAULT 0,
                    ocr_chars_after INTEGER DEFAULT 0,
                    ocr_similarity REAL DEFAULT 0,
                    verdict TEXT DEFAULT '',
                    recovery_used TEXT DEFAULT '',
                    danger_words_found INTEGER DEFAULT 0,
                    risk_score REAL DEFAULT 0,
                    was_filtered INTEGER DEFAULT 0,
                    action_description TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (mission_id) REFERENCES mission_runs(mission_id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS ocr_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    step_number INTEGER NOT NULL,
                    text_sample TEXT,
                    urls_found INTEGER DEFAULT 0,
                    urls_expected INTEGER DEFAULT 0,
                    false_positive_urls INTEGER DEFAULT 0,
                    clickable_found INTEGER DEFAULT 0,
                    clickable_expected INTEGER DEFAULT 0,
                    ocr_quality_score REAL DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS recovery_effectiveness (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recovery_action TEXT NOT NULL,
                    mission_id TEXT NOT NULL,
                    step_number INTEGER NOT NULL,
                    verdict_before TEXT NOT NULL,
                    success INTEGER DEFAULT 0,
                    steps_saved INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_summary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT UNIQUE NOT NULL,
                    missions_run INTEGER DEFAULT 0,
                    missions_succeeded INTEGER DEFAULT 0,
                    total_steps INTEGER DEFAULT 0,
                    success_rate REAL DEFAULT 0,
                    avg_steps_per_mission REAL DEFAULT 0,
                    avg_step_time REAL DEFAULT 0,
                    most_effective_recovery TEXT DEFAULT '',
                    top_failure_reason TEXT DEFAULT '',
                    ocr_accuracy REAL DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)

            conn.commit()
            pass  # S109: get_conn no necesita close()
    # ── Mission lifecycle ───────────────────────────────────────────────────

    def start_mission(self, goal: str, s89_enabled: bool = True,
                      s90_enabled: bool = True) -> str:
        """Inicia una nueva misión y retorna su ID."""
        import hashlib
        mission_id = hashlib.md5(
            f"{goal}:{time.time()}".encode()
        ).hexdigest()[:12]

        with self._lock:
            conn = get_conn(self._db_path, timeout=5)
            conn.execute(
                "INSERT INTO mission_runs (mission_id, goal, started_at, "
                "s89_enabled, s90_enabled) VALUES (?,?,?,?,?)",
                (mission_id, goal, time.time(), int(s89_enabled), int(s90_enabled))
            )
            conn.commit()
            pass  # S109: get_conn no necesita close()
            self._current_mission = mission_id
            self._current_steps = []

        log.info("📊 Misión iniciada: %s (id=%s, s89=%s, s90=%s)",
                goal[:80], mission_id, s89_enabled, s90_enabled)
        return mission_id

    def record_step(self, step_number: int, action_type: str, success: bool,
                    elapsed_s: float, scene_before: Any = None,
                    scene_after: Any = None, decision: Dict = None,
                    verification: Any = None) -> StepMetric:
        """Registra un paso individual de la misión."""
        ocr_before = len(scene_before.ocr_full_text) if scene_before and hasattr(scene_before, 'ocr_full_text') else 0
        ocr_after = len(scene_after.ocr_full_text) if scene_after and hasattr(scene_after, 'ocr_full_text') else 0

        # Calcular similitud OCR
        similarity = 0.0
        if scene_before and scene_after:
            similarity = self._jaccard_similarity(
                getattr(scene_before, 'ocr_full_text', ''),
                getattr(scene_after, 'ocr_full_text', '')
            )

        metric = StepMetric(
            step_number=step_number,
            action_type=action_type,
            success=success,
            elapsed_s=elapsed_s,
            ocr_chars_before=ocr_before,
            ocr_chars_after=ocr_after,
            ocr_similarity=round(similarity, 4),
            verdict=getattr(verification, 'verdict', '') if verification else '',
            recovery_used=decision.get('recovery', '') if decision else '',
            danger_words_found=0,
            risk_score=decision.get('risk_score', 0.0) if decision else 0.0,
            was_filtered=decision.get('risk_mitigated', False) if decision else False,
        )

        with self._lock:
            conn = get_conn(self._db_path, timeout=5)
            conn.execute(
                "INSERT INTO mission_steps (mission_id, step_number, action_type, "
                "success, elapsed_s, ocr_chars_before, ocr_chars_after, "
                "ocr_similarity, verdict, recovery_used, risk_score, was_filtered) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (self._current_mission, step_number, action_type,
                 int(success), elapsed_s, ocr_before, ocr_after,
                 similarity, metric.verdict, metric.recovery_used,
                 metric.risk_score, int(metric.was_filtered))
            )
            conn.commit()
            pass  # S109: get_conn no necesita close()
            self._current_steps.append(metric)

        return metric

    def record_ocr_event(self, step_number: int, text_sample: str,
                         urls_found: int, urls_expected: int = 0,
                         clickable_found: int = 0, clickable_expected: int = 0):
        """Registra un evento de OCR para análisis de precisión."""
        false_positives = max(0, urls_found - urls_expected) if urls_expected > 0 else 0
        quality = min(1.0, urls_found / max(1, urls_expected)) if urls_expected > 0 else 0.5

        with self._lock:
            conn = get_conn(self._db_path, timeout=5)
            conn.execute(
                "INSERT INTO ocr_events (mission_id, step_number, text_sample, "
                "urls_found, urls_expected, false_positive_urls, "
                "clickable_found, clickable_expected, ocr_quality_score) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (self._current_mission, step_number, text_sample[:500],
                 urls_found, urls_expected, false_positives,
                 clickable_found, clickable_expected, round(quality, 3))
            )
            conn.commit()
            pass  # S109: get_conn no necesita close()
    def record_recovery(self, step_number: int, recovery_action: str,
                        verdict_before: str, success: bool, steps_saved: int = 0):
        """Registra efectividad de una recovery action."""
        with self._lock:
            conn = get_conn(self._db_path, timeout=5)
            conn.execute(
                "INSERT INTO recovery_effectiveness (recovery_action, mission_id, "
                "step_number, verdict_before, success, steps_saved) "
                "VALUES (?,?,?,?,?,?)",
                (recovery_action, self._current_mission, step_number,
                 verdict_before, int(success), steps_saved)
            )
            conn.commit()
            pass  # S109: get_conn no necesita close()
    def end_mission(self, success: bool, total_steps: int,
                    hgd_used: bool = False, hgd_nodes: int = 0,
                    hgd_completed: int = 0, error: str = "") -> MissionReport:
        """Finaliza una misión y genera reporte."""
        if not self._current_mission:
            return MissionReport(mission_id="none", goal="none", started_at=0)

        ended_at = time.time()
        successful = sum(1 for s in self._current_steps if s.success)
        failed = total_steps - successful
        recovered = sum(1 for s in self._current_steps if s.recovery_used)
        avg_time = sum(s.elapsed_s for s in self._current_steps) / max(1, len(self._current_steps))
        avg_sim = sum(s.ocr_similarity for s in self._current_steps) / max(1, len(self._current_steps))
        filtered = sum(1 for s in self._current_steps if s.was_filtered)

        # Contar verdicts
        verdicts = {}
        for s in self._current_steps:
            if s.verdict:
                v = str(s.verdict)
                verdicts[v] = verdicts.get(v, 0) + 1

        # Contar recoveries
        recoveries = {}
        for s in self._current_steps:
            if s.recovery_used:
                r = s.recovery_used
                recoveries[r] = recoveries.get(r, 0) + 1

        with self._lock:
            conn = get_conn(self._db_path, timeout=5)
            conn.execute(
                "UPDATE mission_runs SET ended_at=?, total_steps=?, "
                "successful_steps=?, failed_steps=?, recovered_steps=?, "
                "avg_step_time=?, avg_ocr_similarity=?, actions_filtered=?, "
                "hgd_used=?, hgd_nodes=?, hgd_completed=?, success=?, error=?, "
                "verdicts_json=?, recoveries_json=? "
                "WHERE mission_id=?",
                (ended_at, total_steps, successful, failed, recovered,
                 round(avg_time, 4), round(avg_sim, 4), filtered,
                 int(hgd_used), hgd_nodes, hgd_completed,
                 int(success), error,
                 json.dumps(verdicts), json.dumps(recoveries),
                 self._current_mission)
            )
            conn.commit()
            pass  # S109: get_conn no necesita close()
        report = MissionReport(
            mission_id=self._current_mission,
            goal="",
            started_at=0,
            ended_at=ended_at,
            total_steps=total_steps,
            successful_steps=successful,
            failed_steps=failed,
            recovered_steps=recovered,
            verdicts=verdicts,
            recoveries=recoveries,
            avg_step_time=round(avg_time, 4),
            avg_ocr_similarity=round(avg_sim, 4),
            actions_filtered=filtered,
            hgd_used=hgd_used,
            hgd_nodes=hgd_nodes,
            hgd_completed=hgd_completed,
            success=success,
            error=error,
        )

        log.info("📊 Misión finalizada: %s | success=%s | %d/%d pasos | "
                "%.2fs avg | %d recoveries | hgd=%s",
                self._current_mission, success, successful, total_steps,
                avg_time, recovered, hgd_used)

        self._current_mission = None
        self._current_steps = []
        return report

    # ── Reporting ───────────────────────────────────────────────────────────

    def mission_report(self, mission_id: str) -> Optional[MissionReport]:
        """Retorna el reporte de una misión específica."""
        with self._lock:
            conn = get_conn(self._db_path, timeout=5)
            row = conn.execute(
                "SELECT * FROM mission_runs WHERE mission_id=?", (mission_id,)
            ).fetchone()
            pass  # S109: get_conn no necesita close()
        if not row:
            return None

        cols = [c[0] for c in conn.execute("PRAGMA table_info(mission_runs)").fetchall()]
        d = dict(zip(cols, row))
        return MissionReport(
            mission_id=d.get("mission_id", ""),
            goal=d.get("goal", ""),
            started_at=d.get("started_at", 0),
            ended_at=d.get("ended_at", 0),
            total_steps=d.get("total_steps", 0),
            successful_steps=d.get("successful_steps", 0),
            failed_steps=d.get("failed_steps", 0),
            recovered_steps=d.get("recovered_steps", 0),
            verdicts=json.loads(d.get("verdicts_json", "{}")),
            recoveries=json.loads(d.get("recoveries_json", "{}")),
            avg_step_time=d.get("avg_step_time", 0),
            avg_ocr_similarity=d.get("avg_ocr_similarity", 0),
            actions_filtered=d.get("actions_filtered", 0),
            hgd_used=bool(d.get("hgd_used", 0)),
            hgd_nodes=d.get("hgd_nodes", 0),
            hgd_completed=d.get("hgd_completed", 0),
            success=bool(d.get("success", 0)),
            error=d.get("error", ""),
        )

    def recent_missions(self, limit: int = 10) -> List[Dict]:
        """Últimas N misiones ejecutadas."""
        with self._lock:
            conn = get_conn(self._db_path, timeout=5)
            rows = conn.execute(
                "SELECT mission_id, goal, total_steps, successful_steps, "
                "success, avg_step_time, hgd_used, s89_enabled, s90_enabled, "
                "ended_at FROM mission_runs ORDER BY ended_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
            pass  # S109: get_conn no necesita close()
        return [
            {
                "mission_id": r[0],
                "goal": r[1][:80],
                "total_steps": r[2],
                "successful_steps": r[3],
                "success": bool(r[4]),
                "avg_step_time": round(r[5], 3) if r[5] else 0,
                "hgd_used": bool(r[6]),
                "s89": bool(r[7]),
                "s90": bool(r[8]),
                "ended_at": r[9],
            }
            for r in rows
        ]

    def daily_report(self, date: str = None) -> Dict[str, Any]:
        """Reporte diario de métricas agregadas."""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        with self._lock:
            conn = get_conn(self._db_path, timeout=5)

            # Misiones del día
            missions = conn.execute(
                "SELECT COUNT(*), SUM(success), AVG(total_steps), "
                "AVG(avg_step_time), AVG(avg_ocr_similarity), "
                "SUM(actions_filtered), SUM(hgd_used) "
                "FROM mission_runs WHERE date(ended_at, 'unixepoch')=?",
                (date,)
            ).fetchone()

            # Recovery más efectiva
            top_recovery = conn.execute(
                "SELECT recovery_action, COUNT(*), SUM(success) "
                "FROM recovery_effectiveness "
                "WHERE date(created_at)=? "
                "GROUP BY recovery_action ORDER BY COUNT(*) DESC LIMIT 1",
                (date,)
            ).fetchone()

            # Verdict más común en fallos
            top_verdict = conn.execute(
                "SELECT verdict, COUNT(*) FROM mission_steps "
                "WHERE success=0 AND verdict != '' "
                "AND date(created_at)=? "
                "GROUP BY verdict ORDER BY COUNT(*) DESC LIMIT 1",
                (date,)
            ).fetchone()

            pass  # S109: get_conn no necesita close()
        total = missions[0] or 0
        succeeded = missions[1] or 0
        return {
            "date": date,
            "missions_run": total,
            "missions_succeeded": succeeded,
            "success_rate": round(succeeded / max(1, total) * 100, 1),
            "avg_steps_per_mission": round(missions[2] or 0, 1),
            "avg_step_time_s": round(missions[3] or 0, 3),
            "avg_ocr_similarity": round(missions[4] or 0, 3),
            "actions_filtered": missions[5] or 0,
            "hgd_missions": missions[6] or 0,
            "top_recovery": top_recovery[0] if top_recovery else "none",
            "top_recovery_success": top_recovery[2] if top_recovery else 0,
            "top_failure_verdict": top_verdict[0] if top_verdict else "none",
        }

    def overall_stats(self) -> Dict[str, Any]:
        """Estadísticas globales de todas las misiones."""
        with self._lock:
            conn = get_conn(self._db_path, timeout=5)

            missions = conn.execute(
                "SELECT COUNT(*), SUM(success), AVG(total_steps), "
                "AVG(avg_step_time), AVG(avg_ocr_similarity), "
                "SUM(recovered_steps), SUM(actions_filtered), "
                "SUM(hgd_used), SUM(hgd_nodes), SUM(hgd_completed) "
                "FROM mission_runs"
            ).fetchone()

            total_steps = conn.execute(
                "SELECT COUNT(*), SUM(success) FROM mission_steps"
            ).fetchone()

            # Top 5 recovery actions
            recoveries = conn.execute(
                "SELECT recovery_action, COUNT(*), "
                "SUM(success)*100.0/MAX(1,COUNT(*)) "
                "FROM recovery_effectiveness "
                "GROUP BY recovery_action ORDER BY COUNT(*) DESC LIMIT 5"
            ).fetchall()

            pass  # S109: get_conn no necesita close()
        total = missions[0] or 0
        succeeded = missions[1] or 0
        return {
            "total_missions": total,
            "missions_succeeded": succeeded,
            "overall_success_rate": round(succeeded / max(1, total) * 100, 1),
            "avg_steps_per_mission": round(missions[2] or 0, 1),
            "avg_step_time_s": round(missions[3] or 0, 3),
            "avg_ocr_similarity": round(missions[4] or 0, 3),
            "total_recoveries": missions[5] or 0,
            "total_actions_filtered": missions[6] or 0,
            "hgd_missions": missions[7] or 0,
            "total_steps": total_steps[0] or 0,
            "step_success_rate": round(
                (total_steps[1] or 0) / max(1, total_steps[0]) * 100, 1
            ),
            "top_recoveries": [
                {"action": r[0], "count": r[1], "success_rate": round(r[2] or 0, 1)}
                for r in recoveries
            ],
            "target_70pct": (succeeded / max(1, total) * 100) >= 70.0,
        }

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _jaccard_similarity(text_a: str, text_b: str) -> float:
        """Calcula similitud Jaccard entre dos textos."""
        if not text_a and not text_b:
            return 1.0
        words_a = set(text_a.lower().split())
        words_b = set(text_b.lower().split())
        if not words_a and not words_b:
            return 1.0
        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union) if union else 0.0

    # ── Maintenance ─────────────────────────────────────────────────────────

    def vacuum(self):
        """Optimiza la base de datos."""
        with self._lock:
            conn = get_conn(self._db_path, timeout=10)
            conn.execute("VACUUM")
            pass  # S109: get_conn no necesita close()
    def stats(self) -> Dict[str, Any]:
        """Tamaño de la DB y conteos."""
        with self._lock:
            conn = get_conn(self._db_path, timeout=5)
            missions = conn.execute("SELECT COUNT(*) FROM mission_runs").fetchone()[0]
            steps = conn.execute("SELECT COUNT(*) FROM mission_steps").fetchone()[0]
            ocr = conn.execute("SELECT COUNT(*) FROM ocr_events").fetchone()[0]
            recoveries = conn.execute("SELECT COUNT(*) FROM recovery_effectiveness").fetchone()[0]
            pass  # S109: get_conn no necesita close()
        db_size = self._db_path.stat().st_size if self._db_path.exists() else 0
        return {
            "db_size_kb": db_size // 1024,
            "total_missions": missions,
            "total_steps": steps,
            "total_ocr_events": ocr,
            "total_recovery_events": recoveries,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

_metrics: Optional[MissionMetrics] = None


def get_mission_metrics() -> MissionMetrics:
    global _metrics
    if _metrics is None:
        _metrics = MissionMetrics()
    return _metrics


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(description="MissionMetrics — validación empírica S91")
    p.add_argument("--report", action="store_true", help="Reporte diario")
    p.add_argument("--stats", action="store_true", help="Estadísticas globales")
    p.add_argument("--recent", type=int, default=5, help="Últimas N misiones")
    p.add_argument("--mission", type=str, help="Reporte de misión específica")
    args = p.parse_args()

    mm = get_mission_metrics()

    if args.report:
        print(json.dumps(mm.daily_report(), indent=2, ensure_ascii=False))
    elif args.stats:
        print(json.dumps(mm.overall_stats(), indent=2, ensure_ascii=False))
    elif args.mission:
        r = mm.mission_report(args.mission)
        if r:
            print(f"Misión: {r.mission_id}")
            print(f"  Success: {r.success}")
            print(f"  Steps: {r.successful_steps}/{r.total_steps} OK")
            print(f"  Avg time: {r.avg_step_time}s")
            print(f"  Recoveries: {r.recovered_steps}")
            print(f"  Verdicts: {r.verdicts}")
            print(f"  HGD: {r.hgd_used} ({r.hgd_completed}/{r.hgd_nodes} nodes)")
        else:
            print(f"Misión no encontrada: {args.mission}")
    elif args.recent:
        missions = mm.recent_missions(args.recent)
        for m in missions:
            status = "✅" if m["success"] else "❌"
            print(f"{status} {m['mission_id']}: {m['goal'][:60]} "
                  f"({m['successful_steps']}/{m['total_steps']} steps, "
                  f"{m['avg_step_time']}s avg, hgd={m['hgd_used']})")
    else:
        print(json.dumps(mm.stats(), indent=2, ensure_ascii=False))
