"""
core/eidos_gateway.py — EIDOS Unified API Gateway

Single entry point that routes ALL EIDOS traffic through one port.
HTTP reverse proxy pattern: receives requests and forwards them to the
appropriate backend. Replaces the old scattered-port architecture.

Route Table:
    /health              → Aggregated health from ALL backends
    /health/<service>    → Health of a single backend
    /colony/<path>       → Colony Dashboard      (:7777)
    /panel/<path>        → Web Panel             (:8080)
    /vscode/<path>       → VSCode API Server     (:8765)
    /trinity/<path>      → Trinity Agent Server  (:8001)
    /chroma/<path>       → ChromaDB microservice (:8767)
    /ollama/<path>       → Ollama LLM Server     (:11434)
    /<path>              → Bridge-to-EIDOS       (:18003, catch-all)

Features:
    - Service discovery: auto-detect which backends are running on startup
    - Periodic health re-check every 60 seconds
    - Single config: all backend addresses defined in one place
    - Graceful degradation: marks unavailable backends as "down", keeps running

Usage:
    python core/eidos_gateway.py
    # Listens on :8003 by default (EIDOS_GATEWAY_PORT env override)
    # Bridge must run on :18003 (EIDOS_BRIDGE_PORT env override)

Environment variables:
    EIDOS_GATEWAY_PORT  — gateway listen port (default: 8003)
    EIDOS_GATEWAY_HOST  — gateway bind address (default: 127.0.0.1)
    EIDOS_BRIDGE_PORT   — bridge internal port (default: 18003)
    EIDOS_COLONY_PORT   — colony dashboard port (default: 7777)
    EIDOS_PANEL_PORT    — web panel port (default: 8080)
    EIDOS_VSCODE_PORT   — VSCode API port (default: 8765)
    EIDOS_TRINITY_PORT  — Trinity server port (default: 8001)
    EIDOS_CHROMA_PORT   — ChromaDB port (default: 8767)
    EIDOS_OLLAMA_PORT   — Ollama port (default: 11434)
"""
from __future__ import annotations

import json
import os
import sys
import time
import threading
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests

# Flask is already proven available (bridge uses it)
from flask import Flask, request, Response, jsonify

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [gateway] %(levelname)s %(message)s",
)
log = logging.getLogger("eidos_gateway")

# ── Flask App ────────────────────────────────────────────────────────────────
app = Flask("eidos_gateway")

# Disable Flask request logging noise (we log our own)
log_flask = logging.getLogger("werkzeug")
log_flask.setLevel(logging.WARNING)


# ── Configuration ────────────────────────────────────────────────────────────
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (ValueError, TypeError):
        return default


GATEWAY_HOST = os.environ.get("EIDOS_GATEWAY_HOST", "127.0.0.1")
GATEWAY_PORT = _env_int("EIDOS_GATEWAY_PORT", 8003)

# Backend addresses — ONE place to see what runs where
BACKENDS = {
    "bridge": {
        "host": "127.0.0.1",
        "port": _env_int("EIDOS_BRIDGE_PORT", 18003),
        "health_path": "/health",
        "label": "Bridge-to-EIDOS",
    },
    "colony": {
        "host": "127.0.0.1",
        "port": _env_int("EIDOS_COLONY_PORT", 7777),
        "health_path": "/api/status",
        "label": "Colony Dashboard",
    },
    "panel": {
        "host": "127.0.0.1",
        "port": _env_int("EIDOS_PANEL_PORT", 8080),
        "health_path": "/",
        "label": "Web Panel",
    },
    "vscode": {
        "host": "127.0.0.1",
        "port": _env_int("EIDOS_VSCODE_PORT", 8765),
        "health_path": "/api/status",
        "label": "VSCode API",
    },
    "trinity": {
        "host": "127.0.0.1",
        "port": _env_int("EIDOS_TRINITY_PORT", 8001),
        "health_path": "/health",
        "label": "Trinity Agent",
    },
    "chromadb": {
        "host": "127.0.0.1",
        "port": _env_int("EIDOS_CHROMA_PORT", 8767),
        "health_path": "/api/v1/heartbeat",
        "label": "ChromaDB",
    },
    "ollama": {
        "host": "127.0.0.1",
        "port": _env_int("EIDOS_OLLAMA_PORT", 11434),
        "health_path": "/api/tags",
        "label": "Ollama LLM",
    },
}

# How long to wait for a backend health check (seconds)
HEALTH_TIMEOUT = 3.0

# ── Service Discovery ────────────────────────────────────────────────────────
# Tracks which backends are currently reachable
_service_status: Dict[str, str] = {}  # name → "ok" | "down"
_service_lock = threading.Lock()


def _backend_url(name: str, path: str = "") -> str:
    """Build full URL for a backend."""
    cfg = BACKENDS[name]
    base = f"http://{cfg['host']}:{cfg['port']}{cfg['health_path']}"
    if path:
        base = f"http://{cfg['host']}:{cfg['port']}{path}"
    return base


def _check_backend(name: str) -> Tuple[bool, str]:
    """Check if a single backend is reachable. Returns (ok, detail)."""
    cfg = BACKENDS[name]
    url = f"http://{cfg['host']}:{cfg['port']}{cfg['health_path']}"
    try:
        r = requests.get(url, timeout=HEALTH_TIMEOUT)
        if r.status_code < 500:
            return True, f"HTTP {r.status_code}"
        return False, f"HTTP {r.status_code}"
    except requests.exceptions.ConnectionError:
        return False, "connection refused"
    except requests.exceptions.Timeout:
        return False, "timeout"
    except Exception as e:
        return False, str(type(e).__name__)


def discover_services() -> Dict[str, str]:
    """Check all backends and update service status. Returns status dict."""
    global _service_status
    new_status = {}
    for name in BACKENDS:
        ok, detail = _check_backend(name)
        new_status[name] = "ok" if ok else "down"
        prev = _service_status.get(name, "")
        if prev != new_status[name]:
            arrow = "[green]" if ok else "[yellow]"
            log.info(
                "Service %s: %-8s %-20s  %s",
                arrow,
                new_status[name],
                BACKENDS[name]["label"],
                f"({detail})" if not ok else "",
            )
    with _service_lock:
        _service_status = new_status
    return dict(new_status)


def get_service_status() -> Dict[str, str]:
    """Thread-safe read of current service status."""
    with _service_lock:
        return dict(_service_status)


def _discovery_loop():
    """Background thread: re-check backends every 60 seconds."""
    while True:
        time.sleep(60)
        try:
            discover_services()
        except Exception as e:
            log.error("Discovery loop error: %s", e)


# ── HTTP Proxy Helper ────────────────────────────────────────────────────────
# Methods that can carry a body (forward them as-is)
_BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Headers to strip when proxying (hop-by-hop)
_HOP_HEADERS = {
    "host",
    "connection",
    "keep-alive",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "proxy-authorization",
    "proxy-authenticate",
}


def _proxy_request(target_url: str) -> Response:
    """Forward the current Flask request to target_url and return the response."""
    method = request.method.upper()

    # Build headers to forward (keep content-type, auth, etc.)
    forward_headers = {}
    for key, value in request.headers:
        if key.lower() not in _HOP_HEADERS:
            forward_headers[key] = value

    # Make the proxied request
    try:
        if method in _BODY_METHODS:
            # Forward with body
            body = request.get_data()
            upstream = requests.request(
                method=method,
                url=target_url,
                headers=forward_headers,
                data=body,
                timeout=60,
                allow_redirects=False,
            )
        else:
            # Forward without body
            upstream = requests.request(
                method=method,
                url=target_url,
                headers=forward_headers,
                timeout=60,
                allow_redirects=False,
            )
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "backend unreachable", "url": target_url}), 502
    except requests.exceptions.Timeout:
        return jsonify({"error": "backend timeout", "url": target_url}), 504
    except Exception as e:
        log.error("Proxy error to %s: %s", target_url, e)
        return jsonify({"error": "proxy error", "detail": str(e)}), 502

    # Build response, stripping hop-by-hop response headers
    resp_headers = {}
    for key, value in upstream.headers.items():
        if key.lower() not in _HOP_HEADERS:
            resp_headers[key] = value

    return Response(
        upstream.content,
        status=upstream.status_code,
        headers=resp_headers,
    )


def _proxy_to_backend(backend_name: str, path: str = "") -> Response:
    """Forward request to a named backend, optionally appending a path."""
    cfg = BACKENDS[backend_name]
    base = f"http://{cfg['host']}:{cfg['port']}"
    if path:
        target = f"{base}/{path.lstrip('/')}"
    else:
        target = base
    return _proxy_request(target)


def _proxy_to_bridge(path: str = "") -> Response:
    """Forward request to the bridge backend (default catch-all)."""
    return _proxy_to_backend("bridge", path)


# ── Route: Aggregated Health ─────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health_all():
    """Aggregated health check — returns status of ALL backends."""
    status = get_service_status()
    # If we haven't discovered yet, do a quick synchronous check
    if not status:
        status = discover_services()

    all_ok = all(v == "ok" for v in status.values())
    return jsonify(status), 200 if all_ok else 200  # Always 200, body tells truth


@app.route("/health/<service>", methods=["GET"])
def health_single(service: str):
    """Health check for a single backend."""
    if service not in BACKENDS:
        return jsonify({"error": "unknown service", "service": service}), 404
    ok, detail = _check_backend(service)
    cfg = BACKENDS[service]
    return jsonify({
        "service": service,
        "label": cfg["label"],
        "status": "ok" if ok else "down",
        "detail": detail,
        "url": f"http://{cfg['host']}:{cfg['port']}",
    })


# ── Route: Colony Dashboard (:7777) ──────────────────────────────────────────
@app.route("/colony/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@app.route("/colony/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def route_colony(path: str):
    """Proxy requests to Colony Dashboard (:7777)."""
    return _proxy_to_backend("colony", path)


# ── Route: Web Panel (:8080) ─────────────────────────────────────────────────
@app.route("/panel/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@app.route("/panel/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def route_panel(path: str):
    """Proxy requests to Web Panel (:8080)."""
    return _proxy_to_backend("panel", path)


# ── Route: VSCode API (:8765) ────────────────────────────────────────────────
@app.route("/vscode/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@app.route("/vscode/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def route_vscode(path: str):
    """Proxy requests to VSCode API Server (:8765)."""
    return _proxy_to_backend("vscode", path)


# ── Route: Trinity Agent (:8001) ─────────────────────────────────────────────
@app.route("/trinity/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@app.route("/trinity/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def route_trinity(path: str):
    """Proxy requests to Trinity Agent Server (:8001)."""
    return _proxy_to_backend("trinity", path)


# ── Route: ChromaDB (:8767) ──────────────────────────────────────────────────
@app.route("/chroma/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@app.route("/chroma/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def route_chroma(path: str):
    """Proxy requests to ChromaDB microservice (:8767)."""
    return _proxy_to_backend("chromadb", path)


# ── Route: Ollama (:11434) ───────────────────────────────────────────────────
@app.route("/ollama/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@app.route("/ollama/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def route_ollama(path: str):
    """Proxy requests to Ollama LLM Server (:11434)."""
    return _proxy_to_backend("ollama", path)


# ── Route: Catch-all → Bridge (:18003) ───────────────────────────────────────
@app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def route_bridge(path: str):
    """Catch-all: forward all other requests to Bridge-to-EIDOS."""
    return _proxy_to_bridge(path)


# ── Startup / Main ───────────────────────────────────────────────────────────
def run_gateway(port: int = None, host: str = None):
    """Start the unified API gateway.

    Args:
        port: Override listen port (default from EIDOS_GATEWAY_PORT env or 8003)
        host: Override bind address (default from EIDOS_GATEWAY_HOST env or 127.0.0.1)
    """
    if port is None:
        port = GATEWAY_PORT
    if host is None:
        host = GATEWAY_HOST

    log.info("=" * 60)
    log.info("EIDOS Unified API Gateway starting")
    log.info("=" * 60)

    # Print backend registry
    log.info("Backend registry:")
    for name, cfg in BACKENDS.items():
        log.info(
            "  %-10s → %s:%s  %s",
            name,
            cfg["host"],
            cfg["port"],
            cfg["label"],
        )

    # Run initial service discovery
    log.info("Running initial service discovery...")
    status = discover_services()
    up_count = sum(1 for v in status.values() if v == "ok")
    log.info(
        "Discovery complete: %d/%d backends reachable",
        up_count,
        len(status),
    )
    for name, state in sorted(status.items()):
        marker = "[OK]" if state == "ok" else "[DOWN]"
        log.info("  %s %s (%s)", marker, name, BACKENDS[name]["label"])

    if up_count == 0:
        log.warning(
            "No backends reachable — gateway will start but all routes will 502"
        )

    # Start background discovery thread
    discovery_thread = threading.Thread(
        target=_discovery_loop,
        daemon=True,
        name="gateway-discovery",
    )
    discovery_thread.start()
    log.info("Background discovery thread started (interval: 60s)")

    # Print route summary
    log.info("-" * 60)
    log.info("Route table (all paths relative to http://%s:%s):", host, port)
    log.info("  /health             → Aggregated health (all backends)")
    log.info("  /health/<service>   → Single backend health")
    log.info("  /colony/<path>      → Colony Dashboard (:7777)")
    log.info("  /panel/<path>       → Web Panel (:8080)")
    log.info("  /vscode/<path>      → VSCode API (:8765)")
    log.info("  /trinity/<path>     → Trinity Agent (:8001)")
    log.info("  /chroma/<path>      → ChromaDB (:8767)")
    log.info("  /ollama/<path>      → Ollama LLM (:11434)")
    log.info("  /<path>             → Bridge-to-EIDOS (catch-all, :%s)", BACKENDS["bridge"]["port"])
    log.info("-" * 60)
    log.info("Gateway listening on http://%s:%s", host, port)

    # Start Flask
    app.run(host=host, port=port, threaded=True, debug=False)


if __name__ == "__main__":
    run_gateway()
