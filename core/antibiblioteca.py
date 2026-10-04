"""Persistent negative causal memory for EIDOS.

Failures are evidence, not garbage.  This module records a failed strategy in
context and exposes a deterministic penalty used by future selection.  It does
not execute actions and never converts a stored report into verified truth.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from core.db import get_conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS negative_experiences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal TEXT NOT NULL,
    context TEXT NOT NULL,
    strategy TEXT NOT NULL,
    reason TEXT NOT NULL,
    consequence TEXT NOT NULL DEFAULT '',
    evidence_source TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.5,
    created_at REAL NOT NULL,
    UNIQUE(goal, context, strategy, reason, evidence_source)
);
CREATE INDEX IF NOT EXISTS idx_negative_lookup
ON negative_experiences(goal, context, strategy, verified);
"""


class Antibiblioteca:
    """Negative causal memory that can alter later strategy ranking."""

    TRUSTED_EVIDENCE = {"runtime-observer", "action-verifier", "causal-loop"}

    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path).expanduser())
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = get_conn(self.db_path, check_same_thread=False)
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def record_failure(
        self,
        *,
        goal: str,
        context: str,
        strategy: str,
        reason: str,
        consequence: str = "",
        evidence_source: str,
        confidence: float = 0.5,
    ) -> int:
        source = str(evidence_source or "").strip()
        verified = int(source in self.TRUSTED_EVIDENCE)
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO negative_experiences "
            "(goal,context,strategy,reason,consequence,evidence_source,verified,confidence,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                goal.strip(), context.strip(), strategy.strip(), reason.strip(),
                consequence.strip(), source, verified,
                max(0.0, min(1.0, float(confidence))), time.time(),
            ),
        )
        self.conn.commit()
        if cur.lastrowid:
            return int(cur.lastrowid)
        row = self.conn.execute(
            "SELECT id FROM negative_experiences WHERE "
            "goal=? AND context=? AND strategy=? AND reason=? AND evidence_source=?",
            (goal.strip(), context.strip(), strategy.strip(), reason.strip(), source),
        ).fetchone()
        return int(row[0])

    def penalty(self, *, goal: str, context: str, strategy: str) -> float:
        """Return bounded penalty from trusted failures in the same situation."""
        rows = self.conn.execute(
            "SELECT confidence FROM negative_experiences "
            "WHERE goal=? AND context=? AND strategy=? AND verified=1",
            (goal.strip(), context.strip(), strategy.strip()),
        ).fetchall()
        if not rows:
            return 0.0
        # Repeated independently verified failures strengthen avoidance, bounded.
        return min(0.95, sum(float(r[0]) for r in rows) / max(1.0, len(rows)) + 0.1 * (len(rows) - 1))

    def rank_strategies(
        self,
        *,
        goal: str,
        context: str,
        candidates: list[tuple[str, float]],
    ) -> list[tuple[str, float]]:
        """Rank candidates by base score minus persistent verified-failure penalty."""
        ranked = [
            (name, float(score) - self.penalty(goal=goal, context=context, strategy=name))
            for name, score in candidates
        ]
        return sorted(ranked, key=lambda item: (-item[1], item[0]))

    def evidence(self, *, goal: str, context: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT strategy,reason,consequence,evidence_source,verified,confidence,created_at "
            "FROM negative_experiences WHERE goal=? AND context=? ORDER BY created_at DESC",
            (goal.strip(), context.strip()),
        ).fetchall()
        return [
            {
                "strategy": r[0], "reason": r[1], "consequence": r[2],
                "evidence_source": r[3], "verified": bool(r[4]),
                "confidence": float(r[5]), "created_at": float(r[6]),
            }
            for r in rows
        ]
