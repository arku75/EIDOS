"""
EIDOS core/tool_guard.py — Tool Governance Middleware
======================================================
Implementa la capa de control rígido descrita por KaliGPT:

  "EIDOS no confía en el modelo. Confía en la arquitectura."

Reglas duras (hard guards):
  1. observe_screen OBLIGATORIO antes de cualquier click/type/scroll en GUI
  2. exec_shell REQUIERE validación anti-destructiva (rm -rf, mkfs, etc.)
  3. Máx. TOOL_CAP llamadas de tool por ciclo kernel
  4. Namespaces: web tools ≠ desktop tools (no mezclar sin contexto)
  5. Si state_hash no cambia después de 2 acciones GUI → ABORT

Integración:
    from core.tool_guard import ToolGuard
    guard = ToolGuard()
    ok, reason = guard.validate(tool_name, args, state)
    if ok:
        result = execute_tool(tool_name, args)
        guard.record(tool_name, args, result)
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

# ── Constantes de governance ──────────────────────────────────────────────────
TOOL_CAP            = 15           # máx. tool-calls por ciclo de kernel
SCREEN_STALE_LIMIT  = 2            # abortar si N acciones GUI sin cambio de estado
REQUIRE_OBSERVE_BEFORE_GUI = True  # ob
SHELL_DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",        # rm -rf /
    r"mkfs\.",              # formatear disco
    r"dd\s+.*of=/dev/",     # dd → disco
    r":\(\)\{.*\};.*:",     # fork bomb
    r"chmod\s+-R\s+777\s+/",# chmod 777 /
    r">\s*/dev/sda",        # sobreescribir dispositivo
    r"shred\s+/dev/",       # shred disco
    r"wipefs",              # wipefs
    r"fdisk.*--delete",     # fdisk delete
]

# Tools que requieren observe_screen previo
GUI_TOOLS = {
    "click_gui_element", "mouse_click", "type_in_gui",
    "keyboard_type", "keyboard_press", "keyboard_hotkey",
    "dom_click", "dom_type", "scroll_gui",
}

# Tools que son "solo web" (Playwright activo)
WEB_ONLY_TOOLS = {"dom_click", "dom_type", "dom_get_text", "web_navigate"}

# Tools que son "solo desktop" (xdotool)
DESKTOP_ONLY_TOOLS = {"click_gui_element", "type_in_gui", "scroll_gui"}


@dataclass
class ToolCall:
    name:       str
    args:       dict[str, Any]
    timestamp:  float = field(default_factory=time.time)
    result:     str   = ""
    state_hash: str   = ""          # sha256 del screenshot previo


@dataclass
class GuardState:
    call_count:          int  = 0
    last_observe_time:   float = 0.0
    consecutive_no_change: int = 0
    last_state_hash:     str   = ""
    history:             list[ToolCall] = field(default_factory=list)
    aborted:             bool  = False
    abort_reason:        str   = ""


class ToolGuard:
    """
    Middleware entre el LLM y las tool implementations.
    validate() → (allow: bool, reason: str)
    record()   → actualiza estado tras la ejecución
    """

    def __init__(self, tool_cap: int = TOOL_CAP) -> None:
        self.tool_cap = tool_cap
        self.state    = GuardState()

    def reset(self) -> None:
        """Reinicia el estado para un nuevo ciclo del kernel."""
        self.state = GuardState()

    # ── Validación pre-ejecución ──────────────────────────────────────────────

    def validate(
        self,
        tool_name: str,
        args: dict[str, Any],
        current_state_hash: str = "",
        browser_active: bool = False,
    ) -> tuple[bool, str]:
        """
        Valida si se permite ejecutar esta tool.
        Returns: (allowed, reason_if_not)
        """
        s = self.state

        # 1. Límite de iteraciones
        if s.call_count >= self.tool_cap:
            return False, f"TOOL_CAP alcanzado ({self.tool_cap}). Fin del ciclo."

        # 2. Estado abortado
        if s.aborted:
            return False, f"Ciclo abortado: {s.abort_reason}"

        # 3. GUI sin observe_screen previo
        if REQUIRE_OBSERVE_BEFORE_GUI and tool_name in GUI_TOOLS:
            elapsed = time.time() - s.last_observe_time
            if s.last_observe_time == 0 or elapsed > 30:
                return False, (
                    f"'{tool_name}' requiere observe_screen previo "
                    f"(última observación: {elapsed:.0f}s atrás). "
                    f"Ejecuta observe_screen antes de interactuar con GUI."
                )

        # 4. Estado sin cambio → posible loop ciego
        if tool_name in GUI_TOOLS and current_state_hash:
            if current_state_hash == s.last_state_hash:
                s.consecutive_no_change += 1
                if s.consecutive_no_change >= SCREEN_STALE_LIMIT:
                    s.aborted = True
                    s.abort_reason = f"Pantalla sin cambio tras {s.consecutive_no_change} acciones GUI"
                    return False, s.abort_reason
            else:
                s.consecutive_no_change = 0

        # 5. Web tools sin browser activo
        if tool_name in WEB_ONLY_TOOLS and not browser_active:
            return False, f"'{tool_name}' requiere browser Playwright activo."

        # 6. Validación anti-destructiva de exec_shell
        if tool_name == "exec_shell":
            cmd = args.get("command", "")
            danger = self._check_dangerous_shell(cmd)
            if danger:
                return False, f"BLOQUEADO — comando peligroso detectado: {danger}"

        return True, ""

    def _check_dangerous_shell(self, cmd: str) -> str:
        """Retorna descripción del patrón peligroso encontrado, o ''."""
        for pattern in SHELL_DANGEROUS_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                return pattern
        return ""

    # ── Registro post-ejecución ───────────────────────────────────────────────

    def record(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: str,
        new_state_hash: str = "",
    ) -> None:
        """Registra el resultado de una tool call y actualiza el estado."""
        s = self.state
        s.call_count += 1

        if tool_name == "observe_screen":
            s.last_observe_time = time.time()
            if new_state_hash:
                s.last_state_hash = new_state_hash

        if new_state_hash:
            if new_state_hash != s.last_state_hash:
                s.consecutive_no_change = 0
            s.last_state_hash = new_state_hash

        s.history.append(ToolCall(
            name=tool_name, args=args,
            result=result[:500],  # pyre-ignore[arg-type]
            state_hash=new_state_hash
        ))

    # ── Reflection Injector (Oráculo 2: "obligar reflexión post-tool") ────────

    def get_reflection_prompt(self, tool_name: str, result: str) -> str:
        """
        Genera un prompt de reflexión obligatorio después de cada tool call.
        Esto evita loops ciegos (el LLM tiene que confirmar que el resultado
        fue el esperado antes de continuar).
        """
        s = self.state
        return (
            f"[REFLECT] Llamada {s.call_count}/{self.tool_cap}\n"
            f"Tool: {tool_name}\n"
            f"Resultado: {result[:300]}\n\n"  # pyre-ignore[arg-type]
            f"Preguntas de reflexión OBLIGATORIAS (responde brevemente):\n"
            f"1. ¿El resultado fue el esperado? SÍ/NO + por qué\n"
            f"2. ¿El estado del sistema cambió como esperabas?\n"
            f"3. ¿Cuál es el siguiente paso lógico?\n"
            f"4. ¿Hay algún problema que necesite corrección antes de continuar?\n"
        )

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> str:
        s = self.state
        lines = [
            f"[🛡️  TOOL GUARD] Ciclo {s.call_count}/{self.tool_cap}",
            f"  Última observación: {time.time()-s.last_observe_time:.0f}s atrás",
            f"  Sin cambio consecutivo: {s.consecutive_no_change}/{SCREEN_STALE_LIMIT}",
            f"  State hash: {s.last_state_hash[:12] or 'ninguno'}...",  # pyre-ignore[arg-type]
            f"  Abortado: {s.aborted} {('— '+s.abort_reason) if s.aborted else ''}",
        ]
        return "\n".join(lines)

    def summary_for_llm(self) -> str:
        """Resumen compacto del estado para inyectar en el prompt."""
        s = self.state
        if s.aborted:
            return f"⛔ ABORTADO: {s.abort_reason}"
        if s.consecutive_no_change > 0:
            return f"⚠️  Pantalla sin cambio ({s.consecutive_no_change} veces). Prueba otra acción."
        return f"🛡️  {s.call_count}/{self.tool_cap} tool-calls usados."


# Singleton global (por sesión de kernel)
_guard: Optional[ToolGuard] = None

def get_tool_guard(reset: bool = False) -> ToolGuard:
    global _guard
    if _guard is None or reset:
        _guard = ToolGuard()
    return _guard
