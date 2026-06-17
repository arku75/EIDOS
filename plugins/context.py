"""
Plugin Context - Interfaz para plugins
"""

import logging
from typing import Dict, Any, Callable, List, Optional

logger = logging.getLogger(__name__)

class PluginContext:
    """
    Contexto proporcionado a plugins para interactuar con EIDOS.
    Similar al PluginContext de Hermes.
    """
    
    def __init__(self, plugin_name: str, manager: "PluginManager"):
        self.plugin_name = plugin_name
        self._manager = manager
        self._tools: Dict[str, Callable] = {}
        self._hooks: Dict[str, List[Callable]] = {}
        
    def register_tool(self, name: str, handler: Callable, schema: Optional[Dict] = None):
        """Registrar una herramienta"""
        self._tools[name] = {"handler": handler, "schema": schema}
        logger.info(f"Plugin {self.plugin_name} registered tool: {name}")
        
    def register_hook(self, event: str, handler: Callable):
        """Registrar hook para evento"""
        if event not in self._hooks:
            self._hooks[event] = []
        self._hooks[event].append(handler)
        logger.info(f"Plugin {self.plugin_name} registered hook: {event}")
        
    def log(self, message: str, level: str = "info"):
        """Log desde plugin"""
        getattr(logger, level)(f"[{self.plugin_name}] {message}")
        
    def get_config(self, key: Optional[str] = None) -> Any:
        """Obtener configuración"""
        return self._manager.get_plugin_config(self.plugin_name, key)
