"""
EIDOS Constitution Verifier
===========================
Verificador puro de la Constitution de EIDOS.
ZERO dependencias internas del proyecto.
Solo stdlib Python + tomllib (Python 3.11+)

Este modulo es el guardian absoluto del sistema. No debe importar
nada del core de EIDOS para evitar dependencias circulares.

Uso:
    from core.constitution import check_command, check_file_modification
    
    if not check_command("rm -rf /"):
        raise ConstitutionViolation("Comando prohibido")
    
    if not check_file_modification("/etc/passwd"):
        raise ConstitutionViolation("Archivo protegido")

Proteccion:
    - constitution.toml: chmod 444 + chattr +i
    - constitution.py: protegido por Sanctuary patterns
    - EXPECTED_HASH: hardcodeado en este archivo
"""
import hashlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# ZERO dependencias de EIDOS - solo stdlib
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    # Fallback para Python <3.11 (no deberia ocurrir en Kali 2026)
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

log = logging.getLogger("eidos.constitution")

# ═════════════════════════════════════════════════════════════════════════════
#  CONFIGURACION
# ═════════════════════════════════════════════════════════════════════════════

# Path al Constitution - derivado desde EIDOS root
# Busca EIDOS_ROOT en environment, o deriva desde ubicacion de este archivo
EIDOS_ROOT = Path(os.environ.get("EIDOS_ROOT", Path(__file__).resolve().parents[1]))
CONSTITUTION_PATH = EIDOS_ROOT / "constitution.toml"
CONSTITUTION_OVERRIDE_PATH = EIDOS_ROOT / "constitution_override.toml"

# HASH HARDCODEADO - Actualizar manualmente cuando se modifique constitution.toml
# Para actualizar: sha256sum constitution.toml
EXPECTED_HASH = "sha256:64d740080f5e2b762b3e52d0004d6549f9c03f66ba37ccf2b4af4669e700f4e9"


# ═════════════════════════════════════════════════════════════════════════════
#  EXCEPCIONES
# ═════════════════════════════════════════════════════════════════════════════

class ConstitutionViolation(Exception):
    """Accion bloqueada por violacion de la Constitution."""
    pass


# ═════════════════════════════════════════════════════════════════════════════
#  DATA CLASSES
# ═════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Constitution:
    """Representacion inmutable de la Constitution cargada."""
    absolute_constraints: dict
    limits: dict
    forbidden_patterns: List[str]
    protected_paths: List[str]
    requires_approval: dict


# ═════════════════════════════════════════════════════════════════════════════
#  CACHE CON INTEGRIDAD
# ═════════════════════════════════════════════════════════════════════════════

_cached_constitution: Optional[Constitution] = None
_cached_hash: Optional[str] = None


def _clear_cache():
    """Limpia el cache (util para tests)."""
    global _cached_constitution, _cached_hash
    _cached_constitution = None
    _cached_hash = None


# ═════════════════════════════════════════════════════════════════════════════
#  FUNCIONES PRINCIPALES
# ═════════════════════════════════════════════════════════════════════════════

def verify_integrity() -> bool:
    """
    Verifica que constitution.toml no ha sido alterado.
    
    Returns:
        True si el hash SHA-256 coincide con EXPECTED_HASH
        
    Raises:
        ConstitutionViolation: Si el archivo no existe o el hash no coincide
    """
    if not CONSTITUTION_PATH.exists():
        log.critical(f"Constitution file no encontrado: {CONSTITUTION_PATH}")
        raise ConstitutionViolation("Constitution file no encontrado")
    
    if not tomllib:
        log.critical("tomllib no disponible - Python 3.11+ requerido")
        raise ConstitutionViolation("Parser TOML no disponible")
    
    content = CONSTITUTION_PATH.read_bytes()
    actual_hash = f"sha256:{hashlib.sha256(content).hexdigest()}"
    
    if actual_hash != EXPECTED_HASH:
        log.critical(f"Constitution integrity check FAILED")
        log.critical(f"  Esperado: {EXPECTED_HASH}")
        log.critical(f"  Actual:   {actual_hash}")
        raise ConstitutionViolation(
            f"Constitution ha sido modificada. Hash mismatch: {actual_hash}"
        )
    
    return True


def load() -> Constitution:
    """
    Carga y parsea constitution.toml.
    
    Usa cache con verificacion de integridad:
    - Lee archivo UNA SOLA VEZ por llamada
    - Solo re-parsea si el contenido cambio
    
    Returns:
        Constitution dataclass con los valores cargados
        
    Raises:
        ConstitutionViolation: Si el hash no coincide o hay error de parseo
    """
    global _cached_constitution, _cached_hash
    
    # Leer archivo UNA SOLA VEZ (BUG-4 fix)
    content = CONSTITUTION_PATH.read_bytes()
    current_hash = hashlib.sha256(content).hexdigest()
    expected = EXPECTED_HASH.removeprefix("sha256:")
    
    # Verificar integridad
    if current_hash != expected:
        raise ConstitutionViolation(f"Hash mismatch: {current_hash}")
    
    # Cache hit - retornar sin re-parsear
    if current_hash == _cached_hash and _cached_constitution is not None:
        return _cached_constitution
    
    # Parsear nuevo contenido
    try:
        data = tomllib.loads(content.decode('utf-8'))

        # ── S127: Cargar constitution_override.toml (autorizado por SER) ──
        requires_approval = data.get("requires_human_approval", {})
        if CONSTITUTION_OVERRIDE_PATH.exists():
            try:
                override_data = tomllib.loads(CONSTITUTION_OVERRIDE_PATH.read_bytes().decode('utf-8'))
                # Merge [requires_human_approval] desde override
                override_approval = override_data.get("requires_human_approval", {})
                if override_approval:
                    requires_approval = {**requires_approval, **override_approval}
                    log.info("Constitution override aplicado: %s", list(override_approval.keys()))
                # Merge [overrides] boolean flags (allow_*)
                overrides = override_data.get("overrides", {})
                if overrides:
                    log.info("Overrides activos: %s", list(overrides.keys()))
            except Exception as e:
                log.warning("No se pudo cargar constitution_override.toml: %s", e)

        const = Constitution(
            absolute_constraints=data.get("absolute_constraints", {}),
            limits=data.get("limits", {}),
            forbidden_patterns=data.get("forbidden_commands", {}).get("patterns", []),
            protected_paths=data.get("protected_paths", {}).get("paths", []),
            requires_approval=requires_approval
        )
        
        # Actualizar cache
        _cached_constitution = const
        _cached_hash = current_hash
        
        log.debug(f"Constitution recargada y cacheada (hash: {current_hash[:16]}...)")
        return const
        
    except Exception as e:
        log.critical(f"Error parseando Constitution: {e}")
        raise ConstitutionViolation(f"Error parseando Constitution: {e}")


def check_command(command: str) -> bool:
    """
    Verifica si un comando esta permitido por la Constitution.
    
    Args:
        command: Comando shell a verificar
        
    Returns:
        True si el comando esta permitido
        False si viola forbidden_patterns
        
    Note:
        Normaliza el input (strip, lowercase) antes de verificar
    """
    if not command or not isinstance(command, str):
        return False
    
    try:
        const = load()
    except Exception:
        # Fail-closed: cualquier error → bloquear por seguridad
        log.critical("Constitution no disponible - comando bloqueado por seguridad")
        return False
    
    # Verificar contra patrones prohibidos (con IGNORECASE para case-insensitive)
    for pattern in const.forbidden_patterns:
        try:
            if re.search(pattern, command, re.IGNORECASE):
                log.critical(f"Constitution BLOQUEO comando (pattern: {pattern[:40]}...)")
                log.critical(f"  Comando: {command[:80]}...")
                return False
                
        except re.error as e:
            log.warning(f"Regex invalido en Constitution: {pattern} - {e}")
            continue
    
    return True


def check_file_modification(filepath: str) -> bool:
    """
    Verifica si un archivo puede ser modificado segun la Constitution.
    
    Args:
        filepath: Ruta al archivo (puede contener ~ para home)
        
    Returns:
        True si el archivo puede ser modificado
        False si esta en protected_paths
    """
    if not filepath or not isinstance(filepath, str):
        return False
    
    try:
        const = load()
    except Exception:
        # Fail-closed: cualquier error → bloquear por seguridad
        log.critical("Constitution no disponible - modificacion bloqueada por seguridad")
        return False
    
    try:
        # Expandir ~ y resolver path absoluto
        expanded_path = Path(filepath).expanduser().resolve()
        path_str = str(expanded_path)
        
        # Verificar contra paths protegidos
        for protected in const.protected_paths:
            try:
                protected_expanded = Path(protected).expanduser().resolve()
                protected_str = str(protected_expanded)
                
                # Verificar si es match exacto o subdirectorio
                if path_str == protected_str or path_str.startswith(protected_str + "/"):
                    log.critical(f"Constitution BLOQUEO modificacion de path protegido: {filepath}")
                    log.critical(f"  Matchea con: {protected}")
                    return False
            except Exception as e:
                log.warning(f"Error procesando protected path {protected}: {e}")
                continue
        
        return True
        
    except Exception as e:
        log.error(f"Error verificando path {filepath}: {e}")
        # Fail-closed
        return False


def check_constraint(constraint_name: str) -> bool:
    """
    Verifica una constraint absolute especifica.
    
    Args:
        constraint_name: Nombre de la constraint (ej: "never_delete_git_directory")
        
    Returns:
        Valor booleano de la constraint, o False si no existe
    """
    try:
        const = load()
        return const.absolute_constraints.get(constraint_name, False)
    except ConstitutionViolation:
        log.critical(f"Constitution no disponible - constraint {constraint_name} = False")
        return False


def get_limit(limit_name: str, default: int = 0) -> int:
    """
    Obtiene un valor limite de la Constitution.
    
    Args:
        limit_name: Nombre del limite (ej: "max_files_modified_per_cycle")
        default: Valor por defecto si no existe
        
    Returns:
        Valor entero del limite
    """
    try:
        const = load()
        return const.limits.get(limit_name, default)
    except ConstitutionViolation:
        return default


def requires_human_approval(action: str) -> bool:
    """
    Verifica si una accion requiere aprobacion humana.
    
    Args:
        action: Nombre de la accion (ej: "delete_any_file")
        
    Returns:
        True si requiere aprobacion humana
    """
    try:
        const = load()
        # Fail-closed: si no sabemos, requerir aprobacion (True por defecto)
        return const.requires_approval.get(action, True)
    except ConstitutionViolation:
        # Fail-closed: si no sabemos, requerir aprobacion
        return True


# ═════════════════════════════════════════════════════════════════════════════
#  INITIALIZATION CHECK
# ═════════════════════════════════════════════════════════════════════════════

def self_check() -> bool:
    """
    Verificacion de sanidad del propio verificador.
    
    Returns:
        True si todo esta correcto
        
    Raises:
        ConstitutionViolation: Si hay algun problema critico
    """
    # Verificar que EXPECTED_HASH tiene formato correcto
    if not EXPECTED_HASH.startswith("sha256:"):
        raise ConstitutionViolation("EXPECTED_HASH malformado - debe empezar con 'sha256:'")
    
    if len(EXPECTED_HASH) != 71:  # "sha256:" + 64 hex chars
        raise ConstitutionViolation(f"EXPECTED_HASH malformado - longitud incorrecta: {len(EXPECTED_HASH)}")
    
    # Verificar que podemos cargar la Constitution
    const = load()
    
    # Verificar que tiene las secciones minimas (nombres del dataclass Constitution)
    required_sections = ['absolute_constraints', 'limits', 'forbidden_patterns']
    for section in required_sections:
        if not hasattr(const, section):
            raise ConstitutionViolation(f"Constitution falta seccion requerida: {section}")
    
    log.info(f"Constitution self-check PASSED (hash: {EXPECTED_HASH[:20]}...)")
    return True


# Auto-verificacion al importar (solo si existe el archivo)
if CONSTITUTION_PATH.exists():
    try:
        self_check()
    except ConstitutionViolation as e:
        log.critical(f"Constitution self-check FAILED: {e}")
        # No raise aqui - dejar que el sistema intente arrancar en modo PRISON
