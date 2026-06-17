"""
EIDOS Episodic Memory — registra cada interacción (talk/screen/action) y
permite recordar episodios similares para enriquecer respuestas futuras.

Sin embeddings, sin LLM: SQLite full-text + recency + outcome scoring.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from core.db import get_conn
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("eidos.episodic")

DB_PATH = Path.home() / ".eidos" / "episodic.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_LOCK = threading.RLock()
_SINGLETON: Optional["EpisodicMemory"] = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    actor TEXT NOT NULL,
    input_text TEXT,
    output_text TEXT,
    context_json TEXT,
    outcome TEXT,
    feedback_score REAL DEFAULT 0.0,
    tags TEXT
);
CREATE INDEX IF NOT EXISTS idx_episodes_ts ON episodes(ts DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_type ON episodes(type);
CREATE INDEX IF NOT EXISTS idx_episodes_actor ON episodes(actor);

CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    input_text, output_text, tags,
    content='episodes', content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
    INSERT INTO episodes_fts(rowid, input_text, output_text, tags)
    VALUES (new.id, new.input_text, new.output_text, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, input_text, output_text, tags)
    VALUES('delete', old.id, old.input_text, old.output_text, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS episodes_au AFTER UPDATE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, input_text, output_text, tags)
    VALUES('delete', old.id, old.input_text, old.output_text, old.tags);
    INSERT INTO episodes_fts(rowid, input_text, output_text, tags)
    VALUES (new.id, new.input_text, new.output_text, new.tags);
END;

CREATE TABLE IF NOT EXISTS patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_key TEXT UNIQUE NOT NULL,
    description TEXT,
    occurrences INTEGER DEFAULT 1,
    confidence REAL DEFAULT 0.3,
    last_seen REAL,
    created_at REAL
);
"""


class EpisodicMemory:
    """Memoria episódica persistente. Thread-safe."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = str(db_path)
        self._ensure_schema()

    def _conn(self):
        c = get_conn(self.db_path, timeout=10, check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=10000")
        return c

    def _ensure_schema(self):
        with _LOCK, self._conn() as c:
            c.executescript(_SCHEMA)
            c.commit()

    def record(self, type: str, actor: str, input_text: str = "",
               output_text: str = "", context: Optional[Dict[str, Any]] = None,
               outcome: str = "", feedback_score: float = 0.0,
               tags: Optional[List[str]] = None) -> int:
        """Registra un episodio. Devuelve su ID."""
        try:
            with _LOCK, self._conn() as c:
                cur = c.execute(
                    "INSERT INTO episodes "
                    "(ts, type, actor, input_text, output_text, context_json, "
                    "outcome, feedback_score, tags) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (time.time(), type, actor,
                     (input_text or "")[:8000],
                     (output_text or "")[:8000],
                     json.dumps(context or {}, ensure_ascii=False)[:4000],
                     outcome[:200], float(feedback_score),
                     ",".join(tags or [])[:500]),
                )
                c.commit()
                return cur.lastrowid
        except Exception as e:
            log.error("record episodio falló: %s", e)
            return -1

    def recall_similar(self, query: str, k: int = 5,
                       hours_window: int = 24 * 30) -> List[Dict[str, Any]]:
        """Recupera episodios similares por FTS5 + recency.
        hours_window: solo episodios de las últimas N horas (default 30 días).
        """
        if not query or len(query.strip()) < 3:
            return []
        cutoff = time.time() - hours_window * 3600
        # Sanitizar query para FTS5: solo palabras alfanuméricas
        import re as _re
        terms = _re.findall(r"[a-zA-Záéíóúñ0-9]{3,}", query.lower())
        if not terms:
            return []
        fts_query = " OR ".join(terms[:6])
        try:
            with _LOCK, self._conn() as c:
                c.row_factory = sqlite3.Row
                rows = c.execute(
                    "SELECT e.*, "
                    "       bm25(episodes_fts) AS rank "
                    "FROM episodes_fts "
                    "JOIN episodes e ON episodes_fts.rowid = e.id "
                    "WHERE episodes_fts MATCH ? "
                    "  AND e.ts >= ? "
                    "ORDER BY rank LIMIT ?",
                    (fts_query, cutoff, k),
                ).fetchall()
                results = []
                for r in rows:
                    d = dict(r)
                    # Score combinado: BM25 (mejor=menor) invertido + recency boost
                    age_hours = (time.time() - d["ts"]) / 3600
                    recency = max(0.0, 1.0 - age_hours / (hours_window or 1))
                    bm25_score = d.get("rank", 0) or 0
                    d["similarity"] = max(0, -bm25_score) + recency * 0.3 \
                                      + d.get("feedback_score", 0) * 0.2
                    try:
                        d["context"] = json.loads(d.get("context_json") or "{}")
                    except Exception:
                        d["context"] = {}
                    d.pop("context_json", None)
                    results.append(d)
                return sorted(results, key=lambda x: -x["similarity"])
        except Exception as e:
            log.debug("recall_similar falló: %s", e)
            return []

    def stats(self) -> Dict[str, Any]:
        with _LOCK, self._conn() as c:
            total = c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            by_type = dict(c.execute(
                "SELECT type, COUNT(*) FROM episodes GROUP BY type"
            ).fetchall())
            by_actor = dict(c.execute(
                "SELECT actor, COUNT(*) FROM episodes GROUP BY actor"
            ).fetchall())
            patterns = c.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
            return {
                "total_episodes": total,
                "by_type": by_type,
                "by_actor": by_actor,
                "patterns_learned": patterns,
            }

    def learn_patterns(self, min_repetitions: int = 3,
                       lookback_days: int = 7) -> int:
        """Detecta patrones repetidos (mismo input → mismo output) y los promueve.
        Retorna cuántos patrones nuevos/actualizados."""
        cutoff = time.time() - lookback_days * 86400
        promoted = 0
        try:
            with _LOCK, self._conn() as c:
                rows = c.execute(
                    "SELECT input_text, output_text, COUNT(*) AS n "
                    "FROM episodes "
                    "WHERE ts >= ? AND type = 'talk' AND input_text != '' "
                    "GROUP BY substr(input_text,1,80), substr(output_text,1,80) "
                    "HAVING n >= ?",
                    (cutoff, min_repetitions),
                ).fetchall()
                for input_text, output_text, n in rows:
                    key = f"talk:{(input_text or '')[:60]}"
                    conf = min(1.0, 0.3 + 0.1 * n)
                    existing = c.execute(
                        "SELECT id, occurrences FROM patterns WHERE pattern_key=?",
                        (key,),
                    ).fetchone()
                    desc = (f"Cuando SER pregunta «{(input_text or '')[:60]}» "
                            f"→ respuesta tipo «{(output_text or '')[:60]}»")
                    if existing:
                        c.execute(
                            "UPDATE patterns SET occurrences=?, confidence=?, "
                            "last_seen=? WHERE id=?",
                            (existing[1] + n, conf, time.time(), existing[0]),
                        )
                    else:
                        c.execute(
                            "INSERT INTO patterns "
                            "(pattern_key, description, occurrences, confidence, "
                            "last_seen, created_at) VALUES (?,?,?,?,?,?)",
                            (key, desc, n, conf, time.time(), time.time()),
                        )
                    promoted += 1
                c.commit()
        except Exception as e:
            log.error("learn_patterns falló: %s", e)
        return promoted

    def patterns(self, limit: int = 20) -> List[Dict[str, Any]]:
        with _LOCK, self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT * FROM patterns ORDER BY confidence DESC, "
                "occurrences DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def prune(self, keep_days: int = 90) -> int:
        """Elimina episodios antiguos con feedback_score <= 0."""
        cutoff = time.time() - keep_days * 86400
        with _LOCK, self._conn() as c:
            cur = c.execute(
                "DELETE FROM episodes WHERE ts < ? AND feedback_score <= 0",
                (cutoff,),
            )
            c.commit()
            return cur.rowcount


def get_episodic() -> EpisodicMemory:
    global _SINGLETON
    if _SINGLETON is None:
        with _LOCK:
            if _SINGLETON is None:
                _SINGLETON = EpisodicMemory()
                log.info("EpisodicMemory inicializada en %s", DB_PATH)
    return _SINGLETON


# ─── Helpers de conveniencia ────────────────────────────────────────────────

def record_talk(input_text: str, output_text: str,
                context: Optional[Dict[str, Any]] = None) -> int:
    return get_episodic().record(
        type="talk", actor="ser", input_text=input_text,
        output_text=output_text, context=context,
    )


def record_action(action_type: str, target: str = "",
                  context: Optional[Dict[str, Any]] = None,
                  outcome: str = "") -> int:
    return get_episodic().record(
        type="action", actor="eidos_autonomous",
        input_text=action_type, output_text=target,
        context=context, outcome=outcome,
    )


def record_screen_observation(text: str,
                              context: Optional[Dict[str, Any]] = None) -> int:
    return get_episodic().record(
        type="screen_observation", actor="eidos_autonomous",
        input_text=text[:1000], context=context,
    )


if __name__ == "__main__":
    em = get_episodic()
    print("Stats:", em.stats())
    # Self-test
    eid = em.record(type="talk", actor="ser",
                    input_text="qué es docker",
                    output_text="contenedor de aplicaciones",
                    context={"agents": ["test"]})
    print(f"Recorded id={eid}")
    sims = em.recall_similar("docker", k=3)
    print(f"Similar: {len(sims)} encontrados")
    for s in sims:
        print(f"  · [{s['type']}|{s['actor']}] sim={s['similarity']:.2f} "
              f"in='{s['input_text'][:40]}'")
