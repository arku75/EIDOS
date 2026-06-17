"""
core/ser_inbox.py — Buzón de mensajes de Colony para SER  [Fase 8]

Colony escribe aquí cuando necesita aprobación o tiene algo importante.
SER lee con:  eidos inbox
SER aprueba:  eidos inbox approve <id>
SER rechaza:  eidos inbox reject <id> "razón"

Tipos de mensaje:
    proposal_needs_approval — Colony necesita permiso de SER
    achievement             — Colony logró algo importante
    alert_critical          — algo falló y Colony no pudo repararlo
    weekly_summary          — resumen semanal de actividad
"""
from __future__ import annotations

import sqlite3
from core.db import get_conn
import time
import threading
import logging
from pathlib import Path
from typing import Optional, List, Dict

log = logging.getLogger("eidos.ser_inbox")

DB_PATH = Path.home() / ".eidos" / "ser_inbox.db"
_lock   = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL    NOT NULL,
    msg_type     TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    summary      TEXT    NOT NULL DEFAULT '',
    proposal_id  INTEGER,
    action_needed INTEGER DEFAULT 0,
    read         INTEGER DEFAULT 0,
    approved     INTEGER,          -- NULL=pendiente, 1=aprobado, 0=rechazado
    approved_at  REAL,
    rejection_reason TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_inbox_read ON inbox(read);
CREATE INDEX IF NOT EXISTS idx_inbox_ts   ON inbox(ts);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = get_conn(DB_PATH, timeout=5)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _ensure_table() -> None:
    with _conn() as c:
        c.executescript(_SCHEMA)


class SerInbox:
    """Buzón de Colony → SER."""

    def add(self, msg_type: str, title: str, summary: str = "",
            proposal_id: Optional[int] = None, action_needed: bool = False) -> int:
        """Añade un mensaje al inbox de SER. Devuelve el ID."""
        with _lock:
            with _conn() as c:
                cur = c.execute(
                    "INSERT INTO inbox (ts, msg_type, title, summary, proposal_id, action_needed) "
                    "VALUES (?,?,?,?,?,?)",
                    (time.time(), msg_type, title[:200], summary[:1000],
                     proposal_id, 1 if action_needed else 0)
                )
                mid = cur.lastrowid
        log.info("Inbox: [%s] %s (id=%d)", msg_type, title[:60], mid)
        return mid

    def list_unread(self) -> List[Dict]:
        """Devuelve todos los mensajes no leídos."""
        with _conn() as c:
            rows = c.execute(
                "SELECT * FROM inbox WHERE read=0 ORDER BY ts DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_all(self, limit: int = 20) -> List[Dict]:
        with _conn() as c:
            rows = c.execute(
                "SELECT * FROM inbox ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_read(self, msg_id: int) -> None:
        with _lock:
            with _conn() as c:
                c.execute("UPDATE inbox SET read=1 WHERE id=?", (msg_id,))

    def approve(self, msg_id: int) -> bool:
        """SER aprueba una propuesta."""
        with _lock:
            with _conn() as c:
                row = c.execute("SELECT * FROM inbox WHERE id=?", (msg_id,)).fetchone()
                if not row:
                    return False
                c.execute(
                    "UPDATE inbox SET approved=1, approved_at=?, read=1 WHERE id=?",
                    (time.time(), msg_id)
                )
            # Ejecutar la propuesta aprobada
            if row["proposal_id"]:
                self._execute_approved(row["proposal_id"])
        log.info("SER aprobó mensaje %d (propuesta %s)", msg_id, row["proposal_id"])
        return True

    def reject(self, msg_id: int, reason: str = "") -> bool:
        """SER rechaza una propuesta."""
        with _lock:
            with _conn() as c:
                row = c.execute("SELECT * FROM inbox WHERE id=?", (msg_id,)).fetchone()
                if not row:
                    return False
                c.execute(
                    "UPDATE inbox SET approved=0, approved_at=?, rejection_reason=?, read=1 WHERE id=?",
                    (time.time(), reason[:500], msg_id)
                )
        # Broadcastear el rechazo con la razón para que Colony aprenda
        try:
            from core.colony_broadcast import get_broadcast
            get_broadcast().broadcast(
                "colony_ser",
                f"SER rechazó '{row['title'][:60]}': {reason[:100]}",
                msg_type="learning"
            )
        except Exception:
            pass  # error no crítico, continuar
        log.info("SER rechazó mensaje %d: %s", msg_id, reason[:60])
        return True

    def pending_count(self) -> int:
        with _conn() as c:
            return c.execute(
                "SELECT COUNT(*) FROM inbox WHERE read=0 AND action_needed=1"
            ).fetchone()[0]

    def _execute_approved(self, proposal_id: int) -> None:
        """Ejecuta la propuesta aprobada por SER."""
        try:
            from core.colony_governor import get_governor
            from core.colony_proposals import get_proposal_system
            ps  = get_proposal_system()
            gov = get_governor()
            # [S123] FIX: el método es get_proposal (no .get) y _execute_proposal
            # espera el OBJETO proposal (no el id) → el approve de SER nunca
            # ejecutaba nada (AttributeError silenciado en log.debug).
            proposal = ps.get_proposal(proposal_id)
            if proposal:
                gov._execute_proposal(proposal, ps)
        except Exception as e:
            log.debug("execute_approved %d: %s", proposal_id, e)

    def format_for_display(self) -> str:
        """Formatea el inbox para mostrar en el CLI."""
        msgs = self.list_unread()
        if not msgs:
            return "  📭 Inbox vacío — Colony no necesita nada ahora."

        lines = [f"  📬 {len(msgs)} mensaje(s) de Colony:\n"]
        icons = {
            "proposal_needs_approval": "📋",
            "achievement":             "🏆",
            "alert_critical":          "🚨",
            "weekly_summary":          "📊",
        }
        for m in msgs:
            icon = icons.get(m["msg_type"], "💬")
            age  = _fmt_age(m["ts"])
            action = " [REQUIERE ACCIÓN]" if m["action_needed"] else ""
            lines.append(f"  {icon} [{m['id']}] {m['title']}{action}")
            lines.append(f"       {m['summary'][:100]}")
            lines.append(f"       hace {age}")
            lines.append("")
        return "\n".join(lines)


def _fmt_age(ts: float) -> str:
    secs = time.time() - ts
    if secs < 60:   return f"{int(secs)}s"
    if secs < 3600: return f"{int(secs/60)}min"
    if secs < 86400: return f"{int(secs/3600)}h"
    return f"{int(secs/86400)}d"


_instance: Optional[SerInbox] = None
_inst_lock = threading.Lock()


def get_ser_inbox() -> SerInbox:
    global _instance
    if _instance is None:
        with _inst_lock:
            if _instance is None:
                _ensure_table()
                _instance = SerInbox()
    return _instance
