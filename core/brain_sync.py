"""
core/brain_sync.py — Sincronización bidireccional de nodos entre Kali y Mac.

Arquitectura:
  Kali (admin) ←→ Mac (luka) vía SSH alias eidos-mac
  Ambos tienen evolution_brain.db independiente.
  Al sync: nodos nuevos de cada lado fluyen al otro sin duplicados.
  Tag de origen: source + ":kali_sync" o ":mac_sync" para trazabilidad.

knowledge_nodes schema:
  id, concept, definition, category, confidence, source,
  usage_count, last_used, created_at, agent_id, verified, character
"""
from __future__ import annotations

import json
import logging
import sqlite3
from core.db import get_conn
import subprocess
import time
from pathlib import Path

log = logging.getLogger("eidos.brain_sync")

BRAIN_DB   = Path.home() / ".eidos" / "evolution_brain.db"
SYNC_STATE = Path.home() / ".eidos" / "brain_sync_state.db"
MAC_HOST   = "eidos-mac"
MAC_BRAIN  = "/Users/luka/.eidos/evolution_brain.db"
MAC_PYTHON = "/usr/local/bin/python3"

_COLS = "concept, definition, category, confidence, source, last_used, created_at"


def _init_state_db() -> sqlite3.Connection:
    con = get_conn(SYNC_STATE)
    con.execute("""
        CREATE TABLE IF NOT EXISTS sync_log (
            direction  TEXT PRIMARY KEY,
            last_sync  REAL DEFAULT 0,
            nodes_sent INTEGER DEFAULT 0
        )
    """)
    con.execute("INSERT OR IGNORE INTO sync_log(direction) VALUES('to_mac')")
    con.execute("INSERT OR IGNORE INTO sync_log(direction) VALUES('from_mac')")
    con.commit()
    return con


def _get_last_sync(con: sqlite3.Connection, direction: str) -> float:
    row = con.execute("SELECT last_sync FROM sync_log WHERE direction=?", (direction,)).fetchone()
    return row[0] if row else 0.0


def _set_last_sync(con: sqlite3.Connection, direction: str, ts: float, sent: int) -> None:
    con.execute(
        "UPDATE sync_log SET last_sync=?, nodes_sent=? WHERE direction=?",
        (ts, sent, direction)
    )
    con.commit()


def export_new_nodes(since_ts: float) -> list[tuple]:
    """Exporta nodos locales creados/actualizados después de since_ts."""
    con = get_conn(BRAIN_DB)
    rows = con.execute(f"""
        SELECT {_COLS}
        FROM knowledge_nodes
        WHERE last_used > ?
          AND source NOT LIKE '%_sync'
        ORDER BY last_used DESC
        LIMIT 2000
    """, (since_ts,)).fetchall()
    # get_conn usa row_factory=Row; convertir a tuplas para json/repr (sync Mac)
    return [tuple(r) for r in rows]


def import_nodes(nodes: list[tuple], tag_suffix: str) -> int:
    """Importa nodos al brain local. Retorna nodos efectivamente añadidos."""
    if not nodes:
        return 0
    con = get_conn(BRAIN_DB)
    added = 0
    for concept, definition, category, confidence, source, last_used, created_at in nodes:
        tagged = f"{source}{tag_suffix}"
        if not con.execute("SELECT 1 FROM knowledge_nodes WHERE concept=?", (concept,)).fetchone():
            con.execute(f"""
                INSERT INTO knowledge_nodes ({_COLS})
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (concept, definition, category or "general", confidence or 0.5,
                  tagged, last_used, created_at))
            added += 1
    con.commit()
    return added


def _mac_reachable() -> bool:
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes",
             "-o", "LogLevel=ERROR", MAC_HOST, "echo ok"],
            capture_output=True, text=True, timeout=6
        )
        return r.returncode == 0 and "ok" in r.stdout
    except Exception:
        return False


def _export_mac_nodes(since_ts: float) -> list[tuple]:
    """Trae nodos nuevos del Mac vía SSH."""
    script = (
        "import sqlite3,json\n"
        f"con=sqlite3.connect('{MAC_BRAIN}')\n"
        f"rows=con.execute('''SELECT {_COLS} FROM knowledge_nodes "
        f"WHERE last_used>{since_ts} AND source NOT LIKE '''||\"'%_sync'\"||''' "
        "ORDER BY last_used DESC LIMIT 2000''').fetchall()\n"
        "con.close()\nprint(json.dumps(rows))\n"
    )
    # Escribir en archivo temporal para evitar problemas de quoting
    remote_script = "/tmp/eidos_brain_export.py"
    local_script_content = f"""import sqlite3, json
con = sqlite3.connect('{MAC_BRAIN}')
rows = con.execute(
    "SELECT {_COLS} FROM knowledge_nodes "
    "WHERE last_used > ? AND source NOT LIKE '%_sync' "
    "ORDER BY last_used DESC LIMIT 2000",
    ({since_ts},)
).fetchall()
print(json.dumps(rows))
"""
    # Enviar el script al Mac
    write_r = subprocess.run(
        ["ssh", "-o", "LogLevel=ERROR", MAC_HOST,
         f"cat > {remote_script}"],
        input=local_script_content, text=True, capture_output=True, timeout=10
    )
    if write_r.returncode != 0:
        return []

    r = subprocess.run(
        ["ssh", "-o", "LogLevel=ERROR", MAC_HOST, MAC_PYTHON, remote_script],
        capture_output=True, text=True, timeout=30
    )
    if r.returncode != 0 or not r.stdout.strip():
        return []
    try:
        return [tuple(row) for row in json.loads(r.stdout.strip())]
    except Exception:
        return []


def _import_mac_nodes(nodes: list[tuple]) -> int:
    """Envía nodos de Kali al Mac e importa allí."""
    if not nodes:
        return 0

    import_script = f"""import sqlite3, json
nodes = {repr(nodes)}
con = sqlite3.connect('{MAC_BRAIN}')
added = 0
for concept, definition, category, confidence, source, last_used, created_at in nodes:
    tagged = source + ':kali_sync'
    if not con.execute('SELECT 1 FROM knowledge_nodes WHERE concept=?', (concept,)).fetchone():
        con.execute(
            'INSERT INTO knowledge_nodes ({_COLS}) VALUES (?,?,?,?,?,?,?)',
            (concept, definition, category or 'general', confidence or 0.5,
             tagged, last_used, created_at)
        )
        added += 1
con.commit()
print(added)
"""
    remote_script = "/tmp/eidos_brain_import.py"
    write_r = subprocess.run(
        ["ssh", "-o", "LogLevel=ERROR", MAC_HOST, f"cat > {remote_script}"],
        input=import_script, text=True, capture_output=True, timeout=15
    )
    if write_r.returncode != 0:
        return 0

    r = subprocess.run(
        ["ssh", "-o", "LogLevel=ERROR", MAC_HOST, MAC_PYTHON, remote_script],
        capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        return 0
    try:
        return int(r.stdout.strip())
    except Exception:
        return 0


def sync(verbose: bool = True) -> dict:
    """Sync bidireccional Kali ↔ Mac. Retorna resumen."""
    result = {"kali_to_mac": 0, "mac_to_kali": 0, "error": None}

    if not _mac_reachable():
        result["error"] = "Mac no alcanzable (tunnel eidos-mac)"
        if verbose:
            print(f"  ⚠  {result['error']}")
        return result

    state = _init_state_db()
    now = time.time()

    # 1. Kali → Mac
    last_sent = _get_last_sync(state, "to_mac")
    local_new = export_new_nodes(last_sent)
    if local_new:
        sent = _import_mac_nodes(local_new)
        result["kali_to_mac"] = sent
        _set_last_sync(state, "to_mac", now, sent)
        if verbose:
            print(f"  ✅ Kali → Mac: {sent} nodos nuevos ({len(local_new)} candidatos)")
    else:
        if verbose:
            print("  ℹ  Kali → Mac: sin nodos nuevos desde último sync")

    # 2. Mac → Kali
    last_recv = _get_last_sync(state, "from_mac")
    mac_new = _export_mac_nodes(last_recv)
    if mac_new:
        recv = import_nodes(mac_new, ":mac_sync")
        result["mac_to_kali"] = recv
        _set_last_sync(state, "from_mac", now, recv)
        if verbose:
            print(f"  ✅ Mac → Kali: {recv} nodos nuevos ({len(mac_new)} candidatos)")
    else:
        if verbose:
            print("  ℹ  Mac → Kali: sin nodos nuevos desde último sync")

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sync(verbose=True)
