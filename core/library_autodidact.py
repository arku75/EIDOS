"""
EIDOS core/library_autodidact.py — Auto-Learn Python Libraries
================================================================
EIDOS aprende automáticamente las APIs de librerías Python.

Funcionalidad:
- Introspección profunda de módulos: clases, métodos, firmas, docstrings
- Genera "fichas" de aprendizaje compactas almacenadas en SQLite
- El Brain puede consultar estas fichas cuando necesita usar una librería
- Aprendizaje incremental: solo aprende lo que no conoce

Integración:
    from core.library_autodidact import get_autodidact

    ad = get_autodidact()
    ad.learn("requests")           # Aprende la librería requests
    ad.learn("nmap", depth=2)      # Aprende con más profundidad
    info = ad.lookup("requests", "get")  # Busca un método específico
    summary = ad.summarize("requests")   # Resumen completo
"""
from __future__ import annotations

import importlib
import inspect
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from core.db import get_conn
from core.db import get_conn_ctx

log = logging.getLogger("eidos.autodidact")

DB_PATH = os.path.expanduser("~/.eidos/autodidact.db")


# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class APIEntry:
    """Una entrada de API aprendida."""
    library: str
    name: str           # Nombre completo: "requests.get" o "requests.Session.post"
    kind: str           # "function", "class", "method", "constant", "module"
    signature: str      # "def get(url, **kwargs)"
    docstring: str      # Docstring truncado
    return_type: str    # Tipo de retorno si disponible
    params: str         # JSON de parámetros
    example: str        # Ejemplo de uso si está en docstring
    depth: int          # Profundidad en el árbol del módulo
    learned_at: float


# ══════════════════════════════════════════════════════════════════════════════
#  LIBRARY AUTODIDACT
# ══════════════════════════════════════════════════════════════════════════════

class LibraryAutodidact:
    """
    Aprende automáticamente APIs de librerías Python por introspección.
    """

    MAX_DOCSTRING = 500    # Chars máx de docstring a guardar
    MAX_DEPTH = 3          # Profundidad máxima de introspección
    MAX_ENTRIES = 200      # Máx entries por librería (evitar explotar con libs grandes)

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with get_conn_ctx(self.db_path) as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS api_entries (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    library     TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    kind        TEXT NOT NULL,
                    signature   TEXT DEFAULT '',
                    docstring   TEXT DEFAULT '',
                    return_type TEXT DEFAULT '',
                    params      TEXT DEFAULT '{}',
                    example     TEXT DEFAULT '',
                    depth       INTEGER DEFAULT 0,
                    learned_at  REAL,
                    UNIQUE(library, name)
                );
                CREATE INDEX IF NOT EXISTS idx_api_lib ON api_entries(library);
                CREATE INDEX IF NOT EXISTS idx_api_name ON api_entries(name);

                CREATE TABLE IF NOT EXISTS learned_libraries (
                    name        TEXT PRIMARY KEY,
                    version     TEXT DEFAULT '',
                    entry_count INTEGER DEFAULT 0,
                    learned_at  REAL,
                    learn_time_s REAL DEFAULT 0
                );
            """)

    # ── Aprender ─────────────────────────────────────────────────────────────

    def learn(self, library_name: str, depth: int = 2, force: bool = False) -> dict:
        """
        Aprende la API de una librería por introspección.

        Args:
            library_name: Nombre del módulo Python (ej: "requests", "nmap")
            depth: Profundidad de introspección (1=superficial, 3=profundo)
            force: Re-aprender aunque ya exista

        Returns:
            dict con estadísticas del aprendizaje
        """
        # Verificar si ya la conocemos
        if not force and self._is_learned(library_name):
            return {"status": "already_known", "library": library_name}

        t0 = time.time()
        depth = min(depth, self.MAX_DEPTH)

        try:
            mod = importlib.import_module(library_name)
        except ImportError as e:
            return {"status": "error", "library": library_name, "error": str(e)}

        version = getattr(mod, "__version__", getattr(mod, "VERSION", "unknown"))

        # Introspección recursiva
        entries: list[APIEntry] = []
        self._introspect(mod, library_name, library_name, entries, depth, current_depth=0)

        # Truncar si hay demasiadas
        if len(entries) > self.MAX_ENTRIES:
            # Priorizar: funciones y clases sobre constantes
            entries.sort(key=lambda e: (
                0 if e.kind in ("function", "class") else 1,
                -len(e.docstring),
            ))
            entries = entries[:self.MAX_ENTRIES]

        # Guardar en DB
        with get_conn_ctx(self.db_path) as c:
            # Limpiar entries anteriores si force
            if force:
                c.execute("DELETE FROM api_entries WHERE library=?", (library_name,))

            for entry in entries:
                c.execute("""
                    INSERT OR REPLACE INTO api_entries
                    (library, name, kind, signature, docstring, return_type, params, example, depth, learned_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (
                    entry.library, entry.name, entry.kind, entry.signature,
                    entry.docstring, entry.return_type, entry.params,
                    entry.example, entry.depth, entry.learned_at,
                ))

            learn_time = time.time() - t0
            c.execute("""
                INSERT OR REPLACE INTO learned_libraries (name, version, entry_count, learned_at, learn_time_s)
                VALUES (?,?,?,?,?)
            """, (library_name, str(version), len(entries), time.time(), learn_time))

        log.info("Learned %s: %d entries in %.1fs", library_name, len(entries), learn_time)
        return {
            "status": "learned",
            "library": library_name,
            "version": str(version),
            "entries": len(entries),
            "time_s": round(learn_time, 2),
        }

    def _introspect(self, obj, library: str, prefix: str,
                    entries: list[APIEntry], max_depth: int, current_depth: int) -> None:
        """Introspección recursiva de un objeto Python."""
        if current_depth > max_depth or len(entries) >= self.MAX_ENTRIES:
            return

        try:
            members = inspect.getmembers(obj)
        except Exception:
            return

        for name, value in members:
            if name.startswith("_") and not name.startswith("__init__"):
                continue  # Skip private, except __init__
            if name.startswith("__") and name != "__init__":
                continue

            full_name = f"{prefix}.{name}"

            try:
                entry = self._extract_entry(library, full_name, name, value, current_depth)
                if entry:
                    entries.append(entry)

                # Recurse into classes
                if inspect.isclass(value) and current_depth < max_depth:
                    self._introspect(value, library, full_name, entries,
                                     max_depth, current_depth + 1)

                # Recurse into submodules
                if inspect.ismodule(value) and current_depth < max_depth:
                    # Only follow submodules that belong to this library
                    mod_name = getattr(value, "__name__", "")
                    if mod_name.startswith(library):
                        self._introspect(value, library, full_name, entries,
                                         max_depth, current_depth + 1)
            except Exception:
                continue

    def _extract_entry(self, library: str, full_name: str, name: str, value, depth: int) -> Optional[APIEntry]:
        """Extrae información de un miembro del módulo."""
        kind = ""
        sig = ""
        doc = ""
        return_type = ""
        params_json = "{}"
        example = ""

        if inspect.isfunction(value) or inspect.isbuiltin(value):
            kind = "function"
        elif inspect.ismethod(value):
            kind = "method"
        elif inspect.isclass(value):
            kind = "class"
        elif inspect.ismodule(value):
            kind = "module"
        elif isinstance(value, (int, float, str, bool, list, dict, tuple)):
            kind = "constant"
        else:
            return None  # Skip complex objects

        # Signature
        if kind in ("function", "method", "class"):
            try:
                s = inspect.signature(value)
                sig = str(s)
                # Extract params
                params = {}
                for pname, param in s.parameters.items():
                    if pname == "self":
                        continue
                    p_info = {"kind": str(param.kind.name)}
                    if param.default is not inspect.Parameter.empty:
                        try:
                            p_info["default"] = repr(param.default)[:50]
                        except Exception:
                            pass  # error no crítico, continuar
                    if param.annotation is not inspect.Parameter.empty:
                        try:
                            p_info["type"] = str(param.annotation)[:50]
                        except Exception:
                            pass  # error no crítico, continuar
                    params[pname] = p_info
                params_json = json.dumps(params, ensure_ascii=False)

                # Return type
                if hasattr(s, "return_annotation") and s.return_annotation is not inspect.Parameter.empty:
                    try:
                        return_type = str(s.return_annotation)[:100]
                    except Exception:
                        pass  # error no crítico, continuar
            except (ValueError, TypeError):
                pass

        # Docstring
        raw_doc = inspect.getdoc(value) or ""
        doc = raw_doc[:self.MAX_DOCSTRING]

        # Extract example from docstring
        if ">>>" in raw_doc:
            lines = raw_doc.split("\n")
            ex_lines = [l for l in lines if l.strip().startswith(">>>")]
            example = "\n".join(ex_lines[:5])[:200]
        elif "Example" in raw_doc:
            idx = raw_doc.find("Example")
            example = raw_doc[idx:idx+200]

        # Constant value
        if kind == "constant":
            sig = repr(value)[:100]

        return APIEntry(
            library=library, name=full_name, kind=kind,
            signature=sig, docstring=doc, return_type=return_type,
            params=params_json, example=example, depth=depth,
            learned_at=time.time(),
        )

    # ── Consultar ────────────────────────────────────────────────────────────

    def lookup(self, library: str, query: str) -> list[dict]:
        """
        Busca métodos/clases de una librería por nombre parcial.

        Returns:
            Lista de dicts con name, kind, signature, docstring
        """
        with get_conn_ctx(self.db_path) as c:
            rows = c.execute("""
                SELECT name, kind, signature, docstring, return_type, params, example
                FROM api_entries
                WHERE library=? AND name LIKE ?
                ORDER BY depth ASC, name ASC
                LIMIT 20
            """, (library, f"%{query}%")).fetchall()

        return [
            {"name": r[0], "kind": r[1], "signature": r[2],
             "docstring": r[3], "return_type": r[4],
             "params": r[5], "example": r[6]}
            for r in rows
        ]

    def summarize(self, library: str) -> str:
        """Genera un resumen compacto de la librería para inyectar en prompts."""
        with get_conn_ctx(self.db_path) as c:
            lib_info = c.execute(
                "SELECT version, entry_count FROM learned_libraries WHERE name=?",
                (library,)
            ).fetchone()

            if not lib_info:
                return f"[No conozco la librería '{library}']"

            # Get top-level functions and classes
            entries = c.execute("""
                SELECT name, kind, signature, docstring
                FROM api_entries
                WHERE library=? AND depth <= 1
                ORDER BY kind ASC, name ASC
            """, (library,)).fetchall()

        lines = [f"📚 {library} v{lib_info[0]} ({lib_info[1]} APIs)"]

        classes = [e for e in entries if e[1] == "class"]
        funcs = [e for e in entries if e[1] == "function"]

        if classes:
            lines.append("Classes:")
            for name, _, sig, doc in classes[:10]:
                short_name = name.split(".")[-1]
                short_doc = (doc or "").split("\n")[0][:60]
                lines.append(f"  {short_name}{sig[:40]} — {short_doc}")

        if funcs:
            lines.append("Functions:")
            for name, _, sig, doc in funcs[:15]:
                short_name = name.split(".")[-1]
                short_doc = (doc or "").split("\n")[0][:60]
                lines.append(f"  {short_name}{sig[:50]} — {short_doc}")

        return "\n".join(lines)

    def get_for_prompt(self, library: str, task: str = "") -> str:
        """
        Genera contexto de librería optimizado para inyectar en un prompt LLM.
        Si task se proporciona, filtra por relevancia.
        """
        if task:
            # Buscar entries relevantes al task
            results = self.lookup(library, task.split()[0] if task else "")
            if not results:
                results = self.lookup(library, "")
        else:
            results = self.lookup(library, "")

        if not results:
            return ""

        lines = [f"[API Reference: {library}]"]
        for r in results[:10]:
            short = r["name"].split(".")[-1]
            lines.append(f"  {short}{r['signature'][:60]}")
            if r["docstring"]:
                lines.append(f"    {r['docstring'].split(chr(10))[0][:80]}")
        return "\n".join(lines)

    # ── Estado ───────────────────────────────────────────────────────────────

    def list_known(self) -> list[dict]:
        """Lista librerías aprendidas."""
        with get_conn_ctx(self.db_path) as c:
            rows = c.execute(
                "SELECT name, version, entry_count, learned_at FROM learned_libraries ORDER BY name"
            ).fetchall()
        return [
            {"name": r[0], "version": r[1], "entries": r[2],
             "learned_at": r[3]}
            for r in rows
        ]

    @property
    def stats(self) -> dict:
        with get_conn_ctx(self.db_path) as c:
            libs = c.execute("SELECT COUNT(*) FROM learned_libraries").fetchone()[0]
            entries = c.execute("SELECT COUNT(*) FROM api_entries").fetchone()[0]
        return {"libraries": libs, "total_entries": entries}

    def _is_learned(self, library: str) -> bool:
        with get_conn_ctx(self.db_path) as c:
            row = c.execute(
                "SELECT 1 FROM learned_libraries WHERE name=?", (library,)
            ).fetchone()
        return row is not None

    # ── Batch learn ──────────────────────────────────────────────────────────

    def learn_installed(self, max_libs: int = 20) -> list[dict]:
        """Aprende las librerías más comunes instaladas."""
        PRIORITY_LIBS = [
            "requests", "json", "os", "sys", "pathlib", "subprocess",
            "sqlite3", "hashlib", "base64", "urllib", "re",
            "asyncio", "threading", "socket", "struct",
            "playwright", "selenium", "nmap", "scapy",
            "flask", "fastapi", "pydantic",
            "numpy", "PIL", "cv2",
        ]

        results = []
        for lib in PRIORITY_LIBS[:max_libs]:
            try:
                r = self.learn(lib, depth=1)
                results.append(r)
                if r["status"] == "learned":
                    log.info("Auto-learned: %s (%d entries)", lib, r.get("entries", 0))
            except Exception as e:
                results.append({"status": "error", "library": lib, "error": str(e)})

        return results


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_instance: LibraryAutodidact | None = None

def get_autodidact() -> LibraryAutodidact:
    global _instance
    if _instance is None:
        _instance = LibraryAutodidact()
    return _instance
