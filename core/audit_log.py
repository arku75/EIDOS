"""
EIDOS core/audit_log.py — Auditoría Inmutable Ring 4
=====================================================
Historial inmodificable de todos los tool-calls ejecutados por EIDOS.

Implementación: append-only SQLite + hash encadenado (como blockchain simplificado).
  - Cada entrada contiene: timestamp, tool, args, result, risk, hash_prev + hash_self(SHA-256)
  - Intentar modificar un registro previo rompe la cadena de hashes → detectable
  - Soporta verificación de integridad completa con verify_chain()

Uso:
    from core.audit_log import AuditLog, log_tool_call
    log_tool_call("exec_shell", {"command": "ls /tmp"}, result="...", risk=2)
    AuditLog().verify_chain()  → True si la cadena está intacta
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional
from core.db import get_conn
from core.db import get_conn_ctx

DB_PATH = os.path.expanduser("~/.eidos/audit.db")


@dataclass
class AuditEntry:
    id: int
    timestamp: float
    tool: str
    args_json: str
    result_snippet: str
    risk: int
    channel: str          # "cli", "telegram", "discord", "autonomous", "api"
    hash_prev: str
    hash_self: str

    def compute_hash(self) -> str:
        """Recalcula el hash de esta entrada (para verificación)."""
        raw = (
            f"{self.id}|{self.timestamp:.6f}|{self.tool}|"
            f"{self.args_json}|{self.result_snippet}|"
            f"{self.risk}|{self.channel}|{self.hash_prev}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    @property
    def is_valid(self) -> bool:
        return self.hash_self == self.compute_hash()


class AuditLog:
    """
    Registro de auditoría inmutable basado en cadena de hashes.
    Append-only: una vez escrita una entrada, no puede modificarse sin
    romper la cadena, lo que detect_tampering() revelará.
    """

    GENESIS_HASH = "0" * 64  # hash previo del primer registro

    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with get_conn_ctx(self.db_path) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp     REAL NOT NULL,
                    tool          TEXT NOT NULL,
                    args_json     TEXT DEFAULT '{}',
                    result_snippet TEXT DEFAULT '',
                    risk          INTEGER DEFAULT 0,
                    channel       TEXT DEFAULT 'cli',
                    hash_prev     TEXT NOT NULL,
                    hash_self     TEXT NOT NULL
                )
            """)
            c.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_ts
                ON audit_log(timestamp)
            """)

    def _last_hash(self) -> str:
        with get_conn_ctx(self.db_path) as c:
            row = c.execute(
                "SELECT hash_self FROM audit_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else self.GENESIS_HASH  # pyre-ignore[arg-type]

    def log(
        self,
        tool: str,
        args: dict,
        result: str = "",
        risk: int = 0,
        channel: str = "cli",
    ) -> int:
        """
        Registra una entrada inmutable. Devuelve el ID asignado.
        """
        ts          = time.time()
        args_json   = json.dumps(args, ensure_ascii=False)[:500]  # pyre-ignore[arg-type]
        result_snip = result[:300] if result else ""  # pyre-ignore[arg-type]
        hash_prev   = self._last_hash()

        # Calcular hash_self antes de insertar (necesitamos el id, lo hacemos después)
        # Usamos rowid+1 estimado — si hay race condition, recalculamos
        with get_conn_ctx(self.db_path) as conn:
            cur = conn.cursor()
            # Insertar primero con hash_self provisional
            cur.execute(
                """INSERT INTO audit_log
                   (timestamp, tool, args_json, result_snippet, risk, channel, hash_prev, hash_self)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (ts, tool, args_json, result_snip, risk, channel, hash_prev, "PENDING")
            )
            row_id = cur.lastrowid  # cursor tiene lastrowid, no Connection

            # Calcular hash real con el id conocido
            entry = AuditEntry(
                id=row_id, timestamp=ts, tool=tool,
                args_json=args_json, result_snippet=result_snip,
                risk=risk, channel=channel,
                hash_prev=hash_prev, hash_self="",
            )
            hash_self = entry.compute_hash()

            # Actualizar con el hash real
            cur.execute(
                "UPDATE audit_log SET hash_self=? WHERE id=?",
                (hash_self, row_id)
            )
            conn.commit()

        return row_id

    def verify_chain(self) -> tuple[bool, list[int]]:
        """
        Verifica la integridad de toda la cadena de hashes.
        Retorna (True, []) si todo OK, o (False, [ids_corruptos]) si hay tampering.
        """
        with get_conn_ctx(self.db_path) as c:
            rows = c.execute(
                "SELECT id, timestamp, tool, args_json, result_snippet, risk, channel, hash_prev, hash_self FROM audit_log ORDER BY id ASC"
            ).fetchall()

        if not rows:
            return True, []

        corrupted: list[int] = []
        expected_prev = self.GENESIS_HASH

        for row in rows:
            entry = AuditEntry(*row)
            # Verificar hash_prev = hash del anterior
            if entry.hash_prev != expected_prev:
                corrupted.append(entry.id)
            # Verificar hash_self
            if not entry.is_valid:
                corrupted.append(entry.id)
            expected_prev = entry.hash_self

        return len(corrupted) == 0, corrupted

    def get_recent(self, n: int = 20, channel: Optional[str] = None) -> list[AuditEntry]:
        """Obtiene las N entradas más recientes."""
        with get_conn_ctx(self.db_path) as c:
            if channel:
                rows = c.execute(
                    "SELECT * FROM audit_log WHERE channel=? ORDER BY timestamp DESC LIMIT ?",
                    (channel, n)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?",
                    (n,)
                ).fetchall()
        return [AuditEntry(*r) for r in rows]

    def get_by_risk(self, min_risk: int = 7) -> list[AuditEntry]:
        """Obtiene entradas con riesgo >= min_risk (las más peligrosas)."""
        with get_conn_ctx(self.db_path) as c:
            rows = c.execute(
                "SELECT * FROM audit_log WHERE risk>=? ORDER BY timestamp DESC",
                (min_risk,)
            ).fetchall()
        return [AuditEntry(*r) for r in rows]

    def summary(self) -> dict:
        """Resumen estadístico del log."""
        with get_conn_ctx(self.db_path) as c:
            total = c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]  # pyre-ignore[arg-type]
            by_channel = dict(c.execute(
                "SELECT channel, COUNT(*) FROM audit_log GROUP BY channel"
            ).fetchall())
            high_risk  = c.execute(
                "SELECT COUNT(*) FROM audit_log WHERE risk >= 7"
            ).fetchone()[0]  # pyre-ignore[arg-type]
            tools_used = dict(c.execute(
                "SELECT tool, COUNT(*) FROM audit_log GROUP BY tool ORDER BY 2 DESC LIMIT 10"
            ).fetchall())

        ok, corrupted = self.verify_chain()
        return {
            "total_entries": total,
            "chain_intact": ok,
            "corrupted_ids": corrupted,
            "high_risk_calls": high_risk,
            "by_channel": by_channel,
            "top_tools": tools_used,
        }

    def print_recent(self, n: int = 10) -> None:
        """Imprime las últimas N entradas en formato legible."""
        from rich.table import Table
        from rich.console import Console
        from rich import print as rprint
        import datetime

        console = Console()
        entries = self.get_recent(n)

        t = Table(title=f"🔒 EIDOS Audit Log (últimas {n} entradas)", show_lines=True)
        t.add_column("ID",      style="dim",           width=5)
        t.add_column("Hora",    style="cyan",           width=10)
        t.add_column("Canal",   style="yellow",         width=10)
        t.add_column("Tool",    style="bright_green",   width=18)
        t.add_column("Riesgo",  style="red",            width=6)
        t.add_column("Result",  style="white",          width=30)
        t.add_column("Hash✓",   style="green",          width=6)

        for e in entries:
            hora   = datetime.datetime.fromtimestamp(e.timestamp).strftime("%H:%M:%S")
            riesgo = f"{'🔴' if e.risk >= 7 else '🟡' if e.risk >= 4 else '🟢'}{e.risk}"
            valid  = "✅" if e.is_valid else "❌"
            t.add_row(
                str(e.id), hora, e.channel, e.tool,
                riesgo, e.result_snippet[:30], valid  # pyre-ignore[arg-type]
            )

        console.print(t)


# ── Instancia global + helper ────────────────────────────────────────────────

_audit: Optional[AuditLog] = None

def get_audit() -> AuditLog:
    global _audit
    if _audit is None:
        _audit = AuditLog()
    return _audit

def log_tool_call(
    tool: str,
    args: dict,
    result: str = "",
    risk: int = 0,
    channel: str = "cli",
) -> int:
    """Helper global para registrar un tool-call. Llama desde tools.py, telegram.go (via bridge), etc."""
    return get_audit().log(tool, args, result, risk, channel)


if __name__ == "__main__":
    audit = AuditLog()
    # Test: registrar algunas entradas y verificar
    audit.log("exec_shell", {"command": "ls /tmp"}, result="tmp/ ...", risk=1, channel="cli")
    audit.log("read_file",  {"path": "~/.eidos/autonomous.db"}, risk=0, channel="cli")
    audit.log("exec_shell", {"command": "sudo apt install nmap"}, result="OK", risk=6, channel="telegram")

    ok, corrupted = audit.verify_chain()
    print(f"Cadena intacta: {ok}")
    print(f"Resumen: {audit.summary()}")
    audit.print_recent(5)
