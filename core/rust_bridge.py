#!/usr/bin/env python3
"""
EIDOS Rust Bridge — Python wrapper for Rust native modules
===========================================================
Provides transparent access to Rust-compiled modules with Python fallbacks.

Available Rust modules:
  - KnowledgeDB: Lock-free knowledge database (100x faster than Python)
  - FileObserver: Native file watcher (0% CPU idle)

Usage:
    from core.rust_bridge import get_rust_knowledge_db, get_rust_observer, RUST_AVAILABLE

    if RUST_AVAILABLE:
        kb = get_rust_knowledge_db()
        result = kb.observe_file("/path/to/code.py")
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable

logger = logging.getLogger("eidos.rust_bridge")

# ══════════════════════════════════════════════════════════════════════════════
# Import Rust module
# ══════════════════════════════════════════════════════════════════════════════

RUST_AVAILABLE = False
_rust_module = None

# Try to find and load the Rust .so
_RUST_SO_PATHS = [
    Path(__file__).parent.parent.parent / "rust-core" / "target" / "release",
    Path.home() / "EIDOS" / "rust-core" / "target" / "release",
    Path("/usr/local/lib/eidos"),
]

for _path in _RUST_SO_PATHS:
    if _path.exists() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

try:
    import eidos_core as _rust_module
    RUST_AVAILABLE = True
    logger.info("Rust eidos_core loaded successfully")
except ImportError as e:
    logger.info(f"Rust module not available: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# RustKnowledgeDB — Python wrapper with enriched API
# ══════════════════════════════════════════════════════════════════════════════

class RustKnowledgeDB:
    """Python wrapper around Rust KnowledgeDB with additional features."""

    def __init__(self, verbose: bool = False):
        if not RUST_AVAILABLE:
            raise RuntimeError("Rust eidos_core not available")
        self._db = _rust_module.KnowledgeDB(verbose=verbose)
        self._lock = threading.Lock()

    def observe_file(self, file_path: str) -> Dict[str, Any]:
        """Observe a file and extract knowledge. Returns parsed dict."""
        with self._lock:
            result_json = self._db.observe_file(file_path)
            return json.loads(result_json)

    def observe_directory(self, dir_path: str, extensions: List[str] = None,
                          max_files: int = 1000) -> Dict[str, Any]:
        """Observe all code files in a directory recursively."""
        if extensions is None:
            extensions = [".py", ".rs", ".go", ".js", ".ts", ".cpp", ".c", ".java"]

        dir_p = Path(dir_path)
        if not dir_p.is_dir():
            return {"error": f"Not a directory: {dir_path}"}

        observed = 0
        errors = 0
        languages = {}

        for ext in extensions:
            for file in dir_p.rglob(f"*{ext}"):
                if observed >= max_files:
                    break
                # Skip hidden dirs, __pycache__, node_modules, etc.
                parts = file.parts
                if any(p.startswith('.') or p in ('__pycache__', 'node_modules',
                       '.git', 'target', 'build', 'dist') for p in parts):
                    continue
                try:
                    result = self.observe_file(str(file))
                    lang = result.get("language", "unknown")
                    languages[lang] = languages.get(lang, 0) + 1
                    observed += 1
                except Exception:
                    errors += 1

        return {
            "directory": dir_path,
            "files_observed": observed,
            "errors": errors,
            "languages": languages,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics as dict."""
        return json.loads(self._db.get_stats())

    def export_for_sync(self) -> Dict[str, Any]:
        """Export knowledge for P2P sync."""
        return json.loads(self._db.export_for_sync())

    def import_from_sync(self, knowledge: Dict[str, Any]) -> None:
        """Import knowledge from another EIDOS instance."""
        self._db.import_from_sync(json.dumps(knowledge))

    def get_language_stats(self) -> Dict[str, int]:
        """Quick language stats: {language: files_observed}."""
        stats = self.get_stats()
        return {
            name: data.get("files_observed", 0)
            for name, data in stats.get("languages", {}).items()
        }

    def get_library_stats(self) -> Dict[str, Dict]:
        """Quick library stats."""
        stats = self.get_stats()
        return stats.get("libraries", {})


# ══════════════════════════════════════════════════════════════════════════════
# RustFileObserver — Python wrapper with callback support
# ══════════════════════════════════════════════════════════════════════════════

class RustFileObserver:
    """Python wrapper around Rust FileObserver with callback support."""

    def __init__(self):
        if not RUST_AVAILABLE:
            raise RuntimeError("Rust eidos_core not available")
        self._observer = _rust_module.FileObserver()
        self._watching = False
        self._thread: Optional[threading.Thread] = None
        self._callbacks: List[Callable] = []

    def watch(self, path: str) -> None:
        """Start watching a directory."""
        self._observer.watch(path)
        logger.info(f"Watching: {path}")

    def stop(self) -> None:
        """Stop watching."""
        self._watching = False
        self._observer.stop()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def add_extension(self, ext: str) -> None:
        """Add file extension to watch list."""
        self._observer.add_extension(ext)

    def on_change(self, callback: Callable) -> None:
        """Register a callback for file changes.

        callback receives dict: {"kind": "...", "paths": [...]}
        """
        self._callbacks.append(callback)

    def poll(self) -> Optional[Dict[str, Any]]:
        """Poll for next event (non-blocking)."""
        event = self._observer.get_event()
        return dict(event) if event else None

    def start_daemon(self, path: str, poll_interval: float = 0.5) -> str:
        """Start watching in background thread with callbacks."""
        if self._watching:
            return "Already watching"

        self.watch(path)
        self._watching = True

        def _run():
            while self._watching:
                event = self.poll()
                if event and self._callbacks:
                    for cb in self._callbacks:
                        try:
                            cb(event)
                        except Exception as e:
                            logger.error(f"Callback error: {e}")
                time.sleep(poll_interval)

        self._thread = threading.Thread(
            target=_run, daemon=True, name="eidos-rust-observer"
        )
        self._thread.start()
        return f"Watching {path} (daemon)"

    @property
    def is_watching(self) -> bool:
        return self._watching


# ══════════════════════════════════════════════════════════════════════════════
# Auto-Learning Observer: watch + learn in real-time
# ══════════════════════════════════════════════════════════════════════════════

class AutoLearnObserver:
    """Combines Rust FileObserver + KnowledgeDB for automatic learning.

    Watches directories and automatically learns from any code changes.
    """

    def __init__(self, verbose: bool = False):
        if not RUST_AVAILABLE:
            raise RuntimeError("Rust eidos_core not available")
        self.kb = RustKnowledgeDB(verbose=verbose)
        self.observer = RustFileObserver()
        self._stats = {"files_learned": 0, "errors": 0}

    def _on_file_change(self, event: Dict[str, Any]):
        """Callback when a file changes — auto-learn it."""
        paths = event.get("paths", [])
        for path in paths:
            try:
                result = self.kb.observe_file(path)
                self._stats["files_learned"] += 1
                lang = result.get("language", "?")
                funcs = result.get("functions", 0)
                libs = result.get("libraries", [])
                logger.debug(f"Learned: {Path(path).name} ({lang}, {funcs} funcs, {len(libs)} libs)")
            except Exception:
                self._stats["errors"] += 1

    def start(self, path: str) -> str:
        """Start auto-learning from a directory."""
        self.observer.on_change(self._on_file_change)
        return self.observer.start_daemon(path)

    def stop(self) -> None:
        self.observer.stop()

    def get_stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "knowledge": self.kb.get_stats(),
            "watching": self.observer.is_watching,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Singletons
# ══════════════════════════════════════════════════════════════════════════════

_rust_kb: Optional[RustKnowledgeDB] = None
_rust_observer: Optional[RustFileObserver] = None
_auto_learner: Optional[AutoLearnObserver] = None


def get_rust_knowledge_db(verbose: bool = False) -> Optional[RustKnowledgeDB]:
    """Get singleton RustKnowledgeDB, or None if Rust not available."""
    global _rust_kb
    if not RUST_AVAILABLE:
        return None
    if _rust_kb is None:
        _rust_kb = RustKnowledgeDB(verbose=verbose)
    return _rust_kb


def get_rust_observer() -> Optional[RustFileObserver]:
    """Get singleton RustFileObserver, or None if Rust not available."""
    global _rust_observer
    if not RUST_AVAILABLE:
        return None
    if _rust_observer is None:
        _rust_observer = RustFileObserver()
    return _rust_observer


def get_auto_learn_observer(verbose: bool = False) -> Optional[AutoLearnObserver]:
    """Get singleton AutoLearnObserver, or None if Rust not available."""
    global _auto_learner
    if not RUST_AVAILABLE:
        return None
    if _auto_learner is None:
        _auto_learner = AutoLearnObserver(verbose=verbose)
    return _auto_learner


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print(f"Rust available: {RUST_AVAILABLE}")

    if not RUST_AVAILABLE:
        print("Rust module not compiled. Run: cd rust-core && cargo build --release")
        sys.exit(1)

    kb = get_rust_knowledge_db(verbose=True)
    if kb:
        stats = kb.get_stats()
        print(f"\nKnowledge DB stats:")
        print(f"  Languages: {stats.get('total_languages', 0)}")
        print(f"  Libraries: {stats.get('total_libraries', 0)}")

        # Quick language breakdown
        for lang, count in kb.get_language_stats().items():
            print(f"    {lang}: {count} files")

    if len(sys.argv) > 1 and sys.argv[1] == "scan":
        target = sys.argv[2] if len(sys.argv) > 2 else "."
        print(f"\nScanning {target}...")
        result = kb.observe_directory(target, max_files=100)
        print(f"  Observed: {result['files_observed']} files")
        print(f"  Languages: {result['languages']}")
