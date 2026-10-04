"""
core/eidos_chroma_server.py — Microservicio ChromaDB standalone (S83)

Servidor HTTP mínimo que envuelve chromadb PersistentClient en su propio proceso.
Esto elimina los SEGV por thread-safety de los Rust bindings (chromadb NO es
thread-safe). Al correr en proceso propio, solo un hilo accede a chromadb.

API HTTP (v1, compatible con chromadb.HttpClient):
    GET  /api/v1/heartbeat
    POST /api/v1/collections
    GET  /api/v1/collections/<name>
    POST /api/v1/collections/<id>/add
    POST /api/v1/collections/<id>/query
    GET  /api/v1/collections/<id>/count

Uso:
    python3 core/eidos_chroma_server.py --port 8767
"""

from __future__ import annotations

import json
import logging
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

log = logging.getLogger("eidos.chroma_server")

CHROMA_DIR = Path.home() / ".eidos" / "chroma"
COLLECTION_NAME = "eidos_knowledge"

# Inicialización lazy (solo cuando llega la primera petición)
_client = None
_collection = None


def _init_chroma():
    global _client, _collection
    if _client is not None:
        return
    import chromadb
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    _collection = _client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    log.info("ChromaDB microservicio: %d vectores en '%s'",
             _collection.count(), COLLECTION_NAME)


class ChromaHandler(BaseHTTPRequestHandler):
    """Handler HTTP mínimo para operaciones ChromaDB."""

    def log_message(self, fmt, *args):
        log.debug("HTTP %s", fmt % args)

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> Dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        path = urlparse(self.path).path.strip("/")
        parts = path.split("/")

        try:
            _init_chroma()

            if path == "api/v1/heartbeat":
                self._send_json({"heartbeat": int(__import__('time').time() * 1e9)})

            elif parts[:3] == ["api", "v1", "collections"] and len(parts) == 4:
                name = parts[3]
                if name == COLLECTION_NAME:
                    self._send_json({
                        "id": str(_collection.id),
                        "name": _collection.name,
                        "metadata": _collection.metadata,
                    })
                else:
                    self._send_json({"error": "Not found"}, 404)

            elif len(parts) == 5 and parts[3] == COLLECTION_NAME and parts[4] == "count":
                self._send_json({"count": _collection.count()})

            else:
                self._send_json({"error": "Not found"}, 404)

        except Exception as e:
            log.exception("GET error: %s", e)
            self._send_json({"error": str(e)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path.strip("/")
        parts = path.split("/")

        try:
            _init_chroma()
            data = self._read_body()

            if parts[:3] == ["api", "v1", "collections"] and len(parts) == 5:
                # POST /api/v1/collections/<name>/add
                if parts[4] == "add":
                    ids = data.get("ids", [])
                    embeddings = data.get("embeddings")
                    documents = data.get("documents", [])
                    metadatas = data.get("metadatas")

                    if ids:
                        _collection.add(
                            ids=ids,
                            embeddings=embeddings,
                            documents=documents,
                            metadatas=metadatas,
                        )
                    self._send_json({"added": len(ids)})

                # POST /api/v1/collections/<name>/query
                elif parts[4] == "query":
                    query_embeddings = data.get("query_embeddings")
                    n_results = data.get("n_results", 10)
                    results = _collection.query(
                        query_embeddings=query_embeddings,
                        n_results=n_results,
                    )
                    self._send_json(results)

                # POST /api/v1/collections/<name>/upsert
                elif parts[4] == "upsert":
                    ids = data.get("ids", [])
                    embeddings = data.get("embeddings")
                    documents = data.get("documents", [])
                    metadatas = data.get("metadatas")
                    if ids:
                        _collection.upsert(
                            ids=ids,
                            embeddings=embeddings,
                            documents=documents,
                            metadatas=metadatas,
                        )
                    self._send_json({"upserted": len(ids)})

                # POST /api/v1/collections/<name>/delete
                elif parts[4] == "delete":
                    ids = data.get("ids")
                    _collection.delete(ids=ids)
                    self._send_json({"deleted": len(ids) if ids else 0})

                else:
                    self._send_json({"error": f"Unknown action: {parts[4]}"}, 404)

            elif parts[:3] == ["api", "v1", "collections"] and len(parts) == 3:
                # Crear colección
                name = data.get("name", COLLECTION_NAME)
                metadata = data.get("metadata", {})
                col = _client.get_or_create_collection(name=name, metadata=metadata)
                self._send_json({
                    "id": str(col.id),
                    "name": col.name,
                    "metadata": col.metadata,
                })

            else:
                self._send_json({"error": "Not found"}, 404)

        except Exception as e:
            log.exception("POST error: %s", e)
            self._send_json({"error": str(e)}, 500)


def main():
    import argparse
    p = argparse.ArgumentParser(description="EIDOS ChromaDB Microservice")
    p.add_argument("--port", type=int, default=int(os.environ.get("EIDOS_CHROMA_PORT", "8767")))
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    server = HTTPServer((args.host, args.port), ChromaHandler)
    log.info("ChromaDB microservicio en http://%s:%d (pid=%d)",
             args.host, args.port, os.getpid())

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("ChromaDB microservicio detenido")
        server.server_close()


if __name__ == "__main__":
    main()
