"""
Validadores para EIDOS
"""

import re
from typing import Dict, Any, List, Tuple

def validate_config(config: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validar configuración EIDOS"""
    errors = []
    
    # Validar gateway
    gw = config.get("gateway", {})
    if gw.get("enabled"):
        port = gw.get("port", 0)
        if not (1024 <= port <= 65535):
            errors.append(f"Invalid gateway port: {port}")
    
    # Validar plataformas
    platforms = config.get("platforms", {})
    for name, pcfg in platforms.items():
        if pcfg.get("enabled"):
            if name == "telegram" and not pcfg.get("bot_token"):
                errors.append(f"Telegram enabled but no bot_token")
            if name == "discord" and not pcfg.get("bot_token"):
                errors.append(f"Discord enabled but no bot_token")
    
    return len(errors) == 0, errors

def sanitize_input(text: str, max_length: int = 10000) -> str:
    """Sanitizar input de usuario"""
    if not text:
        return ""
    
    # Limitar longitud
    if len(text) > max_length:
        text = text[:max_length] + "... [truncated]"
    
    # Eliminar caracteres de control excepto \n \t
    text = ''.join(c for c in text if c == '\n' or c == '\t' or ord(c) >= 32)
    
    return text
