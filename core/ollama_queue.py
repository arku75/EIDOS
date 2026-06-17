"""
core/ollama_queue.py — Cola central serializada para Ollama  [Fase 1]

Un único worker thread serializa TODAS las peticiones a Ollama.
Elimina la saturación cuando libre + curiosity + CLI corren juntos.

Prioridades:
    URGENT     = 0  — SER hablando en el CLI
    NORMAL     = 1  — eidos libre razonando
    BACKGROUND = 2  — curiosity daemon, indexación

Uso:
    from core.ollama_queue import get_ollama_queue, URGENT, NORMAL, BACKGROUND
    q = get_ollama_queue()
    result = q.ask(prompt="¿qué es Python?", model="lfm2.5-thinking:1.2b", priority=NORMAL)
    print(result)   # str con la respuesta de Ollama, o "" si timeout/error
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import logging
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("eidos.ollama_queue")

URGENT     = 0
NORMAL     = 1
BACKGROUND = 2

OLLAMA_URL     = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_TIMEOUT = 300       # segundos por petición (5 min — Mac Intel Xeon via :11435, CPU-only lento)
CIRCUIT_THRESHOLD = 4       # fallos consecutivos para abrir el circuit breaker
CIRCUIT_COOLDOWN  = 45      # segundos con circuit abierto antes de reintentar


@dataclass(order=True)
class _QueueItem:
    priority: int
    ts:       float
    seq:      int = field(compare=False)
    prompt:   str = field(compare=False)
    model:    str = field(compare=False)
    options:  dict = field(compare=False, default_factory=dict)
    timeout:  int = field(compare=False, default=DEFAULT_TIMEOUT)
    _result:  Optional[threading.Event] = field(compare=False, default=None)
    _value:   list = field(compare=False, default_factory=list)   # [str]


class OllamaQueue:
    """Cola serializada de peticiones a Ollama con circuit breaker."""

    def __init__(self):
        self._q:           queue.PriorityQueue = queue.PriorityQueue()
        self._seq:         int = 0
        self._seq_lock:    threading.Lock = threading.Lock()
        self._failures:    int = 0
        self._circuit_open_until: float = 0.0
        self._worker:      threading.Thread = threading.Thread(
            target=self._run, daemon=True, name="ollama-queue-worker"
        )
        self._worker.start()
        log.info("OllamaQueue iniciada")

    # ── API pública ─────────────────────────────────────────────────────────

    def ask(self, prompt: str, model: str = "lfm2.5-thinking:1.2b",
            priority: int = NORMAL, timeout: int = DEFAULT_TIMEOUT,
            options: Optional[dict] = None) -> str:
        """
        Envía una petición a Ollama y espera el resultado (bloqueante).
        Devuelve la respuesta como str, o "" si timeout/error/circuit abierto.
        """
        if self._is_circuit_open():
            log.debug("Circuit breaker abierto — skip Ollama")
            return ""

        with self._seq_lock:
            self._seq += 1
            seq = self._seq

        evt    = threading.Event()
        item   = _QueueItem(
            priority=priority, ts=time.time(), seq=seq,
            prompt=prompt, model=model,
            options=options or {},
            timeout=timeout,
            _result=evt, _value=[]
        )
        self._q.put(item)
        if not evt.wait(timeout=timeout + 5):  # +5s margen de cola
            log.warning("Timeout esperando slot en OllamaQueue (prio=%d)", priority)
            return ""
        return item._value[0] if item._value else ""

    def ask_chat(self, messages: list, model: str = "lfm2.5-thinking:1.2b",
                 priority: int = NORMAL, timeout: int = DEFAULT_TIMEOUT,
                 options: Optional[dict] = None) -> str:
        """
        Como ask() pero usa el endpoint /api/chat con lista de mensajes.
        messages = [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
        """
        if self._is_circuit_open():
            return ""
        prompt_repr = json.dumps(messages)
        return self.ask(
            prompt=f"__CHAT__:{prompt_repr}",
            model=model, priority=priority,
            timeout=timeout, options=options or {}
        )

    def queue_size(self) -> int:
        return self._q.qsize()

    def is_healthy(self) -> bool:
        return not self._is_circuit_open()

    # ── Worker ──────────────────────────────────────────────────────────────

    def _run(self):
        from pathlib import Path
        _cli_thinking = Path.home() / ".eidos" / "cli_thinking"
        _cli_active   = Path.home() / ".eidos" / "cli_active"
        while True:
            try:
                item: _QueueItem = self._q.get(timeout=1)
            except queue.Empty:
                continue

            if item.priority >= BACKGROUND:
                # BACKGROUND cede si CLI está activo o pensando
                # PriorityQueue garantiza que URGENT (0) se sirve antes al reencolar
                if _cli_thinking.exists() or _cli_active.exists():
                    self._q.put(item)
                    time.sleep(2)  # espera antes de reintentar — deja pasar URGENT
                    continue
                
            # Para peticiones URGENT: limpiar runners huérfanos antes de enviar
            # Evita que una petición de background bloqueada haga esperar a Colony
            if item.priority == URGENT:
                self._kill_orphan_runners()

            try:
                result = self._call_ollama(item)
                item._value.append(result)
                self._failures = 0
            except Exception as e:
                log.warning("OllamaQueue error: %s", e)
                item._value.append("")
                self._failures += 1
                if self._failures >= CIRCUIT_THRESHOLD:
                    self._circuit_open_until = time.time() + CIRCUIT_COOLDOWN
                    log.warning("Circuit breaker ABIERTO por %ds", CIRCUIT_COOLDOWN)
                # Timeout → matar runners huérfanos para la siguiente petición
                if "timed out" in str(e).lower():
                    self._kill_orphan_runners()
            finally:
                if item._result:
                    item._result.set()

    def _kill_orphan_runners(self) -> None:
        """Mata procesos runner hijos del proceso principal de Ollama.
        Usa pgrep -P para encontrar hijos directos — evita matar procesos del sistema.
        """
        import subprocess
        try:
            # Encontrar PID del proceso principal de Ollama (el que escucha en :11434)
            main_res = subprocess.run(
                ["pgrep", "-x", "ollama"], capture_output=True, text=True, timeout=5
            )
            for main_pid in main_res.stdout.strip().split("\n"):
                main_pid = main_pid.strip()
                if not main_pid:
                    continue
                # Matar hijos directos (los runners)
                child_res = subprocess.run(
                    ["pgrep", "-P", main_pid],
                    capture_output=True, text=True, timeout=5
                )
                for child_pid in child_res.stdout.strip().split("\n"):
                    child_pid = child_pid.strip()
                    if child_pid:
                        subprocess.run(["kill", "-9", child_pid], timeout=5)
                        log.info("Ollama runner %s eliminado (hijo de %s)", child_pid, main_pid)
        except Exception as e:
            log.debug("_kill_orphan_runners: %s", e)

    def _call_ollama(self, item: _QueueItem) -> str:
        """Llama a Ollama.
        - Conexión local (puerto 11434): stream=True → cierre de socket cancela generación
        - Tunnel SSH u otro puerto: stream=False → una sola respuesta, mucho más eficiente via SSH
        """
        is_chat = item.prompt.startswith("__CHAT__:")
        # Detectar si es tunnel SSH (puerto != 11434) para usar stream=False
        use_stream = ":11434" in OLLAMA_URL

        if is_chat:
            messages = json.loads(item.prompt[len("__CHAT__:"):])
            payload  = json.dumps({
                "model":    item.model,
                "messages": messages,
                "stream":   use_stream,
                "options":  item.options,
            }).encode()
            endpoint = f"{OLLAMA_URL}/api/chat"
        else:
            payload  = json.dumps({
                "model":   item.model,
                "prompt":  item.prompt,
                "stream":  use_stream,
                "options": item.options,
            }).encode()
            endpoint = f"{OLLAMA_URL}/api/generate"

        req = urllib.request.Request(
            endpoint, data=payload,
            headers={"Content-Type": "application/json"}
        )

        if not use_stream:
            # Modo no-streaming: una sola respuesta JSON completa (eficiente vía tunnel SSH)
            with urllib.request.urlopen(req, timeout=item.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if is_chat:
                return data.get("message", {}).get("content", "").strip()
            else:
                return data.get("response", "").strip()

        # Modo streaming (conexión local directa)
        tokens: list = []
        deadline     = time.time() + item.timeout

        with urllib.request.urlopen(req, timeout=item.timeout) as resp:
            for raw_line in resp:
                if time.time() > deadline:
                    log.debug("Streaming deadline alcanzado, cerrando conexión")
                    break
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if is_chat:
                    tok = chunk.get("message", {}).get("content", "")
                else:
                    tok = chunk.get("response", "")

                if tok:
                    tokens.append(tok)

                if chunk.get("done"):
                    break

        return "".join(tokens).strip()

    def _is_circuit_open(self) -> bool:
        if self._circuit_open_until and time.time() < self._circuit_open_until:
            return True
        if self._circuit_open_until and time.time() >= self._circuit_open_until:
            self._circuit_open_until = 0.0
            self._failures = 0
            log.info("Circuit breaker cerrado — reintentando Ollama")
        return False


# Singleton
_instance: Optional[OllamaQueue] = None
_lock      = threading.Lock()


def get_ollama_queue() -> OllamaQueue:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = OllamaQueue()
    return _instance
