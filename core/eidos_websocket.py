"""
core/eidos_websocket.py — WebSocket Server para streaming en tiempo real (S82)

Basado en eidos_api.py de SER (NO TOCAR). Servidor WebSocket independiente
que emite el estado de EIDOS en tiempo real a clientes conectados.

Usa asyncio + websockets (stdlib-friendly). Corre en puerto 8004.

API:
    python3 -m core.eidos_websocket          # Inicia server en :8004
    ws://localhost:8004/stream               # Stream de eventos en tiempo real
    ws://localhost:8004/dashboard            # Dashboard data cada 2s
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set

log = logging.getLogger("eidos.websocket")

WS_HOST = "127.0.0.1"
WS_PORT = 8004

try:
    import websockets
    from websockets.server import serve as ws_serve
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

CLIENTS: Set = set()
_started = False


def _get_state() -> Dict[str, Any]:
    """Recolecta estado actual de EIDOS para broadcast."""
    state = {"ts": time.time()}

    # Identity
    try:
        from core.eidos_identity import get_identity
        ind = get_identity().independence()
        state["independence_pct"] = ind.get("independence_pct", 0)
    except Exception:
        state["independence_pct"] = 0

    # Mood
    try:
        from core.eidos_affect import get_affect
        aff = get_affect()
        state["mood"] = aff.state.mood
        state["vad"] = list(aff.vad_tuple())
    except Exception:
        state["mood"] = "?"
        state["vad"] = [0.5, 0.5, 0.5]

    # Vivo
    try:
        vivo = Path.home() / ".eidos" / "vivo_status.json"
        if vivo.exists():
            vs = json.loads(vivo.read_text())
            state["cycle"] = vs.get("cycle", 0)
            state["last_cycle_ago_s"] = vs.get("last_cycle_ago_s", 0)
    except Exception:
        pass

    # Events recientes
    try:
        from core.eidos_events import get_event_bus
        recent = get_event_bus().recent(5)
        state["recent_events"] = [
            {"type": e.type, "ts": e.timestamp, "source": e.source}
            for e in recent
        ]
    except Exception:
        state["recent_events"] = []

    return state


async def _handler(websocket):
    """Maneja una conexión WebSocket."""
    CLIENTS.add(websocket)
    remote = websocket.remote_address
    log.info("WS client connected: %s (total: %d)", remote, len(CLIENTS))
    try:
        # Enviar estado inicial
        await websocket.send(json.dumps({
            "type": "connected",
            "message": "Conectado a EIDOS WebSocket",
            "state": _get_state(),
        }))

        # Esperar mensajes del cliente
        async for message in websocket:
            try:
                data = json.loads(message)
                cmd = data.get("command", "")

                if cmd == "ping":
                    await websocket.send(json.dumps({"type": "pong"}))
                elif cmd == "state":
                    await websocket.send(json.dumps({
                        "type": "state", "data": _get_state()}))
                elif cmd == "dashboard":
                    from core.eidos_dashboard import get_dashboard_data
                    await websocket.send(json.dumps({
                        "type": "dashboard", "data": get_dashboard_data()}))
                elif cmd == "subscribe":
                    event_type = data.get("event", "*")
                    await websocket.send(json.dumps({
                        "type": "subscribed", "event": event_type}))
                else:
                    await websocket.send(json.dumps({
                        "type": "error", "message": f"Comando desconocido: {cmd}"}))
            except json.JSONDecodeError:
                await websocket.send(json.dumps({
                    "type": "error", "message": "JSON inválido"}))
    except Exception as e:
        log.debug("WS handler error: %s", e)
    finally:
        CLIENTS.discard(websocket)
        log.info("WS client disconnected: %s (total: %d)", remote, len(CLIENTS))


async def _broadcast_loop():
    """Emite estado a todos los clientes cada 5 segundos."""
    while _started:
        await asyncio.sleep(5)
        if CLIENTS:
            state = _get_state()
            msg = json.dumps({"type": "heartbeat", "state": state})
            # Enviar a todos, ignorar desconectados
            disconnected = set()
            for client in CLIENTS.copy():
                try:
                    await client.send(msg)
                except Exception:
                    disconnected.add(client)
            CLIENTS.difference_update(disconnected)


async def _start_server():
    """Inicia el servidor WebSocket."""
    global _started
    _started = True
    log.info("WebSocket server en ws://%s:%d", WS_HOST, WS_PORT)

    async with ws_serve(_handler, WS_HOST, WS_PORT):
        # También correr broadcast loop
        await _broadcast_loop()


def start_websocket_server():
    """Inicia el servidor WebSocket en un thread separado."""
    if not HAS_WEBSOCKETS:
        log.warning("websockets no instalado. pip install websockets")
        return None

    import threading

    def _run():
        asyncio.run(_start_server())

    t = threading.Thread(target=_run, name="eidos-ws", daemon=True)
    t.start()
    log.info("WebSocket server iniciado en background")
    return t


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not HAS_WEBSOCKETS:
        print("ERROR: websockets no instalado. pip install websockets")
        exit(1)

    print(f"🧬 EIDOS WebSocket Server — ws://{WS_HOST}:{WS_PORT}")
    print(f"   Conectar: websocat ws://{WS_HOST}:{WS_PORT}")
    print(f"   Comandos: ping, state, dashboard, subscribe")
    asyncio.run(_start_server())
