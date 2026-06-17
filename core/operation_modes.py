"""
EIDOS core/operation_modes.py — Modos de Operación PLAN/EDIT/PLAN+EDIT
=========================================================================
Maneja los 3 modos de operación específicos solicitados por el usuario.

Modos:
  PLAN        - Solo observar/estudiar (NO modificar nada)
  EDIT        - Auto-mejora en sandbox únicamente
  PLAN+EDIT   - Control completo (modo por defecto)

Cambio de Modo:
  - Shift+TAB: Cicla entre modos
  - /mode <modo>: Cambia a modo específico
  - Al inicio: Siempre PLAN+EDIT

Comportamientos por Modo:

  PLAN:
    ✅ Leer archivos
    ✅ Buscar en web/docs/wiki
    ✅ Analizar código
    ✅ Ejecutar comandos de solo lectura (ls, cat, grep)
    ❌ Escribir archivos
    ❌ Ejecutar comandos destructivos
    ❌ Modificar sistema

  EDIT:
    ✅ Todo lo de PLAN
    ✅ Escribir en sandbox
    ✅ Auto-mejoras en réplica
    ❌ Modificar archivos reales de EIDOS
    ❌ Ejecutar comandos en sistema real

  PLAN+EDIT:
    ✅ TODO sin restricciones
    ✅ Acceso completo a Kali tools
    ✅ Modificar cualquier archivo
    ✅ Ejecutar cualquier comando

Uso:
    from core.operation_modes import OperationModeManager

    omm = OperationModeManager()

    # Cambiar modo
    omm.set_mode("PLAN")

    # Verificar si acción está permitida
    if omm.can_write_file("/path/to/file"):
        # Escribir archivo
        pass

    # Ciclar entre modos
    omm.cycle_mode()  # PLAN → EDIT → PLAN+EDIT → PLAN → ...
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, List


# ── Tipos básicos ────────────────────────────────────────────────────────────

@dataclass
class ModePermissions:
    """Permisos de cada modo."""
    can_read: bool = True
    can_write_real: bool = False
    can_write_sandbox: bool = False
    can_execute_readonly: bool = True
    can_execute_write: bool = False
    can_web_fetch: bool = True
    can_modify_eidos: bool = False
    can_use_kali_tools: bool = False


# ── Operation Mode Manager ───────────────────────────────────────────────────

class OperationModeManager:
    """
    Gestor de modos de operación de EIDOS (PLAN/EDIT/PLAN+EDIT).

    Maneja permisos y restricciones de cada modo.
    """

    MODES = ["PLAN", "EDIT", "PLAN+EDIT"]

    def __init__(self, initial_mode: str = "PLAN+EDIT"):
        self.current_mode = initial_mode
        self._mode_history: List[tuple[str, float]] = []
        self._mode_change_callbacks: List[Callable] = []

        # Registrar modo inicial
        self._log_mode_change(initial_mode)

    def _log_mode_change(self, mode: str) -> None:
        """Registra un cambio de modo en el historial."""
        import time
        self._mode_history.append((mode, time.time()))

    def _get_permissions(self, mode: str) -> ModePermissions:
        """Obtiene los permisos de un modo específico."""
        if mode == "PLAN":
            return ModePermissions(
                can_read=True,
                can_write_real=False,
                can_write_sandbox=False,
                can_execute_readonly=True,
                can_execute_write=False,
                can_web_fetch=True,
                can_modify_eidos=False,
                can_use_kali_tools=False,
            )

        elif mode == "EDIT":
            return ModePermissions(
                can_read=True,
                can_write_real=False,
                can_write_sandbox=True,
                can_execute_readonly=True,
                can_execute_write=False,  # Solo en sandbox
                can_web_fetch=True,
                can_modify_eidos=False,  # Solo en sandbox
                can_use_kali_tools=False,
            )

        elif mode == "PLAN+EDIT":
            return ModePermissions(
                can_read=True,
                can_write_real=True,
                can_write_sandbox=True,
                can_execute_readonly=True,
                can_execute_write=True,
                can_web_fetch=True,
                can_modify_eidos=True,
                can_use_kali_tools=True,
            )

        else:
            # Modo desconocido - modo restrictivo por defecto
            return ModePermissions()

    @property
    def permissions(self) -> ModePermissions:
        """Obtiene los permisos del modo actual."""
        return self._get_permissions(self.current_mode)

    def set_mode(self, mode: str) -> bool:
        """
        Cambia el modo de operación.

        Args:
            mode: Nuevo modo (PLAN, EDIT, PLAN+EDIT)

        Returns:
            True si el cambio fue exitoso, False si el modo es inválido.
        """
        mode = mode.upper()

        if mode not in self.MODES:
            return False

        old_mode = self.current_mode
        self.current_mode = mode
        self._log_mode_change(mode)

        # Notificar a callbacks
        for callback in self._mode_change_callbacks:
            try:
                callback(old_mode, mode)
            except Exception:
                pass  # error no crítico, continuar
        return True

    def cycle_mode(self) -> str:
        """
        Cicla entre modos: PLAN → EDIT → PLAN+EDIT → PLAN → ...

        Returns:
            El nuevo modo
        """
        current_idx = self.MODES.index(self.current_mode)
        next_idx = (current_idx + 1) % len(self.MODES)
        next_mode = self.MODES[next_idx]

        self.set_mode(next_mode)
        return next_mode

    def on_mode_change(self, callback: Callable[[str, str], None]) -> None:
        """
        Registra un callback que se ejecutará en cada cambio de modo.

        Args:
            callback: Función (old_mode, new_mode) -> None
        """
        self._mode_change_callbacks.append(callback)

    # ── Verificaciones de permisos ───────────────────────────────────────────

    def can_read_file(self, path: str) -> bool:
        """Verifica si se puede leer un archivo."""
        return self.permissions.can_read

    def can_write_file(self, path: str) -> bool:
        """Verifica si se puede escribir un archivo."""
        path_obj = Path(path)

        # Verificar si es sandbox
        is_sandbox = ".eidos/mirror/sandbox" in str(path_obj) or \
                     "/tmp/" in str(path_obj) or \
                     str(path_obj).startswith("/tmp")

        if is_sandbox:
            return self.permissions.can_write_sandbox

        # Archivo real
        return self.permissions.can_write_real

    def can_execute_command(self, command: str) -> bool:
        """
        Verifica si se puede ejecutar un comando.

        Args:
            command: Comando a verificar

        Returns:
            True si está permitido, False si está bloqueado
        """
        # Comandos de solo lectura (siempre permitidos)
        readonly_cmds = [
            "ls", "cat", "head", "tail", "grep", "find", "which",
            "echo", "pwd", "whoami", "id", "uname", "date",
            "ps", "top", "free", "df", "du", "file", "wc",
            "stat", "readlink", "basename", "dirname",
        ]

        # Comandos destructivos (solo en PLAN+EDIT)
        write_cmds = [
            "rm", "mv", "cp", "mkdir", "rmdir", "touch",
            "chmod", "chown", "ln", "dd", "mkfs",
            "apt", "apt-get", "pip", "npm", "cargo",
            "git", "docker", "systemctl", "service",
        ]

        # Extraer comando base
        cmd_base = command.strip().split()[0] if command.strip() else ""

        # Verificar si es comando de solo lectura
        if any(cmd_base.endswith(ro) for ro in readonly_cmds):
            return self.permissions.can_execute_readonly

        # Verificar si es comando de escritura
        if any(cmd_base.endswith(wr) for wr in write_cmds):
            return self.permissions.can_execute_write

        # Por defecto, considerar como comando de escritura
        return self.permissions.can_execute_write

    def can_modify_eidos_code(self, file_path: str) -> bool:
        """
        Verifica si se puede modificar código de EIDOS.

        Args:
            file_path: Path al archivo

        Returns:
            True si está permitido
        """
        path_obj = Path(file_path)

        # Verificar si es archivo de EIDOS
        is_eidos_file = "core/" in str(path_obj) or \
                        str(path_obj).startswith("/home/ser/EIDOS/core")

        if not is_eidos_file:
            # No es archivo de EIDOS - usar permisos normales
            return self.can_write_file(file_path)

        # Es archivo de EIDOS - verificar modo
        return self.permissions.can_modify_eidos

    def can_use_tool(self, tool_name: str) -> bool:
        """
        Verifica si se puede usar una herramienta específica.

        Args:
            tool_name: Nombre de la tool (ej: "nmap", "metasploit", etc.)

        Returns:
            True si está permitido
        """
        # Tools de Kali Linux (Purple Team Arsenal)
        kali_tools = [
            "nmap", "metasploit", "sqlmap", "burpsuite", "wireshark",
            "aircrack", "john", "hashcat", "hydra", "gobuster",
            "nikto", "zap", "beef", "empire", "mimikatz",
        ]

        if any(tool_name.lower().startswith(kt) for kt in kali_tools):
            return self.permissions.can_use_kali_tools

        # Otras tools - siempre permitidas
        return True

    def get_mode_indicator(self) -> str:
        """
        Retorna el indicador visual del modo actual para el prompt.

        Returns:
            String formateado para mostrar en el prompt
        """
        indicators = {
            "PLAN": "\033[94m[EIDOS|PLAN]\033[0m",  # Azul
            "EDIT": "\033[93m[EIDOS|EDIT]\033[0m",  # Amarillo
            "PLAN+EDIT": "\033[92m[EIDOS|FULL]\033[0m",  # Verde
        }

        return indicators.get(self.current_mode, f"[EIDOS|{self.current_mode}]")

    def get_status(self) -> dict:
        """Retorna el estado actual del modo manager."""
        return {
            "current_mode": self.current_mode,
            "permissions": {
                "can_read": self.permissions.can_read,
                "can_write_real": self.permissions.can_write_real,
                "can_write_sandbox": self.permissions.can_write_sandbox,
                "can_execute_readonly": self.permissions.can_execute_readonly,
                "can_execute_write": self.permissions.can_execute_write,
                "can_web_fetch": self.permissions.can_web_fetch,
                "can_modify_eidos": self.permissions.can_modify_eidos,
                "can_use_kali_tools": self.permissions.can_use_kali_tools,
            },
            "mode_history": [
                {
                    "mode": mode,
                    "timestamp": datetime.fromtimestamp(ts).isoformat()
                }
                for mode, ts in self._mode_history[-10:]  # Últimos 10
            ]
        }

    def print_status(self) -> None:
        """Imprime el estado del modo manager."""
        print("╔═══════════════════════════════════════════════════════════════════════╗")
        print("║                     OPERATION MODE MANAGER STATUS                     ║")
        print("╚═══════════════════════════════════════════════════════════════════════╝\n")

        print(f"Current Mode: {self.get_mode_indicator()}\n")

        print("Permissions:")
        perms = self.permissions
        print(f"  Read files:          {'✅' if perms.can_read else '❌'}")
        print(f"  Write real files:    {'✅' if perms.can_write_real else '❌'}")
        print(f"  Write sandbox:       {'✅' if perms.can_write_sandbox else '❌'}")
        print(f"  Execute (readonly):  {'✅' if perms.can_execute_readonly else '❌'}")
        print(f"  Execute (write):     {'✅' if perms.can_execute_write else '❌'}")
        print(f"  Web fetch:           {'✅' if perms.can_web_fetch else '❌'}")
        print(f"  Modify EIDOS:        {'✅' if perms.can_modify_eidos else '❌'}")
        print(f"  Use Kali tools:      {'✅' if perms.can_use_kali_tools else '❌'}")

        print("\nMode Descriptions:\n")

        print("  PLAN:      Solo observar - NO modificar nada")
        print("  EDIT:      Auto-mejoras en sandbox únicamente")
        print("  PLAN+EDIT: Control completo (Purple Team mode)")

        print("\nMode Cycling:")
        print("  Shift+TAB: PLAN → EDIT → PLAN+EDIT → PLAN → ...")


# ── Singleton global ─────────────────────────────────────────────────────────

_operation_mode_manager: Optional[OperationModeManager] = None

def get_operation_mode_manager() -> OperationModeManager:
    """Obtiene la instancia singleton del operation mode manager."""
    global _operation_mode_manager
    if _operation_mode_manager is None:
        _operation_mode_manager = OperationModeManager()
    return _operation_mode_manager


# ── CLI rápido ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    omm = OperationModeManager()

    if len(sys.argv) < 2:
        omm.print_status()
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "set":
        if len(sys.argv) < 3:
            print("Uso: python operation_modes.py set <PLAN|EDIT|PLAN+EDIT>")
            sys.exit(1)

        mode = sys.argv[2]
        if omm.set_mode(mode):
            print(f"✅ Modo cambiado a: {mode}")
        else:
            print(f"❌ Modo inválido: {mode}")

    elif cmd == "cycle":
        new_mode = omm.cycle_mode()
        print(f"🔄 Modo cambiado a: {new_mode}")

    elif cmd == "status":
        omm.print_status()

    elif cmd == "can":
        if len(sys.argv) < 4:
            print("Uso: python operation_modes.py can <read|write|execute> <path_or_command>")
            sys.exit(1)

        action = sys.argv[2]
        target = " ".join(sys.argv[3:])

        if action == "read":
            can_do = omm.can_read_file(target)
        elif action == "write":
            can_do = omm.can_write_file(target)
        elif action == "execute":
            can_do = omm.can_execute_command(target)
        else:
            print(f"❌ Acción desconocida: {action}")
            sys.exit(1)

        icon = "✅" if can_do else "❌"
        status = "PERMITIDO" if can_do else "BLOQUEADO"
        print(f"{icon} {action.upper()} '{target}': {status}")

    else:
        print(f"❌ Comando desconocido: {cmd}")
        print("Comandos: set, cycle, status, can")
        sys.exit(1)
