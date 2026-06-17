"""
EIDOS core/ipc_bridge.py — Inter-Process Communication Bridge
================================================================
Comunicación bidireccional entre EIDOS CLI y VSEIDOS.

Cuando ambos están abiertos, se comunican en tiempo real a través de:
  1. Socket Unix (~/.eidos/ipc.sock) — comunicación de baja latencia
  2. Fichero compartido (~/.eidos/ipc_mailbox/) — fallback si socket falla

Protocolo:
  - Mensajes JSON con tipo, payload y timestamp
  - Tipos: query, result, event, status, extension_action, colony_dispatch

Ejemplo de flujo:
  1. VSEIDOS envía: {"type": "query", "text": "explain this code", "source": "vseidos"}
  2. EIDOS CLI recibe → despacha a ColonyQueryEngine → responde
  3. EIDOS envía resultado a VSEIDOS
  4. Ambos siguen su trabajo independiente

Uso:
    from core.ipc_bridge import get_ipc_bridge
    bridge = get_ipc_bridge()
    bridge.start_server()  # EIDOS CLI inicia servidor
    bridge.send_event("status", {"state": "ready"})
    bridge.send_query("explain TCP", callback=handle_response)
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Dict, List, Callable

log = logging.getLogger("eidos.ipc_bridge")

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

EIDOS_DIR = Path.home() / ".eidos"
SOCKET_PATH = EIDOS_DIR / "ipc.sock"
MAILBOX_DIR = EIDOS_DIR / "ipc_mailbox"
MAX_MSG_SIZE = 1024 * 1024  # 1MB por mensaje
HEARTBEAT_INTERVAL = 10.0   # segundos entre heartbeats


# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS DE MENSAJE
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class IPCMessage:
    """Mensaje IPC entre EIDOS CLI y VSEIDOS."""
    msg_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    msg_type: str = "event"         # query, result, event, status, extension_action, colony_dispatch
    source: str = "eidos_cli"       # eidos_cli, vseidos
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    reply_to: str = ""              # msg_id al que responde

    def to_json(self) -> str:
        return json.dumps({
            "msg_id": self.msg_id,
            "msg_type": self.msg_type,
            "source": self.source,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "reply_to": self.reply_to,
        })

    @staticmethod
    def from_json(data: str) -> "IPCMessage":
        d = json.loads(data)
        return IPCMessage(
            msg_id=d.get("msg_id", ""),
            msg_type=d.get("msg_type", "event"),
            source=d.get("source", "unknown"),
            payload=d.get("payload", {}),
            timestamp=d.get("timestamp", 0),
            reply_to=d.get("reply_to", ""),
        )


# ══════════════════════════════════════════════════════════════════════════════
#  IPC BRIDGE
# ══════════════════════════════════════════════════════════════════════════════

class IPCBridge:
    """
    Puente de comunicación bidireccional EIDOS CLI <-> VSEIDOS.

    Funciona como servidor (EIDOS CLI) o cliente (VSEIDOS).
    Si el socket no está disponible, usa el mailbox como fallback.
    """

    def __init__(self, identity: str = "eidos_cli"):
        self.identity = identity
        self._lock = threading.Lock()
        self._server_socket: Optional[socket.socket] = None
        self._client_connections: List[socket.socket] = []
        self._running = False
        self._server_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._message_handlers: Dict[str, List[Callable]] = {}
        self._pending_replies: Dict[str, Callable] = {}
        self._message_log: List[Dict] = []

        # Crear directorios
        EIDOS_DIR.mkdir(parents=True, exist_ok=True)
        MAILBOX_DIR.mkdir(parents=True, exist_ok=True)
        (MAILBOX_DIR / "to_cli").mkdir(exist_ok=True)
        (MAILBOX_DIR / "to_vseidos").mkdir(exist_ok=True)

        log.info("[IPC] Bridge inicializado — identity=%s", identity)

    # ── SERVIDOR (EIDOS CLI) ─────────────────────────────────────────────────

    def start_server(self) -> bool:
        """Inicia el servidor IPC (llamado por EIDOS CLI)."""
        if self._running:
            return True

        # Limpiar socket viejo
        if SOCKET_PATH.exists():
            try:
                SOCKET_PATH.unlink()
            except Exception:
                pass  # error no crítico, continuar
        try:
            self._server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._server_socket.bind(str(SOCKET_PATH))
            self._server_socket.listen(5)
            self._server_socket.settimeout(1.0)
            self._running = True

            # Thread de aceptar conexiones
            self._server_thread = threading.Thread(
                target=self._accept_loop, daemon=True, name="ipc-server"
            )
            self._server_thread.start()

            # Thread de heartbeat
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop, daemon=True, name="ipc-heartbeat"
            )
            self._heartbeat_thread.start()

            # Thread de polling mailbox
            threading.Thread(
                target=self._mailbox_poll_loop, daemon=True, name="ipc-mailbox"
            ).start()

            print(f"[IPC] Servidor iniciado en {SOCKET_PATH}")
            return True

        except Exception as e:
            log.error("[IPC] Error iniciando servidor: %s", e)
            self._running = False
            return False

    def stop_server(self) -> None:
        """Detiene el servidor IPC."""
        self._running = False
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass  # error no crítico, continuar
        for conn in self._client_connections:
            try:
                conn.close()
            except Exception:
                pass  # error no crítico, continuar
        self._client_connections.clear()
        if SOCKET_PATH.exists():
            try:
                SOCKET_PATH.unlink()
            except Exception:
                pass  # error no crítico, continuar
        print("[IPC] Servidor detenido")

    def _accept_loop(self) -> None:
        """Loop de aceptar conexiones entrantes."""
        while self._running:
            try:
                conn, _ = self._server_socket.accept()
                self._client_connections.append(conn)
                threading.Thread(
                    target=self._handle_client, args=(conn,),
                    daemon=True, name=f"ipc-client-{len(self._client_connections)}"
                ).start()
                print(f"[IPC] Nueva conexión — total: {len(self._client_connections)}")
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    log.warning("[IPC] Accept error: %s", e)

    def _handle_client(self, conn: socket.socket) -> None:
        """Maneja mensajes de un cliente conectado."""
        conn.settimeout(2.0)
        buffer = b""
        while self._running:
            try:
                data = conn.recv(MAX_MSG_SIZE)
                if not data:
                    break
                buffer += data

                # Procesar mensajes completos (delimitados por \n)
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    try:
                        msg = IPCMessage.from_json(line.decode("utf-8"))
                        self._dispatch_message(msg)
                    except Exception as e:
                        log.warning("[IPC] Parse error: %s", e)

            except socket.timeout:
                continue
            except Exception:
                break

        with self._lock:
            if conn in self._client_connections:
                self._client_connections.remove(conn)
        try:
            conn.close()
        except Exception:
            pass  # error no crítico, continuar
    def _heartbeat_loop(self) -> None:
        """Envía heartbeats periódicos."""
        while self._running:
            time.sleep(HEARTBEAT_INTERVAL)
            if self._running:
                self.send_event("heartbeat", {
                    "identity": self.identity,
                    "connections": len(self._client_connections),
                    "uptime": time.time(),
                })

    # ── MAILBOX FALLBACK ─────────────────────────────────────────────────────

    def _mailbox_poll_loop(self) -> None:
        """Polling del mailbox como fallback."""
        my_inbox = MAILBOX_DIR / "to_cli" if self.identity == "eidos_cli" else MAILBOX_DIR / "to_vseidos"
        while self._running:
            time.sleep(1.0)
            try:
                for msg_file in sorted(my_inbox.glob("*.json")):
                    try:
                        data = msg_file.read_text()
                        msg = IPCMessage.from_json(data)
                        self._dispatch_message(msg)
                        msg_file.unlink()
                    except Exception:
                        try:
                            msg_file.unlink()
                        except Exception:
                            pass  # error no crítico, continuar
            except Exception:
                pass  # error no crítico, continuar
    def _send_via_mailbox(self, msg: IPCMessage) -> bool:
        """Envía mensaje via mailbox (fallback)."""
        target = "to_vseidos" if self.identity == "eidos_cli" else "to_cli"
        target_dir = MAILBOX_DIR / target
        try:
            filename = f"{msg.timestamp:.0f}_{msg.msg_id}.json"
            (target_dir / filename).write_text(msg.to_json())
            return True
        except Exception as e:
            log.warning("[IPC] Mailbox send failed: %s", e)
            return False

    # ── DISPATCH ─────────────────────────────────────────────────────────────

    def _dispatch_message(self, msg: IPCMessage) -> None:
        """Despacha un mensaje recibido a los handlers registrados."""
        self._message_log.append({
            "msg_id": msg.msg_id,
            "type": msg.msg_type,
            "source": msg.source,
            "time": msg.timestamp,
        })
        # Mantener log limitado
        if len(self._message_log) > 100:
            self._message_log = self._message_log[-50:]

        # Verificar si es respuesta a una query pendiente
        if msg.reply_to and msg.reply_to in self._pending_replies:
            callback = self._pending_replies.pop(msg.reply_to)
            try:
                callback(msg)
            except Exception as e:
                log.warning("[IPC] Callback error: %s", e)
            return

        # Dispatch a handlers por tipo
        handlers = self._message_handlers.get(msg.msg_type, [])
        handlers += self._message_handlers.get("*", [])  # wildcard
        for handler in handlers:
            try:
                handler(msg)
            except Exception as e:
                log.warning("[IPC] Handler error: %s", e)

        # Handler interno: colony_dispatch → ejecutar query en ColonyEngine
        if msg.msg_type == "colony_dispatch":
            self._handle_colony_dispatch(msg)

        # Handler interno: extension_action → instalar/desinstalar
        if msg.msg_type == "extension_action":
            self._handle_extension_action(msg)

    def _handle_colony_dispatch(self, msg: IPCMessage) -> None:
        """Despacha una query a ColonyQueryEngine y responde."""
        try:
            from core.colony_query_engine import get_colony_engine
            engine = get_colony_engine()
            text = msg.payload.get("text", "")
            profile = msg.payload.get("profile", "auto")
            result = engine.query(text, profile=profile, requester=msg.source)

            reply = IPCMessage(
                msg_type="result",
                source=self.identity,
                payload=result.to_dict(),
                reply_to=msg.msg_id,
            )
            self.broadcast(reply)
        except Exception as e:
            reply = IPCMessage(
                msg_type="result",
                source=self.identity,
                payload={"success": False, "response": f"[IPC ERROR] {e}"},
                reply_to=msg.msg_id,
            )
            self.broadcast(reply)

    def _handle_extension_action(self, msg: IPCMessage) -> None:
        """Maneja acciones de extensiones desde VSEIDOS."""
        try:
            from core.extension_intelligence import get_extension_intelligence
            ei = get_extension_intelligence()
            action = msg.payload.get("action", "")
            ext_id = msg.payload.get("ext_id", "")

            if action == "install" and ext_id:
                success = ei.install(ext_id, reason=f"ipc:{msg.source}")
                self.send_event("extension_result", {
                    "action": "install", "ext_id": ext_id, "success": success,
                })
            elif action == "uninstall" and ext_id:
                success = ei.uninstall(ext_id, reason=f"ipc:{msg.source}")
                self.send_event("extension_result", {
                    "action": "uninstall", "ext_id": ext_id, "success": success,
                })
            elif action == "scan":
                scan = ei.analyze_workspace()
                self.send_event("extension_scan", {
                    "languages": list(scan.detected_languages),
                    "to_install": [{"id": e.ext_id, "name": e.name} for e in scan.to_install],
                })
        except Exception as e:
            log.warning("[IPC] Extension action error: %s", e)

    # ── API PÚBLICA — ENVIAR ─────────────────────────────────────────────────

    def send_event(self, event_name: str, data: Dict[str, Any] = None) -> bool:
        """Envía un evento a todos los peers conectados."""
        msg = IPCMessage(
            msg_type="event",
            source=self.identity,
            payload={"event": event_name, **(data or {})},
        )
        return self.broadcast(msg)

    def send_query(self, text: str, profile: str = "auto",
                   callback: Callable = None) -> str:
        """
        Envía una query para que el peer la procese con ColonyEngine.

        Returns:
            msg_id para tracking
        """
        msg = IPCMessage(
            msg_type="colony_dispatch",
            source=self.identity,
            payload={"text": text, "profile": profile},
        )
        if callback:
            self._pending_replies[msg.msg_id] = callback
        self.broadcast(msg)
        return msg.msg_id

    def broadcast(self, msg: IPCMessage) -> bool:
        """Envía mensaje a todos los peers conectados."""
        data = (msg.to_json() + "\n").encode("utf-8")
        sent = False

        with self._lock:
            dead = []
            for conn in self._client_connections:
                try:
                    conn.sendall(data)
                    sent = True
                except Exception:
                    dead.append(conn)
            for conn in dead:
                self._client_connections.remove(conn)
                try:
                    conn.close()
                except Exception:
                    pass  # error no crítico, continuar
        # Si no enviamos por socket, usar mailbox
        if not sent:
            sent = self._send_via_mailbox(msg)

        return sent

    # ── CLIENTE (para conectar desde VSEIDOS u otro proceso) ─────────────────

    def connect_to_server(self) -> bool:
        """Conecta al servidor IPC como cliente."""
        if not SOCKET_PATH.exists():
            return False

        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(str(SOCKET_PATH))
            self._client_connections.append(client)

            # Thread para recibir mensajes
            threading.Thread(
                target=self._handle_client, args=(client,),
                daemon=True, name="ipc-server-conn"
            ).start()

            print(f"[IPC] Conectado al servidor")
            return True
        except Exception as e:
            log.warning("[IPC] Connect failed: %s", e)
            return False

    # ── HANDLERS ─────────────────────────────────────────────────────────────

    def on(self, msg_type: str, handler: Callable) -> None:
        """Registra un handler para un tipo de mensaje."""
        if msg_type not in self._message_handlers:
            self._message_handlers[msg_type] = []
        self._message_handlers[msg_type].append(handler)

    # ── STATUS ───────────────────────────────────────────────────────────────

    def is_connected(self) -> bool:
        """¿Hay algún peer conectado?"""
        return len(self._client_connections) > 0

    def is_server_running(self) -> bool:
        """¿Está el servidor IPC activo?"""
        return self._running

    def get_status(self) -> Dict:
        """Estado del bridge IPC."""
        return {
            "identity": self.identity,
            "server_running": self._running,
            "connections": len(self._client_connections),
            "socket_path": str(SOCKET_PATH),
            "socket_exists": SOCKET_PATH.exists(),
            "mailbox_dir": str(MAILBOX_DIR),
            "messages_processed": len(self._message_log),
            "pending_replies": len(self._pending_replies),
        }

    def get_recent_messages(self, limit: int = 20) -> List[Dict]:
        """Mensajes recientes procesados."""
        return self._message_log[-limit:]


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_ipc_bridge: Optional[IPCBridge] = None


def get_ipc_bridge(identity: str = "eidos_cli") -> IPCBridge:
    global _ipc_bridge
    if _ipc_bridge is None:
        _ipc_bridge = IPCBridge(identity=identity)
    return _ipc_bridge


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  EIDOS IPC Bridge — Status")
    print("=" * 60)

    bridge = get_ipc_bridge()
    status = bridge.get_status()
    for k, v in status.items():
        print(f"  {k}: {v}")

    print("\n  IPC Bridge funcional")
