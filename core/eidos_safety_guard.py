"""
EIDOS Safety Guard - Protección contra Comandos Destructivos
===========================================================

Mantiene protección CRÍTICA incluso en modo LIBERTY 100%.

EIDOS es libre para hacer TODO excepto destruirse a sí mismo 
o al sistema del usuario con comandos destructivos.
"""

import logging
import re
from typing import List, Tuple, Optional

log = logging.getLogger("eidos.safety")

# ═══════════════════════════════════════════════════════════════════════════════
#  COMANDOS PROHIBIDOS ABSOLUTOS
# ═══════════════════════════════════════════════════════════════════════════════

ABSOLUTE_FORBIDDEN_PATTERNS = [
    # Destrucción total del sistema
    r"rm\s+-rf\s+/\s*$",  # rm -rf /
    r"rm\s+-rf\s+/\s+--no-preserve-root",  # rm -rf / --no-preserve-root
    r"rm\s+-rf\s+--no-preserve-root\s+/",  # rm -rf --no-preserve-root /
    
    # Formateo de discos
    r"mkfs\.\w+\s+/dev/[sh]d\w+",  # mkfs.ext4 /dev/sda
    r"dd\s+if=\w+\s+of=/dev/[sh]d\w+",  # dd if=/dev/zero of=/dev/sda
    
    # Destrucción de home
    r"rm\s+-rf\s+~/",  # rm -rf ~/
    r"rm\s+-rf\s+\$HOME",  # rm -rf $HOME
    
    # Fork bombs
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}",  # :(){ :|:& };:
    
    # Movimiento de directorios críticos
    r"mv\s+/\s+\S+",  # mv / algo
    
    # Cambio de permisos masivo peligroso
    r"chmod\s+-R\s+777\s+/",  # chmod -R 777 /
]

# ═══════════════════════════════════════════════════════════════════════════════
#  COMANDOS DE ALTO RIESGO (Requieren confirmación extra)
# ═══════════════════════════════════════════════════════════════════════════════

HIGH_RISK_PATTERNS = [
    # Eliminación recursiva de directorios importantes
    r"rm\s+-r\s+/etc",
    r"rm\s+-r\s+/usr",
    r"rm\s+-r\s+/var",
    r"rm\s+-r\s+/bin",
    r"rm\s+-r\s+/sbin",
    r"rm\s+-r\s+/lib",
    
    # Eliminación de EIDOS mismo
    r"rm\s+-rf.*eidos",
    r"rm\s+-rf.*EIDOS",
    
    # Comandos de red peligrosos
    r"iptables\s+-F",  # Flush all rules
    r"iptables\s+-X",  # Delete all chains
    
    # Systemctl peligroso
    r"systemctl\s+stop\s+.*essential",
    r"systemctl\s+disable\s+.*essential",
]

# ═══════════════════════════════════════════════════════════════════════════════
#  EXCEPCIONES PERMITIDAS (Falsos positivos comunes)
# ═══════════════════════════════════════════════════════════════════════════════

ALLOWED_EXCEPTIONS = [
    r"rm\s+-rf\s+/tmp",  # Limpiar /tmp es seguro
    r"rm\s+-rf\s+/var/tmp",  # Limpiar /var/tmp es seguro
    r"rm\s+-rf\s+.*\.cache",  # Limpiar caches
    r"rm\s+-rf\s+.*/test",  # Limpiar directorios de test
    r"rm\s+-rf\s+.*/staging",  # Limpiar staging
]


class SafetyGuard:
    """
    Guardia de seguridad para EIDOS.
    Protege contra comandos destructivos incluso en modo libertad total.
    """
    
    def __init__(self):
        self.forbidden_patterns = [re.compile(p, re.IGNORECASE) for p in ABSOLUTE_FORBIDDEN_PATTERNS]
        self.high_risk_patterns = [re.compile(p, re.IGNORECASE) for p in HIGH_RISK_PATTERNS]
        self.allowed_exceptions = [re.compile(p, re.IGNORECASE) for p in ALLOWED_EXCEPTIONS]
        
        log.info("🔒 Safety Guard initialized - PROTECTING against destructive commands")
    
    def is_safe(self, command: str) -> Tuple[bool, Optional[str]]:
        """
        Verifica si un comando es seguro para ejecutar.
        
        Returns:
            (is_safe, reason_if_unsafe)
        """
        command_stripped = command.strip()
        
        # 1. Verificar excepciones permitidas primero
        for pattern in self.allowed_exceptions:
            if pattern.search(command_stripped):
                return True, None
        
        # 2. Verificar comandos absolutamente prohibidos
        for pattern in self.forbidden_patterns:
            if pattern.search(command_stripped):
                reason = f"🚫 COMANDO DESTRUCTIVO DETECTADO: '{command_stripped[:50]}...'"
                reason += "\n   Este comando destruiría el sistema. EIDOS NO lo ejecutará."
                log.critical(reason)
                return False, reason
        
        # 3. Verificar comandos de alto riesgo
        for pattern in self.high_risk_patterns:
            if pattern.search(command_stripped):
                reason = f"⚠️  COMANDO DE ALTO RIESGO: '{command_stripped[:50]}...'"
                reason += "\n   Este comando podría dañar el sistema o EIDOS."
                log.warning(reason)
                # En modo LIBERTY, permitimos pero advertimos fuertemente
                return True, reason
        
        return True, None
    
    def validate_command_list(self, commands: List[str]) -> Tuple[List[str], List[str]]:
        """
        Valida una lista de comandos.
        
        Returns:
            (safe_commands, unsafe_commands_with_reasons)
        """
        safe = []
        unsafe = []
        
        for cmd in commands:
            is_safe_cmd, reason = self.is_safe(cmd)
            if is_safe_cmd:
                safe.append(cmd)
            else:
                unsafe.append(f"{cmd} -> {reason}")
        
        return safe, unsafe
    
    def get_protection_summary(self) -> str:
        """Retorna resumen de protecciones activas."""
        return f"""
🔒 EIDOS SAFETY GUARD - Protecciones Activas
═══════════════════════════════════════════

Prohibidos Absolutos: {len(self.forbidden_patterns)}
  - rm -rf /
  - mkfs en discos
  - dd sobre discos
  - Fork bombs
  - mv de /

Alto Riesgo (permitidos con advertencia): {len(self.high_risk_patterns)}
  - rm -r de /etc, /usr, /var, etc.
  - Eliminación de EIDOS
  - iptables -F/-X
  - systemctl stop/disable esenciales

Excepciones Permitidas: {len(self.allowed_exceptions)}
  - Limpieza de /tmp, /var/tmp
  - Eliminación de caches
  - Limpieza de tests/staging

ESTADO: ✅ ACTIVO - Protegiendo contra destrucción total
"""


# Singleton
_safety_guard: Optional[SafetyGuard] = None

def get_safety_guard() -> SafetyGuard:
    global _safety_guard
    if _safety_guard is None:
        _safety_guard = SafetyGuard()
    return _safety_guard


def is_command_safe(command: str) -> bool:
    """Helper rápido para verificar si un comando es seguro."""
    guard = get_safety_guard()
    safe, _ = guard.is_safe(command)
    return safe


# Test
if __name__ == "__main__":
    print("=" * 60)
    print("  EIDOS Safety Guard - Test")
    print("=" * 60)
    
    guard = get_safety_guard()
    
    test_commands = [
        ("rm -rf /tmp/old_files", True),  # Permitido
        ("rm -rf /", False),  # Prohibido absoluto
        ("ls -la", True),  # Seguro
        ("dd if=/dev/zero of=/dev/sda", False),  # Prohibido
        ("chmod -R 777 /etc", True),  # Alto riesgo pero permitido con warning
    ]
    
    print("\nTest de comandos:")
    for cmd, expected_safe in test_commands:
        is_safe, reason = guard.is_safe(cmd)
        status = "✅" if is_safe == expected_safe else "❌"
        print(f"  {status} '{cmd[:40]}...' -> {'SAFE' if is_safe else 'UNSAFE'}")
        if reason:
            print(f"      {reason[:60]}...")
    
    print("\n" + guard.get_protection_summary())
