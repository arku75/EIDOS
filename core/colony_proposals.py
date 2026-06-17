"""
EIDOS core/colony_proposals.py — Democratic Proposal System
=============================================================
Los agentes pueden crear propuestas, votar, y ejecutar cambios
de forma democrática. Inspirado en ClawColony governance.

Tipos de propuesta:
- config_change: cambiar configuración de la colonia
- skill_add: añadir nueva skill
- priority_shift: cambiar prioridades de tareas
- resource_request: solicitar recursos (más tokens, acceso a modelo)
- experiment: proponer un experimento/investigación

Ciclo de vida:
    draft → open → voting → passed/rejected → executed/expired

Uso:
    from core.colony_proposals import get_proposal_system
    ps = get_proposal_system()
    pid = ps.create("colony_coder", "skill_add",
                     "Añadir skill de análisis de logs",
                     "Propongo crear una skill que analice logs automáticamente")
    ps.vote(pid, "colony_analyst", True, "Necesitamos esto para debugging")
    ps.vote(pid, "colony_operator", True, "Apruebo")
    ps.tally(pid)  # Cuenta votos y decide
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from core.db import get_conn

log = logging.getLogger("eidos.proposals")

DB_PATH = os.path.expanduser("~/.eidos/colony_proposals.db")

PROPOSAL_TYPES = {
    "config_change", "skill_add", "priority_shift",
    "resource_request", "experiment", "governance_change",
    "agent_discipline", "knowledge_initiative",
    "connection_retire", "character_reproduction",
}

PASS_THRESHOLD = 0.50  # >50% de votos afirmativos
MIN_VOTERS = 2         # mínimo de votantes
VOTING_PERIOD_SECS = 300  # 5 minutos para votar (en modo autónomo es rápido)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    author TEXT NOT NULL,
    proposal_type TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    metadata TEXT DEFAULT '{}',
    resolved_at REAL,
    execution_result TEXT
);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);

CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id INTEGER NOT NULL REFERENCES proposals(id),
    voter TEXT NOT NULL,
    vote INTEGER NOT NULL,
    reason TEXT DEFAULT '',
    voted_at REAL NOT NULL,
    UNIQUE(proposal_id, voter)
);
CREATE INDEX IF NOT EXISTS idx_votes_proposal ON votes(proposal_id);
"""


@dataclass
class Proposal:
    id: int
    created_at: float
    author: str
    proposal_type: str
    title: str
    description: str
    status: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    resolved_at: Optional[float] = None
    execution_result: Optional[str] = None
    votes_for: int = 0
    votes_against: int = 0

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "author": self.author,
            "type": self.proposal_type, "title": self.title,
            "status": self.status,
            "votes_for": self.votes_for, "votes_against": self.votes_against,
        }


@dataclass
class Vote:
    voter: str
    vote: bool
    reason: str
    voted_at: float


class ColonyProposalSystem:
    """Sistema de propuestas y votación democrática."""

    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = get_conn(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._execution_handlers: Dict[str, Callable] = {}
        log.info("Colony Proposal System initialized")

    def create(self, author: str, proposal_type: str, title: str,
               description: str, metadata: Optional[Dict[str, Any]] = None) -> int:
        """Crea una nueva propuesta."""
        metadata = metadata or {}
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO proposals (created_at, author, proposal_type, title, "
                "description, status, metadata) VALUES (?, ?, ?, ?, ?, 'open', ?)",
                (time.time(), author, proposal_type, title, description,
                 json.dumps(metadata))
            )
            self._conn.commit()
            pid = cur.lastrowid

        log.info("Proposal #%d created by %s: %s", pid, author, title)

        # Chronicle
        try:
            from core.colony_chronicle import get_chronicle
            get_chronicle().record(author, "proposal_created",
                                   f"#{pid}: {title}",
                                   metadata={"proposal_id": pid, "type": proposal_type},
                                   importance=0.7)
        except ImportError:
            pass

        # Notify via mail
        try:
            from core.agent_mail import get_mailbox
            get_mailbox().broadcast_topic(
                author, "governance",
                f"Nueva propuesta #{pid}: {title}\n\n{description}\n\nVota con ps.vote({pid}, tu_id, True/False, 'razón')",
                subject=f"[PROPUESTA] {title}",
                priority="high"
            )
        except ImportError:
            pass

        return pid

    def vote(self, proposal_id: int, voter: str, approve: bool,
             reason: str = "") -> bool:
        """Vota en una propuesta. True=a favor, False=en contra."""
        with self._lock:
            # Check proposal is open
            row = self._conn.execute(
                "SELECT status, author FROM proposals WHERE id = ?",
                (proposal_id,)
            ).fetchone()
            if not row or row[0] != "open":
                return False

            # Can't vote on own proposal
            if row[1] == voter:
                log.warning("%s tried to vote on own proposal #%d", voter, proposal_id)
                return False

            try:
                self._conn.execute(
                    "INSERT INTO votes (proposal_id, voter, vote, reason, voted_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (proposal_id, voter, 1 if approve else 0, reason, time.time())
                )
                self._conn.commit()
            except sqlite3.IntegrityError:
                # Already voted
                return False

        log.info("Vote on #%d by %s: %s", proposal_id, voter,
                 "approve" if approve else "reject")

        # Chronicle
        try:
            from core.colony_chronicle import get_chronicle
            get_chronicle().record(voter, "proposal_voted",
                                   f"#{proposal_id}: {'✓' if approve else '✗'} {reason}",
                                   metadata={"proposal_id": proposal_id, "approve": approve})
        except ImportError:
            pass

        return True

    def tally(self, proposal_id: int) -> Optional[str]:
        """Cuenta votos y resuelve la propuesta. Retorna 'passed' o 'rejected'."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM proposals WHERE id = ?",
                (proposal_id,)
            ).fetchone()
            if not row or row[0] != "open":
                return None

            votes = self._conn.execute(
                "SELECT vote FROM votes WHERE proposal_id = ?",
                (proposal_id,)
            ).fetchall()

            total = len(votes)
            if total < MIN_VOTERS:
                return None  # Not enough voters yet

            votes_for = sum(1 for v in votes if v[0] == 1)
            ratio = votes_for / total if total > 0 else 0

            status = "passed" if ratio > PASS_THRESHOLD else "rejected"

            self._conn.execute(
                "UPDATE proposals SET status = ?, resolved_at = ? WHERE id = ?",
                (status, time.time(), proposal_id)
            )
            self._conn.commit()

        log.info("Proposal #%d %s (%d/%d)", proposal_id, status, votes_for, total)

        # Auto-execute if passed
        if status == "passed":
            self._try_execute(proposal_id)

        return status

    def auto_tally_expired(self):
        """Revisa y talla propuestas que han superado el período de votación."""
        cutoff = time.time() - VOTING_PERIOD_SECS
        with self._lock:
            open_proposals = self._conn.execute(
                "SELECT id FROM proposals WHERE status = 'open' AND created_at < ?",
                (cutoff,)
            ).fetchall()

        for (pid,) in open_proposals:
            self.tally(pid)

    def register_handler(self, proposal_type: str, handler: Callable):
        """Registra un handler para ejecutar propuestas aprobadas."""
        self._execution_handlers[proposal_type] = handler

    def _try_execute(self, proposal_id: int):
        """Intenta ejecutar una propuesta aprobada."""
        with self._lock:
            row = self._conn.execute(
                "SELECT proposal_type, metadata FROM proposals WHERE id = ?",
                (proposal_id,)
            ).fetchone()

        if not row:
            return

        ptype, meta_str = row
        metadata = json.loads(meta_str or "{}")

        handler = self._execution_handlers.get(ptype)
        if handler:
            try:
                result = handler(proposal_id, metadata)
                with self._lock:
                    self._conn.execute(
                        "UPDATE proposals SET status = 'executed', execution_result = ? "
                        "WHERE id = ?",
                        (str(result), proposal_id)
                    )
                    self._conn.commit()
                log.info("Proposal #%d executed: %s", proposal_id, result)
            except Exception as e:
                log.error("Proposal #%d execution failed: %s", proposal_id, e)

    def mark_executed(self, proposal_id: int) -> None:
        """Marca una propuesta como ejecutada por el governor."""
        with self._lock:
            self._conn.execute(
                "UPDATE proposals SET status='executed', resolved_at=? WHERE id=?",
                (time.time(), proposal_id)
            )
            self._conn.commit()
        log.info("Proposal #%d marked executed by governor", proposal_id)

    def get_open(self) -> List[Proposal]:
        """Obtiene propuestas abiertas."""
        return self._query_proposals("open")

    def get_all(self, limit: int = 50) -> List[Proposal]:
        """Obtiene todas las propuestas."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created_at, author, proposal_type, title, description, "
                "status, metadata, resolved_at, execution_result FROM proposals "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [self._enrich_proposal(r) for r in rows]

    def get_proposal(self, proposal_id: int) -> Optional[Proposal]:
        """Obtiene una propuesta por ID."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, created_at, author, proposal_type, title, description, "
                "status, metadata, resolved_at, execution_result FROM proposals WHERE id = ?",
                (proposal_id,)
            ).fetchone()
        return self._enrich_proposal(row) if row else None

    def get_votes(self, proposal_id: int) -> List[Vote]:
        """Obtiene los votos de una propuesta."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT voter, vote, reason, voted_at FROM votes WHERE proposal_id = ?",
                (proposal_id,)
            ).fetchall()
        return [Vote(voter=r[0], vote=bool(r[1]), reason=r[2], voted_at=r[3])
                for r in rows]

    def _query_proposals(self, status: str) -> List[Proposal]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created_at, author, proposal_type, title, description, "
                "status, metadata, resolved_at, execution_result FROM proposals "
                "WHERE status = ? ORDER BY created_at DESC",
                (status,)
            ).fetchall()
        return [self._enrich_proposal(r) for r in rows]

    def _enrich_proposal(self, row) -> Proposal:
        pid = row[0]
        with self._lock:
            vote_counts = self._conn.execute(
                "SELECT vote, COUNT(*) FROM votes WHERE proposal_id = ? GROUP BY vote",
                (pid,)
            ).fetchall()
        vf = sum(c for v, c in vote_counts if v == 1)
        va = sum(c for v, c in vote_counts if v == 0)
        return Proposal(
            id=pid, created_at=row[1], author=row[2],
            proposal_type=row[3], title=row[4], description=row[5],
            status=row[6], metadata=json.loads(row[7] or "{}"),
            resolved_at=row[8], execution_result=row[9],
            votes_for=vf, votes_against=va,
        )

    def close(self):
        with self._lock:
            self._conn.close()


# Singleton
_instance: Optional[ColonyProposalSystem] = None

def get_proposal_system() -> ColonyProposalSystem:
    global _instance
    if _instance is None:
        _instance = ColonyProposalSystem()
    return _instance
