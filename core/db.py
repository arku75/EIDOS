"""
core/db.py — Capa de acceso a datos UNIFICADA para EIDOS [S109]

Resuelve el problema de raíz identificado por DeepSeek (S108):
609 sqlite3.connect() dispersos en 155 archivos SIN abstracción.

Esta capa proporciona get_conn(db_name) que devuelve SIEMPRE una conexión
con:
  - WAL mode (journal_mode=wal)
  - synchronous=NORMAL (balance velocidad/durabilidad)
  - busy_timeout=30000ms (30s timeout para concurrencia)
  - cache_size=-40000 (40MB cache)
  - mmap_size=268435456 (256MB memory-mapped I/O)
  - foreign_keys=ON
  - recursive_triggers=ON

Uso:
    from core.db import get_conn

    conn = get_conn("self.db")
    # o con path absoluto:
    conn = get_conn(Path.home() / ".eidos" / "evolution_brain.db")
    # o con Path:
    conn = get_conn(Path.home() / ".eidos" / "vivo.db")

Regla de oro: NUNCA más sqlite3.connect() directo. Si se necesita una
conexión que no pase por get_conn(), documentar por qué en el código.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

log = logging.getLogger("eidos.db")

# ── Constantes ─────────────────────────────────────────────────────────────

EIDOS_DB_DIR = Path(os.environ.get("EIDOS_HOME", str(Path.home() / ".eidos"))).expanduser()

# PRAGMAs que se aplican a TODA conexión
DEFAULT_PRAGMAS: Dict[str, Any] = {
    "journal_mode": "wal",           # Write-Ahead Logging
    "synchronous": "NORMAL",         # Balance velocidad/durabilidad (no OFF por seguridad)
    "busy_timeout": 30000,           # 30s timeout en caso de bloqueo
    "cache_size": -40000,            # 40MB cache (negativo = KB)
    "mmap_size": 268435456,          # 256MB memory-mapped I/O
    "foreign_keys": "ON",            # Integridad referencial
    "recursive_triggers": "ON",      # Triggers pueden invocar otros triggers
}

# Conexiones cacheadas por path (thread-safe)
_conn_cache: Dict[str, sqlite3.Connection] = {}
_cache_lock = threading.Lock()

# Estadísticas para verify_state.sh
_stats: Dict[str, int] = {
    "connections_created": 0,
    "connections_reused": 0,
    "connections_closed": 0,
    "errors": 0,
}


# ── Funciones principales ──────────────────────────────────────────────────

def _resolve_db_path(db_name: Union[str, Path]) -> Path:
    """Resuelve un nombre de DB a path absoluto.

    - Si es path absoluto, lo usa tal cual.
    - Si es path relativo o solo nombre, lo busca en ~/.eidos/
    """
    p = Path(db_name)
    if p.is_absolute():
        return p

    # Si el nombre viene sin .db, añadirlo
    if p.suffix != ".db":
        p = p.with_suffix(".db")

    # Si es relativo, resolver contra ~/.eidos/
    if not p.is_absolute():
        p = EIDOS_DB_DIR / p

    return p


def get_conn(db_name: Union[str, Path],
             *,
             read_only: bool = False,
             cache: bool = True,
             timeout: int = 30,
             check_same_thread: bool = True,
             isolation_level: Optional[str] = '',
             uri: bool = False,
             **kwargs) -> sqlite3.Connection:
    """Obtiene una conexión SQLite configurada con los PRAGMAs estándar de EIDOS.

    Args:
        db_name: Nombre de la DB (ej: "self.db"), path relativo, o path absoluto.
        read_only: Abrir en modo solo-lectura (default: False).
        cache: Usar caché de conexiones (default: True). Desactivar para
               conexiones de corta duración en threads.
        timeout: Timeout en segundos para la conexión (default: 30).
        check_same_thread: Pasar a sqlite3.connect(). Usar False cuando
               la conexión se comparte entre threads (default: True).
        isolation_level: Nivel de aislamiento. '' = autocommit (default).
               Usar None para autocommit explícito, 'DEFERRED', etc.
        uri: Si es True, db_name se interpreta como URI (file:...).
        **kwargs: Argumentos adicionales pasados a sqlite3.connect().

    Returns:
        Conexión sqlite3 configurada con PRAGMAs EIDOS.

    Raises:
        FileNotFoundError: Si la DB no existe y es read_only.
        sqlite3.Error: Si hay error de conexión.
    """
    db_path = _resolve_db_path(db_name)
    # SQLite connections created with check_same_thread=True are bound to the
    # creating thread. Cache them per-thread so another worker can never receive
    # a connection it is forbidden to use. Connections explicitly created with
    # check_same_thread=False retain shared-cache semantics; callers that do not
    # want sharing must pass cache=False.
    thread_scope = threading.get_ident() if check_same_thread else "shared"
    cache_key = (
        f"{db_path}:{thread_scope}:{check_same_thread}:"
        f"{isolation_level}:{read_only}"
    )

    # Si está en caché y los parámetros coinciden, devolver la caché
    if cache and not read_only:
        with _cache_lock:
            if cache_key in _conn_cache:
                try:
                    conn = _conn_cache[cache_key]
                    # Verificar que sigue viva
                    conn.execute("SELECT 1")
                    _stats["connections_reused"] += 1
                    return conn
                except (sqlite3.ProgrammingError, sqlite3.OperationalError):
                    # Conexión muerta, eliminarla de la caché
                    del _conn_cache[cache_key]
                    log.debug("Conexión cacheada muerta: %s", cache_key)

    # Crear nueva conexión
    if uri:
        connect_target = str(db_path)
    elif read_only:
        connect_target = f"file:{db_path}?mode=ro"
        uri = True
    else:
        connect_target = str(db_path)

    conn_kwargs = {
        "timeout": timeout,
        "check_same_thread": check_same_thread,
        "isolation_level": isolation_level,
    }
    if uri or read_only:
        conn_kwargs["uri"] = True
    conn_kwargs.update(kwargs)

    if not read_only and not uri:
        db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(connect_target, **conn_kwargs)
    conn.row_factory = sqlite3.Row

    # Aplicar PRAGMAs estándar
    _apply_pragmas(conn, read_only=read_only)

    # Guardar en caché
    if cache and not read_only:
        with _cache_lock:
            _conn_cache[cache_key] = conn

    _stats["connections_created"] += 1
    log.debug("get_conn(%s) -> nueva conexión (total=%d, reusadas=%d, chk_thread=%s)",
              db_path.name, _stats["connections_created"], _stats["connections_reused"],
              check_same_thread)

    return conn


def _apply_pragmas(conn: sqlite3.Connection, *, read_only: bool = False):
    """Aplica los PRAGMAs estándar a una conexión."""
    for pragma, value in DEFAULT_PRAGMAS.items():
        if read_only and pragma in ("journal_mode", "cache_size", "mmap_size"):
            continue  # No se pueden cambiar en modo read-only
        try:
            conn.execute(f"PRAGMA {pragma}={value}")
        except sqlite3.OperationalError as e:
            # En read-only, algunos PRAGMAs pueden fallar — ignorar
            log.debug("PRAGMA %s=%s falló (read_only=%s): %s",
                      pragma, value, read_only, e)
            _stats["errors"] += 1


def close_db(db_name: Union[str, Path]):
    """Cierra y elimina de la caché una conexión específica.

    Usa el mismo formato de clave que get_conn() para que las claves
    coincidan: db_path:check_same_thread:isolation_level:read_only.
    Como no conocemos los parámetros originales, iteramos todas las
    entradas cuyo prefijo coincida con db_path y las cerramos todas.
    """
    db_path = _resolve_db_path(db_name)
    prefix = str(db_path)

    with _cache_lock:
        keys_to_close = [k for k in _conn_cache if k.startswith(prefix)]
        for cache_key in keys_to_close:
            try:
                _conn_cache[cache_key].close()
                _stats["connections_closed"] += 1
            except Exception as e:
                log.debug("Error cerrando %s: %s", cache_key, e)
            del _conn_cache[cache_key]


def close_all():
    """Cierra TODAS las conexiones cacheadas. Usar en shutdown."""
    with _cache_lock:
        for key, conn in list(_conn_cache.items()):
            try:
                conn.close()
                _stats["connections_closed"] += 1
            except Exception as e:
                log.debug("Error cerrando %s: %s", key, e)
        _conn_cache.clear()
    log.info("close_all(): %d conexiones cerradas", _stats["connections_closed"])


@contextmanager
def get_conn_ctx(db_name: Union[str, Path], *, read_only: bool = False, cache: bool = False):
    """Context manager que obtiene conexión y la cierra al salir.

    Útil para conexiones de corta duración donde no se quiere mantener
    la conexión en caché. Auto-commitea al salir sin excepción.
    """
    conn = get_conn(db_name, read_only=read_only, cache=cache)
    try:
        yield conn
        if not read_only:
            conn.commit()
    finally:
        if not cache:
            try:
                conn.close()
                _stats["connections_closed"] += 1
            except Exception:
                pass


# ── Decorador para migración progresiva ────────────────────────────────────

def with_conn(db_name: Union[str, Path], *, read_only: bool = False):
    """Decorador que inyecta una conexión como primer argumento.

    Uso:
        @with_conn("self.db")
        def mi_funcion(conn, arg1, arg2):
            conn.execute("SELECT ...")
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with get_conn_ctx(db_name, read_only=read_only, cache=False) as conn:
                return func(conn, *args, **kwargs)
        return wrapper
    return decorator


# ── Estadísticas ────────────────────────────────────────────────────────────

def stats() -> Dict[str, Any]:
    """Retorna estadísticas de uso de la capa DB."""
    with _cache_lock:
        cached = len(_conn_cache)
    return {
        **_stats,
        "cached_connections": cached,
        "cache_keys": list(_conn_cache.keys()) if cached else [],
        "pragma_defaults": dict(DEFAULT_PRAGMAS),
    }


# ── Verificación de salud ─────────────────────────────────────────────────

def health_check() -> Dict[str, Any]:
    """Verifica que la capa DB funciona correctamente."""
    results = {
        "ok": True,
        "checks": {},
        "errors": [],
    }

    # 1. Comprobar que podemos conectar a self.db
    try:
        conn = get_conn("self.db")
        row = conn.execute("SELECT 1").fetchone()
        results["checks"]["connect_self_db"] = row is not None
    except Exception as e:
        results["checks"]["connect_self_db"] = False
        results["errors"].append(f"self.db: {e}")

    # 2. Verificar WAL mode en self.db
    try:
        conn = get_conn("self.db")
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        results["checks"]["wal_mode"] = mode.lower() == "wal"
    except Exception as e:
        results["checks"]["wal_mode"] = False
        results["errors"].append(f"WAL check: {e}")

    # 3. Verificar synchronous
    try:
        conn = get_conn("self.db")
        sync = conn.execute("PRAGMA synchronous").fetchone()[0]
        results["checks"]["synchronous_normal"] = sync == 1  # 1 = NORMAL
    except Exception as e:
        results["checks"]["synchronous_normal"] = False
        results["errors"].append(f"sync check: {e}")

    # 4. Contar cuántos connects directos quedan
    import subprocess
    try:
        r = subprocess.run(
            ["grep", "-r", "sqlite3.connect", str(Path(__file__).parent)],
            capture_output=True, text=True, timeout=5
        )
        # Descontar los connects dentro de db.py mismo
        total = len([l for l in r.stdout.split("\n") if l and "db.py" not in l])
        results["checks"]["raw_connects_remaining"] = total
    except Exception:
        results["checks"]["raw_connects_remaining"] = "error"

    results["ok"] = all(
        v for v in results["checks"].values()
        if isinstance(v, bool)
    )

    return results


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="EIDOS DB Layer (S109)")
    ap.add_argument("--stats", action="store_true", help="Mostrar estadísticas")
    ap.add_argument("--health", action="store_true", help="Health check")
    ap.add_argument("--close-all", action="store_true", help="Cerrar todas las conexiones")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.stats:
        print(json.dumps(stats(), indent=2, ensure_ascii=False, default=str))
    elif args.health:
        print(json.dumps(health_check(), indent=2, ensure_ascii=False, default=str))
    elif args.close_all:
        close_all()
        print("Todas las conexiones cerradas.")
    else:
        # Test rápido
        conn = get_conn("self.db")
        wal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        sync = conn.execute("PRAGMA synchronous").fetchone()[0]
        cache = conn.execute("PRAGMA cache_size").fetchone()[0]
        mmap = conn.execute("PRAGMA mmap_size").fetchone()[0]
        print(f"self.db: WAL={wal} sync={sync} cache={cache} mmap={mmap}")
        print(f"Stats: {json.dumps(stats(), indent=2, ensure_ascii=False)}")
