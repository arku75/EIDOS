"""
core/colony_broadcast.py — Canal de mensajes entre personajes de Colony

Los personajes se comunican entre sí sin necesidad de que SER intervenga.
Cada personaje puede publicar lo que aprende y leer lo que aprendieron los demás.

Uso:
    from core.colony_broadcast import get_broadcast
    bc = get_broadcast()

    # Un personaje publica lo que aprendió
    bc.broadcast("colony_coder", "json es un módulo nativo de Python 3.11", "learning")

    # Otro personaje lee los mensajes recientes
    msgs = bc.read_new("colony_lumen", since_seconds=120)
    for m in msgs:
        print(m["from_char"], ":", m["message"])

    # Pulso de Colony: resumen de lo que está pasando
    pulse = bc.get_colony_pulse()
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
import threading
import logging
from pathlib import Path
from typing import List, Dict, Optional
from core.db import get_conn

log = logging.getLogger("eidos.colony_broadcast")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
_lock    = threading.Lock()


def _ensure_table() -> None:
    """Crea la tabla colony_broadcasts si no existe."""
    try:
        conn = get_conn(BRAIN_DB, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS colony_broadcasts (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                msg_id    TEXT UNIQUE,
                from_char TEXT NOT NULL,
                message   TEXT NOT NULL,
                msg_type  TEXT DEFAULT 'learning',
                ts        REAL NOT NULL,
                read_by   TEXT DEFAULT ''
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cb_ts    ON colony_broadcasts(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cb_char  ON colony_broadcasts(from_char)")
        conn.commit()
        pass  # S109: get_conn no necesita close()
    except Exception as e:
        log.debug("ensure_table: %s", e)


class ColonyBroadcast:
    """Canal de mensajes entre personajes de Colony."""

    def broadcast(self, from_char: str, message: str,
                  msg_type: str = "learning") -> bool:
        """
        Publica un mensaje de un personaje para toda Colony.
        msg_type: 'learning' | 'experiment' | 'synthesis' | 'alert'
        """
        if not message or not message.strip():
            return False
        try:
            with _lock:
                now    = time.time()
                msg_id = hashlib.md5(f"{from_char}:{message[:80]}:{now}".encode()).hexdigest()[:16]
                conn   = get_conn(BRAIN_DB, timeout=5)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    "INSERT OR IGNORE INTO colony_broadcasts "
                    "(msg_id, from_char, message, msg_type, ts) VALUES (?,?,?,?,?)",
                    (msg_id, from_char, message[:500], msg_type, now)
                )
                conn.commit()
                pass  # S109: get_conn no necesita close()
                log.debug("[%s] broadcast: %s", from_char, message[:60])
                return True
        except Exception as e:
            log.debug("broadcast falló: %s", e)
            return False

    def read_new(self, character: str, since_seconds: int = 300) -> List[Dict]:
        """
        Lee mensajes recientes de OTROS personajes (no del propio).
        Devuelve lista de dicts: {from_char, message, msg_type, ts}
        """
        try:
            since = time.time() - since_seconds
            conn  = get_conn(BRAIN_DB, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            rows  = conn.execute(
                "SELECT from_char, message, msg_type, ts FROM colony_broadcasts "
                "WHERE from_char != ? AND ts > ? ORDER BY ts DESC LIMIT 10",
                (character, since)
            ).fetchall()
            pass  # S109: get_conn no necesita close()
            return [{"from_char": r[0], "message": r[1],
                     "msg_type": r[2], "ts": r[3]} for r in rows]
        except Exception as e:
            log.debug("read_new falló: %s", e)
            return []

    def get_colony_pulse(self, last_minutes: int = 10) -> Dict:
        """
        Resumen del estado actual de Colony:
        - Cuántos mensajes por personaje
        - Último mensaje de cada uno
        - Total de actividad
        """
        try:
            since = time.time() - (last_minutes * 60)
            conn  = get_conn(BRAIN_DB, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            rows  = conn.execute(
                "SELECT from_char, COUNT(*) as cnt, MAX(ts) as last_ts "
                "FROM colony_broadcasts WHERE ts > ? GROUP BY from_char",
                (since,)
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) FROM colony_broadcasts WHERE ts > ?", (since,)
            ).fetchone()[0]
            last_msgs = conn.execute(
                "SELECT from_char, message FROM colony_broadcasts "
                "WHERE ts > ? ORDER BY ts DESC LIMIT 5",
                (since,)
            ).fetchall()
            pass  # S109: get_conn no necesita close()
            return {
                "total_messages": total,
                "active_minutes": last_minutes,
                "per_character": {r[0]: {"count": r[1], "last_ts": r[2]} for r in rows},
                "last_5": [{"from": r[0], "msg": r[1][:80]} for r in last_msgs],
            }
        except Exception as e:
            log.debug("get_colony_pulse falló: %s", e)
            return {"total_messages": 0, "active_minutes": last_minutes,
                    "per_character": {}, "last_5": []}

    def get_peer_knowledge(self, character: str, limit: int = 5) -> str:
        """
        Devuelve un resumen de lo que los otros personajes han aprendido,
        formateado para inyectar en el prompt del personaje.
        """
        msgs = self.read_new(character, since_seconds=600)
        if not msgs:
            return ""
        lines = []
        for m in msgs[:limit]:
            name = m["from_char"].replace("colony_", "").upper()
            lines.append(f"  [{name}] {m['message'][:100]}")
        return "Mis compañeros aprendieron:\n" + "\n".join(lines)


# Singleton
_instance: Optional[ColonyBroadcast] = None
_inst_lock = threading.Lock()


def get_broadcast() -> ColonyBroadcast:
    global _instance
    if _instance is None:
        with _inst_lock:
            if _instance is None:
                _ensure_table()
                _instance = ColonyBroadcast()
    return _instance
