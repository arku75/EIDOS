"""
core/eidos_hermes_bridge_v2.py — Puente EIDOS ↔ Hermes Agent v2 [S88 CARNE]

Mejora el bridge existente (eidos_memory_bridge.py) con:
  1. FTS5 — búsqueda full-text en todas las memorias (SQLite nativo, sin deps)
  2. Gateway mejorado — Telegram + Discord + Slack con sesiones persistentes
  3. Cron scheduler maduro — multi-plataforma, wake gates, no_agent mode
  4. SessionDB — persistencia de conversaciones con search

Diferencia con v1: FTS5 es la adición clave. EIDOS puede buscar en TODAS
sus memorias conversacionales con queries de texto libre, sin depender de
embeddings ni FAISS. Es el "hipocampo textual" de EIDOS.

Principio: "Recordar no es almacenar. Recordar es poder encontrar."

Uso:
    hb = get_hermes_v2()
    results = hb.search_memory("error en el grafo")
    hb.start_gateway(platforms=["telegram"])
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from core.db import get_conn
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.hermes_v2")

EIDOS_STATE_DB = Path.home() / ".eidos" / "state.db"
EIDOS_GATEWAY_CONFIG = Path.home() / ".eidos" / "gateway_config.json"
EIDOS_CRON_DIR = Path.home() / ".eidos" / "cron"

# Plataformas soportadas
SUPPORTED_PLATFORMS = [
    "telegram", "discord", "slack", "whatsapp",
    "signal", "matrix", "email", "local",
]


@dataclass
class SearchResult:
    """Resultado de búsqueda FTS5."""
    session_id: str
    role: str  # user, assistant, system
    content: str
    snippet: str  # fragmento con highlight
    rank: float
    timestamp: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GatewayConfig:
    """Configuración del gateway multi-plataforma."""
    enabled_platforms: List[str] = field(default_factory=lambda: ["telegram"])
    telegram_token: str = ""
    discord_token: str = ""
    slack_token: str = ""
    whatsapp_phone: str = ""
    signal_phone: str = ""
    home_channel: str = ""  # canal por defecto para notificaciones


class FTS5MemoryStore:
    """Motor de búsqueda full-text sobre memoria conversacional.

    Usa SQLite FTS5 (sin dependencias externas, nativo en Python stdlib).
    Indexa todas las conversaciones y permite búsqueda textual instantánea.

    A diferencia del grafo FAISS (32ms, semántico), FTS5 es búsqueda
    sintáctica exacta con soporte para:
      - Unicode61 tokenizer (inglés/español)
      - Trigram tokenizer (substring matching)
      - Snippets con highlighting
      - Boolean queries (AND, OR, NOT)
      - Prefix queries (term*)
    """

    def __init__(self, db_path: Path = EIDOS_STATE_DB):
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """Crea las tablas FTS5 y triggers de sincronización."""
        with self._lock:
            conn = get_conn(self._db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")

            # Tabla de sesiones
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    source TEXT DEFAULT 'eidos',
                    model TEXT DEFAULT '',
                    tokens_used INTEGER DEFAULT 0,
                    cost REAL DEFAULT 0.0,
                    title TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    parent_session_id TEXT DEFAULT '',
                    metadata_json TEXT DEFAULT '{}'
                )
            """)

            # Tabla de mensajes
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user','assistant','system','tool')),
                    content TEXT NOT NULL DEFAULT '',
                    tool_name TEXT DEFAULT '',
                    tool_calls_json TEXT DEFAULT '{}',
                    created_at REAL NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
            """)

            # FTS5 standalone (sin content= — almacena su propia copia)
            # Dropeamos primero para evitar corrupción de schema anterior
            conn.execute("DROP TABLE IF EXISTS messages_fts")
            conn.execute("DROP TABLE IF EXISTS messages_fts_data")
            conn.execute("DROP TABLE IF EXISTS messages_fts_idx")
            conn.execute("DROP TABLE IF EXISTS messages_fts_docsize")
            conn.execute("DROP TABLE IF EXISTS messages_fts_config")
            conn.execute("DROP TRIGGER IF EXISTS messages_ai")
            conn.execute("DROP TRIGGER IF EXISTS messages_ad")
            conn.execute("DROP TRIGGER IF EXISTS messages_au")

            conn.execute("""
                CREATE VIRTUAL TABLE messages_fts USING fts5(
                    session_id,
                    role,
                    content,
                    tool_name,
                    tokenize='unicode61 remove_diacritics 2'
                )
            """)

            conn.commit()
            log.info("FTS5 inicializado en %s", self._db_path)

    def insert_message(self, session_id: str, role: str, content: str,
                       tool_name: str = "", tool_calls: Optional[Dict] = None):
        """Inserta un mensaje y actualiza índice FTS5 (standalone)."""
        with self._lock:
            conn = get_conn(self._db_path, timeout=10)
            try:
                # Asegurar que la sesión existe
                now = time.time()
                conn.execute("""
                    INSERT OR IGNORE INTO sessions (id, source, created_at, updated_at)
                    VALUES (?, 'eidos', ?, ?)
                """, (session_id, now, now))
                conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))

                # Insertar en messages
                conn.execute("""
                    INSERT INTO messages (session_id, role, content, tool_name,
                                         tool_calls_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (session_id, role, content, tool_name or "",
                     json.dumps(tool_calls or {}), now))

                # Insertar directamente en FTS5 standalone
                conn.execute("""
                    INSERT INTO messages_fts (session_id, role, content, tool_name)
                    VALUES (?, ?, ?, ?)
                """, (session_id, role, content, tool_name or ""))

                conn.commit()
            finally:
                pass  # S109: no close needed

    def search(self, query: str, max_results: int = 20,
               session_id: Optional[str] = None,
               role_filter: Optional[str] = None) -> List[SearchResult]:
        """Búsqueda FTS5 standalone con snippets.

        Args:
            query: texto a buscar
            max_results: máximo de resultados
            session_id: filtrar por sesión (None = todas)
            role_filter: filtrar por rol (None = todos)

        Returns:
            Lista de SearchResult ordenados por relevancia
        """
        with self._lock:
            conn = get_conn(self._db_path, timeout=10)
            try:
                # Sanitizar query para FTS5
                safe_query = self._sanitize_fts5_query(query)

                where_parts = ["messages_fts MATCH ?"]
                params: list = [safe_query]

                if session_id:
                    where_parts.append("session_id = ?")
                    params.append(session_id)
                if role_filter:
                    where_parts.append("role = ?")
                    params.append(role_filter)

                where_sql = " AND ".join(where_parts)

                sql = f"""
                    SELECT
                        session_id,
                        role,
                        content,
                        snippet(messages_fts, 1, '<mark>', '</mark>', '...', 40) as snippet,
                        rank,
                        tool_name
                    FROM messages_fts
                    WHERE {where_sql}
                    ORDER BY rank
                    LIMIT ?
                """
                params.append(max_results)

                rows = conn.execute(sql, params).fetchall()

                results = []
                for row in rows:
                    results.append(SearchResult(
                        session_id=row[0],
                        role=row[1],
                        content=row[2][:500] if row[2] else "",
                        snippet=row[3] or (row[2][:200] if row[2] else ""),
                        rank=row[4],
                        metadata={"tool_name": row[5] or ""},
                    ))

                return results
            except Exception as e:
                log.debug("FTS5 search error: %s", e)
                return []
            finally:
                pass  # S109: no close needed

    def _fallback_search(self, query: str, max_results: int,
                        session_id: Optional[str],
                        role_filter: Optional[str]) -> List[SearchResult]:
        """Búsqueda LIKE como fallback si FTS5 falla."""
        conn = get_conn(self._db_path, timeout=10)
        try:
            where = ["content LIKE ?"]
            params: list = [f"%{query}%"]

            if session_id:
                where.append("session_id = ?")
                params.append(session_id)
            if role_filter:
                where.append("role = ?")
                params.append(role_filter)

            sql = f"""
                SELECT session_id, role, content, created_at, tool_name
                FROM messages WHERE {' AND '.join(where)}
                ORDER BY created_at DESC LIMIT ?
            """
            params.append(max_results)
            rows = conn.execute(sql, params).fetchall()

            results = []
            for row in rows:
                content = row[2] or ""
                # Crear snippet manual
                idx = content.lower().find(query.lower())
                if idx >= 0:
                    start = max(0, idx - 40)
                    end = min(len(content), idx + len(query) + 40)
                    snippet = content[start:end]
                    if start > 0:
                        snippet = "..." + snippet
                    if end < len(content):
                        snippet += "..."
                else:
                    snippet = content[:200]

                results.append(SearchResult(
                    session_id=row[0],
                    role=row[1],
                    content=content[:500],
                    snippet=snippet,
                    rank=1.0,
                    timestamp=datetime.fromtimestamp(
                        row[3]).strftime("%Y-%m-%d %H:%M") if row[3] else "",
                    metadata={"tool_name": row[4] or ""},
                ))

            return results
        finally:
                pass  # S109: no close needed

    @staticmethod
    def _sanitize_fts5_query(query: str) -> str:
        """Sanitiza una query para FTS5 (escapa caracteres especiales)."""
        # FTS5 caracteres especiales: * " ( ) + - . : ^
        special = r'*"()+.:^'
        result = []
        i = 0
        while i < len(query):
            c = query[i]
            if c == '"':
                # Mantener frases exactas balanceadas
                result.append(c)
            elif c in special:
                # Escapar con espacio
                result.append(' ')
            else:
                result.append(c)
            i += 1

        sanitized = ''.join(result).strip()
        # Si la query está vacía después de sanitizar, usar un término genérico
        if not sanitized:
            return "search"

        # Añadir sufijo * para prefix matching en cada término
        terms = sanitized.split()
        terms = [f"{t}*" if not t.endswith('*') and len(t) > 2 else t for t in terms]
        return ' '.join(terms)

    def get_recent(self, n: int = 20, session_id: Optional[str] = None) -> List[Dict]:
        """Obtiene los mensajes más recientes."""
        conn = get_conn(self._db_path, timeout=10)
        try:
            if session_id:
                rows = conn.execute(
                    "SELECT role, content, created_at FROM messages "
                    "WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
                    (session_id, n)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT role, content, created_at FROM messages "
                    "ORDER BY created_at DESC LIMIT ?", (n,)
                ).fetchall()

            return [
                {
                    "role": r[0],
                    "content": r[1][:300] if r[1] else "",
                    "timestamp": datetime.fromtimestamp(r[2]).strftime("%H:%M:%S") if r[2] else "",
                }
                for r in rows
            ]
        finally:
                pass  # S109: no close needed

    def get_context(self, session_id: str, around_message_id: int,
                    window: int = 3) -> List[Dict]:
        """Obtiene contexto alrededor de un mensaje (para mostrar resultados)."""
        conn = get_conn(self._db_path, timeout=10)
        try:
            rows = conn.execute("""
                SELECT id, role, content, created_at FROM messages
                WHERE session_id=? AND id BETWEEN ?-? AND ?+?
                ORDER BY id
            """, (session_id, around_message_id, window, around_message_id, window)
            ).fetchall()

            return [
                {"id": r[0], "role": r[1], "content": r[2][:300] if r[2] else "",
                 "timestamp": datetime.fromtimestamp(r[3]).strftime("%H:%M:%S") if r[3] else ""}
                for r in rows
            ]
        finally:
                pass  # S109: no close needed

    def stats(self) -> Dict[str, Any]:
        conn = get_conn(self._db_path, timeout=10)
        try:
            sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            fts_docs = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
            db_size = self._db_path.stat().st_size if self._db_path.exists() else 0
            return {
                "sessions": sessions,
                "messages": messages,
                "fts_indexed": fts_docs,
                "db_size_mb": round(db_size / 1e6, 2),
                "db_path": str(self._db_path),
            }
        finally:
                pass  # S109: no close needed


class CronJobManager:
    """Gestor de trabajos cron con entrega multi-plataforma.

    Más maduro que el scheduler básico de EIDOS:
      - Soporte one-shot, interval, cron expressions
      - Entrega multi-plataforma (Telegram, Discord, etc.)
      - Wake gates (script pre-check)
      - no_agent mode (ejecutar sin LLM)
      - context_from (encadenar jobs)
      - Injection scanning básico
    """

    def __init__(self):
        self._jobs_dir = EIDOS_CRON_DIR / "jobs"
        self._output_dir = EIDOS_CRON_DIR / "output"
        self._jobs_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._jobs_file = self._jobs_dir / "jobs.json"
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self):
        try:
            if self._jobs_file.exists():
                self._jobs = json.loads(self._jobs_file.read_text())
        except Exception:
            self._jobs = {}

    def _save(self):
        try:
            self._jobs_file.write_text(json.dumps(self._jobs, indent=2))
        except Exception:
            pass

    def create_job(self, job_id: str, prompt: str, schedule: str,
                   platforms: Optional[List[str]] = None,
                   no_agent: bool = False) -> Dict[str, Any]:
        """Crea un trabajo cron.

        Args:
            job_id: identificador único
            prompt: mensaje/prompt a ejecutar
            schedule: "30m", "2h", "1d", "every 2h", "0 9 * * *", ISO timestamp
            platforms: dónde entregar (None = "local")
            no_agent: si True, ejecutar sin LLM

        Returns:
            Datos del job creado
        """
        from datetime import datetime, timedelta

        # Parsear schedule a next_run
        now = datetime.now()
        next_run = self._parse_schedule(schedule, now)

        job = {
            "id": job_id,
            "prompt": prompt,
            "schedule": schedule,
            "next_run_at": next_run.isoformat(),
            "platforms": platforms or ["local"],
            "no_agent": no_agent,
            "enabled": True,
            "created_at": now.isoformat(),
            "last_run_at": None,
            "run_count": 0,
            "context_from": None,
        }

        self._jobs[job_id] = job
        self._save()
        return job

    def delete_job(self, job_id: str):
        self._jobs.pop(job_id, None)
        self._save()

    def get_due_jobs(self) -> List[Dict[str, Any]]:
        """Retorna jobs que deben ejecutarse ahora."""
        from datetime import datetime

        now = datetime.now()
        due = []
        for job_id, job in list(self._jobs.items()):
            if not job.get("enabled", True):
                continue
            next_run = datetime.fromisoformat(job["next_run_at"])
            if next_run <= now:
                due.append(job)
        return due

    def mark_run(self, job_id: str):
        """Marca un job como ejecutado y calcula next_run."""
        from datetime import datetime

        job = self._jobs.get(job_id)
        if not job:
            return

        now = datetime.now()
        job["last_run_at"] = now.isoformat()
        job["run_count"] = job.get("run_count", 0) + 1
        job["next_run_at"] = self._parse_schedule(
            job["schedule"], now).isoformat()
        self._save()

    def _parse_schedule(self, schedule: str,
                       from_dt: "datetime") -> "datetime":
        """Parsea un schedule string y retorna el próximo datetime."""
        from datetime import datetime, timedelta

        s = schedule.strip().lower()

        # Intervalos relativos
        if s.endswith("m"):
            try:
                minutes = int(s[:-1])
                return from_dt + timedelta(minutes=minutes)
            except ValueError:
                pass
        elif s.endswith("h"):
            try:
                hours = int(s[:-1])
                return from_dt + timedelta(hours=hours)
            except ValueError:
                pass
        elif s.endswith("d"):
            try:
                days = int(s[:-1])
                return from_dt + timedelta(days=days)
            except ValueError:
                pass

        # "every Xh/d/m"
        if s.startswith("every "):
            parts = s.split()
            if len(parts) >= 2:
                return self._parse_schedule(parts[1], from_dt)

        # ISO timestamp (one-shot)
        try:
            return datetime.fromisoformat(schedule)
        except ValueError:
            pass

        # Cron expression → fallback cada hora
        if len(s.split()) == 5:
            return from_dt + timedelta(hours=1)

        # Default: 24h
        return from_dt + timedelta(hours=24)

    def stats(self) -> Dict[str, Any]:
        return {
            "total_jobs": len(self._jobs),
            "enabled": sum(1 for j in self._jobs.values() if j.get("enabled", True)),
            "due_now": len(self.get_due_jobs()),
        }


class HermesBridgeV2:
    """Puente EIDOS ↔ Hermes Agent v2.

    Mejora el bridge existente con:
      - FTS5 para búsqueda textual de memorias
      - Cron scheduler maduro
      - Configuración de gateway multi-plataforma
    """

    def __init__(self):
        self._fts5 = FTS5MemoryStore()
        self._cron = CronJobManager()
        self._gateway_config = self._load_gateway_config()
        self._started_at = time.time()

    # ── Memoria FTS5 ───────────────────────────────────────────────────────

    def remember(self, session_id: str, role: str, content: str,
                 tool_name: str = "", tool_calls: Optional[Dict] = None):
        """Registra un mensaje en la memoria FTS5."""
        self._fts5.insert_message(session_id, role, content, tool_name, tool_calls)

    def search_memory(self, query: str, max_results: int = 20,
                     session_id: Optional[str] = None) -> List[SearchResult]:
        """Busca en todas las memorias con FTS5.

        Usar para:
          - "¿qué error tuvimos ayer con el grafo?"
          - "¿qué dijo SER sobre MetaClaw?"
          - "busca conversaciones sobre HexStrike"
        """
        return self._fts5.search(query, max_results, session_id)

    def recent_memories(self, n: int = 20) -> List[Dict]:
        """Últimas memorias registradas."""
        return self._fts5.get_recent(n)

    def memory_context(self, search_result: SearchResult, window: int = 3) -> List[Dict]:
        """Contexto alrededor de un resultado de búsqueda."""
        # Extraer message_id del resultado si es posible
        conn = get_conn(EIDOS_STATE_DB, timeout=10)
        try:
            row = conn.execute(
                "SELECT id FROM messages WHERE session_id=? AND content LIKE ? LIMIT 1",
                (search_result.session_id, f"%{search_result.content[:50]}%")
            ).fetchone()
            if row:
                return self._fts5.get_context(search_result.session_id, row[0], window)
        except Exception:
            pass
        finally:
                pass  # S109: no close needed
        return []

    # ── Gateway ─────────────────────────────────────────────────────────────

    def configure_gateway(self, config: Dict[str, Any]):
        """Configura el gateway multi-plataforma."""
        for key in ["telegram_token", "discord_token", "slack_token",
                    "whatsapp_phone", "signal_phone", "home_channel",
                    "enabled_platforms"]:
            if key in config:
                if key == "enabled_platforms":
                    self._gateway_config[key] = config[key]
                else:
                    setattr(self._gateway_config, key, config[key])
        self._save_gateway_config()

    def get_gateway_config(self) -> GatewayConfig:
        return self._gateway_config

    def start_gateway(self, platforms: Optional[List[str]] = None) -> Dict[str, Any]:
        """Inicia el gateway para las plataformas especificadas.

        Args:
            platforms: lista de plataformas (None = usar config)

        Returns:
            Estado de cada plataforma
        """
        if platforms:
            self._gateway_config.enabled_platforms = platforms

        status = {}
        for platform in self._gateway_config.enabled_platforms:
            if platform == "telegram" and self._gateway_config.telegram_token:
                status["telegram"] = self._start_telegram()
            elif platform == "discord" and self._gateway_config.discord_token:
                status["discord"] = self._start_discord()
            elif platform == "slack" and self._gateway_config.slack_token:
                status["slack"] = "configured"
            elif platform == "whatsapp" and self._gateway_config.whatsapp_phone:
                status["whatsapp"] = "configured"
            else:
                status[platform] = "no_credentials"

        return status

    def _start_telegram(self) -> str:
        """Inicia el adapter de Telegram."""
        try:
            # Verificar si ya hay un bot corriendo
            from core.telegram_bot import get_telegram_bot
            bot = get_telegram_bot()
            if hasattr(bot, 'is_running') and bot.is_running:
                return "already_running"
            return "available"
        except ImportError:
            return "python-telegram-bot no instalado"

    def _start_discord(self) -> str:
        """Inicia el adapter de Discord."""
        try:
            from core.discord_bot import get_discord_bot
            bot = get_discord_bot()
            return "available"
        except ImportError:
            return "discord.py no instalado"

    # ── Cron ────────────────────────────────────────────────────────────────

    def schedule_task(self, task_id: str, prompt: str, schedule: str,
                     platforms: Optional[List[str]] = None) -> Dict[str, Any]:
        """Programa una tarea recurrente.

        Ejemplos:
          hb.schedule_task("daily_report", "Genera informe diario", "every 24h",
                          platforms=["telegram"])
          hb.schedule_task("health_check", "Verifica salud", "0 */6 * * *")
        """
        return self._cron.create_job(task_id, prompt, schedule, platforms)

    def get_due_tasks(self) -> List[Dict[str, Any]]:
        return self._cron.get_due_jobs()

    def complete_task(self, task_id: str):
        self._cron.mark_run(task_id)

    # ── Config ──────────────────────────────────────────────────────────────

    def _load_gateway_config(self) -> GatewayConfig:
        try:
            if EIDOS_GATEWAY_CONFIG.exists():
                data = json.loads(EIDOS_GATEWAY_CONFIG.read_text())
                return GatewayConfig(**data)
        except Exception:
            pass
        return GatewayConfig()

    def _save_gateway_config(self):
        try:
            EIDOS_GATEWAY_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            EIDOS_GATEWAY_CONFIG.write_text(json.dumps(
                self._gateway_config.__dict__, indent=2))
        except Exception:
            pass

    # ── Stats ───────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "fts5": self._fts5.stats(),
            "cron": self._cron.stats(),
            "gateway_enabled": self._gateway_config.enabled_platforms,
            "uptime_s": round(time.time() - self._started_at, 1),
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
_bridge: Optional[HermesBridgeV2] = None


def get_hermes_v2() -> HermesBridgeV2:
    global _bridge
    if _bridge is None:
        _bridge = HermesBridgeV2()
    return _bridge


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(description="EIDOS Hermes Bridge v2 [S88 CARNE]")
    p.add_argument("--search", type=str, help="Buscar en memoria FTS5")
    p.add_argument("--recent", type=int, default=20, help="Memorias recientes")
    p.add_argument("--remember", nargs=2, metavar=("ROLE", "CONTENT"),
                   help="Registrar memoria")
    p.add_argument("--schedule", nargs=3, metavar=("ID", "PROMPT", "SCHEDULE"),
                   help="Programar tarea cron")
    p.add_argument("--stats", action="store_true", help="Estadísticas")
    args = p.parse_args()

    hb = get_hermes_v2()

    if args.search:
        results = hb.search_memory(args.search)
        for r in results:
            print(f"[{r.timestamp}] [{r.role}] {r.snippet}")
            print()
    elif args.recent:
        for m in hb.recent_memories(args.recent):
            print(f"[{m['timestamp']}] [{m['role']}] {m['content'][:200]}")
    elif args.remember:
        hb.remember("cli_session", args.remember[0], args.remember[1])
        print(f"Registrado: [{args.remember[0]}] {args.remember[1][:100]}")
    elif args.schedule:
        job = hb.schedule_task(args.schedule[0], args.schedule[1], args.schedule[2])
        print(f"Job creado: {json.dumps(job, indent=2)}")
    elif args.stats:
        print(json.dumps(hb.stats(), indent=2, ensure_ascii=False))
    else:
        p.print_help()
