"""
core/realtime_monitor.py — EIDOS emite eventos en tiempo real via WebSocket.

Claude (y SER desde cualquier browser) puede conectarse a ws://localhost:8004
y ver todo lo que hace EIDOS: mensajes Colony, acciones GUI, visión, errores.

EIDOS también puede recibir mensajes desde el monitor → feedback en tiempo real.

Uso:
  python3 core/realtime_monitor.py          → servidor WebSocket en :8004
  eidos monitor                             → lo arranca via CLI
  ws://localhost:8004                       → conectar con cualquier cliente WS

Desde Claude: via endpoint /monitor en claude_bridge (:8003)
"""
import asyncio
import json
import logging
import time
import threading
import queue
import os
from pathlib import Path
from typing import Dict, Any, Set, Optional
from core.db import get_conn

log = logging.getLogger("realtime_monitor")

MONITOR_PORT = 8004
EIDOS_HOME   = Path.home() / ".eidos"

# Cola global de eventos (thread-safe)
_event_queue: queue.Queue = queue.Queue(maxsize=500)
_clients: Set = set()
_monitor_thread: Optional[threading.Thread] = None
_loop: Optional[asyncio.AbstractEventLoop] = None


# ── API pública — EIDOS llama estas funciones desde cualquier módulo ──────────

def emit(event_type: str, data: Any, source: str = "eidos"):
    """
    Emite un evento al monitor en tiempo real.
    Non-blocking: si la cola está llena, descarta el evento.
    """
    event = {
        "type":      event_type,
        "source":    source,
        "data":      data,
        "timestamp": time.strftime("%H:%M:%S"),
        "ts":        time.time()
    }
    try:
        _event_queue.put_nowait(event)
    except queue.Full:
        pass  # Nunca bloquear el hilo principal


def emit_colony(character: str, message: str, response: str = ""):
    """Emite un intercambio Colony."""
    emit("colony_message", {
        "character": character,
        "message":   message[:500],
        "response":  response[:500]
    }, source="colony")


def emit_action(action_type: str, target: str, result: str = ""):
    """Emite una acción GUI o shell."""
    emit("action", {
        "action": action_type,
        "target": target[:200],
        "result": result[:200]
    }, source="action")


def emit_vision(description: str, question: str = ""):
    """Emite lo que EIDOS ve en la pantalla."""
    emit("vision", {
        "description": description[:800],
        "question":    question
    }, source="vision")


def emit_error(error: str, module: str = ""):
    """Emite un error para que Claude/SER pueda corregir."""
    emit("error", {
        "error":  error[:500],
        "module": module
    }, source="error")


def emit_service(name: str, status: str, port: int = 0):
    """Emite estado de un servicio."""
    emit("service_status", {
        "service": name,
        "status":  status,
        "port":    port
    }, source="watchdog")


def emit_brain(concept: str, category: str, confidence: float):
    """Emite cuando EIDOS aprende algo nuevo."""
    emit("brain_update", {
        "concept":    concept[:100],
        "category":   category,
        "confidence": confidence
    }, source="brain")


def emit_goal(goal_id: str, title: str, progress: str):
    """Emite progreso en una meta."""
    emit("goal_progress", {
        "id":       goal_id,
        "title":    title[:100],
        "progress": progress[:200]
    }, source="goals")


# ── Servidor WebSocket ─────────────────────────────────────────────────────────

async def _ws_handler(websocket):
    """Maneja cada cliente WebSocket conectado."""
    _clients.add(websocket)
    client_addr = websocket.remote_address if hasattr(websocket, 'remote_address') else "unknown"
    log.info(f"Monitor: cliente conectado desde {client_addr}")

    # Enviar estado inicial
    try:
        welcome = json.dumps({
            "type": "connected",
            "source": "monitor",
            "data": {
                "message": "EIDOS Monitor conectado. Viendo todo en tiempo real.",
                "port": MONITOR_PORT,
                "eidos_home": str(EIDOS_HOME)
            },
            "timestamp": time.strftime("%H:%M:%S"),
            "ts": time.time()
        })
        await websocket.send(welcome)
    except Exception:
        pass

    try:
        # Escuchar mensajes del cliente (feedback de Claude/SER)
        async def receive_loop():
            async for msg in websocket:
                try:
                    data = json.loads(msg)
                    log.info(f"Monitor recibió: {data}")
                    # Guardar en brain como feedback
                    _save_feedback(data)
                except Exception:
                    pass

        recv_task = asyncio.create_task(receive_loop())

        # Enviar eventos al cliente
        while True:
            try:
                event = _event_queue.get_nowait()
                await websocket.send(json.dumps(event, ensure_ascii=False))
            except queue.Empty:
                await asyncio.sleep(0.1)
            except Exception as e:
                log.debug(f"Send error: {e}")
                break

        recv_task.cancel()

    except Exception as e:
        log.debug(f"WS handler error: {e}")
    finally:
        _clients.discard(websocket)
        log.info(f"Monitor: cliente desconectado")


def _save_feedback(data: Dict):
    """Guarda feedback de Claude/SER en brain."""
    try:
        import sqlite3, uuid
        brain = EIDOS_HOME / "evolution_brain.db"
        conn = get_conn(brain)
        conn.execute("""
            INSERT OR REPLACE INTO knowledge_nodes
            (id, concept, definition, category, confidence, source, last_used, agent_id, character)
            VALUES (?, ?, ?, 'monitor_feedback', 0.95, 'realtime_monitor', ?, 'eidos', 'EIDOS')
        """, (
            str(uuid.uuid4()),
            f"feedback:monitor:{time.strftime('%Y%m%d_%H%M%S')}",
            json.dumps(data, ensure_ascii=False)[:1000],
            time.time()
        ))
        conn.commit()
        pass  # S109: get_conn no necesita close()
    except Exception as e:
        log.debug(f"Feedback save error: {e}")


async def _run_server():
    """Arranca el servidor WebSocket."""
    try:
        import websockets
        # Silenciar tracebacks de handshakes inválidos: sondas TCP planas (p.ej. el
        # watchdog comprobando el puerto) generaban un traceback enorme por conexión
        # (InvalidMessage / EOFError). Son inofensivas; las degradamos a CRITICAL
        # para no inundar el journal (eran ~366 líneas/día). [S122]
        logging.getLogger("websockets.server").setLevel(logging.CRITICAL)
        logging.getLogger("websockets.protocol").setLevel(logging.CRITICAL)
        log.info(f"Monitor WebSocket iniciando en ws://localhost:{MONITOR_PORT}")
        async with websockets.serve(_ws_handler, "0.0.0.0", MONITOR_PORT):
            log.info(f"Monitor WebSocket activo en :{MONITOR_PORT}")
            await asyncio.Future()  # run forever
    except ImportError:
        log.error("websockets no instalado — pip install websockets")
        # Fallback: HTTP SSE (Server-Sent Events) más simple
        await _run_sse_server()
    except Exception as e:
        log.error(f"Monitor error: {e}")


async def _run_sse_server():
    """Fallback: servidor HTTP con SSE para streaming de eventos."""
    from aiohttp import web

    async def sse_handler(request):
        resp = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Access-Control-Allow-Origin": "*"
        })
        await resp.prepare(request)

        while True:
            try:
                event = _event_queue.get_nowait()
                data = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                await resp.write(data.encode())
            except queue.Empty:
                await asyncio.sleep(0.1)
            except Exception:
                break
        return resp

    async def status_handler(request):
        return web.json_response({
            "ok": True,
            "clients": len(_clients),
            "queue_size": _event_queue.qsize(),
            "port": MONITOR_PORT
        })

    app = web.Application()
    app.router.add_get("/events", sse_handler)
    app.router.add_get("/status", status_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", MONITOR_PORT)
    await site.start()
    log.info(f"Monitor SSE activo en http://localhost:{MONITOR_PORT}/events")
    await asyncio.Future()


def start_monitor_thread():
    """Arranca el monitor en un hilo daemon (no bloquea el hilo principal)."""
    global _monitor_thread, _loop

    if _monitor_thread and _monitor_thread.is_alive():
        return

    def _thread_main():
        global _loop
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        _loop.run_until_complete(_run_server())

    _monitor_thread = threading.Thread(target=_thread_main, daemon=True, name="eidos-monitor")
    _monitor_thread.start()
    log.info("Monitor WebSocket thread iniciado")


def get_status() -> Dict:
    """Estado del monitor."""
    return {
        "running":    _monitor_thread is not None and _monitor_thread.is_alive(),
        "port":       MONITOR_PORT,
        "clients":    len(_clients),
        "queue_size": _event_queue.qsize(),
        "url":        f"ws://localhost:{MONITOR_PORT}"
    }


# ── Punto de entrada standalone ────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    log.info("EIDOS Realtime Monitor arrancando...")

    # Emitir evento de inicio
    emit("startup", {"message": "EIDOS Monitor iniciado", "pid": os.getpid()})

    asyncio.run(_run_server())
