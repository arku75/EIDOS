#!/usr/bin/env python3
"""
EIDOS core/mcp_protocol.py — Model Context Protocol
=====================================================
Implementacion del Model Context Protocol (MCP) para EIDOS.

MCP permite a EIDOS:
1. Exponer sus tools como servidor MCP (stdin/stdout JSON-RPC)
2. Conectar a servidores MCP externos como cliente
3. Listar y ejecutar tools locales y remotos

Protocolo: JSON-RPC 2.0 sobre stdin/stdout (estandar MCP)

Tools expuestos:
- eidos_chat: enviar mensaje al LLM
- eidos_shell: ejecutar comando (con Shield)
- eidos_memory: buscar/guardar en memoria
- eidos_status: obtener estado del sistema
- eidos_skill: ejecutar una skill

Uso como servidor:
    from core.mcp_protocol import MCPServer
    server = MCPServer()
    server.serve()  # Escucha en stdin/stdout

Uso como cliente:
    from core.mcp_protocol import MCPClient
    client = MCPClient()
    client.connect(["python3", "external_mcp_server.py"])
    tools = client.list_tools()
    result = client.call_tool("some_tool", {"arg": "value"})
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable

# ══════════════════════════════════════════════════════════════════════════════
# Constantes JSON-RPC 2.0
# ══════════════════════════════════════════════════════════════════════════════

JSONRPC_VERSION = "2.0"

# Error codes (JSON-RPC 2.0 standard)
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# MCP specific
MCP_PROTOCOL_VERSION = "2024-11-05"


# ══════════════════════════════════════════════════════════════════════════════
# Data Classes
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class MCPTool:
    """Definicion de un tool MCP."""
    name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass
class MCPToolResult:
    """Resultado de ejecutar un tool MCP."""
    content: List[Dict[str, Any]] = field(default_factory=list)
    is_error: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "isError": self.is_error,
        }


# ══════════════════════════════════════════════════════════════════════════════
# JSON-RPC Helpers
# ══════════════════════════════════════════════════════════════════════════════

def make_response(result: Any, req_id: Any) -> Dict[str, Any]:
    """Crea una respuesta JSON-RPC 2.0."""
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": req_id,
        "result": result,
    }


def make_error(code: int, message: str, req_id: Any = None, data: Any = None) -> Dict[str, Any]:
    """Crea una respuesta de error JSON-RPC 2.0."""
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": req_id,
        "error": error,
    }


def make_request(method: str, params: Dict[str, Any] = None, req_id: Any = None) -> Dict[str, Any]:
    """Crea una peticion JSON-RPC 2.0."""
    req = {
        "jsonrpc": JSONRPC_VERSION,
        "method": method,
    }
    if params is not None:
        req["params"] = params
    if req_id is not None:
        req["id"] = req_id
    return req


# ══════════════════════════════════════════════════════════════════════════════
# MCP Server
# ══════════════════════════════════════════════════════════════════════════════

class MCPServer:
    """
    Servidor MCP que expone tools de EIDOS.

    Escucha en stdin, responde en stdout.
    Protocolo: JSON-RPC 2.0 (una linea JSON por mensaje).
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self._tools: Dict[str, MCPTool] = {}
        self._handlers: Dict[str, Callable] = {}
        self._running = False

        # Registrar tools de EIDOS por defecto
        self._register_default_tools()
        self._log(f"Inicializado: {len(self._tools)} tools registrados")

    def _log(self, msg: str):
        if self.verbose:
            # Log a stderr para no contaminar stdout (canal MCP)
            print(f"🔌 [MCPServer] {msg}", file=sys.stderr)

    def register_tool(self, tool: MCPTool, handler: Callable):
        """
        Registra un tool MCP con su handler.

        Args:
            tool: Definicion del tool
            handler: Funcion que ejecuta el tool (recibe params dict, retorna MCPToolResult)
        """
        self._tools[tool.name] = tool
        self._handlers[tool.name] = handler
        self._log(f"  Tool registrado: {tool.name}")

    def _register_default_tools(self):
        """Registra los tools de EIDOS por defecto."""

        # ── eidos_chat ──
        self.register_tool(
            MCPTool(
                name="eidos_chat",
                description="Enviar un mensaje al LLM de EIDOS y obtener respuesta",
                input_schema={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "Mensaje para el LLM"},
                        "model": {"type": "string", "description": "Modelo a usar (opcional)"},
                    },
                    "required": ["message"],
                },
            ),
            self._handle_eidos_chat,
        )

        # ── eidos_shell ──
        self.register_tool(
            MCPTool(
                name="eidos_shell",
                description="Ejecutar un comando shell (protegido por Shield)",
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Comando a ejecutar"},
                        "timeout": {"type": "integer", "description": "Timeout en segundos", "default": 30},
                    },
                    "required": ["command"],
                },
            ),
            self._handle_eidos_shell,
        )

        # ── eidos_memory ──
        self.register_tool(
            MCPTool(
                name="eidos_memory",
                description="Buscar o guardar en la memoria de EIDOS",
                input_schema={
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["search", "store"], "description": "Accion a realizar"},
                        "content": {"type": "string", "description": "Contenido a buscar o guardar"},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags (para store)"},
                    },
                    "required": ["action", "content"],
                },
            ),
            self._handle_eidos_memory,
        )

        # ── eidos_status ──
        self.register_tool(
            MCPTool(
                name="eidos_status",
                description="Obtener el estado actual del sistema EIDOS",
                input_schema={
                    "type": "object",
                    "properties": {},
                },
            ),
            self._handle_eidos_status,
        )

        # ── eidos_skill ──
        self.register_tool(
            MCPTool(
                name="eidos_skill",
                description="Ejecutar una skill de EIDOS",
                input_schema={
                    "type": "object",
                    "properties": {
                        "skill_name": {"type": "string", "description": "Nombre de la skill"},
                        "params": {"type": "object", "description": "Parametros de la skill"},
                    },
                    "required": ["skill_name"],
                },
            ),
            self._handle_eidos_skill,
        )

    # ── Tool handlers ──

    def _handle_eidos_chat(self, params: Dict[str, Any]) -> MCPToolResult:
        """Handler para eidos_chat."""
        message = params.get("message", "")
        model = params.get("model")

        try:
            from core.agent import Agent
            agent = Agent()
            response = agent.chat(message, model=model)
            return MCPToolResult(
                content=[{"type": "text", "text": str(response)}]
            )
        except ImportError:
            # Fallback: respuesta directa sin agent
            return MCPToolResult(
                content=[{"type": "text", "text": f"[EIDOS echo] {message}"}]
            )
        except Exception as e:
            return MCPToolResult(
                content=[{"type": "text", "text": f"Error: {e}"}],
                is_error=True,
            )

    def _handle_eidos_shell(self, params: Dict[str, Any]) -> MCPToolResult:
        """Handler para eidos_shell (con Shield)."""
        command = params.get("command", "")
        timeout = params.get("timeout", 30)

        if not command:
            return MCPToolResult(
                content=[{"type": "text", "text": "Error: no command provided"}],
                is_error=True,
            )

        # Intentar usar Shield para validacion
        try:
            from core.shield import Shield
            shield = Shield()
            if not shield.is_safe(command):
                return MCPToolResult(
                    content=[{"type": "text", "text": f"Shield blocked: {command}"}],
                    is_error=True,
                )
        except ImportError:
            pass  # Sin shield, ejecutar directamente

        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=timeout
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr] {result.stderr}"
            return MCPToolResult(
                content=[{"type": "text", "text": output or "(no output)"}]
            )
        except subprocess.TimeoutExpired:
            return MCPToolResult(
                content=[{"type": "text", "text": f"Timeout after {timeout}s"}],
                is_error=True,
            )
        except Exception as e:
            return MCPToolResult(
                content=[{"type": "text", "text": f"Error: {e}"}],
                is_error=True,
            )

    def _handle_eidos_memory(self, params: Dict[str, Any]) -> MCPToolResult:
        """Handler para eidos_memory."""
        action = params.get("action", "search")
        content = params.get("content", "")
        tags = params.get("tags", [])

        try:
            from core.memory_vec import get_semantic_memory
            mem = get_semantic_memory()

            if action == "store":
                mem_id = mem.store(content, tags=tags)
                return MCPToolResult(
                    content=[{"type": "text", "text": f"Stored memory #{mem_id}"}]
                )
            else:  # search
                results = mem.search(content, limit=5)
                text = "\n".join(
                    f"[{r.get('score', 0):.2f}] {r['content'][:100]}"
                    for r in results
                ) or "No results found"
                return MCPToolResult(
                    content=[{"type": "text", "text": text}]
                )
        except ImportError:
            return MCPToolResult(
                content=[{"type": "text", "text": "memory_vec module not available"}],
                is_error=True,
            )
        except Exception as e:
            return MCPToolResult(
                content=[{"type": "text", "text": f"Error: {e}"}],
                is_error=True,
            )

    def _handle_eidos_status(self, params: Dict[str, Any]) -> MCPToolResult:
        """Handler para eidos_status."""
        import platform
        import os

        status = {
            "system": platform.system(),
            "hostname": platform.node(),
            "python": platform.python_version(),
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "tools_registered": len(self._tools),
            "tool_names": list(self._tools.keys()),
            "mcp_version": MCP_PROTOCOL_VERSION,
        }

        # Intentar obtener status de EIDOS
        try:
            from core.checkpoint import get_status
            eidos_status = get_status()
            status["eidos"] = eidos_status
        except ImportError:
            status["eidos"] = "checkpoint module not available"

        return MCPToolResult(
            content=[{"type": "text", "text": json.dumps(status, indent=2)}]
        )

    def _handle_eidos_skill(self, params: Dict[str, Any]) -> MCPToolResult:
        """Handler para eidos_skill."""
        skill_name = params.get("skill_name", "")
        skill_params = params.get("params", {})

        if not skill_name:
            return MCPToolResult(
                content=[{"type": "text", "text": "Error: no skill_name provided"}],
                is_error=True,
            )

        try:
            from core.dispatcher import dispatch_skill
            result = dispatch_skill(skill_name, **skill_params)
            return MCPToolResult(
                content=[{"type": "text", "text": str(result)}]
            )
        except ImportError:
            return MCPToolResult(
                content=[{"type": "text", "text": f"dispatcher not available, cannot run skill '{skill_name}'"}],
                is_error=True,
            )
        except Exception as e:
            return MCPToolResult(
                content=[{"type": "text", "text": f"Skill error: {e}"}],
                is_error=True,
            )

    # ── Protocolo MCP ──

    def _handle_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Procesa una peticion JSON-RPC y devuelve respuesta."""
        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        # ── initialize ──
        if method == "initialize":
            return make_response({
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "eidos-mcp-server",
                    "version": "1.0.0",
                },
            }, req_id)

        # ── notifications/initialized (notificacion, no requiere respuesta) ──
        if method == "notifications/initialized":
            return None

        # ── tools/list ──
        if method == "tools/list":
            tools_list = [t.to_dict() for t in self._tools.values()]
            return make_response({"tools": tools_list}, req_id)

        # ── tools/call ──
        if method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})

            if tool_name not in self._handlers:
                return make_error(METHOD_NOT_FOUND, f"Tool not found: {tool_name}", req_id)

            try:
                handler = self._handlers[tool_name]
                result = handler(tool_args)
                return make_response(result.to_dict(), req_id)
            except Exception as e:
                return make_error(INTERNAL_ERROR, str(e), req_id, {"traceback": traceback.format_exc()})

        # ── ping ──
        if method == "ping":
            return make_response({}, req_id)

        # ── Metodo no reconocido ──
        return make_error(METHOD_NOT_FOUND, f"Method not found: {method}", req_id)

    def serve(self):
        """
        Arranca el servidor MCP. Lee JSON-RPC de stdin, escribe respuestas a stdout.
        Bloquea hasta que se cierre stdin.
        """
        self._running = True
        self._log("Servidor MCP iniciado (stdin/stdout)")

        try:
            while self._running:
                line = sys.stdin.readline()
                if not line:
                    break  # EOF

                line = line.strip()
                if not line:
                    continue

                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    response = make_error(PARSE_ERROR, "Invalid JSON")
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
                    continue

                # Validar JSON-RPC
                if request.get("jsonrpc") != JSONRPC_VERSION:
                    response = make_error(INVALID_REQUEST, "Invalid jsonrpc version", request.get("id"))
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
                    continue

                response = self._handle_request(request)

                if response is not None:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()

        except KeyboardInterrupt:
            self._log("Servidor detenido (Ctrl+C)")
        except Exception as e:
            self._log(f"Error: {e}")
        finally:
            self._running = False

    def stop(self):
        """Detiene el servidor."""
        self._running = False

    def list_tools(self) -> List[Dict[str, Any]]:
        """Lista tools disponibles localmente."""
        return [t.to_dict() for t in self._tools.values()]

    def call_tool(self, name: str, params: Dict[str, Any] = None) -> MCPToolResult:
        """Ejecuta un tool local directamente (sin JSON-RPC)."""
        params = params or {}

        if name not in self._handlers:
            return MCPToolResult(
                content=[{"type": "text", "text": f"Tool not found: {name}"}],
                is_error=True,
            )

        return self._handlers[name](params)


# ══════════════════════════════════════════════════════════════════════════════
# MCP Client
# ══════════════════════════════════════════════════════════════════════════════

class MCPClient:
    """
    Cliente MCP para conectar a servidores externos.

    Arranca un proceso hijo y se comunica via stdin/stdout JSON-RPC.
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self._process: Optional[subprocess.Popen] = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._remote_tools: List[Dict[str, Any]] = []

    def _log(self, msg: str):
        if self.verbose:
            print(f"🔗 [MCPClient] {msg}", file=sys.stderr)

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def connect(self, server_cmd: List[str], timeout: int = 10) -> bool:
        """
        Conecta a un servidor MCP externo.

        Args:
            server_cmd: Comando para arrancar el servidor (e.g. ["python3", "server.py"])
            timeout: Timeout para la conexion inicial

        Returns:
            True si la conexion fue exitosa
        """
        try:
            self._process = subprocess.Popen(
                server_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            # Enviar initialize
            init_response = self._send_request("initialize", {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "eidos-mcp-client",
                    "version": "1.0.0",
                },
            }, timeout=timeout)

            if init_response and "result" in init_response:
                # Enviar notifications/initialized
                self._send_notification("notifications/initialized")
                self._log(f"Conectado a: {' '.join(server_cmd)}")

                # Obtener tools
                self._refresh_tools()
                return True
            else:
                self._log(f"⚠️ Initialize falló")
                self.disconnect()
                return False

        except Exception as e:
            self._log(f"⚠️ Error conectando: {e}")
            return False

    def disconnect(self):
        """Desconecta del servidor MCP."""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass  # error no crítico, continuar
            self._process = None
            self._remote_tools = []
            self._log("Desconectado")

    def _send_request(self, method: str, params: Dict[str, Any] = None,
                       timeout: int = 30) -> Optional[Dict[str, Any]]:
        """Envia una peticion JSON-RPC al servidor y espera respuesta."""
        if not self._process or self._process.poll() is not None:
            self._log("⚠️ No hay conexion activa")
            return None

        req_id = self._next_id()
        request = make_request(method, params, req_id)

        with self._lock:
            try:
                self._process.stdin.write(json.dumps(request) + "\n")
                self._process.stdin.flush()

                # Leer respuesta (con timeout simple)
                import select
                ready, _, _ = select.select([self._process.stdout], [], [], timeout)

                if ready:
                    line = self._process.stdout.readline()
                    if line:
                        return json.loads(line.strip())

                self._log(f"⚠️ Timeout esperando respuesta para {method}")
                return None

            except Exception as e:
                self._log(f"⚠️ Error en request: {e}")
                return None

    def _send_notification(self, method: str, params: Dict[str, Any] = None):
        """Envia una notificacion (sin esperar respuesta)."""
        if not self._process:
            return

        notification = {"jsonrpc": JSONRPC_VERSION, "method": method}
        if params:
            notification["params"] = params

        try:
            self._process.stdin.write(json.dumps(notification) + "\n")
            self._process.stdin.flush()
        except Exception:
            pass  # error no crítico, continuar
    def _refresh_tools(self):
        """Obtiene la lista de tools del servidor remoto."""
        response = self._send_request("tools/list")
        if response and "result" in response:
            self._remote_tools = response["result"].get("tools", [])
            self._log(f"  {len(self._remote_tools)} tools remotos disponibles")

    def list_tools(self) -> List[Dict[str, Any]]:
        """Lista tools disponibles en el servidor remoto."""
        return self._remote_tools

    def call_tool(self, name: str, arguments: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """
        Ejecuta un tool en el servidor remoto.

        Args:
            name: Nombre del tool
            arguments: Argumentos del tool

        Returns:
            Resultado del tool o None si falló
        """
        response = self._send_request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })

        if response and "result" in response:
            return response["result"]
        elif response and "error" in response:
            self._log(f"⚠️ Tool error: {response['error']}")
            return response["error"]

        return None

    @property
    def connected(self) -> bool:
        """Si el cliente esta conectado."""
        return self._process is not None and self._process.poll() is None


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_server: Optional[MCPServer] = None
_client: Optional[MCPClient] = None

def get_mcp_server() -> MCPServer:
    """Obtiene la instancia singleton del servidor MCP."""
    global _server
    if _server is None:
        _server = MCPServer()
    return _server

def get_mcp_client() -> MCPClient:
    """Obtiene la instancia singleton del cliente MCP."""
    global _client
    if _client is None:
        _client = MCPClient()
    return _client


# ══════════════════════════════════════════════════════════════════════════════
# CLI Testing
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import io

    print(f"\n{'=' * 70}")
    print(f"EIDOS MCP PROTOCOL — Test Suite")
    print(f"{'=' * 70}\n")

    passed = 0
    failed = 0

    def test(name, condition):
        global passed, failed
        if condition:
            print(f"  ✅ {name}")
            passed += 1
        else:
            print(f"  ❌ {name}")
            failed += 1

    server = MCPServer(verbose=False)

    # ── Test 1: Tools registrados ──
    print("\n📌 Test 1: Tools registrados")
    tools = server.list_tools()
    test("5 tools registrados", len(tools) == 5)
    tool_names = [t["name"] for t in tools]
    test("eidos_chat registrado", "eidos_chat" in tool_names)
    test("eidos_shell registrado", "eidos_shell" in tool_names)
    test("eidos_memory registrado", "eidos_memory" in tool_names)
    test("eidos_status registrado", "eidos_status" in tool_names)
    test("eidos_skill registrado", "eidos_skill" in tool_names)

    # ── Test 2: JSON-RPC helpers ──
    print("\n📌 Test 2: JSON-RPC helpers")
    resp = make_response({"tools": []}, 1)
    test("make_response tiene jsonrpc", resp["jsonrpc"] == "2.0")
    test("make_response tiene id", resp["id"] == 1)
    test("make_response tiene result", "result" in resp)

    err = make_error(METHOD_NOT_FOUND, "not found", 2)
    test("make_error tiene error", "error" in err)
    test("make_error code correcto", err["error"]["code"] == METHOD_NOT_FOUND)

    req = make_request("tools/list", {}, 3)
    test("make_request tiene method", req["method"] == "tools/list")

    # ── Test 3: Handle initialize ──
    print("\n📌 Test 3: Handle initialize")
    init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    init_resp = server._handle_request(init_req)
    test("initialize retorna resultado", init_resp is not None)
    test("initialize tiene protocolVersion", "protocolVersion" in init_resp.get("result", {}))

    # ── Test 4: Handle tools/list ──
    print("\n📌 Test 4: Handle tools/list")
    list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    list_resp = server._handle_request(list_req)
    test("tools/list retorna tools", "tools" in list_resp.get("result", {}))
    test("tools/list tiene 5 tools", len(list_resp["result"]["tools"]) == 5)

    # ── Test 5: Handle tools/call — eidos_status ──
    print("\n📌 Test 5: Handle tools/call eidos_status")
    call_req = {
        "jsonrpc": "2.0", "id": 3,
        "method": "tools/call",
        "params": {"name": "eidos_status", "arguments": {}},
    }
    call_resp = server._handle_request(call_req)
    test("call retorna result", "result" in call_resp)
    test("result tiene content", "content" in call_resp.get("result", {}))
    content = call_resp["result"]["content"]
    test("content es lista", isinstance(content, list) and len(content) > 0)

    # Parsear el status JSON
    status_text = content[0].get("text", "")
    try:
        status = json.loads(status_text)
        test("status tiene hostname", "hostname" in status)
        test("status tiene tools", "tools_registered" in status)
    except json.JSONDecodeError:
        test("status es JSON valido", False)
        test("status tiene hostname", False)

    # ── Test 6: Handle tools/call — eidos_shell ──
    print("\n📌 Test 6: Handle tools/call eidos_shell")
    shell_req = {
        "jsonrpc": "2.0", "id": 4,
        "method": "tools/call",
        "params": {"name": "eidos_shell", "arguments": {"command": "echo 'hello_mcp'"}},
    }
    shell_resp = server._handle_request(shell_req)
    test("shell retorna result", "result" in shell_resp)
    shell_content = shell_resp["result"]["content"][0]["text"]
    test("shell output correcto", "hello_mcp" in shell_content)

    # ── Test 7: Handle tool no existente ──
    print("\n📌 Test 7: Tool no existente")
    bad_req = {
        "jsonrpc": "2.0", "id": 5,
        "method": "tools/call",
        "params": {"name": "nonexistent_tool", "arguments": {}},
    }
    bad_resp = server._handle_request(bad_req)
    test("error retornado", "error" in bad_resp)
    test("error code METHOD_NOT_FOUND", bad_resp["error"]["code"] == METHOD_NOT_FOUND)

    # ── Test 8: Handle metodo no reconocido ──
    print("\n📌 Test 8: Metodo no reconocido")
    unknown_req = {"jsonrpc": "2.0", "id": 6, "method": "unknown/method", "params": {}}
    unknown_resp = server._handle_request(unknown_req)
    test("error para metodo desconocido", "error" in unknown_resp)

    # ── Test 9: Handle ping ──
    print("\n📌 Test 9: Ping")
    ping_req = {"jsonrpc": "2.0", "id": 7, "method": "ping", "params": {}}
    ping_resp = server._handle_request(ping_req)
    test("ping retorna result", "result" in ping_resp)

    # ── Test 10: call_tool directo ──
    print("\n📌 Test 10: call_tool directo (sin JSON-RPC)")
    result = server.call_tool("eidos_status")
    test("call_tool retorna MCPToolResult", isinstance(result, MCPToolResult))
    test("call_tool no es error", not result.is_error)
    test("call_tool tiene content", len(result.content) > 0)

    result_bad = server.call_tool("no_existe")
    test("call_tool inexistente es error", result_bad.is_error)

    # ── Test 11: MCPClient (sin servidor real) ──
    print("\n📌 Test 11: MCPClient basico")
    client = MCPClient(verbose=False)
    test("client no conectado inicialmente", not client.connected)
    test("list_tools vacio", len(client.list_tools()) == 0)

    # ── Test 12: Tool schema validation ──
    print("\n📌 Test 12: Tool schema")
    for tool_dict in tools:
        has_name = "name" in tool_dict
        has_desc = "description" in tool_dict
        has_schema = "inputSchema" in tool_dict
        test(f"  {tool_dict['name']} schema valido", has_name and has_desc and has_schema)

    # ── Test 13: eidos_shell sin comando ──
    print("\n📌 Test 13: eidos_shell sin comando")
    empty_shell = server.call_tool("eidos_shell", {"command": ""})
    test("shell vacio es error", empty_shell.is_error)

    # ── Test 14: eidos_chat (fallback) ──
    print("\n📌 Test 14: eidos_chat (fallback sin Agent)")
    chat_result = server.call_tool("eidos_chat", {"message": "hello"})
    test("chat retorna algo", len(chat_result.content) > 0)
    chat_text = chat_result.content[0].get("text", "")
    test("chat tiene respuesta", len(chat_text) > 0)
    print(f"      Chat response: {chat_text[:60]}")

    print(f"\n{'=' * 70}")
    print(f"RESULTADOS: {passed} passed, {failed} failed, {passed + failed} total")
    print(f"{'=' * 70}\n")

    if failed > 0:
        sys.exit(1)
    print("✅ MCP Protocol funcional\n")
