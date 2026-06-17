"""
Sistema de configuración EIDOS - Adaptado de Hermes
Configuración YAML con variables de entorno
"""

from .loader import load_config, get_config_path, save_config
from .env_loader import load_eidos_dotenv

__all__ = ['load_config', 'get_config_path', 'save_config', 'load_eidos_dotenv']
