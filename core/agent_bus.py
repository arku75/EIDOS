"""
EIDOS core/agent_bus.py — Inter-Agent Communication Bus
========================================================
Sistema de comunicación entre agentes, basado en patrones de:
  - ClosedClaw: ClawTalk protocol + ClawDense compression
  - tinyclaw: pub/sub event bus + blackboard collaboration
  - tinyclaw_agi: tag routing [@agent: message]

Proporciona:
  1. EventBus      — pub/sub con wildcard subscriptions
  2. Blackboard    — estado compartido entre agentes (key-value con locks)
  3. TagRouter     — routing de mensajes por [@agent_id: msg] tags
  4. TokenCompressor — ClawDense-inspired compression para inter-agent comms
  5. DockerSandbox — aislamiento de agentes creados por EIDOS en Docker

Uso:
    from core.agent_bus import get_bus, get_blackboard

    bus = get_bus()
    board = get_blackboard()

    # Pub/sub
    bus.subscribe("scan.*", callback)
    bus.publish("scan.nmap", {"target": "192.168.1.1"})

    # Blackboard
    board.write("scan_results", {"ports": [22, 80, 443]}, agent="pentesting")
    data = board.read("scan_results")

    # Tag routing
    bus.route("[@pentesting: escanea 192.168.1.1]")

    # Token compression
    from core.agent_bus import compress, decompress
    short = compress("Authentication required for system call to exec_shell")
    # → "!req @exec_shell"
"""
from __future__ import annotations

import re
import json
import time
import threading
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from pathlib import Path

import logging
log = logging.getLogger("eidos.agent_bus")


# ═══════════════════════════════════════════════════════════════════════════════
#  1. EVENT BUS — Pub/Sub con wildcards
#     Basado en: tinyclaw inter-agent pub/sub
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Event:
    topic: str
    data: Any
    source: str = "unknown"
    timestamp: float = field(default_factory=time.time)


class EventBus:
    """
    Bus de eventos pub/sub para comunicación inter-agente.

    Soporta wildcards: "scan.*" matchea "scan.nmap", "scan.nikto", etc.
    """

    def __init__(self, max_history: int = 200):
        self._subs: dict[str, list[Callable]] = defaultdict(list)
        self._history: list[Event] = []
        self._max_history = max_history
        self._lock = threading.Lock()

    def subscribe(self, pattern: str, callback: Callable[[Event], None]) -> None:
        """Suscribirse a eventos que matcheen el pattern."""
        with self._lock:
            self._subs[pattern].append(callback)

    def unsubscribe(self, pattern: str, callback: Callable = None) -> None:
        """Desuscribirse. Si callback=None, elimina todas las subs del pattern."""
        with self._lock:
            if callback:
                self._subs[pattern] = [c for c in self._subs[pattern] if c != callback]
            else:
                self._subs.pop(pattern, None)

    def publish(self, topic: str, data: Any = None, source: str = "unknown") -> None:
        """Publica un evento en el bus."""
        event = Event(topic=topic, data=data, source=source)

        with self._lock:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history.pop(0)

        # Notificar subscribers
        for pattern, callbacks in list(self._subs.items()):
            if self._matches(pattern, topic):
                for cb in callbacks:
                    try:
                        cb(event)
                    except Exception as e:
                        log.error(f"[EventBus] Callback error for {topic}: {e}")

    def _matches(self, pattern: str, topic: str) -> bool:
        """Match con wildcards: 'scan.*' matchea 'scan.nmap'."""
        if pattern == "*":
            return True
        if "*" not in pattern:
            return pattern == topic
        # Convertir wildcard a regex
        regex = "^" + re.escape(pattern).replace(r"\*", "[^.]*") + "$"
        return bool(re.match(regex, topic))

    def get_history(self, topic_filter: str = "*", limit: int = 50) -> list[Event]:
        """Obtiene historial de eventos filtrado."""
        with self._lock:
            matching = [e for e in self._history if self._matches(topic_filter, e.topic)]
            return matching[-limit:]


# ═══════════════════════════════════════════════════════════════════════════════
#  2. BLACKBOARD — Estado compartido entre agentes
#     Basado en: tinyclaw delegation blackboard collaboration
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class BlackboardEntry:
    key: str
    value: Any
    agent: str
    timestamp: float = field(default_factory=time.time)
    version: int = 1


class Blackboard:
    """
    Estado compartido key-value para colaboración entre agentes.

    Thread-safe con versioning para detectar conflictos.
    """

    def __init__(self):
        self._data: dict[str, BlackboardEntry] = {}
        self._lock = threading.RLock()
        self._watchers: dict[str, list[Callable]] = defaultdict(list)

    def write(self, key: str, value: Any, agent: str = "unknown") -> int:
        """Escribe en el blackboard. Retorna versión."""
        with self._lock:
            existing = self._data.get(key)
            version = (existing.version + 1) if existing else 1
            entry = BlackboardEntry(key=key, value=value, agent=agent, version=version)
            self._data[key] = entry

        # Notificar watchers
        for cb in self._watchers.get(key, []):
            try:
                cb(entry)
            except Exception:
                pass  # error no crítico, continuar
        return version

    def read(self, key: str) -> Any:
        """Lee un valor del blackboard."""
        with self._lock:
            entry = self._data.get(key)
            return entry.value if entry else None

    def read_entry(self, key: str) -> Optional[BlackboardEntry]:
        """Lee la entrada completa (con metadata)."""
        with self._lock:
            return self._data.get(key)

    def watch(self, key: str, callback: Callable[[BlackboardEntry], None]) -> None:
        """Observa cambios en una clave."""
        self._watchers[key].append(callback)

    def keys(self) -> list[str]:
        """Lista todas las claves."""
        with self._lock:
            return list(self._data.keys())

    def snapshot(self) -> dict[str, Any]:
        """Snapshot completo del blackboard."""
        with self._lock:
            return {k: v.value for k, v in self._data.items()}

    def clear(self) -> None:
        """Limpia el blackboard."""
        with self._lock:
            self._data.clear()


# ═══════════════════════════════════════════════════════════════════════════════
#  3. TAG ROUTER — Routing por tags [@agent: message]
#     Basado en: tinyclaw_agi multi-agent tag routing
# ═══════════════════════════════════════════════════════════════════════════════

_TAG_RX = re.compile(r"\[@(\w+):\s*([^\]]+)\]")


class TagRouter:
    """
    Enruta mensajes a agentes usando tags [@agent_id: message].

    Soporta fan-out a múltiples agentes: "[@coding: fix] [@pentesting: scan]"
    """

    def __init__(self, bus: EventBus):
        self.bus = bus
        self._handlers: dict[str, Callable] = {}

    def register(self, agent_id: str, handler: Callable[[str], str]) -> None:
        """Registra un handler para un agent_id."""
        self._handlers[agent_id] = handler

    def route(self, message: str) -> dict[str, str]:
        """
        Parsea tags y enruta a los agentes correspondientes.

        Returns:
            Dict de {agent_id: response}
        """
        results = {}
        matches = _TAG_RX.findall(message)

        for agent_id, payload in matches:
            handler = self._handlers.get(agent_id)
            if handler:
                try:
                    result = handler(payload.strip())
                    results[agent_id] = result
                    self.bus.publish(f"route.{agent_id}", {
                        "payload": payload.strip(),
                        "result": result[:200],
                    }, source="tag_router")
                except Exception as e:
                    results[agent_id] = f"[ERROR] {e}"
            else:
                results[agent_id] = f"[UNKNOWN AGENT] {agent_id}"

        return results

    def parse_tags(self, message: str) -> list[tuple[str, str]]:
        """Extrae tags sin ejecutar."""
        return _TAG_RX.findall(message)


# ═══════════════════════════════════════════════════════════════════════════════
#  4. TOKEN COMPRESSOR — ClawDense-inspired compression
#     Basado en: ClosedClaw ClawDense (~60% token reduction)
# ═══════════════════════════════════════════════════════════════════════════════

# Prefijos ClawDense
_COMPRESS_MAP = {
    # Acciones
    "authentication": "!auth",
    "authorization": "!authz",
    "required": "!req",
    "permission": "!perm",
    "execute": "@exec",
    "executing": "@exec~",
    "execution": "@exec_",
    "system call": "@sys",
    "function call": "@fn",
    "tool call": "@tool",
    # Estado
    "success": "::ok",
    "successful": "::ok",
    "failure": "::fail",
    "failed": "::fail",
    "error": "::err",
    "warning": "::warn",
    "completed": "::done",
    "in progress": "::wip",
    "pending": "::pend",
    # Flujo
    "return": "<<ret",
    "request": ">>req",
    "response": "<<res",
    "query": "?q",
    "search": "?s",
    "result": "<<r",
    # Datos
    "file system": "@fs",
    "network": "@net",
    "database": "@db",
    "memory": "@mem",
    "browser": "@web",
    "screenshot": "@ss",
    "terminal": "@term",
    "process": "@proc",
    # Seguridad
    "vulnerability": "!vuln",
    "exploit": "!xpl",
    "payload": "!pld",
    "scan": "!scan",
    "target": "!tgt",
    "credential": "!cred",
    "privilege": "!priv",
    "escalation": "!esc",
}

_DECOMPRESS_MAP = {v: k for k, v in _COMPRESS_MAP.items()}


def compress(text: str) -> str:
    """Comprime texto usando ClawDense notation."""
    result = text.lower()
    for long, short in sorted(_COMPRESS_MAP.items(), key=lambda x: -len(x[0])):
        result = result.replace(long, short)
    return result


def decompress(text: str) -> str:
    """Descomprime texto ClawDense a legible."""
    result = text
    for short, long in sorted(_DECOMPRESS_MAP.items(), key=lambda x: -len(x[0])):
        result = result.replace(short, long)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  5. DOCKER SANDBOX — Aislamiento de agentes creados por EIDOS
#     EIDOS crea agentes en Docker para no romper su sistema (regla de SER)
# ═══════════════════════════════════════════════════════════════════════════════

class DockerSandbox:
    """
    Ejecuta agentes/código en contenedores Docker aislados.

    EIDOS usa esto cuando crea nuevos agentes para sí mismo:
    - El PC de SER es el cuerpo y vida de EIDOS
    - Nuevos agentes se aíslan en Docker para proteger el sistema host
    """

    DEFAULT_IMAGE = "python:3.11-slim"
    TIMEOUT = 300  # 5 min por defecto

    @staticmethod
    def is_available() -> bool:
        """Verifica si Docker está disponible."""
        try:
            r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    @staticmethod
    def run(command: str, image: str = None,
            timeout: int = None, memory: str = "512m",
            network: bool = False, volumes: dict = None) -> tuple[bool, str]:
        """
        Ejecuta un comando en un contenedor Docker aislado.

        Args:
            command: Comando a ejecutar
            image: Imagen Docker (default: python:3.11-slim)
            timeout: Timeout en segundos
            memory: Límite de memoria (default: 512m)
            network: Permitir red (default: False para aislamiento)
            volumes: Volúmenes a montar {host_path: container_path}

        Returns:
            (success: bool, output: str)
        """
        image = image or DockerSandbox.DEFAULT_IMAGE
        timeout = timeout or DockerSandbox.TIMEOUT

        args = [
            "docker", "run", "--rm",
            "--memory", memory,
            "--cpus", "1.0",
            "--pids-limit", "100",
            "--read-only",
            "--security-opt", "no-new-privileges",
        ]

        if not network:
            args.extend(["--network", "none"])

        # Montar volúmenes (read-only por defecto)
        if volumes:
            for host, container in volumes.items():
                args.extend(["-v", f"{host}:{container}:ro"])

        # Tmpfs para escritura temporal
        args.extend(["--tmpfs", "/tmp:rw,noexec,nosuid,size=100m"])

        args.extend([image, "sh", "-c", command])

        try:
            r = subprocess.run(
                args, capture_output=True, text=True, timeout=timeout
            )
            output = (r.stdout + r.stderr).strip()
            return r.returncode == 0, output[:5000]
        except subprocess.TimeoutExpired:
            return False, f"[TIMEOUT] Docker command exceeded {timeout}s"
        except Exception as e:
            return False, f"[DOCKER ERROR] {e}"

    @staticmethod
    def run_agent_code(code: str, requirements: list = None) -> tuple[bool, str]:
        """
        Ejecuta código Python de un agente en sandbox Docker.

        Args:
            code: Código Python a ejecutar
            requirements: Lista de pip packages necesarios
        """
        setup = ""
        if requirements:
            pkgs = " ".join(requirements)
            setup = f"pip install -q {pkgs} && "

        import tempfile, os
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, dir='/tmp') as f:
            f.write(code)
            tmp_path = f.name

        try:
            return DockerSandbox.run(
                f"{setup}python /tmp/agent_code.py",
                volumes={tmp_path: "/tmp/agent_code.py"},
                network=bool(requirements),  # Red solo si necesita instalar
            )
        finally:
            os.unlink(tmp_path)


# ═══════════════════════════════════════════════════════════════════════════════
#  SINGLETONS
# ═══════════════════════════════════════════════════════════════════════════════

_bus: Optional[EventBus] = None
_blackboard: Optional[Blackboard] = None
_router: Optional[TagRouter] = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
        log.info("📡 [Agent Bus] EventBus inicializado")
    return _bus


def get_blackboard() -> Blackboard:
    global _blackboard
    if _blackboard is None:
        _blackboard = Blackboard()
        log.info("📋 [Agent Bus] Blackboard inicializado")
    return _blackboard


def get_router() -> TagRouter:
    global _router
    if _router is None:
        _router = TagRouter(get_bus())
        log.info("🔀 [Agent Bus] TagRouter inicializado")
    return _router
