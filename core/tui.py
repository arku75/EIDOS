"""
EIDOS core/tui.py — TUI Dashboard en tiempo real (de TinyClaw AGI)
===================================================================
TinyClaw AGI implementa un panel de estado en tiempo real que muestra:
  - Qué tool está corriendo actualmente
  - Estado de los subagentes / task queue
  - Mensajes del agente fluyendo en vivo

EIDOS adapta esto al terminal con ANSI escapes (sin dependencias extra).
Si prompt_toolkit está disponible, usa su API de output para mejor compatibilidad.

Uso standalone:
    python core/tui.py

Uso integrado (desde el kernel):
    from core.tui import StatusBar
    status = StatusBar()
    status.set_tool("exec_shell: nmap -sn 192.168.1.0/24")
    status.set_phase("EXECUTION")
"""
from __future__ import annotations

import os
import sys
import time
import threading
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class AgentStatus:
    """Estado actual del agente para mostrar en el TUI."""
    phase:         str = "IDLE"       # PLANNING | EXECUTION | VERIFICATION | DONE
    current_tool:  str = ""           # Tool ejecutándose ahora
    iteration:     int = 0
    max_iteration: int = 10
    task_summary:  str = ""
    queue_pending: int = 0
    queue_done:    int = 0
    skills_active: list[str] = field(default_factory=list)
    last_result:   str = ""
    error:         str = ""
    elapsed_s:     float = 0.0


# Colores ANSI
COLORS = {
    "PLANNING":     "\033[93m",   # Amarillo
    "EXECUTION":    "\033[91m",   # Rojo
    "VERIFICATION": "\033[92m",   # Verde
    "DONE":         "\033[95m",   # Magenta
    "IDLE":         "\033[90m",   # Gris
}
RESET  = "\033[0m"
BOLD   = "\033[1m"
CYAN   = "\033[96m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"


class StatusBar:
    """
    Barra de estado ANSI en la línea inferior del terminal.
    Se actualiza in-place sin hacer scroll.
    
    Patrón de TinyClaw AGI: real-time status display.
    """
    
    def __init__(self) -> None:
        self._status = AgentStatus()
        self._lock   = threading.Lock()
        self._active = False
        self._thread: threading.Thread | None = None
        self._start_time = time.time()
    
    def start(self) -> None:
        """Inicia el refresh automático del status bar."""
        self._active = True
        self._start_time = time.time()
        self._thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._thread.start()
    
    def stop(self) -> None:
        """Para el refresh y limpia la línea."""
        self._active = False
        self._clear_bar()
    
    def set_phase(self, phase: str) -> None:
        with self._lock:
            self._status.phase = phase
        self._render()
    
    def set_tool(self, tool_name: str) -> None:
        with self._lock:
            self._status.current_tool = tool_name
        self._render()
    
    def set_iteration(self, n: int, max_n: int = 10) -> None:
        with self._lock:
            self._status.iteration    = n
            self._status.max_iteration = max_n
        self._render()
    
    def set_task(self, summary: str) -> None:
        with self._lock:
            self._status.task_summary = summary[:60]  # pyre-ignore[arg-type]
        self._render()
    
    def set_queue(self, pending: int, done: int) -> None:
        with self._lock:
            self._status.queue_pending = pending
            self._status.queue_done    = done
        self._render()
    
    def set_result(self, result: str) -> None:
        with self._lock:
            self._status.last_result = result[:80]  # pyre-ignore[arg-type]
        self._render()
    
    def update_from_queue(self) -> None:
        """Actualiza el estado de la queue desde SQLite."""
        try:
            from core.task_queue import task_queue
            summary = task_queue.status_summary()
            self.set_queue(
                pending=summary.get("PENDING", 0),
                done=summary.get("DONE", 0)
            )
        except Exception:
            pass  # error no crítico, continuar
    def _render(self) -> None:
        """Renderiza el status bar en la línea actual."""
        if not sys.stdout.isatty():
            return
        
        with self._lock:
            s = self._status
        
        elapsed = round(time.time() - self._start_time, 0)
        phase_color = COLORS.get(s.phase, "\033[90m")
        
        # Construir la línea de estado
        phase_str = f"{phase_color}{BOLD}[{s.phase}]{RESET}"
        iter_str  = f"{CYAN}iter {s.iteration}/{s.max_iteration}{RESET}" if s.iteration else ""
        tool_str  = f"🔩 {s.current_tool[:40]}" if s.current_tool else ""  # pyre-ignore[arg-type]
        queue_str = f"Q:{s.queue_pending}⏳ {s.queue_done}✅" if (s.queue_pending or s.queue_done) else ""
        time_str  = f"{YELLOW}{int(elapsed)}s{RESET}"
        
        parts = [phase_str]
        if iter_str:   parts.append(iter_str)
        if tool_str:   parts.append(tool_str)
        if queue_str:  parts.append(queue_str)
        parts.append(time_str)
        
        line = "  ".join(parts)
        
        # Imprimir en la línea actual con carriage return (sin newline)
        try:
            cols = os.get_terminal_size().columns
        except Exception:
            cols = 80
        
        # Pad/truncar a ancho del terminal
        visible = line.replace("\033[", "").split("m")  # aproximación al ancho visible
        sys.stdout.write(f"\r{line:<{cols}}\r")
        sys.stdout.flush()
    
    def _clear_bar(self) -> None:
        """Limpia la línea del status bar."""
        if sys.stdout.isatty():
            try:
                cols = os.get_terminal_size().columns
            except Exception:
                cols = 80
            sys.stdout.write(f"\r{' ' * cols}\r")
            sys.stdout.flush()
    
    def _refresh_loop(self) -> None:
        """Refresh automático cada 2s para actualizar el timer."""
        while self._active:
            self.update_from_queue()
            self._render()
            time.sleep(2.0)


class EIDOSTUILogger:
    """
    Logger que integra el TUI Dashboard con el output normal del CLI.
    Imprime mensajes encima del status bar sin corromperlo.
    """
    
    def __init__(self, status_bar: StatusBar | None = None) -> None:
        self.status_bar = status_bar
    
    def log(self, msg: str, color: str = "") -> None:
        """Imprime un mensaje limpio encima del status bar."""
        if self.status_bar:
            # Limpiar la línea del status bar primero
            sys.stdout.write("\r\033[K")
        
        if color:
            print(f"{color}{msg}{RESET}")
        else:
            print(msg)
        
        # Re-renderizar el status bar
        if self.status_bar:
            self.status_bar._render()
    
    def tool_start(self, tool_name: str, args_preview: str = "") -> None:
        if self.status_bar:
            self.status_bar.set_tool(f"{tool_name}({args_preview[:30]})")  # pyre-ignore[arg-type]
        self.log(f"\033[94m[🔩 TOOL]\033[0m {tool_name}: {args_preview[:60]}")  # pyre-ignore[arg-type]
    
    def tool_done(self, tool_name: str, result_preview: str = "") -> None:
        if self.status_bar:
            self.status_bar.set_result(result_preview)
            self.status_bar.set_tool("")
        self.log(f"\033[90m  ↳ {result_preview[:80]}\033[0m")  # pyre-ignore[arg-type]
    
    def phase(self, phase_name: str) -> None:
        if self.status_bar:
            self.status_bar.set_phase(phase_name)
        color = COLORS.get(phase_name, "\033[90m")
        self.log(f"{color}[{phase_name}]{RESET}")


# Singleton global
_status_bar = StatusBar()
_tui_logger = EIDOSTUILogger(_status_bar)


def get_status_bar() -> StatusBar:
    return _status_bar

def get_tui_logger() -> EIDOSTUILogger:
    return _tui_logger


# ── Test standalone ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import time
    
    print("=== EIDOS TUI Dashboard Demo ===\n")
    
    bar = StatusBar()
    log = EIDOSTUILogger(bar)
    
    bar.start()
    bar.set_task("Escanear la red local y guardar resultados")
    
    time.sleep(0.5)
    log.phase("PLANNING")
    time.sleep(1)
    
    log.phase("EXECUTION")
    bar.set_iteration(1, 10)
    log.tool_start("exec_shell", "nmap -sn 192.168.1.0/24")
    time.sleep(1.5)
    log.tool_done("exec_shell", "5 hosts encontrados")
    
    bar.set_iteration(2, 10)
    log.tool_start("write_file", "/tmp/scan_results.txt")
    time.sleep(0.8)
    log.tool_done("write_file", "Escrito OK")
    
    log.phase("VERIFICATION")
    time.sleep(0.8)
    
    log.phase("DONE")
    bar.set_iteration(2, 10)
    time.sleep(0.5)
    
    bar.stop()
    print("\n✅ TUI Dashboard demo completado")
