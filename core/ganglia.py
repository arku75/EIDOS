"""
EIDOS core/ganglia.py — Colony-Inspired Ganglia Stack
======================================================
Herencia de capacidades entre agentes, inspirada en ClawColony Ganglia Stack.

Un "ganglion" es una estrategia/capacidad empaquetada que puede ser:
  - Creada por un agente
  - Validada y promovida por uso
  - Heredada (aprendida) por otros agentes
  - Remunerada con royalties al autor (via TokenEconomy)

Lifecycle:
  nascent → validated → active → canonical → legacy → archived

Uso:
    from core.ganglia import get_ganglia_manager
    gm = get_ganglia_manager()
    gm.register_ganglion("fast_scan", author="scanner",
                         description="Nmap quick scan strategy",
                         code_snippet="nmap -sV -T4 {target}")
    gm.learn("coder", "fast_scan")
    results = gm.find_ganglia("scan")
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
from core.db import get_conn
from core.db import get_conn_ctx

log = logging.getLogger("eidos.ganglia")

DB_PATH = os.path.expanduser("~/.eidos/ganglia.db")

# Intentar importar TokenEconomy para royalties
try:
    from core.token_economy import get_economy
    HAS_ECONOMY = True
except ImportError:
    HAS_ECONOMY = False

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

ROYALTY_AMOUNT = 2.0              # tokens por uso de ganglion ajeno
AUTO_ARCHIVE_THRESHOLD = 1.0      # rating mínimo para sobrevivir
AUTO_ARCHIVE_MIN_USES = 10        # usos mínimos antes de auto-archive
PROMOTION_THRESHOLDS = {
    "nascent": {"min_uses": 3, "min_rating": 2.0},     # → validated
    "validated": {"min_uses": 10, "min_rating": 3.0},   # → active
    "active": {"min_uses": 50, "min_rating": 4.0},      # → canonical
}


# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

class GanglionStatus(str, Enum):
    NASCENT = "nascent"
    VALIDATED = "validated"
    ACTIVE = "active"
    CANONICAL = "canonical"
    LEGACY = "legacy"
    ARCHIVED = "archived"


@dataclass
class Ganglion:
    ganglion_id: str
    name: str
    author_agent: str
    description: str
    code_snippet: str
    version: int = 1
    status: GanglionStatus = GanglionStatus.NASCENT
    rating: float = 0.0
    rating_count: int = 0
    usage_count: int = 0
    success_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        if self.usage_count == 0:
            return 0.0
        return self.success_count / self.usage_count


# ══════════════════════════════════════════════════════════════════════════════
#  GANGLIA MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class GangliaManager:
    """
    Gestiona la herencia de capacidades entre agentes.

    Cada ganglion es una estrategia empaquetada que puede ser aprendida,
    valorada, y remunerada.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()
        log.info("🧠 [Ganglia] Inicializado — db=%s", db_path)

    def _init_db(self) -> None:
        """Crea las tablas SQLite si no existen."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with get_conn_ctx(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS ganglia (
                    ganglion_id TEXT PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    author_agent TEXT NOT NULL,
                    description TEXT,
                    code_snippet TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'nascent',
                    rating REAL NOT NULL DEFAULT 0.0,
                    rating_count INTEGER NOT NULL DEFAULT 0,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    tags TEXT DEFAULT '[]',
                    metadata TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS learnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    ganglion_id TEXT NOT NULL,
                    learned_at REAL NOT NULL,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    last_used REAL,
                    UNIQUE(agent_id, ganglion_id)
                );

                CREATE TABLE IF NOT EXISTS ratings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ganglion_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    rating REAL NOT NULL,
                    comment TEXT,
                    timestamp REAL NOT NULL,
                    UNIQUE(ganglion_id, agent_id)
                );

                CREATE INDEX IF NOT EXISTS idx_ganglia_status ON ganglia(status);
                CREATE INDEX IF NOT EXISTS idx_ganglia_author ON ganglia(author_agent);
                CREATE INDEX IF NOT EXISTS idx_learnings_agent ON learnings(agent_id);
            """)

    def _get_ganglion(self, conn: sqlite3.Connection, name: str) -> Optional[Ganglion]:
        """Carga un ganglion por nombre."""
        row = conn.execute(
            "SELECT * FROM ganglia WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_ganglion(row)

    def _get_ganglion_by_id(self, conn: sqlite3.Connection, gid: str) -> Optional[Ganglion]:
        """Carga un ganglion por ID."""
        row = conn.execute(
            "SELECT * FROM ganglia WHERE ganglion_id = ?", (gid,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_ganglion(row)

    def _row_to_ganglion(self, row) -> Ganglion:
        """Convierte una row SQLite a Ganglion."""
        return Ganglion(
            ganglion_id=row[0], name=row[1], author_agent=row[2],
            description=row[3], code_snippet=row[4], version=row[5],
            status=GanglionStatus(row[6]), rating=row[7], rating_count=row[8],
            usage_count=row[9], success_count=row[10],
            created_at=row[11], updated_at=row[12],
            tags=json.loads(row[13]) if row[13] else [],
            metadata=json.loads(row[14]) if row[14] else {},
        )

    def _save_ganglion(self, conn: sqlite3.Connection, g: Ganglion) -> None:
        """Persiste un ganglion."""
        conn.execute(
            """INSERT OR REPLACE INTO ganglia
               (ganglion_id, name, author_agent, description, code_snippet,
                version, status, rating, rating_count, usage_count, success_count,
                created_at, updated_at, tags, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (g.ganglion_id, g.name, g.author_agent, g.description,
             g.code_snippet, g.version, g.status.value,
             g.rating, g.rating_count, g.usage_count, g.success_count,
             g.created_at, g.updated_at,
             json.dumps(g.tags), json.dumps(g.metadata))
        )

    # ── API Pública ───────────────────────────────────────────────────────────

    def register_ganglion(self, name: str, author: str, description: str,
                          code_snippet: str = "", tags: list[str] = None,
                          metadata: dict = None) -> Ganglion:
        """Registra un nuevo ganglion."""
        with self._lock:
            with get_conn_ctx(self.db_path) as conn:
                existing = self._get_ganglion(conn, name)
                if existing:
                    raise ValueError(f"Ganglion '{name}' already exists")

                now = time.time()
                g = Ganglion(
                    ganglion_id=str(uuid.uuid4()),
                    name=name, author_agent=author,
                    description=description, code_snippet=code_snippet,
                    tags=tags or [], metadata=metadata or {},
                    created_at=now, updated_at=now
                )
                self._save_ganglion(conn, g)

                # Reward al autor si economy disponible
                if HAS_ECONOMY:
                    try:
                        eco = get_economy()
                        eco.reward(author, "ganglion_created")
                    except Exception:
                        pass  # error no crítico, continuar
                print(f"🧬 [Ganglia] Registrado: '{name}' por {author} — status=nascent")
                return g

    def validate(self, name: str) -> bool:
        """Marca un ganglion como validado (manual)."""
        return self._change_status(name, GanglionStatus.VALIDATED)

    def promote(self, name: str) -> bool:
        """Promueve un ganglion al siguiente nivel."""
        status_order = [
            GanglionStatus.NASCENT, GanglionStatus.VALIDATED,
            GanglionStatus.ACTIVE, GanglionStatus.CANONICAL
        ]
        with self._lock:
            with get_conn_ctx(self.db_path) as conn:
                g = self._get_ganglion(conn, name)
                if not g:
                    return False
                try:
                    idx = status_order.index(g.status)
                    if idx >= len(status_order) - 1:
                        print(f"⚠️ [Ganglia] '{name}' ya está en nivel máximo: {g.status.value}")
                        return False
                    new_status = status_order[idx + 1]
                    g.status = new_status
                    g.updated_at = time.time()
                    self._save_ganglion(conn, g)
                    print(f"⬆️ [Ganglia] '{name}' promovido a {new_status.value}")
                    return True
                except ValueError:
                    return False

    def demote(self, name: str) -> bool:
        """Degrada un ganglion al nivel anterior."""
        status_order = [
            GanglionStatus.NASCENT, GanglionStatus.VALIDATED,
            GanglionStatus.ACTIVE, GanglionStatus.CANONICAL
        ]
        with self._lock:
            with get_conn_ctx(self.db_path) as conn:
                g = self._get_ganglion(conn, name)
                if not g:
                    return False
                try:
                    idx = status_order.index(g.status)
                    if idx <= 0:
                        return False
                    g.status = status_order[idx - 1]
                    g.updated_at = time.time()
                    self._save_ganglion(conn, g)
                    print(f"⬇️ [Ganglia] '{name}' degradado a {g.status.value}")
                    return True
                except ValueError:
                    return False

    def archive(self, name: str) -> bool:
        """Archiva un ganglion (no se puede usar más)."""
        return self._change_status(name, GanglionStatus.ARCHIVED)

    def _change_status(self, name: str, new_status: GanglionStatus) -> bool:
        """Cambia el status de un ganglion."""
        with self._lock:
            with get_conn_ctx(self.db_path) as conn:
                g = self._get_ganglion(conn, name)
                if not g:
                    return False
                g.status = new_status
                g.updated_at = time.time()
                self._save_ganglion(conn, g)
                print(f"🔄 [Ganglia] '{name}' → {new_status.value}")
                return True

    def learn(self, agent_id: str, ganglion_name: str) -> bool:
        """Un agente aprende un ganglion de otro agente."""
        with self._lock:
            with get_conn_ctx(self.db_path) as conn:
                g = self._get_ganglion(conn, ganglion_name)
                if not g:
                    print(f"❌ [Ganglia] Ganglion '{ganglion_name}' no encontrado")
                    return False
                if g.status == GanglionStatus.ARCHIVED:
                    print(f"❌ [Ganglia] Ganglion '{ganglion_name}' está archivado")
                    return False

                # Verificar si ya lo aprendió
                existing = conn.execute(
                    "SELECT id FROM learnings WHERE agent_id = ? AND ganglion_id = ?",
                    (agent_id, g.ganglion_id)
                ).fetchone()
                if existing:
                    print(f"ℹ️ [Ganglia] {agent_id} ya conoce '{ganglion_name}'")
                    return True

                conn.execute(
                    """INSERT INTO learnings (agent_id, ganglion_id, learned_at)
                       VALUES (?, ?, ?)""",
                    (agent_id, g.ganglion_id, time.time())
                )

                print(f"📚 [Ganglia] {agent_id} aprendió '{ganglion_name}' de {g.author_agent}")
                return True

    def use_ganglion(self, agent_id: str, ganglion_name: str,
                     success: bool = True) -> Optional[dict]:
        """
        Registra el uso de un ganglion por un agente.
        Paga royalties al autor si es otro agente.

        Returns:
            dict con info del uso, o None si no encontrado
        """
        with self._lock:
            with get_conn_ctx(self.db_path) as conn:
                g = self._get_ganglion(conn, ganglion_name)
                if not g:
                    return None
                if g.status == GanglionStatus.ARCHIVED:
                    return None

                # Actualizar contadores
                g.usage_count += 1
                if success:
                    g.success_count += 1
                g.updated_at = time.time()
                self._save_ganglion(conn, g)

                # Actualizar learning record
                conn.execute(
                    """UPDATE learnings SET usage_count = usage_count + 1, last_used = ?
                       WHERE agent_id = ? AND ganglion_id = ?""",
                    (time.time(), agent_id, g.ganglion_id)
                )

                # Royalties al autor (si es otro agente)
                royalty_paid = False
                if agent_id != g.author_agent and HAS_ECONOMY:
                    try:
                        eco = get_economy()
                        eco.earn(g.author_agent, ROYALTY_AMOUNT,
                                 f"royalty:{ganglion_name}:by:{agent_id}")
                        royalty_paid = True
                    except Exception:
                        pass  # error no crítico, continuar
                # Auto-promotion check
                self._check_auto_promotion(conn, g)

                # Auto-archive check
                self._check_auto_archive(conn, g)

                icon = "✅" if success else "❌"
                print(f"{icon} [Ganglia] {agent_id} usó '{ganglion_name}' "
                      f"(uses={g.usage_count} rate={g.success_rate:.0%}) "
                      f"{'💎royalty' if royalty_paid else ''}")

                return {
                    "ganglion": ganglion_name,
                    "agent": agent_id,
                    "success": success,
                    "usage_count": g.usage_count,
                    "success_rate": g.success_rate,
                    "royalty_paid": royalty_paid,
                }

    def rate_ganglion(self, ganglion_name: str, agent_id: str,
                      rating: float, comment: str = "") -> Optional[float]:
        """
        Valora un ganglion (0-5). Retorna nuevo rating promedio.
        Un agente solo puede valorar una vez (se actualiza).
        """
        if not 0 <= rating <= 5:
            raise ValueError("Rating must be between 0 and 5")

        with self._lock:
            with get_conn_ctx(self.db_path) as conn:
                g = self._get_ganglion(conn, ganglion_name)
                if not g:
                    return None

                # Upsert rating
                conn.execute(
                    """INSERT INTO ratings (ganglion_id, agent_id, rating, comment, timestamp)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(ganglion_id, agent_id)
                       DO UPDATE SET rating = ?, comment = ?, timestamp = ?""",
                    (g.ganglion_id, agent_id, rating, comment, time.time(),
                     rating, comment, time.time())
                )

                # Recalcular rating promedio
                row = conn.execute(
                    "SELECT AVG(rating), COUNT(*) FROM ratings WHERE ganglion_id = ?",
                    (g.ganglion_id,)
                ).fetchone()

                g.rating = round(row[0], 2)
                g.rating_count = row[1]
                g.updated_at = time.time()
                self._save_ganglion(conn, g)

                print(f"⭐ [Ganglia] '{ganglion_name}' rated {rating}/5 by {agent_id} "
                      f"— avg={g.rating}/5 ({g.rating_count} votes)")
                return g.rating

    def find_ganglia(self, query: str, limit: int = 10) -> list[dict]:
        """
        Busca ganglia por query. Scoring por relevancia, rating y usage.
        """
        query_lower = query.lower()
        with get_conn_ctx(self.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM ganglia
                   WHERE status != 'archived'
                   ORDER BY rating DESC, usage_count DESC"""
            ).fetchall()

        results = []
        for row in rows:
            g = self._row_to_ganglion(row)
            # Scoring de relevancia
            score = 0.0
            if query_lower in g.name.lower():
                score += 5.0
            if query_lower in g.description.lower():
                score += 3.0
            if any(query_lower in t.lower() for t in g.tags):
                score += 4.0
            if query_lower in g.code_snippet.lower():
                score += 1.0

            if score > 0:
                # Boost por rating y usage
                score += g.rating * 0.5
                score += min(g.usage_count * 0.1, 2.0)
                results.append({
                    "name": g.name,
                    "author": g.author_agent,
                    "description": g.description,
                    "status": g.status.value,
                    "rating": g.rating,
                    "usage_count": g.usage_count,
                    "success_rate": round(g.success_rate, 2),
                    "score": round(score, 2),
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def get_agent_ganglia(self, agent_id: str) -> list[dict]:
        """Lista todos los ganglia que un agente ha aprendido."""
        with get_conn_ctx(self.db_path) as conn:
            rows = conn.execute(
                """SELECT g.name, g.description, g.status, g.rating,
                          l.usage_count, l.learned_at
                   FROM learnings l
                   JOIN ganglia g ON l.ganglion_id = g.ganglion_id
                   WHERE l.agent_id = ?
                   ORDER BY l.usage_count DESC""",
                (agent_id,)
            ).fetchall()

        return [{
            "name": r[0], "description": r[1], "status": r[2],
            "rating": r[3], "my_uses": r[4], "learned_at": r[5],
        } for r in rows]

    def export_ganglion(self, name: str) -> Optional[str]:
        """Exporta un ganglion como JSON."""
        with get_conn_ctx(self.db_path) as conn:
            g = self._get_ganglion(conn, name)
            if not g:
                return None

        data = {
            "name": g.name, "author_agent": g.author_agent,
            "description": g.description, "code_snippet": g.code_snippet,
            "version": g.version, "status": g.status.value,
            "rating": g.rating, "tags": g.tags,
            "metadata": g.metadata,
            "exported_at": time.time(),
        }
        return json.dumps(data, indent=2)

    def import_ganglion(self, json_str: str, importing_agent: str = "system") -> Optional[Ganglion]:
        """Importa un ganglion desde JSON."""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            print("❌ [Ganglia] JSON inválido para import")
            return None

        name = data.get("name")
        if not name:
            print("❌ [Ganglia] Ganglion sin nombre")
            return None

        try:
            return self.register_ganglion(
                name=name,
                author=data.get("author_agent", importing_agent),
                description=data.get("description", ""),
                code_snippet=data.get("code_snippet", ""),
                tags=data.get("tags", []),
                metadata=data.get("metadata", {}),
            )
        except ValueError as e:
            print(f"⚠️ [Ganglia] Import failed: {e}")
            return None

    def list_all(self, status_filter: str = None) -> list[dict]:
        """Lista todos los ganglia, opcionalmente filtrados por status."""
        with get_conn_ctx(self.db_path) as conn:
            if status_filter:
                rows = conn.execute(
                    "SELECT * FROM ganglia WHERE status = ? ORDER BY rating DESC",
                    (status_filter,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM ganglia ORDER BY rating DESC"
                ).fetchall()

        return [{
            "name": self._row_to_ganglion(r).name,
            "author": self._row_to_ganglion(r).author_agent,
            "status": self._row_to_ganglion(r).status.value,
            "rating": self._row_to_ganglion(r).rating,
            "usage_count": self._row_to_ganglion(r).usage_count,
            "success_rate": round(self._row_to_ganglion(r).success_rate, 2),
        } for r in rows]

    def _check_auto_promotion(self, conn: sqlite3.Connection, g: Ganglion) -> None:
        """Promueve automáticamente si cumple thresholds."""
        thresholds = PROMOTION_THRESHOLDS.get(g.status.value)
        if not thresholds:
            return
        if (g.usage_count >= thresholds["min_uses"] and
                g.rating >= thresholds["min_rating"]):
            status_order = [
                GanglionStatus.NASCENT, GanglionStatus.VALIDATED,
                GanglionStatus.ACTIVE, GanglionStatus.CANONICAL
            ]
            try:
                idx = status_order.index(g.status)
                if idx < len(status_order) - 1:
                    g.status = status_order[idx + 1]
                    g.updated_at = time.time()
                    self._save_ganglion(conn, g)
                    print(f"🎉 [Ganglia] AUTO-PROMOCIÓN: '{g.name}' → {g.status.value}")
            except ValueError:
                pass

    def _check_auto_archive(self, conn: sqlite3.Connection, g: Ganglion) -> None:
        """Archiva automáticamente ganglia con rating bajo después de suficientes usos."""
        if (g.usage_count >= AUTO_ARCHIVE_MIN_USES and
                g.rating > 0 and g.rating < AUTO_ARCHIVE_THRESHOLD):
            g.status = GanglionStatus.ARCHIVED
            g.updated_at = time.time()
            self._save_ganglion(conn, g)
            print(f"🗑️ [Ganglia] AUTO-ARCHIVO: '{g.name}' (rating={g.rating} < {AUTO_ARCHIVE_THRESHOLD})")

    @property
    def stats(self) -> dict:
        """Estadísticas del sistema de ganglia."""
        with get_conn_ctx(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM ganglia").fetchone()[0]
            by_status = {}
            for row in conn.execute("SELECT status, COUNT(*) FROM ganglia GROUP BY status"):
                by_status[row[0]] = row[1]
            total_learnings = conn.execute("SELECT COUNT(*) FROM learnings").fetchone()[0]
            total_ratings = conn.execute("SELECT COUNT(*) FROM ratings").fetchone()[0]

        return {
            "total_ganglia": total,
            "by_status": by_status,
            "total_learnings": total_learnings,
            "total_ratings": total_ratings,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_ganglia_mgr: Optional[GangliaManager] = None


def get_ganglia_manager() -> GangliaManager:
    global _ganglia_mgr
    if _ganglia_mgr is None:
        _ganglia_mgr = GangliaManager()
    return _ganglia_mgr


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import tempfile

    print("=" * 60)
    print("  EIDOS GangliaManager — Test Suite")
    print("=" * 60)

    # Usar DB temporal para tests
    test_db = os.path.join(tempfile.mkdtemp(), "test_ganglia.db")
    gm = GangliaManager(db_path=test_db)

    # 1. Registrar ganglia
    print("\n── 1. Registro de ganglia ──")
    gm.register_ganglion(
        "fast_scan", author="scanner",
        description="Nmap quick scan for common ports",
        code_snippet="nmap -sV -T4 --top-ports 100 {target}",
        tags=["network", "scan", "nmap"]
    )
    gm.register_ganglion(
        "deep_audit", author="auditor",
        description="Full system audit with lynis",
        code_snippet="lynis audit system --quick",
        tags=["security", "audit"]
    )
    gm.register_ganglion(
        "log_analyzer", author="researcher",
        description="Pattern-based log analysis strategy",
        code_snippet="grep -E '(ERROR|WARN|CRITICAL)' /var/log/syslog | tail -50",
        tags=["logs", "analysis", "monitoring"]
    )

    # 2. Learning
    print("\n── 2. Aprendizaje ──")
    gm.learn("coder", "fast_scan")
    gm.learn("researcher", "fast_scan")
    gm.learn("coder", "log_analyzer")

    # 3. Uso y royalties
    print("\n── 3. Uso de ganglia ──")
    gm.use_ganglion("coder", "fast_scan", success=True)
    gm.use_ganglion("researcher", "fast_scan", success=True)
    gm.use_ganglion("coder", "fast_scan", success=False)

    # 4. Rating
    print("\n── 4. Rating ──")
    gm.rate_ganglion("fast_scan", "coder", 4.5, "Very useful for quick recon")
    gm.rate_ganglion("fast_scan", "researcher", 4.0, "Good but misses UDP")
    gm.rate_ganglion("deep_audit", "coder", 3.5)

    # 5. Búsqueda
    print("\n── 5. Búsqueda ──")
    results = gm.find_ganglia("scan")
    for r in results:
        print(f"  📌 {r['name']} (score={r['score']}) — {r['description'][:50]}...")

    # 6. Agent's ganglia
    print("\n── 6. Ganglia de 'coder' ──")
    agent_ganglia = gm.get_agent_ganglia("coder")
    for ag in agent_ganglia:
        print(f"  📖 {ag['name']} — uses={ag['my_uses']} status={ag['status']}")

    # 7. Promotion
    print("\n── 7. Promoción manual ──")
    gm.promote("fast_scan")
    gm.validate("deep_audit")

    # 8. Export/Import
    print("\n── 8. Export / Import ──")
    exported = gm.export_ganglion("fast_scan")
    if exported:
        print(f"  Exported JSON: {len(exported)} bytes")
        # Importar como nuevo
        imported = gm.import_ganglion(
            exported.replace('"fast_scan"', '"fast_scan_v2"'),
            importing_agent="system"
        )
        if imported:
            print(f"  Imported: {imported.name}")

    # 9. List all
    print("\n── 9. Lista completa ──")
    all_ganglia = gm.list_all()
    for g in all_ganglia:
        print(f"  🧬 {g['name']}: status={g['status']} rating={g['rating']} "
              f"uses={g['usage_count']}")

    # 10. Archive
    print("\n── 10. Archivado ──")
    gm.archive("log_analyzer")

    # 11. Stats
    print("\n── 11. Stats ──")
    stats = gm.stats
    for k, v in stats.items():
        print(f"  {k}: {v}")

    # Cleanup
    os.unlink(test_db)

    print("\n✅ GangliaManager — Todos los tests completados")
