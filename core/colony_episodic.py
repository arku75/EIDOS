"""
core/colony_episodic.py — Memoria episódica de EIDOS

Los humanos recuerdan CUÁNDO aprendieron algo y EN QUÉ CONTEXTO.
Este módulo registra cada episodio significativo:
  - Qué estudió, quién lo pidió, cuándo, con qué resultado
  - Permite a Colony decir "el 14 de mayo SER me pidió estudiar n8n"
  - Base para iniciativa propia: "antes de que preguntes, aprendí que..."

Uso:
    from core.colony_episodic import record_episode, get_recent_episodes
    record_episode("SER", "study_url", "https://github.com/n8n-io/n8n",
                   outcome="success", nodes_added=18, summary="n8n workflows...")
"""
from __future__ import annotations

import hashlib
import sqlite3
from core.db import get_conn
import time
from pathlib import Path
from typing import Dict, List, Optional

EPISODIC_DB = Path.home() / ".eidos" / "episodic_memory.db"

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS episodes (
    id           TEXT PRIMARY KEY,
    timestamp    REAL NOT NULL,
    actor        TEXT NOT NULL,       -- "SER", "Colony", "curiosity", "libre"
    action       TEXT NOT NULL,       -- "study_url", "study_topic", "practice", "shell"
    subject      TEXT NOT NULL,       -- URL o tema estudiado
    outcome      TEXT DEFAULT 'ok',   -- "success", "partial", "failed"
    nodes_added  INTEGER DEFAULT 0,
    summary      TEXT DEFAULT '',
    practice_cmd TEXT DEFAULT '',
    practice_ok  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ep_ts     ON episodes(timestamp DESC);
CREATE INDEX IF NOT EXISTS ep_actor  ON episodes(actor);
CREATE INDEX IF NOT EXISTS ep_subj   ON episodes(subject);
"""


def _conn() -> sqlite3.Connection:
    EPISODIC_DB.parent.mkdir(parents=True, exist_ok=True)
    c = get_conn(EPISODIC_DB, timeout=10)
    c.executescript(_SCHEMA)
    return c


def record_episode(
    actor: str,
    action: str,
    subject: str,
    outcome: str = "success",
    nodes_added: int = 0,
    summary: str = "",
    practice_cmd: str = "",
    practice_ok: bool = False,
) -> str:
    """Registra un episodio. Devuelve el ID del episodio creado."""
    ep_id = hashlib.md5(
        f"{actor}:{action}:{subject}:{time.time()}".encode()
    ).hexdigest()[:16]
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO episodes "
        "(id,timestamp,actor,action,subject,outcome,nodes_added,"
        " summary,practice_cmd,practice_ok) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            ep_id, time.time(),
            actor[:40], action[:40], subject[:300],
            outcome, nodes_added,
            summary[:1500], practice_cmd[:300], int(practice_ok),
        ),
    )
    c.commit()
    c.close()
    return ep_id


def get_recent_episodes(limit: int = 10) -> List[Dict]:
    """Devuelve los N episodios más recientes."""
    try:
        c = _conn()
        rows = c.execute(
            "SELECT * FROM episodes ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        c.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_episodes_about(topic: str, limit: int = 5) -> List[Dict]:
    """Busca episodios relacionados con un tema."""
    try:
        c = _conn()
        pat = f"%{topic[:50]}%"
        rows = c.execute(
            "SELECT * FROM episodes "
            "WHERE subject LIKE ? OR summary LIKE ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (pat, pat, limit),
        ).fetchall()
        c.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def format_episode(ep: Dict) -> str:
    """Formatea un episodio en una línea legible."""
    # [S123] timestamp puede venir como epoch (int/float) o como str ISO
    # ("2026-06-10T18:00:00") según quién escribió el episodio → aceptar ambos.
    _raw_ts = ep.get("timestamp", 0)
    if isinstance(_raw_ts, str):
        try:
            _raw_ts = float(_raw_ts)
        except ValueError:
            try:
                from datetime import datetime
                _raw_ts = datetime.fromisoformat(_raw_ts).timestamp()
            except Exception:
                _raw_ts = 0
    ts  = time.strftime("%Y-%m-%d %H:%M", time.localtime(_raw_ts))
    # [S123] Esquema real de episodic_memory: episode_type/content/source_module/
    # importance (los campos actor/action/subject son del esquema antiguo → fallback).
    if "content" in ep or "episode_type" in ep:
        return (
            f"[{ts}] {ep.get('source_module','?')} → {ep.get('episode_type','?')}: "
            f"'{str(ep.get('content','?'))[:80]}' "
            f"(importancia {ep.get('importance','?')}, tono {ep.get('emotional_tone','?')})"
        )
    return (
        f"[{ts}] {ep.get('actor','?')} → {ep.get('action','?')}: "
        f"'{ep.get('subject','?')[:60]}' "
        f"({ep.get('outcome','?')}, +{ep.get('nodes_added',0)} nodos)"
    )


def episodes_summary_for_colony(topic: str = "", limit: int = 5) -> str:
    """Texto compacto con episodios recientes, para incluir en el contexto de Colony."""
    eps = get_episodes_about(topic, limit) if topic else get_recent_episodes(limit)
    if not eps:
        return ""
    lines = ["Episodios recientes de aprendizaje:"]
    for ep in eps:
        lines.append(f"  • {format_episode(ep)}")
    return "\n".join(lines)
