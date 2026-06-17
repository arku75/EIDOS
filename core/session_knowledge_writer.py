#!/usr/bin/env python3
"""
core/session_knowledge_writer.py — Pipeline sesión Claude → Colony

Cada vez que hablo con SER, puedo escribir conocimiento estructurado
que Colony absorbe automáticamente en la próxima indexación.

Uso directo:
    from core.session_knowledge_writer import write_session
    write_session("Título", ["concepto 1", "concepto 2"], decisiones=[...])

Desde terminal:
    python3 core/session_knowledge_writer.py "título" "concepto1" "concepto2"
"""
from __future__ import annotations

import sys
import time
import logging
from pathlib import Path
from datetime import datetime
from core.db import get_conn

log = logging.getLogger("eidos.session_writer")

SESSIONS_DIR = Path.home() / ".eidos" / "sessions"
BRAIN_DB     = Path.home() / ".eidos" / "evolution_brain.db"


def write_session(
    title: str,
    concepts: list[str],
    decisions: list[str] | None = None,
    source: str = "claude_session",
) -> Path:
    """Escribe un archivo de sesión que docs_indexer absorberá.

    Returns:
        Path al archivo creado.
    """
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out = SESSIONS_DIR / f"{ts}_{title[:40].replace(' ', '_')}.md"

    lines = [
        f"# {title}",
        f"> Sesión: {datetime.now().strftime('%Y-%m-%d %H:%M')} — fuente: {source}",
        "",
        "## Conceptos aprendidos",
        "",
    ]
    for c in concepts:
        lines.append(f"- {c}")

    if decisions:
        lines += ["", "## Decisiones tomadas", ""]
        for d in decisions:
            lines.append(f"- {d}")

    out.write_text("\n".join(lines), encoding="utf-8")
    log.info("Sesión escrita: %s (%d conceptos)", out.name, len(concepts))

    # Intentar indexar inmediatamente si la DB existe
    if BRAIN_DB.exists():
        try:
            _quick_index(out, source)
        except Exception as e:
            log.debug("Quick index falló (se indexará en próxima corrida): %s", e)

    return out


def _quick_index(md_path: Path, source: str) -> int:
    """Indexa el archivo de sesión directamente en evolution_brain.db."""
    import sqlite3
    import hashlib

    conn = get_conn(BRAIN_DB, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    new_nodes = 0
    now = time.time()

    content = md_path.read_text(encoding="utf-8")
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("- ") or len(line) < 5:
            continue
        concept = line[2:].strip()
        if len(concept) < 10:
            continue
        node_id = hashlib.md5(concept.encode()).hexdigest()[:16]
        try:
            existing = conn.execute(
                "SELECT id FROM knowledge_nodes WHERE concept=?", (concept,)
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO knowledge_nodes "
                    "(id, concept, definition, source, confidence, created_at, last_used, usage_count) "
                    "VALUES (?,?,?,?,?,?,?,1)",
                    (node_id, concept[:120], concept[:500], source, 0.9, now, now),
                )
                new_nodes += 1
        except Exception:
            pass  # error no crítico, continuar
    conn.commit()
    pass  # S109: get_conn no necesita close()
    if new_nodes:
        log.info("Quick index: %d nodos nuevos desde sesión", new_nodes)
    return new_nodes


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 2:
        print("Uso: python3 session_knowledge_writer.py 'título' 'concepto1' 'concepto2' ...")
        sys.exit(1)
    title = args[0]
    concepts = args[1:]
    path = write_session(title, concepts)
    print(f"Escrito: {path}")
