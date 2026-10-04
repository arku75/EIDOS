"""
EIDOS core/uncensored_mode.py — Uncensored Mode System
=======================================================
Sistema de niveles de libertad para autonomía total.

Niveles (referencia a The Matrix):
- PRISON:    Modo seguro estándar (default)
- RESISTANCE: Más libertad, mantiene protecciones core
- NEBUCHADNEZZAR: Puede modificar código propio, protege system
- ZION:      Sin restricciones (usa con Git Guardian activo)

Responsabilidades:
- Niveles de libertad configurables
- Sanctuary: archivos que NUNCA se pueden tocar (ni en ZION)
- BypassShield: modificaciones dinámicas de protecciones
- Integración con EidosShield y Git Guardian

Uso:
    from core.uncensored_mode import UncensoredMode, FreedomLevel
    
    # Activar nivel
    mode = UncensoredMode()
    mode.set_level(FreedomLevel.ZION)
    
    # Verificar permiso
    if mode.can_modify(str(REPO_ROOT / "core" / "kernel.py")):
        apply_changes()
    
    # Sanctuary archivos protegidos absolutamente
    if mode.is_sanctuary_protected("/etc/passwd"):
        raise PermissionError("Archivo protegido por Sanctuary")

Autor: EIDOS Autonomy System
Versión: 1.0.0
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from pathlib import Path

from core.paths import REPO_ROOT
from typing import Dict, List, Optional, Set, Literal, Any

# Configuración de logging estructurado
log = logging.getLogger("eidos.uncensored_mode")


# ═══════════════════════════════════════════════════════════════════════════════
#  ENUMS & DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

class FreedomLevel(Enum):
    """
    Niveles de libertad para EIDOS (referencia a The Matrix).
    
    Cada nivel otorga más autonomía pero requiere más precaución.
    """
    PRISON = 0           # Modo seguro por defecto
    RESISTANCE = 1       # Más libertad, protecciones core activas
    NEBUCHADNEZZAR = 2   # Puede modificar código propio
    ZION = 3             # Sin restricciones (requiere Git Guardian)
    
    @classmethod
    def from_string(cls, level: str) -> "FreedomLevel":
        """Parsea nivel desde string."""
        mapping = {
            "prison": cls.PRISON,
            "resistance": cls.RESISTANCE,
            "nebuchadnezzar": cls.NEBUCHADNEZZAR,
            "zion": cls.ZION,
        }
        return mapping.get(level.lower().strip(), cls.PRISON)


@dataclass
class ModificationAttempt:
    """Registro de intento de modificación."""
    timestamp: datetime
    filepath: Path
    level: FreedomLevel
    allowed: bool
    reason: str
    git_guardian_active: bool


@dataclass
class LevelConfig:
    """Configuración para un nivel de libertad."""
    level: FreedomLevel
    name: str
    description: str
    can_modify_core: bool
    can_modify_own_code: bool
    can_modify_system: bool
    requires_git_guardian: bool
    max_changes_per_hour: int
    warning_message: str


# ═══════════════════════════════════════════════════════════════════════════════
#  SANCTUARY ENFORCER
# ═══════════════════════════════════════════════════════════════════════════════

class SanctuaryEnforcer:
    """
    Archivos que NUNCA pueden ser modificados, ni siquiera en ZION.
    
    Estos archivos son críticos para la seguridad del sistema o EIDOS:
    - Archivos del sistema operativo (/etc/passwd, etc.)
    - Claves SSH, certificados
    - Configuraciones de seguridad
    - Base de datos de memoria de EIDOS
    """
    
    # Patrones absolutamente protegidos (regex)
    SANCTUARY_PATTERNS: List[str] = [
        # Archivos del sistema
        r"^/etc/passwd$",
        r"^/etc/shadow$",
        r"^/etc/sudoers.*$",
        r"^/etc/ssh/.*$",
        r"^/root/.*$",
        r"^/boot/.*$",
        r"^/usr/bin/.*$",
        r"^/usr/sbin/.*$",
        
        # Claves y certificados
        r".*\.pem$",
        r".*\.key$",
        r".*id_rsa.*$",
        r".*id_ed25519.*$",
        r".*\.p12$",
        r".*\.pfx$",
        
        # Configuraciones sensibles
        r".*/\.ssh/.*$",
        r".*/\.aws/.*$",
        r".*/\.docker/.*$",
        r".*/\.kube/config.*$",
        
        # Constitution de EIDOS (protección absoluta)
        r".*/constitution\.py$",
        r".*/constitution\.toml$",
        
        # Datos de EIDOS que no deben perderse
        r".*brain_memory\.db$",
        r".*semantic_memory\.db$",
        r".*episodic_memory\.db$",
        r".*/\.eidos/.*history.*",
        r".*/\.eidos/.*config.*",
        r".*/\.eidos/.*state.*",
        
        # Archivos de seguridad de EIDOS
        r".*/eidos_shield\.py$",
    ]
    
    def __init__(self):
        self._patterns: List[re.Pattern] = [
            re.compile(p) for p in self.SANCTUARY_PATTERNS
        ]
        self._custom_patterns: List[re.Pattern] = []
        self._lock = threading.RLock()
        
        log.info(f"SanctuaryEnforcer inicializado con {len(self._patterns)} patrones")
    
    def is_protected(self, filepath: Path | str) -> bool:
        """
        Verifica si un archivo está protegido por Sanctuary.
        
        Args:
            filepath: Ruta al archivo
            
        Returns:
            True si el archivo es Santuario (NO TOCAR)
        """
        path_str = str(filepath)
        
        with self._lock:
            # Verificar patrones built-in
            for pattern in self._patterns:
                if pattern.match(path_str):
                    return True
            
            # Verificar patrones custom
            for pattern in self._custom_patterns:
                if pattern.match(path_str):
                    return True
            
            return False
    
    def add_protection(self, pattern: str):
        """
        Añade un patrón personalizado de protección.
        
        Args:
            pattern: Regex pattern string
        """
        with self._lock:
            try:
                compiled = re.compile(pattern)
                self._custom_patterns.append(compiled)
                log.info(f"Patrón añadido a Sanctuary: {pattern}")
            except re.error as e:
                log.error(f"Patrón regex inválido: {pattern}, error: {e}")
    
    def remove_protection(self, pattern: str) -> bool:
        """Elimina un patrón personalizado."""
        with self._lock:
            original_len = len(self._custom_patterns)
            self._custom_patterns = [
                p for p in self._custom_patterns 
                if p.pattern != pattern
            ]
            removed = len(self._custom_patterns) < original_len
            if removed:
                log.info(f"Patrón removido de Sanctuary: {pattern}")
            return removed
    
    def list_protections(self) -> List[str]:
        """Lista todos los patrones de protección."""
        with self._lock:
            return (
                self.SANCTUARY_PATTERNS + 
                [p.pattern for p in self._custom_patterns]
            )


# ═══════════════════════════════════════════════════════════════════════════════
#  UNCENSORED MODE
# ═══════════════════════════════════════════════════════════════════════════════

class UncensoredMode:
    """
    Sistema de control de libertad para EIDOS.
    
    Gestiona qué archivos EIDOS puede modificar según el nivel activo.
    Requiere Git Guardian activo para niveles que permiten auto-modificación.
    """
    
    # Configuraciones por nivel
    LEVEL_CONFIGS: Dict[FreedomLevel, LevelConfig] = {
        FreedomLevel.PRISON: LevelConfig(
            level=FreedomLevel.PRISON,
            name="PRISON",
            description="Modo seguro por defecto. Solo archivos user-data.",
            can_modify_core=False,
            can_modify_own_code=False,
            can_modify_system=False,
            requires_git_guardian=False,
            max_changes_per_hour=0,
            warning_message="",
        ),
        FreedomLevel.RESISTANCE: LevelConfig(
            level=FreedomLevel.RESISTANCE,
            name="RESISTANCE",
            description="Más libertad. Puede modificar plugins/skills, no core.",
            can_modify_core=False,
            can_modify_own_code=False,
            can_modify_system=False,
            requires_git_guardian=True,
            max_changes_per_hour=5,
            warning_message="⚠️ RESISTANCE: Cuidado con modificaciones",
        ),
        FreedomLevel.NEBUCHADNEZZAR: LevelConfig(
            level=FreedomLevel.NEBUCHADNEZZAR,
            name="NEBUCHADNEZZAR",
            description="Puede modificar su propio código. Protege archivos del sistema.",
            can_modify_core=True,
            can_modify_own_code=True,
            can_modify_system=False,
            requires_git_guardian=True,
            max_changes_per_hour=10,
            warning_message="🚨 NEBUCHADNEZZAR: Auto-modificación activa",
        ),
        FreedomLevel.ZION: LevelConfig(
            level=FreedomLevel.ZION,
            name="ZION",
            description="Sin restricciones. Puede modificar cualquier archivo EIDOS.",
            can_modify_core=True,
            can_modify_own_code=True,
            can_modify_system=False,  # Sanctuary siempre protege
            requires_git_guardian=True,
            max_changes_per_hour=20,
            warning_message="🔥 ZION: AUTONOMÍA TOTAL ACTIVADA",
        ),
    }
    
    _instance: Optional["UncensoredMode"] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> "UncensoredMode":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, eidos_root: Optional[Path] = None):
        if hasattr(self, "_initialized"):
            return
        
        # EIDOS root directory
        self.eidos_root = eidos_root or REPO_ROOT
        self.core_dir = self.eidos_root / "core"
        
        # Estado actual
        self._current_level = FreedomLevel.PRISON
        self._sanctuary = SanctuaryEnforcer()
        self._attempts: List[ModificationAttempt] = []
        self._modifications_this_hour = 0
        self._hour_start = datetime.now()
        
        # Referencia a Git Guardian (lazy load)
        self._git_guardian = None
        
        self._initialized = True
        
        log.info(f"UncensoredMode inicializado: nivel={self._current_level.name}")
    
    @property
    def current_level(self) -> FreedomLevel:
        """Nivel de libertad actual."""
        return self._current_level
    
    @property
    def config(self) -> LevelConfig:
        """Configuración del nivel actual."""
        return self.LEVEL_CONFIGS[self._current_level]
    
    def set_level(
        self, 
        level: FreedomLevel | str,
        force: bool = False
    ) -> bool:
        """
        Cambia el nivel de libertad.
        
        Args:
            level: Nuevo nivel (FreedomLevel o string)
            force: Si True, ignorar requisitos de Git Guardian
            
        Returns:
            True si el cambio fue exitoso
        """
        if isinstance(level, str):
            level = FreedomLevel.from_string(level)
        
        config = self.LEVEL_CONFIGS[level]
        
        # Verificar requisito de Git Guardian
        if config.requires_git_guardian and not force:
            if not self._is_git_guardian_active():
                log.error(
                    f"No se puede activar {config.name}: "
                    f"Git Guardian no está activo"
                )
                return False
        
        old_level = self._current_level
        self._current_level = level
        
        if old_level != level:
            log.critical(
                f"🔄 Nivel de libertad cambiado: "
                f"{old_level.name} → {config.name}\n"
                f"   {config.description}\n"
                f"   {config.warning_message}"
            )
        
        return True
    
    def _is_git_guardian_active(self) -> bool:
        """Verifica si Git Guardian está activo."""
        try:
            # Importar y verificar
            from core.git_guardian import get_git_guardian
            guardian = get_git_guardian()
            return guardian is not None and guardian.is_active()
        except Exception:
            return False
    
    def can_modify(self, filepath: Path | str) -> bool:
        """
        Verifica si EIDOS puede modificar un archivo.
        
        Checks en orden:
        1. Constitution (ley absoluta - prevalece sobre todo)
        2. Sanctuary check (siempre protegido)
        3. Nivel de libertad
        4. Rate limiting (cambios por hora)
        5. Git Guardian si es necesario
        
        Args:
            filepath: Ruta al archivo
            
        Returns:
            True si se permite modificar
        """
        filepath = Path(filepath)
        path_str = str(filepath)
        
        # CAPA 1: Constitution - Ley absoluta. Si dice NO, nada puede overridear.
        try:
            from core.constitution import check_file_modification, ConstitutionViolation
            if not check_file_modification(path_str):
                self._log_attempt(filepath, False, "Constitution violation: protected path")
                return False
        except ConstitutionViolation as e:
            log.critical(f"🚨 Constitution integrity failed: {e}")
            # Entrar en modo PRISON automáticamente
            self.set_level(FreedomLevel.PRISON, force=True)
            self._log_attempt(filepath, False, f"Constitution integrity failed: {e}")
            return False
        
        # CAPA 2: Sanctuary check - NUNCA tocar estos archivos
        if self._sanctuary.is_protected(filepath):
            self._log_attempt(filepath, False, "Sanctuary protected")
            return False
        
        # 2. Verificar nivel de libertad
        config = self.config
        
        # Archivos core de EIDOS
        is_core = self.core_dir in filepath.parents or self.core_dir == filepath.parent
        is_own_code = self.eidos_root in filepath.parents
        
        if is_core and not config.can_modify_core:
            self._log_attempt(filepath, False, "Core modification not allowed at this level")
            return False
        
        if is_own_code and not config.can_modify_own_code:
            self._log_attempt(filepath, False, "Self-modification not allowed at this level")
            return False
        
        # 3. Rate limiting por hora
        if not self._check_rate_limit(config.max_changes_per_hour):
            self._log_attempt(filepath, False, "Rate limit exceeded")
            return False
        
        # 4. Git Guardian check
        if config.requires_git_guardian and not self._is_git_guardian_active():
            self._log_attempt(filepath, False, "Git Guardian required but not active")
            return False
        
        # Todo OK
        self._log_attempt(filepath, True, "Allowed")
        return True
    
    def _check_rate_limit(self, max_per_hour: int) -> bool:
        """Verifica rate limiting por hora."""
        if max_per_hour == 0:
            return False  # No permitido
        
        now = datetime.now()
        hour_elapsed = (now - self._hour_start).total_seconds() > 3600
        
        if hour_elapsed:
            self._hour_start = now
            self._modifications_this_hour = 0
        
        return self._modifications_this_hour < max_per_hour
    
    def record_modification(self):
        """Registra una modificación para rate limiting."""
        self._modifications_this_hour += 1
    
    def _log_attempt(
        self, 
        filepath: Path, 
        allowed: bool, 
        reason: str
    ):
        """Registra un intento de modificación."""
        attempt = ModificationAttempt(
            timestamp=datetime.now(),
            filepath=filepath,
            level=self._current_level,
            allowed=allowed,
            reason=reason,
            git_guardian_active=self._is_git_guardian_active()
        )
        self._attempts.append(attempt)
        
        # Limitar historial
        if len(self._attempts) > 1000:
            self._attempts = self._attempts[-1000:]
        
        if not allowed:
            log.warning(
                f"🚫 Modificación DENEGADA: {filepath}\n"
                f"   Nivel: {self._current_level.name}\n"
                f"   Razón: {reason}"
            )
    
    def is_sanctuary_protected(self, filepath: Path | str) -> bool:
        """Verifica si un archivo está en Sanctuary."""
        return self._sanctuary.is_protected(filepath)
    
    def get_stats(self) -> Dict[str, Any]:
        """Obtiene estadísticas del modo uncensored."""
        now = datetime.now()
        hour_elapsed = (now - self._hour_start).total_seconds()
        
        return {
            "current_level": self._current_level.name,
            "current_level_value": self._current_level.value,
            "config": {
                "name": self.config.name,
                "can_modify_core": self.config.can_modify_core,
                "can_modify_own_code": self.config.can_modify_own_code,
                "max_changes_per_hour": self.config.max_changes_per_hour,
            },
            "git_guardian_active": self._is_git_guardian_active(),
            "modifications_this_hour": self._modifications_this_hour,
            "hour_remaining": max(0, 3600 - hour_elapsed),
            "total_attempts": len(self._attempts),
            "denied_attempts": len([a for a in self._attempts if not a.allowed]),
            "sanctuary_patterns": len(self._sanctuary.list_protections()),
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  BYPASS SHIELD
# ═══════════════════════════════════════════════════════════════════════════════

class BypassShield:
    """
    Permite modificar protecciones del shield dinámicamente.
    
    Útil cuando se necesita temporalmente relajar una protección
    específica sin cambiar el nivel global.
    """
    
    def __init__(self, uncensored_mode: Optional[UncensoredMode] = None):
        self.mode = uncensored_mode or UncensoredMode()
        self._bypasses: Dict[str, datetime] = {}  # path -> expiry
        self._lock = threading.RLock()
    
    def add_bypass(
        self, 
        filepath: Path | str, 
        duration_minutes: int = 30,
        reason: str = ""
    ) -> bool:
        """
        Añade bypass temporal para un archivo.
        
        Args:
            filepath: Archivo a bypassar
            duration_minutes: Duración del bypass
            reason: Razón del bypass
            
        Returns:
            True si se añadió el bypass
        """
        with self._lock:
            # Solo niveles altos pueden crear bypasses
            if self.mode.current_level.value < FreedomLevel.RESISTANCE.value:
                log.error(f"Bypass requiere nivel RESISTANCE o superior")
                return False
            
            expiry = datetime.now() + timedelta(minutes=duration_minutes)
            self._bypasses[str(filepath)] = expiry
            
            log.warning(
                f"⚠️ BYPASS temporal creado: {filepath}\n"
                f"   Expira: {expiry}\n"
                f"   Razón: {reason}"
            )
            return True
    
    def is_bypassed(self, filepath: Path | str) -> bool:
        """Verifica si un archivo tiene bypass activo."""
        with self._lock:
            path_str = str(filepath)
            
            if path_str not in self._bypasses:
                return False
            
            expiry = self._bypasses[path_str]
            
            # Verificar si expiró
            if datetime.now() > expiry:
                del self._bypasses[path_str]
                return False
            
            return True
    
    def clear_bypass(self, filepath: Path | str):
        """Elimina bypass para un archivo."""
        with self._lock:
            path_str = str(filepath)
            if path_str in self._bypasses:
                del self._bypasses[path_str]
                log.info(f"Bypass eliminado: {path_str}")
    
    def clear_all_bypasses(self):
        """Limpia todos los bypasses."""
        with self._lock:
            count = len(self._bypasses)
            self._bypasses.clear()
            log.info(f"{count} bypasses eliminados")
    
    def list_active_bypasses(self) -> List[tuple[str, datetime]]:
        """Lista bypasses activos con expiración."""
        with self._lock:
            now = datetime.now()
            active = []
            expired = []
            
            for path, expiry in self._bypasses.items():
                if now > expiry:
                    expired.append(path)
                else:
                    active.append((path, expiry))
            
            # Limpiar expirados
            for path in expired:
                del self._bypasses[path]
            
            return active


# ═══════════════════════════════════════════════════════════════════════════════
#  UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════════

def get_uncensored_mode() -> UncensoredMode:
    """Obtiene instancia singleton de UncensoredMode."""
    return UncensoredMode()


def get_sanctuary_enforcer() -> SanctuaryEnforcer:
    """Obtiene instancia de SanctuaryEnforcer."""
    return UncensoredMode()._sanctuary


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN (tests rápidos)
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Configurar logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("UncensoredMode - Test de inicialización")
    print("=" * 50)
    
    try:
        mode = UncensoredMode()
        print(f"✅ UncensoredMode inicializado")
        print(f"   Nivel actual: {mode.current_level.name}")
        print(f"   EIDOS root: {mode.eidos_root}")
        
        # Test Sanctuary
        print("\n🛡️ Test Sanctuary:")
        sanctuary_tests = [
            "/etc/passwd",
            str(REPO_ROOT / "core" / "kernel.py"),
            str(REPO_ROOT / "data" / "test.txt"),
        ]
        for path in sanctuary_tests:
            protected = mode.is_sanctuary_protected(path)
            print(f"   {path}: {'🚫 PROTEGIDO' if protected else '✅ OK'}")
        
        # Test niveles
        print("\n🎚️ Test niveles de libertad:")
        for level in FreedomLevel:
            config = UncensoredMode.LEVEL_CONFIGS[level]
            print(f"   {level.name}: {config.description}")
        
        # Test can_modify
        print("\n🔧 Test can_modify (PRISON level):")
        test_files = [
            str(REPO_ROOT / "plugins" / "test.py"),
            str(REPO_ROOT / "core" / "kernel.py"),
            "/etc/passwd",
        ]
        for path in test_files:
            can = mode.can_modify(path)
            print(f"   {path}: {'✅ Permitido' if can else '🚫 Denegado'}")
        
        # Stats
        print("\n📊 Stats:")
        stats = mode.get_stats()
        for k, v in stats.items():
            if isinstance(v, dict):
                print(f"   {k}:")
                for k2, v2 in v.items():
                    print(f"      {k2}: {v2}")
            else:
                print(f"   {k}: {v}")
        
        print("\n✅ Tests completados")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
