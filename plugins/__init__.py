"""
Sistema de Plugins para EIDOS
"""

from .manager import PluginManager, get_plugin_manager
from .context import PluginContext

__all__ = ['PluginManager', 'get_plugin_manager', 'PluginContext']
