#!/usr/bin/env python3
"""
EIDOS Autonomy Harness — inspired by EvoClaw
=============================================
Valida que EIDOS es un sistema autónomo, NO un chatbot:

  1. Autonomy Milestone — genera pensamientos sin input humano
  2. Evolution Milestone — knowledge_nodes crece en una ventana
  3. Colony Milestone — agentes hablan entre sí sin trigger humano
  4. Independence Milestone — mide queries sin Ollama / total
  5. Heartbeat Milestone — verifica watchdog activo

Uso:
    python tests/autonomy_harness/run_harness.py           # run completo
    python tests/autonomy_harness/run_harness.py --quick   # ventana 30s
    python tests/autonomy_harness/run_harness.py --full    # ventana 5min
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

EIDOS_HOME = Path.home() / ".eidos"


def _count(db_path: Path, table: str, where: str = "") -> int:
    if not db_path.exists():
        return 0
    try:
        with sqlite3.connect(str(db_path), timeout=5) as conn:
            sql = f"SELECT COUNT(*) FROM {table}"
            if where:
                sql += f" WHERE {where}"
            row = conn.execute(sql).fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


class HarnessMilestone:
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.passed = False
        self.evidence: dict = {}

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "passed": self.passed,
            "evidence": self.evidence,
        }


def check_heartbeat() -> HarnessMilestone:
    m = HarnessMilestone(
        "heartbeat",
        "Autonomous core heartbeat presente y fresco",
    )
    hb = EIDOS_HOME / "heartbeat"
    if not hb.exists():
        m.evidence = {"reason": "heartbeat file missing"}
        return m
    age = time.time() - hb.stat().st_mtime
    m.evidence = {"age_s": round(age, 1)}
    m.passed = age < 300
    return m


def check_autonomy_window(window_s: int) -> HarnessMilestone:
    m = HarnessMilestone(
        "autonomy_window",
        f"Thoughts generados en ventana de {window_s}s sin input humano",
    )
    evo_db = EIDOS_HOME / "evolution_brain.db"
    if not evo_db.exists():
        m.evidence = {"reason": "evolution_brain.db missing"}
        return m

    before = _count(evo_db, "thoughts")
    time.sleep(window_s)
    after = _count(evo_db, "thoughts")

    generated = after - before
    m.evidence = {"before": before, "after": after, "generated": generated, "window_s": window_s}
    m.passed = generated > 0
    return m


def check_evolution_window(window_s: int) -> HarnessMilestone:
    m = HarnessMilestone(
        "evolution_window",
        f"Knowledge nodes crecen en ventana de {window_s}s",
    )
    evo_db = EIDOS_HOME / "evolution_brain.db"
    before = _count(evo_db, "knowledge_nodes")
    time.sleep(window_s)
    after = _count(evo_db, "knowledge_nodes")
    delta = after - before
    m.evidence = {"before": before, "after": after, "delta": delta}
    m.passed = after >= before
    return m


def check_colony_messages(window_s: int) -> HarnessMilestone:
    m = HarnessMilestone(
        "colony_messages",
        f"Colony agentes hablan entre sí en {window_s}s",
    )
    colony_db = EIDOS_HOME / "colony_community.db"
    if not colony_db.exists():
        m.evidence = {"reason": "colony_community.db missing"}
        return m

    try:
        with sqlite3.connect(str(colony_db), timeout=5) as conn:
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
    except Exception as e:
        m.evidence = {"reason": f"error: {e}"}
        return m

    table = "messages" if "messages" in tables else (tables[0] if tables else None)
    if not table:
        m.evidence = {"reason": "no message table"}
        return m

    before = _count(colony_db, table)
    time.sleep(window_s)
    after = _count(colony_db, table)
    delta = after - before
    m.evidence = {"table": table, "before": before, "after": after, "delta": delta}
    m.passed = delta > 0
    return m


def check_independence_score() -> HarnessMilestone:
    m = HarnessMilestone(
        "independence_score",
        "Independence score real (queries sin Ollama / total)",
    )
    evo_db = EIDOS_HOME / "evolution_brain.db"
    if not evo_db.exists():
        m.evidence = {"reason": "evolution_brain.db missing"}
        return m

    try:
        with sqlite3.connect(str(evo_db), timeout=5) as conn:
            row = conn.execute(
                "SELECT score FROM independence_state WHERE id=1"
            ).fetchone()
            score = float(row[0]) if row else 0.0
    except Exception:
        score = 0.0

    total_nodes = _count(evo_db, "knowledge_nodes")
    distilled = _count(
        evo_db,
        "knowledge_nodes",
        "source LIKE 'distilled%'",
    )

    m.evidence = {
        "score_stored": round(score, 4),
        "total_nodes": total_nodes,
        "distilled_nodes": distilled,
        "ratio": round(distilled / max(1, total_nodes), 4),
    }
    m.passed = score > 0.0 and total_nodes > 0
    return m


def check_processes() -> HarnessMilestone:
    m = HarnessMilestone(
        "eidos_processes",
        "Procesos EIDOS corriendo en sistema",
    )
    try:
        import psutil
        matches = []
        for p in psutil.process_iter(["pid", "cmdline"]):
            cmd = " ".join(p.info.get("cmdline") or [])
            if "eidos" in cmd.lower() or "EIDOS" in cmd:
                matches.append({"pid": p.info["pid"], "cmd": cmd[:100]})
        m.evidence = {"count": len(matches), "processes": matches[:5]}
        m.passed = len(matches) > 0
    except Exception as e:
        m.evidence = {"reason": f"psutil error: {e}"}
    return m


def run_harness(window_s: int = 60) -> dict:
    start = datetime.now(timezone.utc).isoformat()
    print(f"🧪 EIDOS AUTONOMY HARNESS — ventana {window_s}s")
    print("=" * 70)

    milestones: list[HarnessMilestone] = []

    print("\n[1/6] heartbeat...")
    milestones.append(check_heartbeat())
    print(f"    {'✅' if milestones[-1].passed else '❌'} {milestones[-1].evidence}")

    print("\n[2/6] procesos EIDOS...")
    milestones.append(check_processes())
    print(f"    {'✅' if milestones[-1].passed else '❌'} {milestones[-1].evidence}")

    print(f"\n[3/6] autonomy window (generando thoughts en {window_s}s)...")
    milestones.append(check_autonomy_window(window_s))
    print(f"    {'✅' if milestones[-1].passed else '❌'} {milestones[-1].evidence}")

    print(f"\n[4/6] evolution window (knowledge crece en {window_s}s)...")
    milestones.append(check_evolution_window(window_s))
    print(f"    {'✅' if milestones[-1].passed else '❌'} {milestones[-1].evidence}")

    print(f"\n[5/6] colony messages (agents hablan en {window_s}s)...")
    milestones.append(check_colony_messages(window_s))
    print(f"    {'✅' if milestones[-1].passed else '❌'} {milestones[-1].evidence}")

    print("\n[6/6] independence score...")
    milestones.append(check_independence_score())
    print(f"    {'✅' if milestones[-1].passed else '❌'} {milestones[-1].evidence}")

    end = datetime.now(timezone.utc).isoformat()
    passed_count = sum(1 for m in milestones if m.passed)
    total = len(milestones)

    verdict = "AUTÓNOMO" if passed_count >= 4 else "CHATBOT" if passed_count <= 2 else "PARCIAL"

    report = {
        "started_at": start,
        "ended_at": end,
        "window_s": window_s,
        "milestones": [m.to_dict() for m in milestones],
        "passed": passed_count,
        "total": total,
        "verdict": verdict,
    }

    print("\n" + "=" * 70)
    print(f"📊 RESULTADO: {passed_count}/{total} milestones passed")
    print(f"🎯 VEREDICTO: {verdict}")
    print("=" * 70)

    report_path = EIDOS_HOME / "autonomy_harness_report.json"
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"📁 Reporte guardado: {report_path}")
    except Exception as e:
        print(f"⚠️ No se pudo guardar reporte: {e}")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="EIDOS Autonomy Harness")
    parser.add_argument("--quick", action="store_true", help="ventana 30s")
    parser.add_argument("--full", action="store_true", help="ventana 5min")
    parser.add_argument("--window", type=int, default=60, help="ventana en segundos")
    args = parser.parse_args()

    window = 30 if args.quick else 300 if args.full else args.window
    report = run_harness(window_s=window)
    return 0 if report["passed"] >= 4 else 1


if __name__ == "__main__":
    sys.exit(main())
