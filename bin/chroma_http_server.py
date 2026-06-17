#!/usr/bin/env python3
"""
chroma_http_server.py — Microservicio HTTP ChromaDB [S94]
==========================================================

Servidor HTTP minimalista que expone ChromaDB vía REST para que el bridge
y otros procesos multi-threaded accedan sin riesgo SEGV (chromadb Rust
bindings no son thread-safe).

El proceso es single-threaded (HTTP stdlib), serializando naturalmente
todas las operaciones contra ChromaDB.

Endpoints (compatibles con colony_chroma._try_http_init):
  GET  /api/v1/heartbeat                          → {"heartbeat": "ok"}
  GET  /api/v1/collections/<name>                  → collection info
  GET  /api/v1/collections/<name>/count            → vector count
  POST /api/v1/collections/<name>/upsert           → upsert documents
  POST /api/v1/collections/<name>/query            → query documents

Uso:
  python3 bin/chroma_http_server.py --port 8767
"""

from __future__ import annotations

import json
import logging
import os
import sys
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional

# Añadir EIDOS al path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [chroma-http] %(levelname)s %(message)s",
)
log = logging.getLogger("chroma_http")

# ── ChromaMemory singleton (inicializado lazy) ──────────────────────────────
_chroma = None


def _get_chroma():
    """Inicializa ChromaMemory sin intentar HTTP (evitar circular)."""
    global _chroma
    if _chroma is None:
        # Forzar modo embedded (no intentar HTTP a nosotros mismos)
        os.environ.setdefault("EIDOS_CHROMA_EMBEDDED", "1")
        from core.colony_chroma import ChromaMemory
        _chroma = ChromaMemory()
        log.info("ChromaMemory inicializado (embedded): ready=%s count=%d",
                 _chroma.is_ready(), _chroma.count())
    return _chroma


# ── HTTP Handler ────────────────────────────────────────────────────────────

class ChromaHandler(BaseHTTPRequestHandler):
    """Maneja requests REST para ChromaDB."""

    def log_message(self, fmt, *args):
        """Suprimir logs HTTP por defecto (muy ruidosos)."""
        log.debug("HTTP %s", fmt % args)

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw)

    def _parse_path(self):
        """Devuelve (collection_name, action) desde el path."""
        path = self.path.rstrip("/")
        # /api/v1/heartbeat
        if path == "/api/v1/heartbeat":
            return None, "heartbeat"
        # /api/v1/collections/<name>/count
        # /api/v1/collections/<name>/upsert
        # /api/v1/collections/<name>/query
        # /api/v1/collections/<name>
        parts = path.split("/")
        if len(parts) >= 5 and parts[1:4] == ["api", "v1", "collections"]:
            name = parts[4]
            action = parts[5] if len(parts) > 5 else "info"
            return name, action
        return None, None

    # ── Routing ──────────────────────────────────────────────────────────

    def do_GET(self):
        col, action = self._parse_path()

        if action == "heartbeat":
            self._send_json({"heartbeat": "ok", "ts": __import__("time").time()})
            return

        chroma = _get_chroma()
        if not chroma.is_ready():
            self._send_json({"error": "ChromaDB not ready"}, status=503)
            return

        if action == "info":
            self._send_json({
                "name": col,
                "count": chroma.count(),
                "ready": chroma.is_ready(),
            })
        elif action == "count":
            self._send_json({"count": chroma.count()})
        else:
            self._send_json({"error": f"Unknown action: {action}"}, status=404)

    def do_POST(self):
        col, action = self._parse_path()

        chroma = _get_chroma()
        if not chroma.is_ready():
            self._send_json({"error": "ChromaDB not ready"}, status=503)
            return

        data = self._read_json()

        if action == "upsert":
            ids = data.get("ids", [])
            docs = data.get("documents", [])
            metas = data.get("metadatas", [])
            embs = data.get("embeddings")

            if not ids:
                self._send_json({"error": "ids required"}, status=400)
                return

            # Usar accesso directo a _collection para upsert
            try:
                with chroma._lock:
                    kwargs = {"ids": ids, "documents": docs, "metadatas": metas}
                    if embs:
                        kwargs["embeddings"] = embs
                    chroma._collection.upsert(**kwargs)
                self._send_json({"status": "ok", "count": len(ids)})
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)

        elif action == "query":
            query_embs = data.get("query_embeddings")
            n_results = data.get("n_results", 5)

            if not query_embs:
                self._send_json({"error": "query_embeddings required"}, status=400)
                return

            try:
                with chroma._lock:
                    results = chroma._collection.query(
                        query_embeddings=query_embs,
                        n_results=n_results,
                        include=["documents", "metadatas", "distances"],
                    )
                self._send_json({
                    "ids": results.get("ids", [[]]),
                    "documents": results.get("documents", [[]]),
                    "distances": results.get("distances", [[]]),
                    "metadatas": results.get("metadatas", [[]]),
                })
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)

        else:
            self._send_json({"error": f"Unknown action: {action}"}, status=404)


def main():
    parser = argparse.ArgumentParser(description="ChromaDB HTTP microservicio")
    parser.add_argument("--port", type=int, default=8767, help="Puerto HTTP (default: 8767)")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), ChromaHandler)
    log.info("ChromaDB HTTP microservicio en http://%s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Detenido por señal")
        server.shutdown()


if __name__ == "__main__":
    main()
