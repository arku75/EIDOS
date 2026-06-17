"""
Seguridad para EIDOS Gateway
Firewall upfront - bloquea comandos peligrosos
"""

import re
import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

class SecurityChecker:
    """Verificación de seguridad para comandos"""
    
    # Patrones bloqueados (críticos)
    BLOCKED_PATTERNS = [
        r"rm\s+-rf\s+/",
        r"rm\s+-rf\s+~/?$",
        r"mkfs",
        r"dd\s+if=.*of=/dev/(sda|hda|nvme)",
        r">\s*/dev/(sda|hda|nvme)",
        r"shutdown\s+-h\s+now",
        r"reboot\s+-f",
        r":(){ :|:& };:",  # Fork bomb
        r"wget.*\|.*sh",
        r"curl.*\|.*sh",
    ]
    
    # Patrones que requieren confirmación
    CONFIRM_PATTERNS = [
        r"sudo\s+",
        r"rm\s+-rf",
        r"chmod\s+777",
        r"chown\s+-R",
        r"DROP\s+TABLE",
        r"DELETE\s+FROM",
    ]
    
    def __init__(self):
        self._blocked = [re.compile(p, re.IGNORECASE) for p in self.BLOCKED_PATTERNS]
        self._confirm = [re.compile(p, re.IGNORECASE) for p in self.CONFIRM_PATTERNS]
    
    def check_command(self, cmd: str) -> Tuple[bool, Optional[str]]:
        """
        Verificar comando.
        Returns: (allowed, reason)
        - allowed: True si permitido, False si bloqueado
        - reason: None si permitido, string explicando por qué si bloqueado
        """
        cmd_stripped = cmd.strip()
        
        # Check blocked
        for pattern in self._blocked:
            if pattern.search(cmd_stripped):
                reason = f"Comando bloqueado por seguridad: {pattern.pattern}"
                logger.warning(f"🚫 BLOCKED: {cmd_stripped[:50]}...")
                return False, reason
        
        # Check requires confirmation
        for pattern in self._confirm:
            if pattern.search(cmd_stripped):
                reason = f"Requiere confirmación: {pattern.pattern}"
                logger.info(f"⚠️ CONFIRMATION NEEDED: {cmd_stripped[:50]}...")
                return True, reason
        
        return True, None
    
    def sanitize_path(self, path: str) -> str:
        """Sanitizar ruta de archivo"""
        # Eliminar null bytes
        path = path.replace('\x00', '')
        # Normalizar
        path = re.sub(r'\.\.+', '.', path)
        return path

# Singleton
_checker: Optional[SecurityChecker] = None

def get_security_checker() -> SecurityChecker:
    global _checker
    if _checker is None:
        _checker = SecurityChecker()
    return _checker
