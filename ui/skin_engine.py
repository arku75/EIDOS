"""
Skin Engine para EIDOS
Temas personalizables para CLI
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Skins predefinidos
DEFAULT_SKINS = {
    "default": {
        "name": "Default",
        "colors": {
            "primary": "cyan",
            "secondary": "blue",
            "success": "green",
            "warning": "yellow",
            "error": "red",
            "info": "blue",
            "text": "white",
            "muted": "gray"
        },
        "symbols": {
            "prompt": "❯",
            "bullet": "•",
            "check": "✓",
            "cross": "✗",
            "arrow": "→",
            "info": "ℹ",
            "warning": "⚠",
            "error": "✖"
        },
        "borders": {
            "top": "─",
            "bottom": "─",
            "left": "│",
            "right": "│",
            "corner_tl": "┌",
            "corner_tr": "┐",
            "corner_bl": "└",
            "corner_br": "┘"
        }
    },
    "minimal": {
        "name": "Minimal",
        "colors": {
            "primary": "white",
            "secondary": "white",
            "success": "white",
            "warning": "white",
            "error": "white",
            "info": "white",
            "text": "white",
            "muted": "gray"
        },
        "symbols": {
            "prompt": ">",
            "bullet": "-",
            "check": "[OK]",
            "cross": "[ERR]",
            "arrow": "->",
            "info": "[i]",
            "warning": "[!]",
            "error": "[X]"
        },
        "borders": {
            "top": "-",
            "bottom": "-",
            "left": "|",
            "right": "|",
            "corner_tl": "+",
            "corner_tr": "+",
            "corner_bl": "+",
            "corner_br": "+"
        }
    },
    "fancy": {
        "name": "Fancy",
        "colors": {
            "primary": "magenta",
            "secondary": "cyan",
            "success": "bright_green",
            "warning": "bright_yellow",
            "error": "bright_red",
            "info": "bright_blue",
            "text": "white",
            "muted": "dark_gray"
        },
        "symbols": {
            "prompt": "▶",
            "bullet": "◆",
            "check": "✔",
            "cross": "✘",
            "arrow": "➤",
            "info": "ℹ",
            "warning": "⚠",
            "error": "✖"
        },
        "borders": {
            "top": "━",
            "bottom": "━",
            "left": "┃",
            "right": "┃",
            "corner_tl": "┏",
            "corner_tr": "┓",
            "corner_bl": "┗",
            "corner_br": "┛"
        }
    }
}

class SkinEngine:
    """
    Motor de skins para personalizar la apariencia de EIDOS CLI.
    """
    
    def __init__(self, skins_dir: Optional[Path] = None):
        self.skins_dir = skins_dir or Path.home() / ".eidos" / "skins"
        self.skins_dir.mkdir(parents=True, exist_ok=True)
        self._skins: Dict[str, Dict] = {}
        self._current_skin: str = "default"
        
        # Cargar skins por defecto
        self._skins.update(DEFAULT_SKINS)
        
        # Cargar skins personalizados
        self._load_custom_skins()
    
    def _load_custom_skins(self):
        """Cargar skins personalizados desde directorio"""
        for skin_file in self.skins_dir.glob("*.json"):
            try:
                skin_data = json.loads(skin_file.read_text())
                skin_name = skin_file.stem
                self._skins[skin_name] = skin_data
                logger.debug(f"Loaded custom skin: {skin_name}")
            except Exception as e:
                logger.error(f"Error loading skin {skin_file}: {e}")
    
    def get_skin(self, name: Optional[str] = None) -> Dict[str, Any]:
        """Obtener configuración de skin"""
        skin_name = name or self._current_skin
        return self._skins.get(skin_name, self._skins["default"])
    
    def set_skin(self, name: str) -> bool:
        """Cambiar skin activo"""
        if name in self._skins:
            self._current_skin = name
            logger.info(f"Skin changed to: {name}")
            return True
        logger.warning(f"Skin not found: {name}")
        return False
    
    def list_skins(self) -> Dict[str, str]:
        """Listar skins disponibles"""
        return {
            name: skin.get("name", name)
            for name, skin in self._skins.items()
        }
    
    def save_custom_skin(self, name: str, skin_data: Dict):
        """Guardar skin personalizado"""
        skin_file = self.skins_dir / f"{name}.json"
        skin_file.write_text(json.dumps(skin_data, indent=2))
        self._skins[name] = skin_data
        logger.info(f"Saved custom skin: {name}")
    
    def get_symbol(self, symbol_name: str) -> str:
        """Obtener símbolo del skin actual"""
        skin = self.get_skin()
        return skin.get("symbols", {}).get(symbol_name, "•")
    
    def get_color(self, color_name: str) -> str:
        """Obtener color del skin actual"""
        skin = self.get_skin()
        return skin.get("colors", {}).get(color_name, "white")
    
    def format_message(
        self,
        message: str,
        level: str = "info",
        use_symbol: bool = True
    ) -> str:
        """Formatear mensaje con símbolo y color del skin"""
        symbol = ""
        if use_symbol:
            symbol_map = {
                "info": "info",
                "warning": "warning",
                "error": "error",
                "success": "check"
            }
            symbol = self.get_symbol(symbol_map.get(level, "bullet")) + " "
        
        return f"{symbol}{message}"
    
    def draw_box(self, content: str, width: int = 60) -> str:
        """Dibujar caja con bordes del skin"""
        skin = self.get_skin()
        borders = skin.get("borders", {})
        
        lines = content.split('\n')
        max_len = max(len(line) for line in lines) if lines else 0
        width = min(width, max_len + 4)
        
        top = borders.get("corner_tl", "┌") + borders.get("top", "─") * (width - 2) + borders.get("corner_tr", "┐")
        bottom = borders.get("corner_bl", "└") + borders.get("bottom", "─") * (width - 2) + borders.get("corner_br", "┘")
        
        result = [top]
        for line in lines:
            padded = line[:width-4].ljust(width - 4)
            result.append(f"{borders.get('left', '│')} {padded} {borders.get('right', '│')}")
        result.append(bottom)
        
        return '\n'.join(result)

# Singleton
_engine: Optional[SkinEngine] = None

def get_skin_engine() -> SkinEngine:
    global _engine
    if _engine is None:
        _engine = SkinEngine()
    return _engine
