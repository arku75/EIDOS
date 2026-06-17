"""
EIDOS File Watcher — Detecta archivos nuevos y los envía al document reader.

Observa directorios en busca de archivos .pdf/.epub/.docx/.md y al detectar
uno nuevo, lo pasa a eidos_document_reader para su lectura y persistencia
en el grafo de conocimiento.

Usa polling ligero (inotify no siempre disponible en todos los entornos).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("eidos.file_watcher")

WATCH_EXTENSIONS = {".pdf", ".epub", ".docx", ".md"}


class FileWatcher:
    """Observa directorios locales y reacciona a archivos nuevos de documentos."""

    def __init__(self, watch_dirs=None):
        if watch_dirs is None:
            watch_dirs = [
                str(Path.home() / "Descargas"),
                str(Path.home() / "Documents"),
                str(Path.home() / "EIDOS"),
            ]
        self.watch_dirs = [d for d in watch_dirs if os.path.isdir(d)]
        self._seen = set()       # paths ya procesados
        self._running = False
        self._thread = None
        self._poll_every = 5     # segundos entre escaneos

    def _scan(self):
        """Escanea los directorios en busca de archivos nuevos."""
        for d in self.watch_dirs:
            try:
                for entry in os.scandir(d):
                    if entry.is_file():
                        ext = Path(entry.name).suffix.lower()
                        if ext in WATCH_EXTENSIONS:
                            path = entry.path
                            if path not in self._seen:
                                self._seen.add(path)
                                self._on_new_file(path)
            except Exception as e:
                log.debug("FileWatcher scan error in %s: %s", d, e)

    def _on_new_file(self, path: str):
        """Callback cuando se detecta un archivo nuevo de documento.

        Llama a eidos_document_reader para leerlo y persistir su contenido
        en el grafo de conocimiento."""
        try:
            from core.eidos_document_reader import get_document_reader
            reader = get_document_reader()
            result = reader.read_document(path)
            status = result.get("status", "?") if isinstance(result, dict) else "ok"
            log.info("FileWatcher: leído '%s' → %s", path, status)
        except Exception as e:
            log.debug("FileWatcher: eidos_document_reader no disponible "
                      "para '%s': %s", path, e)

    def start(self):
        """Arranca el watcher en un thread daemon."""
        if self._running:
            return
        self._running = True
        self._scan()  # escaneo inicial para detectar lo que ya existe
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="eidos-file-watcher")
        self._thread.start()
        log.info("FileWatcher iniciado en %d directorios", len(self.watch_dirs))

    def _loop(self):
        """Bucle de escaneo periódico."""
        while self._running:
            try:
                self._scan()
            except Exception as e:
                log.debug("FileWatcher loop error: %s", e)
            time.sleep(self._poll_every)

    def stop(self):
        """Detiene el watcher."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        log.info("FileWatcher detenido")


_SINGLETON = None


def get_file_watcher() -> FileWatcher:
    """Singleton del FileWatcher."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = FileWatcher()
    return _SINGLETON


def start():
    """Arranca el watcher global."""
    get_file_watcher().start()


def stop():
    """Detiene el watcher global."""
    if _SINGLETON is not None:
        _SINGLETON.stop()
