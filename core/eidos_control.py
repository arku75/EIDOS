"""
EIDOS Control — Las Manos de EIDOS
Control de mouse, teclado, y automatización de aplicaciones GUI

Capacidades:
- Mover y hacer clic con el mouse
- Escribir texto y atajos de teclado
- Abrir, cerrar, minimizar ventanas
- Interactuar con cualquier aplicación
- Automatización de flujos de trabajo
- Gestos complejos (drag, scroll, combos)

Autonomía: EIDOS decide qué hacer, cuándo hacerlo, y cómo.
"""
import subprocess
import time
import threading
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict, Callable
import random


@dataclass
class Point:
    """Punto en pantalla"""
    x: int
    y: int


@dataclass
class Action:
    """Acción a realizar"""
    action_type: str  # "move", "click", "type", "key", "wait", "scroll"
    params: Dict
    description: str


class EidosControl:
    """
    Sistema de control de EIDOS
    
    Le permite a EIDOS:
    - Controlar el mouse (mover, clic, drag, scroll)
    - Escribir texto y enviar atajos
    - Gestionar ventanas (mover, redimensionar, minimizar)
    - Abrir aplicaciones
    - Ejecutar secuencias complejas de acciones
    
    Autonomía:
    - Decide qué aplicación usar
    - Decide dónde hacer clic
    - Ajusta velocidad y timing según contexto
    - Aprende de errores y reintenta
    """
    
    def __init__(self):
        """Inicializa el sistema de control"""
        
        # Verificar herramientas disponibles
        self.has_xdotool = self._check_xdotool()
        self.has_wmctrl = self._check_wmctrl()
        
        # Pantalla
        self.screen_width = 1920
        self.screen_height = 1080
        self._update_screen_size()
        
        # Historial de acciones
        self.action_history: List[Action] = []
        
        # Cola de acciones pendientes
        self.pending_actions: List[Action] = []
        self.executing = False
        
        print(f"🖱️  [EIDOS Control] Inicializado")
        print(f"   Pantalla: {self.screen_width}x{self.screen_height}")
        print(f"   xdotool: {'✅' if self.has_xdotool else '❌'}")
        print(f"   wmctrl: {'✅' if self.has_wmctrl else '❌'}")
    
    def _check_xdotool(self) -> bool:
        """Verifica si xdotool está instalado"""
        try:
            result = subprocess.run(
                ["which", "xdotool"],
                capture_output=True,
                timeout=2
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def _check_wmctrl(self) -> bool:
        """Verifica si wmctrl está instalado"""
        try:
            result = subprocess.run(
                ["which", "wmctrl"],
                capture_output=True,
                timeout=2
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def _update_screen_size(self):
        """Actualiza el tamaño de pantalla"""
        try:
            result = subprocess.run(
                ["xdotool", "getdisplaygeometry"],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                self.screen_width = int(parts[0])
                self.screen_height = int(parts[1])
        except Exception:
            pass  # error no crítico, continuar
    def get_mouse_position(self) -> Point:
        """
        Obtiene posición actual del mouse
        
        Returns:
            Point con coordenadas (x, y)
        """
        try:
            result = subprocess.run(
                ["xdotool", "getmouselocation", "--shell"],
                capture_output=True,
                text=True,
                timeout=2
            )
            x, y = 0, 0
            for line in result.stdout.split('\n'):
                if line.startswith('X='):
                    x = int(line.split('=')[1])
                elif line.startswith('Y='):
                    y = int(line.split('=')[1])
            return Point(x, y)
        except Exception:
            return Point(0, 0)
    
    def move_mouse(self, x: int, y: int, duration: float = 0.5) -> bool:
        """
        Mueve el mouse a una posición
        
        Args:
            x: Coordenada X
            y: Coordenada Y
            duration: Tiempo de movimiento (segundos)

        Returns:
            True si exitoso
        """
        # SEGURIDAD (S116): no tocar el ratón si SER está activo o congeló el control
        try:
            from core.eidos_input_safety import can_control_input
            _ok, _why = can_control_input()
            if not _ok:
                print(f"   🛑 control de ratón cedido a SER: {_why}")
                return False
        except Exception:
            pass
        if not self.has_xdotool:
            return False

        try:
            # Movimiento suave en pasos
            start = self.get_mouse_position()
            steps = int(duration * 60)  # 60 FPS
            
            if steps > 1:
                for i in range(steps + 1):
                    t = i / steps
                    # Interpolación suave (ease-out)
                    t = 1 - (1 - t) ** 2
                    curr_x = int(start.x + (x - start.x) * t)
                    curr_y = int(start.y + (y - start.y) * t)
                    subprocess.run(
                        ["xdotool", "mousemove", str(curr_x), str(curr_y)],
                        timeout=1,
                        check=False
                    )
                    time.sleep(duration / steps)
            else:
                subprocess.run(
                    ["xdotool", "mousemove", str(x), str(y)],
                    timeout=2,
                    check=True
                )
            
            self._log_action("move", {"x": x, "y": y}, f"Mouse a ({x}, {y})")
            return True
            
        except Exception as e:
            print(f"   ⚠️  Error moviendo mouse: {e}")
            return False
    
    def click(self, button: str = "left", clicks: int = 1) -> bool:
        """
        Hace clic con el mouse
        
        Args:
            button: "left", "middle", "right"
            clicks: Número de clicks

        Returns:
            True si exitoso
        """
        # SEGURIDAD (S116): no hacer click si SER está activo o congeló el control
        try:
            from core.eidos_input_safety import can_control_input
            _ok, _why = can_control_input()
            if not _ok:
                print(f"   🛑 click cedido a SER: {_why}")
                return False
        except Exception:
            pass
        if not self.has_xdotool:
            return False

        try:
            button_map = {
                "left": 1,
                "middle": 2,
                "right": 3
            }
            btn = button_map.get(button, 1)
            
            for _ in range(clicks):
                subprocess.run(
                    ["xdotool", "click", str(btn)],
                    timeout=2,
                    check=True
                )
                if clicks > 1:
                    time.sleep(0.1)
            
            self._log_action("click", {"button": button, "clicks": clicks}, f"Click {button} x{clicks}")
            return True
            
        except Exception as e:
            print(f"   ⚠️  Error en click: {e}")
            return False
    
    def click_at(self, x: int, y: int, button: str = "left", clicks: int = 1, move_first: bool = True) -> bool:
        """
        Mueve y hace clic en una posición
        
        Args:
            x, y: Coordenadas
            button: Botón del mouse
            clicks: Número de clicks
            move_first: Mover antes de clicar
            
        Returns:
            True si exitoso
        """
        if move_first:
            if not self.move_mouse(x, y):
                return False
            time.sleep(0.1)
        
        return self.click(button, clicks)
    
    def scroll(self, amount: int, direction: str = "down") -> bool:
        """
        Hace scroll con el mouse
        
        Args:
            amount: Cantidad de scroll
            direction: "up" o "down"
            
        Returns:
            True si exitoso
        """
        if not self.has_xdotool:
            return False
        
        try:
            button = 4 if direction == "up" else 5
            for _ in range(abs(amount)):
                subprocess.run(
                    ["xdotool", "click", str(button)],
                    timeout=1,
                    check=True
                )
                time.sleep(0.05)
            
            self._log_action("scroll", {"amount": amount, "direction": direction}, f"Scroll {direction} x{amount}")
            return True
            
        except Exception as e:
            print(f"   ⚠️  Error en scroll: {e}")
            return False
    
    def type_text(self, text: str, interval: float = 0.01) -> bool:
        """
        Escribe texto
        
        Args:
            text: Texto a escribir
            interval: Intervalo entre caracteres
            
        Returns:
            True si exitoso
        """
        if not self.has_xdotool:
            return False
        
        try:
            # Escapar caracteres especiales para xdotool
            escaped = text.replace("'", "'\"'\"'")
            
            subprocess.run(
                ["xdotool", "type", "--delay", str(int(interval * 1000)), escaped],
                timeout=10,
                check=True,
                shell=False
            )
            
            self._log_action("type", {"text": text[:50]}, f"Tipo: {text[:30]}...")
            return True
            
        except Exception as e:
            print(f"   ⚠️  Error escribiendo: {e}")
            return False
    
    def press_key(self, key: str, modifiers: Optional[List[str]] = None) -> bool:
        """
        Presiona una tecla o combinación
        
        Args:
            key: Tecla a presionar
            modifiers: Lista de modificadores (ctrl, alt, shift, super)
            
        Returns:
            True si exitoso
        """
        if not self.has_xdotool:
            return False
        
        try:
            if modifiers:
                mod_str = "+".join(modifiers) + "+" + key
                subprocess.run(
                    ["xdotool", "key", mod_str],
                    timeout=2,
                    check=True
                )
            else:
                subprocess.run(
                    ["xdotool", "key", key],
                    timeout=2,
                    check=True
                )
            
            desc = "+".join(modifiers or []) + "+" + key if modifiers else key
            self._log_action("key", {"key": key, "modifiers": modifiers}, f"Tecla: {desc}")
            return True
            
        except Exception as e:
            print(f"   ⚠️  Error en tecla: {e}")
            return False
    
    def open_application(self, app_name: str) -> bool:
        """
        Abre una aplicación
        
        Args:
            app_name: Nombre del comando (firefox, code, etc.)
            
        Returns:
            True si exitoso
        """
        try:
            subprocess.Popen(
                [app_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            
            time.sleep(1)  # Esperar a que arranque
            
            self._log_action("open", {"app": app_name}, f"Abrir: {app_name}")
            return True
            
        except Exception as e:
            print(f"   ⚠️  Error abriendo app: {e}")
            return False
    
    def get_window_id(self, window_name: str) -> Optional[int]:
        """
        Obtiene el ID de una ventana por nombre
        
        Args:
            window_name: Título o parte del título
            
        Returns:
            ID de ventana o None
        """
        if not self.has_xdotool:
            return None
        
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", window_name],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0 and result.stdout.strip():
                return int(result.stdout.strip().split('\n')[0])
            return None
        except Exception:
            return None
    
    def focus_window(self, window_name: str) -> bool:
        """
        Enfoca una ventana
        
        Args:
            window_name: Nombre de la ventana
            
        Returns:
            True si exitoso
        """
        if not self.has_xdotool:
            return False
        
        try:
            window_id = self.get_window_id(window_name)
            if window_id:
                subprocess.run(
                    ["xdotool", "windowactivate", str(window_id)],
                    timeout=2,
                    check=True
                )
                self._log_action("focus", {"window": window_name}, f"Enfocar: {window_name}")
                return True
            return False
        except Exception as e:
            print(f"   ⚠️  Error enfocando ventana: {e}")
            return False
    
    def minimize_window(self, window_name: str) -> bool:
        """Minimiza una ventana"""
        if not self.has_wmctrl:
            return False
        
        try:
            window_id = self.get_window_id(window_name)
            if window_id:
                subprocess.run(
                    ["wmctrl", "-i", "-r", str(window_id), "-b", "add,hidden"],
                    timeout=2,
                    check=True
                )
                return True
            return False
        except Exception:
            return False
    
    def maximize_window(self, window_name: str) -> bool:
        """Maximiza una ventana"""
        if not self.has_wmctrl:
            return False
        
        try:
            window_id = self.get_window_id(window_name)
            if window_id:
                subprocess.run(
                    ["wmctrl", "-i", "-r", str(window_id), "-b", "add,maximized_vert,maximized_horz"],
                    timeout=2,
                    check=True
                )
                return True
            return False
        except Exception:
            return False
    
    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int, duration: float = 0.5) -> bool:
        """
        Drag & drop desde una posición a otra
        
        Args:
            start_x, start_y: Posición inicial
            end_x, end_y: Posición final
            duration: Duración del drag
            
        Returns:
            True si exitoso
        """
        if not self.has_xdotool:
            return False
        
        try:
            # Mover a inicio y presionar
            self.move_mouse(start_x, start_y, 0.2)
            time.sleep(0.1)
            subprocess.run(
                ["xdotool", "mousedown", "1"],
                timeout=2,
                check=True
            )
            
            # Mover a destino
            self.move_mouse(end_x, end_y, duration)
            time.sleep(0.1)
            
            # Soltar
            subprocess.run(
                ["xdotool", "mouseup", "1"],
                timeout=2,
                check=True
            )
            
            self._log_action("drag", {
                "start": (start_x, start_y),
                "end": (end_x, end_y)
            }, f"Drag ({start_x},{start_y}) -> ({end_x},{end_y})")
            return True
            
        except Exception as e:
            print(f"   ⚠️  Error en drag: {e}")
            # Asegurar que se suelte el botón
            try:
                subprocess.run(["xdotool", "mouseup", "1"], timeout=1)
            except Exception:
                pass  # error no crítico, continuar
            return False
    
    def execute_sequence(self, actions: List[Action]) -> bool:
        """
        Ejecuta una secuencia de acciones
        
        Args:
            actions: Lista de Action a ejecutar
            
        Returns:
            True si todas exitosas
        """
        print(f"▶️  [EIDOS Control] Ejecutando secuencia de {len(actions)} acciones")
        
        for i, action in enumerate(actions):
            print(f"   {i+1}/{len(actions)}: {action.description}")
            
            success = False
            if action.action_type == "move":
                success = self.move_mouse(**action.params)
            elif action.action_type == "click":
                success = self.click(**action.params)
            elif action.action_type == "type":
                success = self.type_text(**action.params)
            elif action.action_type == "key":
                success = self.press_key(**action.params)
            elif action.action_type == "wait":
                time.sleep(action.params.get("seconds", 1))
                success = True
            elif action.action_type == "scroll":
                success = self.scroll(**action.params)
            
            if not success:
                print(f"   ❌ Falló: {action.description}")
                return False
            
            # Delay entre acciones
            time.sleep(0.2)
        
        print(f"   ✅ Secuencia completada")
        return True
    
    def _log_action(self, action_type: str, params: Dict, description: str):
        """Registra una acción en el historial"""
        action = Action(
            action_type=action_type,
            params=params,
            description=description
        )
        self.action_history.append(action)
        
        # Limitar historial
        if len(self.action_history) > 1000:
            self.action_history = self.action_history[-500:]
    
    def human_like_typing(self, text: str, typo_chance: float = 0.01) -> bool:
        """
        Escribe como humano (con variación de velocidad y errores ocasionales)
        
        Args:
            text: Texto a escribir
            typo_chance: Probabilidad de error (0.01 = 1%)
            
        Returns:
            True si exitoso
        """
        if not self.has_xdotool:
            return False
        
        try:
            for char in text:
                # Variación de velocidad
                delay = random.uniform(0.01, 0.05)
                
                # Error ocasional (borrar y reescribir)
                if random.random() < typo_chance and char.isalpha():
                    wrong_char = random.choice('abcdefghijklmnopqrstuvwxyz')
                    subprocess.run(
                        ["xdotool", "type", "--delay", "0", wrong_char],
                        timeout=1
                    )
                    time.sleep(0.1)
                    subprocess.run(
                        ["xdotool", "key", "BackSpace"],
                        timeout=1
                    )
                    time.sleep(0.1)
                
                # Escribir caracter correcto
                subprocess.run(
                    ["xdotool", "type", "--delay", "0", char],
                    timeout=1
                )
                time.sleep(delay)
            
            self._log_action("type_human", {"text": text[:50]}, f"Tipo humano: {text[:30]}...")
            return True
            
        except Exception as e:
            print(f"   ⚠️  Error en typing humano: {e}")
            return False
    
    def get_stats(self) -> Dict:
        """Estadísticas del sistema de control"""
        return {
            'actions_executed': len(self.action_history),
            'screen_size': (self.screen_width, self.screen_height),
            'tools': {
                'xdotool': self.has_xdotool,
                'wmctrl': self.has_wmctrl
            },
            'recent_actions': [a.description for a in self.action_history[-10:]]
        }


# ═══════════════════════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════════════════════

eidos_control = EidosControl()


def get_control_system() -> EidosControl:
    """Obtiene la instancia singleton del sistema de control."""
    return eidos_control


def get_vision_system():
    """Obtiene la instancia singleton del sistema de visión."""
    from core.eidos_vision import get_vision_system as gv
    return gv()


def move(x: int, y: int) -> bool:
    """Mueve mouse a (x,y)"""
    return eidos_control.move_mouse(x, y)


def click(x: Optional[int] = None, y: Optional[int] = None, button: str = "left") -> bool:
    """Hace clic (opcionalmente en posición específica)"""
    if x is not None and y is not None:
        return eidos_control.click_at(x, y, button)
    return eidos_control.click(button)


def type_text(text: str) -> bool:
    """Escribe texto"""
    return eidos_control.type_text(text)


def press(key: str, *modifiers) -> bool:
    """Presiona tecla con modificadores"""
    return eidos_control.press_key(key, list(modifiers) if modifiers else None)


def open_app(app: str) -> bool:
    """Abre aplicación"""
    return eidos_control.open_application(app)


def focus(window: str) -> bool:
    """Enfoca ventana"""
    return eidos_control.focus_window(window)


# ═══════════════════════════════════════════════════════════════════════════
# Test
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=== Test EIDOS Control ===\n")
    
    # Test 1: Posición del mouse
    print("Test 1: Posición del mouse")
    pos = eidos_control.get_mouse_position()
    print(f"  Posición actual: ({pos.x}, {pos.y})")
    
    # Test 2: Mover mouse
    print("\nTest 2: Mover mouse al centro")
    center_x = eidos_control.screen_width // 2
    center_y = eidos_control.screen_height // 2
    eidos_control.move_mouse(center_x, center_y)
    print(f"  Movido a ({center_x}, {center_y})")
    
    # Test 3: Click
    print("\nTest 3: Click izquierdo")
    eidos_control.click("left")
    print("  Click realizado")
    
    # Test 4: Tecla
    print("\nTest 4: Tecla Escape")
    eidos_control.press_key("Escape")
    print("  Escape presionado")
    
    # Test 5: Stats
    print("\nTest 5: Estadísticas")
    stats = eidos_control.get_stats()
    print(f"  Acciones: {stats['actions_executed']}")
    print(f"  Tools: {stats['tools']}")
    
    print("\n✅ EIDOS Control listo")
