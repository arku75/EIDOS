"""
Plugin Manager - Gestiona plugins de EIDOS
Adaptado de Hermes
"""

import importlib
import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any

from .context import PluginContext

logger = logging.getLogger(__name__)

VALID_HOOKS = {
    "pre_tool_call",
    "post_tool_call",
    "pre_message",
    "post_message",
    "on_session_start",
    "on_session_end",
    "on_gateway_dispatch",
}

class Plugin:
    """Representa un plugin cargado"""
    
    def __init__(self, name: str, path: Path, context: PluginContext):
        self.name = name
        self.path = path
        self.context = context
        self.enabled = True
        self.module: Optional[Any] = None
        self._error: Optional[str] = None
        
class PluginManager:
    """Gestiona carga y ejecución de plugins"""
    
    def __init__(self, plugins_dir: Optional[Path] = None):
        self.plugins_dir = plugins_dir or Path.home() / ".eidos" / "plugins"
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self._plugins: Dict[str, Plugin] = {}
        self._global_hooks: Dict[str, List[Callable]] = {hook: [] for hook in VALID_HOOKS}
        
    def discover_plugins(self):
        """Descubrir plugins en directorio"""
        for item in self.plugins_dir.iterdir():
            if item.is_dir() and (item / "plugin.py").exists():
                self._load_plugin(item)
                
    def _load_plugin(self, plugin_dir: Path):
        """Cargar plugin desde directorio"""
        name = plugin_dir.name
        plugin_file = plugin_dir / "plugin.py"
        manifest_file = plugin_dir / "manifest.json"
        
        try:
            # Leer manifest si existe
            manifest = {}
            if manifest_file.exists():
                manifest = json.loads(manifest_file.read_text())
            
            # Crear contexto
            context = PluginContext(name, self)
            
            # Cargar módulo
            spec = importlib.util.spec_from_file_location(f"eidos_plugin_{name}", plugin_file)
            module = importlib.util.module_from_spec(spec)
            
            # Hacer disponible el contexto en el módulo
            module.plugin_context = context
            
            sys.modules[f"eidos_plugin_{name}"] = module
            spec.loader.exec_module(module)
            
            # Llamar a register si existe
            if hasattr(module, "register"):
                module.register(context)
            
            # Crear y guardar plugin
            plugin = Plugin(name, plugin_dir, context)
            plugin.module = module
            self._plugins[name] = plugin
            
            # Registrar hooks
            for hook_name, handlers in context._hooks.items():
                for handler in handlers:
                    if hook_name in self._global_hooks:
                        self._global_hooks[hook_name].append(handler)
            
            logger.info(f"✅ Plugin loaded: {name}")
            
        except Exception as e:
            logger.error(f"❌ Failed to load plugin {name}: {e}")
    
    def get_plugin_config(self, plugin_name: str, key: Optional[str] = None) -> Any:
        """Obtener configuración de plugin"""
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return None
        
        config_file = plugin.path / "config.json"
        if not config_file.exists():
            return None
        
        try:
            config = json.loads(config_file.read_text())
            if key:
                return config.get(key)
            return config
        except:
            return None
    
    def invoke_hook(self, hook_name: str, **kwargs) -> List[Any]:
        """Invocar todos los handlers de un hook"""
        results = []
        
        for handler in self._global_hooks.get(hook_name, []):
            try:
                result = handler(**kwargs)
                results.append(result)
            except Exception as e:
                logger.error(f"Hook error: {e}")
        
        return results
    
    def list_plugins(self) -> List[Dict]:
        """Listar plugins cargados"""
        return [
            {
                "name": p.name,
                "enabled": p.enabled,
                "path": str(p.path),
                "tools": list(p.context._tools.keys()),
                "hooks": list(p.context._hooks.keys()),
            }
            for p in self._plugins.values()
        ]
    
    def enable_plugin(self, name: str):
        """Habilitar plugin"""
        if name in self._plugins:
            self._plugins[name].enabled = True
    
    def disable_plugin(self, name: str):
        """Deshabilitar plugin"""
        if name in self._plugins:
            self._plugins[name].enabled = False

# Singleton
_manager: Optional[PluginManager] = None

def get_plugin_manager() -> PluginManager:
    global _manager
    if _manager is None:
        _manager = PluginManager()
    return _manager
