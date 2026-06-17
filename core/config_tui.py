"""
EIDOS core/config_tui.py — TUI de Configuración Interactiva
============================================================
Interfaz interactiva de configuración con navegación por flechas.

Controles:
  ↑↓              Navegar entre opciones
  Enter           Toggle TRUE/FALSE
  Esc / q         Salir del TUI
  s               Guardar configuración

Opciones Configurables:
  - Sandbox Mode (ON/OFF)
  - Auto-Learning (ON/OFF)
  - Phoenix Guardian (ON/OFF)
  - Mirror Guardian (ON/OFF)
  - Auto-Checkpoint Interval
  - Purple Team Mode (unrestricted)
  - RAM Guardian (ON/OFF)
  - Smart Cache (ON/OFF)

Uso:
    from core.config_tui import ConfigTUI

    tui = ConfigTUI()
    tui.run()  # Abre interfaz interactiva
"""
from __future__ import annotations

import json
import os
import sys
import termios
import tty
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, List, Any, Dict


# ── Configuración ────────────────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".eidos"
CONFIG_FILE = CONFIG_DIR / "config.json"


# ── Tipos básicos ────────────────────────────────────────────────────────────

@dataclass
class EidosConfig:
    """Configuración de EIDOS."""

    # Modos operacionales
    sandbox_mode: bool = False
    purple_team_mode: bool = True  # Sin restricciones por defecto
    current_mode: str = "PLAN+EDIT"  # PLAN, EDIT, PLAN+EDIT

    # Guardians
    phoenix_enabled: bool = True
    mirror_enabled: bool = True

    # Optimizaciones
    auto_learning: bool = True
    auto_checkpoint: bool = True
    checkpoint_interval_min: int = 30
    ram_guardian: bool = False  # Desactivado por defecto
    smart_cache: bool = True

    # Purple Team Arsenal
    kali_tools_enabled: bool = True
    auto_installer: bool = True

    # Misc
    verbose: bool = True
    save_logs: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Convierte la config a diccionario."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EidosConfig:
        """Crea config desde diccionario."""
        return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})

    def save(self, path: Optional[Path] = None) -> None:
        """Guarda la configuración a disco."""
        if path is None:
            path = CONFIG_FILE

        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> EidosConfig:
        """Carga la configuración desde disco."""
        if path is None:
            path = CONFIG_FILE

        if not path.exists():
            return cls()

        try:
            with open(path, "r") as f:
                data = json.load(f)
            return cls.from_dict(data)
        except Exception:
            return cls()


@dataclass
class ConfigOption:
    """Opción de configuración en el TUI."""
    key: str
    label: str
    description: str
    type: str  # bool, int, str
    current_value: Any
    choices: Optional[List[Any]] = None


# ── Config TUI ───────────────────────────────────────────────────────────────

class ConfigTUI:
    """
    Interfaz TUI para configuración de EIDOS.

    Permite navegar con flechas y cambiar opciones con Enter.
    """

    def __init__(self):
        self.config = EidosConfig.load()
        self.selected_index = 0
        self.running = True
        self.modified = False

        # Opciones configurables
        self.options = self._build_options()

    def _build_options(self) -> List[ConfigOption]:
        """Construye la lista de opciones configurables."""
        return [
            ConfigOption(
                key="sandbox_mode",
                label="Sandbox Mode",
                description="Solo probar en sandbox - NO modificar sistema real",
                type="bool",
                current_value=self.config.sandbox_mode
            ),
            ConfigOption(
                key="purple_team_mode",
                label="Purple Team Mode",
                description="Sin restricciones - acceso completo a Kali tools",
                type="bool",
                current_value=self.config.purple_team_mode
            ),
            ConfigOption(
                key="current_mode",
                label="Operation Mode",
                description="Modo de operación actual",
                type="str",
                current_value=self.config.current_mode,
                choices=["PLAN", "EDIT", "PLAN+EDIT"]
            ),
            ConfigOption(
                key="phoenix_enabled",
                label="Phoenix Guardian",
                description="Auto-resurrección si EIDOS no responde (3h)",
                type="bool",
                current_value=self.config.phoenix_enabled
            ),
            ConfigOption(
                key="mirror_enabled",
                label="Mirror Guardian",
                description="Validación cruzada de auto-mejoras",
                type="bool",
                current_value=self.config.mirror_enabled
            ),
            ConfigOption(
                key="auto_learning",
                label="Auto-Learning",
                description="Aprender de cada acción automáticamente",
                type="bool",
                current_value=self.config.auto_learning
            ),
            ConfigOption(
                key="auto_checkpoint",
                label="Auto-Checkpoint",
                description="Guardar estado automáticamente",
                type="bool",
                current_value=self.config.auto_checkpoint
            ),
            ConfigOption(
                key="checkpoint_interval_min",
                label="Checkpoint Interval",
                description="Intervalo de auto-checkpoint en minutos",
                type="int",
                current_value=self.config.checkpoint_interval_min,
                choices=[10, 20, 30, 60, 120]
            ),
            ConfigOption(
                key="ram_guardian",
                label="RAM Guardian",
                description="Monitoreo y limpieza automática de RAM",
                type="bool",
                current_value=self.config.ram_guardian
            ),
            ConfigOption(
                key="smart_cache",
                label="Smart Cache",
                description="Cache inteligente (Vision/OCR ~1000× faster)",
                type="bool",
                current_value=self.config.smart_cache
            ),
            ConfigOption(
                key="kali_tools_enabled",
                label="Kali Tools",
                description="Habilitar Purple Team Arsenal",
                type="bool",
                current_value=self.config.kali_tools_enabled
            ),
            ConfigOption(
                key="auto_installer",
                label="Auto-Installer",
                description="Instalar dependencias automáticamente",
                type="bool",
                current_value=self.config.auto_installer
            ),
            ConfigOption(
                key="verbose",
                label="Verbose Mode",
                description="Mostrar logs detallados",
                type="bool",
                current_value=self.config.verbose
            ),
            ConfigOption(
                key="save_logs",
                label="Save Logs",
                description="Guardar logs a disco",
                type="bool",
                current_value=self.config.save_logs
            ),
        ]

    def _clear_screen(self) -> None:
        """Limpia la pantalla."""
        os.system("clear" if os.name != "nt" else "cls")

    def _get_key(self) -> str:
        """Lee una tecla del teclado sin echo."""
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)

        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)

            # Detectar secuencias de escape (flechas)
            if ch == "\x1b":
                ch2 = sys.stdin.read(1)
                if ch2 == "[":
                    ch3 = sys.stdin.read(1)
                    if ch3 == "A":
                        return "UP"
                    elif ch3 == "B":
                        return "DOWN"
                    elif ch3 == "C":
                        return "RIGHT"
                    elif ch3 == "D":
                        return "LEFT"
                return "ESC"

            return ch

        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def _render(self) -> None:
        """Renderiza la interfaz TUI."""
        self._clear_screen()

        # Header
        print("╔═══════════════════════════════════════════════════════════════════════╗")
        print("║                   EIDOS CONFIGURATION INTERFACE                       ║")
        print("╚═══════════════════════════════════════════════════════════════════════╝\n")

        # Instrucciones
        print("  ↑↓ Navigate   Enter Toggle   s Save   q Quit\n")
        print("─" * 75 + "\n")

        # Opciones
        for i, opt in enumerate(self.options):
            # Indicador de selección
            cursor = "→" if i == self.selected_index else " "

            # Formatear valor
            if opt.type == "bool":
                value_str = "TRUE " if opt.current_value else "FALSE"
                color = "\033[92m" if opt.current_value else "\033[91m"  # Verde/Rojo
                value_display = f"{color}{value_str}\033[0m"
            elif opt.type == "int":
                value_display = f"{opt.current_value} min"
            elif opt.type == "str":
                value_display = opt.current_value
            else:
                value_display = str(opt.current_value)

            # Label con padding
            label_padded = f"{opt.label}:".ljust(25)

            # Línea principal
            print(f"{cursor} {label_padded} [{value_display}]")

            # Descripción (solo para opción seleccionada)
            if i == self.selected_index:
                desc_indent = " " * 3
                print(f"{desc_indent}\033[90m{opt.description}\033[0m")

            print()

        # Footer
        print("─" * 75)

        if self.modified:
            print("\n⚠️  Configuración modificada - presiona 's' para guardar")

    def _toggle_current_option(self) -> None:
        """Toggle el valor de la opción actual."""
        opt = self.options[self.selected_index]

        if opt.type == "bool":
            opt.current_value = not opt.current_value
            setattr(self.config, opt.key, opt.current_value)
            self.modified = True

        elif opt.type == "int" and opt.choices:
            # Ciclar entre opciones
            current_idx = opt.choices.index(opt.current_value)
            next_idx = (current_idx + 1) % len(opt.choices)
            opt.current_value = opt.choices[next_idx]
            setattr(self.config, opt.key, opt.current_value)
            self.modified = True

        elif opt.type == "str" and opt.choices:
            # Ciclar entre opciones
            current_idx = opt.choices.index(opt.current_value)
            next_idx = (current_idx + 1) % len(opt.choices)
            opt.current_value = opt.choices[next_idx]
            setattr(self.config, opt.key, opt.current_value)
            self.modified = True

    def _save_config(self) -> None:
        """Guarda la configuración."""
        try:
            self.config.save()
            self.modified = False

            # Mostrar confirmación temporal
            self._clear_screen()
            print("\n\n")
            print("  ✅ Configuración guardada exitosamente!")
            print()
            print(f"  Archivo: {CONFIG_FILE}")
            print()
            print("  Presiona cualquier tecla para continuar...")

            self._get_key()

        except Exception as e:
            self._clear_screen()
            print("\n\n")
            print(f"  ❌ Error guardando configuración: {e}")
            print()
            print("  Presiona cualquier tecla para continuar...")

            self._get_key()

    def run(self) -> None:
        """Ejecuta el TUI en modo interactivo."""
        try:
            while self.running:
                self._render()

                key = self._get_key()

                if key == "UP":
                    self.selected_index = (self.selected_index - 1) % len(self.options)

                elif key == "DOWN":
                    self.selected_index = (self.selected_index + 1) % len(self.options)

                elif key == "\r" or key == "\n":  # Enter
                    self._toggle_current_option()

                elif key.lower() == "s":
                    self._save_config()

                elif key.lower() == "q" or key == "ESC":
                    if self.modified:
                        self._clear_screen()
                        print("\n\n")
                        print("  ⚠️  Hay cambios sin guardar")
                        print()
                        print("  s - Guardar y salir")
                        print("  q - Salir sin guardar")
                        print("  c - Cancelar")
                        print()

                        choice = self._get_key()

                        if choice.lower() == "s":
                            self._save_config()
                            self.running = False
                        elif choice.lower() == "q":
                            self.running = False
                    else:
                        self.running = False

        except KeyboardInterrupt:
            pass

        finally:
            self._clear_screen()
            print("\n👋 Configuración cerrada\n")


# ── Funciones de utilidad ───────────────────────────────────────────────────

def get_config() -> EidosConfig:
    """Obtiene la configuración actual de EIDOS."""
    return EidosConfig.load()


def save_config(config: EidosConfig) -> None:
    """Guarda la configuración de EIDOS."""
    config.save()


# ── CLI rápido ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Verificar si estamos en terminal interactivo
    if not sys.stdin.isatty():
        print("❌ Este programa requiere un terminal interactivo")
        sys.exit(1)

    tui = ConfigTUI()
    tui.run()
