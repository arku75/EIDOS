"""
EIDOS Environment Loader
Adaptado de Hermes - Carga segura de variables de entorno
"""

import os
import sys
from pathlib import Path
from typing import List, Optional

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# Sufijos que indican credenciales
_CREDENTIAL_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_KEY", "_PASSWORD")
_WARNED_KEYS: set = set()

def _sanitize_credentials():
    """Reject suspicious credential encoding without mutating secret values."""
    for key, value in list(os.environ.items()):
        if not any(key.endswith(suffix) for suffix in _CREDENTIAL_SUFFIXES):
            continue
        try:
            value.encode("ascii")
            continue
        except UnicodeEncodeError:
            pass
        
        if key not in _WARNED_KEYS:
            _WARNED_KEYS.add(key)
            print(
                f"⚠️  {key} contiene caracteres no-ASCII; se conserva sin modificar",
                file=sys.stderr,
            )

def load_eidos_dotenv(
    eidos_home: Optional[Path] = None,
    project_env: Optional[Path] = None
) -> List[Path]:
    """
    Carga archivos .env de EIDOS.
    
    Orden de carga:
    1. ~/.eidos/.env (usuario)
    2. ./.env (proyecto, fallback)
    """
    loaded: List[Path] = []
    
    if eidos_home is None:
        eidos_home = Path.home() / ".eidos"
    
    user_env = eidos_home / ".env"
    
    if load_dotenv:
        if user_env.exists():
            load_dotenv(dotenv_path=user_env, override=True, encoding="utf-8")
            loaded.append(user_env)
        
        if project_env and project_env.exists():
            load_dotenv(dotenv_path=project_env, override=not loaded, encoding="utf-8")
            loaded.append(project_env)
    
    _sanitize_credentials()
    return loaded

def get_eidos_home() -> Path:
    """Obtener directorio home de EIDOS"""
    env_home = os.getenv("EIDOS_HOME")
    if env_home:
        return Path(env_home)
    return Path.home() / ".eidos"

def ensure_eidos_home() -> Path:
    """Asegurar que ~/.eidos existe con permisos correctos"""
    home = get_eidos_home()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        home.chmod(0o700)
    except OSError:
        pass

    # State/config directories are private by default.
    for subdir in ("config", "sessions", "logs", "cron", "plugins"):
        path = home / subdir
        path.mkdir(exist_ok=True, mode=0o700)
        try:
            path.chmod(0o700)
        except OSError:
            pass
    
    return home
