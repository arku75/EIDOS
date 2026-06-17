"""
EIDOS Database Pool — Centralized SQLite access with WAL + busy_timeout.
All core modules should use get_connection() instead of raw sqlite3.connect()
to prevent "database is locked" errors under concurrent access.
"""
import sqlite3
import threading
from typing import Optional
from core.db import get_conn

_DB_PATH: Optional[str] = None
_LOCK = threading.Lock()

def set_db_path(path: str):
    global _DB_PATH
    _DB_PATH = path

def get_db_path() -> str:
    import os
    if _DB_PATH:
        return _DB_PATH
    return os.path.expanduser("~/.eidos/evolution_brain.db")

def get_connection(db_path: Optional[str] = None, timeout: int = 30) -> sqlite3.Connection:
    """
    Returns a properly configured SQLite connection via core/db.py (S109).
    WAL mode, busy_timeout, and synchronous are now guaranteed by get_conn().
    Delegates to core.db.get_conn for connection pooling.
    """
    path = db_path or get_db_path()
    return get_conn(path, timeout=timeout)
