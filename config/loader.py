"""
EIDOS Config Loader
Carga configuración YAML con soporte de variables de entorno
"""

import copy
import os
import re
from pathlib import Path
from typing import Dict, Any, Optional

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False
    yaml = None

def _expand_env_vars(data: Any) -> Any:
    """Expand ${VAR} and ${VAR:-default} without shell evaluation."""
    if isinstance(data, dict):
        return {k: _expand_env_vars(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_expand_env_vars(item) for item in data]
    elif isinstance(data, str):
        pattern = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}')
        def replace(match: re.Match) -> str:
            name, default = match.group(1), match.group(2)
            value = os.getenv(name)
            if value is not None and value != "":
                return value
            return default if default is not None else match.group(0)
        return pattern.sub(replace, data)
    return data

DEFAULT_CONFIG = {
    "gateway": {
        "enabled": True,
        "port": 18789,
        "host": "127.0.0.1",
        "enabled_platforms": ["local"],
        "session_timeout": 3600,
    },
    "platforms": {
        "telegram": {
            "enabled": False,
            "bot_token": "${TELEGRAM_BOT_TOKEN}",
            "allowed_users": [],
        },
        "discord": {
            "enabled": False,
            "bot_token": "${DISCORD_BOT_TOKEN}",
            "allowed_users": [],
        },
        "slack": {
            "enabled": False,
            "bot_token": "${SLACK_BOT_TOKEN}",
            "app_token": "${SLACK_APP_TOKEN}",
        },
    },
    "ollama": {
        "url": "${OLLAMA_URL:-http://localhost:11434}",
        "default_model": "hermes3:8b-llama3.1-q4_K_M",
    },
    "logging": {
        "level": "${EIDOS_LOG_LEVEL:-info}",
        "file": "~/.eidos/logs/eidos.log",
    },
}

def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge mappings so partial user config preserves defaults."""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def get_config_path() -> Path:
    """Obtener ruta del archivo de configuración"""
    env_path = os.getenv("EIDOS_CONFIG")
    if env_path:
        return Path(env_path)
    return Path.home() / ".eidos" / "config.yaml"

def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Cargar configuración de EIDOS.
    
    Orden de precedencia:
    1. Archivo config.yaml
    2. Variables de entorno
    3. Valores por defecto
    """
    if config_path is None:
        config_path = get_config_path()
    
    config = copy.deepcopy(DEFAULT_CONFIG)
    
    if HAS_YAML and config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                user_config = yaml.safe_load(f) or {}
            if not isinstance(user_config, dict):
                raise ValueError("config root must be a mapping")
            _deep_merge(config, user_config)
        except Exception as e:
            print(f"⚠️ Error loading config: {e}")
    
    # Expandir variables de entorno
    config = _expand_env_vars(config)
    
    return config

def save_config(config: Dict[str, Any], config_path: Optional[Path] = None):
    """Guardar configuración a archivo YAML"""
    if not HAS_YAML:
        raise ImportError("PyYAML requerido para guardar config")
    
    if config_path is None:
        config_path = get_config_path()
    
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
