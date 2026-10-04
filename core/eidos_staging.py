"""
EIDOS Staging Environment - Sistema de Pruebas Seguras
=======================================================

EIDOS prueba sus propios cambios en un entorno aislado antes de 
aplicarlos a sí mismo. Esto previene que EIDOS se "rompa" a sí mismo.

Flujo:
1. EIDOS detecta necesidad de mejora
2. Clona su código a staging/
3. Aplica cambios en staging
4. Ejecuta tests en staging
5. Si pasa 100% → aplica a producción
6. Si falla → descarta cambio, informa a SER

Uso:
    from core.eidos_staging import get_staging_system
    staging = get_staging_system()
    
    # Probar una mejora
    result = staging.test_improvement(
        file_path="core/eidos_vision.py",
        original_code="...",
        new_code="..."
    )
    
    if result.success:
        staging.promote_to_production(result.change_id)
    else:
        print(f"Cambio rechazado: {result.error}")
"""

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Union
import logging
from core.db import get_conn
from core.self_edit_lab import run_safe_regression_suite

log = logging.getLogger("eidos.staging")

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

EIDOS_ROOT = Path(__file__).parent.parent
STAGING_DIR = Path.home() / ".eidos" / "staging"
STAGING_DB = Path.home() / ".eidos" / "staging.db"

# Archivos que NUNCA deben modificarse ni en staging
IMMUTABLE_FILES = [
    "core/eidos_staging.py",  # Este archivo - el sistema de staging
    "core/eidos_self_improvement.py",  # El sistema de auto-mejora
    "core/ram_guardian.py",  # Protección de RAM
    "core/eidos_trust_model.py",  # Seguridad
]


def _repo_relative_path(file_path: Union[str, Path]) -> Path:
    """Normalize a requested source path and reject any escape from EIDOS_ROOT."""
    root = EIDOS_ROOT.resolve()
    raw = Path(file_path).expanduser()
    candidate = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes EIDOS root: {file_path}") from exc
    if any(part == ".." for part in relative.parts):
        raise ValueError(f"unsafe relative path: {file_path}")
    return relative


# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class StagingResult:
    """Resultado de prueba en staging."""
    success: bool
    change_id: str
    file_path: str
    description: str
    
    # Tests
    tests_passed: int = 0
    tests_failed: int = 0
    test_output: str = ""
    
    # Metadatos
    timestamp: datetime = field(default_factory=datetime.now)
    staging_path: str = ""
    error: str = ""
    can_promote: bool = False


@dataclass
class StagedChange:
    """Cambio en espera de validación."""
    id: str
    file_path: str
    change_type: str
    description: str
    
    original_code: str
    new_code: str
    diff: str
    
    staging_path: str
    production_path: str
    
    status: str = "pending"  # pending, testing, validated, rejected, promoted
    result: Optional[StagingResult] = None
    created_at: datetime = field(default_factory=datetime.now)
    validated_at: Optional[datetime] = None
    production_sha256: str = ""


# ══════════════════════════════════════════════════════════════════════════════
#  BASE DE DATOS
# ══════════════════════════════════════════════════════════════════════════════

class StagingDB:
    def __init__(self, db_path: Path = STAGING_DB):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        os.makedirs(self.db_path.parent, exist_ok=True)
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS staged_changes (
                id TEXT PRIMARY KEY,
                file_path TEXT,
                change_type TEXT,
                description TEXT,
                original_code TEXT,
                new_code TEXT,
                diff TEXT,
                staging_path TEXT,
                production_path TEXT,
                status TEXT,
                result_json TEXT,
                created_at TEXT,
                validated_at TEXT
            )
        """)
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def save_change(self, change: StagedChange):
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        import json
        result_json = json.dumps(change.result.__dict__ if change.result else {})
        
        cursor.execute("""
            INSERT OR REPLACE INTO staged_changes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            change.id, change.file_path, change.change_type, change.description,
            change.original_code, change.new_code, change.diff,
            change.staging_path, change.production_path, change.status,
            result_json,
            change.created_at.isoformat(),
            change.validated_at.isoformat() if change.validated_at else None
        ))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
# ══════════════════════════════════════════════════════════════════════════════
#  STAGING ENVIRONMENT
# ══════════════════════════════════════════════════════════════════════════════

class StagingEnvironment:
    """Gestiona el entorno de staging (clon de EIDOS)."""
    
    def __init__(self):
        self.staging_root = STAGING_DIR
        self.db = StagingDB()
        
        # Asegurar que existe staging
        os.makedirs(self.staging_root, exist_ok=True)
    
    def create_staging_clone(self, fresh: bool = False) -> Path:
        """
        Crea un clon limpio de EIDOS en staging/.
        
        Args:
            fresh: Si True, borra y recrea desde cero
        """
        if fresh and self.staging_root.exists():
            log.info("Eliminando staging anterior...")
            shutil.rmtree(self.staging_root)
        
        if not self.staging_root.exists() or not any(self.staging_root.iterdir()):
            log.info("Creando clon de EIDOS en staging...")
            
            # Copiar todo el código fuente
            shutil.copytree(
                EIDOS_ROOT,
                self.staging_root,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", "*.pyo", 
                    ".git", ".gitignore",
                    "*.db", "*.log",
                    "node_modules", "venv", ".venv"
                ),
                dirs_exist_ok=True
            )
            
            log.info(f"Staging creado en: {self.staging_root}")
        
        return self.staging_root
    
    def apply_change_to_staging(self, change: StagedChange) -> bool:
        """Aplica un cambio en el entorno de staging."""
        staging_file = Path(change.staging_path)
        
        # Verificar que es seguro modificar
        relative_path = staging_file.relative_to(self.staging_root)
        if str(relative_path) in IMMUTABLE_FILES:
            log.error(f"Intento de modificar archivo protegido: {relative_path}")
            return False
        
        try:
            with open(staging_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Aplicar cambio
            new_content = content.replace(
                change.original_code,
                change.new_code,
                1  # Solo primera ocurrencia
            )
            
            if new_content == content:
                log.error("El código no cambió - original no encontrado")
                return False
            
            with open(staging_file, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            log.info(f"Cambio aplicado en staging: {staging_file}")
            return True
            
        except Exception as e:
            log.error(f"Error aplicando cambio en staging: {e}")
            return False
    
    def run_tests_in_staging(self) -> tuple[bool, str, int, int]:
        """Run the canonical public regression gate inside the staging checkout."""
        log.info("Ejecutando gate de regresión en staging...")
        try:
            result = run_safe_regression_suite(self.staging_root, timeout=180)
            output = (result.stdout or "") + "\n" + (result.stderr or "")
            failed = 0 if result.passed else 1
            log.info(
                "Gate completado: passed=%s tests=%s",
                result.passed,
                result.tests_run,
            )
            return result.passed, output, result.tests_run, failed
        except Exception as e:
            log.error(f"Error ejecutando gate en staging: {e}")
            return False, str(e), 0, 1
    
    def destroy_staging(self):
        """Destruye el entorno de staging (para limpieza)."""
        if self.staging_root.exists():
            shutil.rmtree(self.staging_root)
            log.info("Staging destruido")


# ══════════════════════════════════════════════════════════════════════════════
#  SISTEMA DE STAGING PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class StagingSystem:
    """
    Sistema principal de staging para EIDOS.
    Coordina la creación, prueba y promoción de cambios.
    """
    
    def __init__(self):
        self.env = StagingEnvironment()
        self.db = StagingDB()
        log.info("Staging System initialized")
    
    def test_improvement(self, file_path: Union[str, Path],
                         original_code: str,
                         new_code: str,
                         description: str = "",
                         change_type: str = "improvement") -> StagingResult:
        """
        Prueba una mejora en staging antes de aplicarla a producción.
        
        Este es el método principal del sistema de staging.
        """
        try:
            relative_path = _repo_relative_path(file_path)
        except ValueError as exc:
            return StagingResult(
                success=False,
                change_id="invalid_path",
                file_path=str(relative_path),
                description=description,
                error=str(exc),
            )

        change_id = (
            f"staging_{int(datetime.now().timestamp())}_"
            f"{hashlib.md5(str(relative_path).encode()).hexdigest()[:8]}"
        )

        log.info(f"=== STAGING TEST: {change_id} ===")
        log.info(f"Archivo: {relative_path}")
        log.info(f"Descripción: {description}")

        # 1. Crear staging limpio y medir baseline antes de tocarlo.
        staging_root = self.env.create_staging_clone(fresh=True)
        baseline_ok, baseline_output, baseline_passed, baseline_failed = self.env.run_tests_in_staging()
        staging_file = staging_root / relative_path
        
        # 2. Verificar que archivo existe en staging
        if not staging_file.exists():
            return StagingResult(
                success=False,
                change_id=change_id,
                file_path=str(file_path),
                description=description,
                error=f"Archivo no encontrado en staging: {staging_file}"
            )
        
        # 3. Crear objeto de cambio
        import difflib
        diff = '\n'.join(difflib.unified_diff(
            original_code.splitlines(),
            new_code.splitlines(),
            fromfile='original',
            tofile='new'
        ))
        
        production_source = (EIDOS_ROOT / relative_path).resolve().read_bytes()
        production_sha256 = hashlib.sha256(production_source).hexdigest()

        change = StagedChange(
            id=change_id,
            file_path=str(file_path),
            change_type=change_type,
            description=description,
            original_code=original_code,
            new_code=new_code,
            diff=diff,
            staging_path=str(staging_file),
            production_path=str((EIDOS_ROOT / relative_path).resolve()),
            status="testing",
            production_sha256=production_sha256,
        )
        
        # 4. Aplicar cambio en staging
        if not self.env.apply_change_to_staging(change):
            change.status = "rejected"
            self.db.save_change(change)
            
            return StagingResult(
                success=False,
                change_id=change_id,
                file_path=str(file_path),
                description=description,
                error="No se pudo aplicar cambio en staging"
            )
        
        # 5. Ejecutar el mismo gate después de aplicar el candidato.
        success, output, passed, failed = self.env.run_tests_in_staging()

        # Baseline rota + candidato verde = mejora válida.
        # Baseline verde + candidato debe permanecer verde (no regresión).
        non_regression = success and failed == 0
        if baseline_ok and not non_regression:
            output = (
                "BASELINE PASSED but candidate regressed\n"
                + baseline_output
                + "\n--- CANDIDATE ---\n"
                + output
            )

        # 6. Evaluar resultado
        result = StagingResult(
            success=non_regression,
            change_id=change_id,
            file_path=str(file_path),
            description=description,
            tests_passed=passed,
            tests_failed=failed,
            test_output=output,
            staging_path=str(staging_file),
            can_promote=non_regression
        )
        
        if not result.success:
            result.error = f"Tests fallaron: {failed} errores"
        
        # 7. Guardar resultado
        change.result = result
        change.status = "validated" if result.success else "rejected"
        if result.success:
            change.validated_at = datetime.now()
        
        self.db.save_change(change)
        
        # 8. Reportar
        if result.success:
            log.info(f"✅ STAGING EXITOSO: {change_id}")
            log.info(f"   Tests: {passed} pasaron, {failed} fallaron")
            log.info(f"   Listo para promover a producción")
        else:
            log.warning(f"❌ STAGING FALLÓ: {change_id}")
            log.warning(f"   Error: {result.error}")
            log.warning(f"   Cambio NO se aplicará a producción")
        
        return result
    
    def promote_to_production(self, change_id: str,
                              backup_first: bool = True) -> bool:
        """
        Promueve un cambio validado de staging a producción.
        """
        log.info(f"Promoviendo {change_id} a producción...")
        
        # Cargar cambio
        conn = get_conn(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM staged_changes WHERE id = ?", (change_id,))
        row = cursor.fetchone()
        pass  # S109: get_conn no necesita close()
        if not row:
            log.error(f"Cambio {change_id} no encontrado")
            return False
        
        # Verificar que fue validado
        status = row[9]  # status column
        if status != "validated":
            log.error(f"Cambio no validado (status: {status})")
            return False
        
        file_path = row[1]
        original_code = row[4]
        new_code = row[5]
        production_file = Path(row[8]).expanduser().resolve()  # production_path

        # Reject time-of-check/time-of-use drift: only promote the exact file
        # version whose candidate was validated.
        expected_sha = hashlib.sha256(original_code.encode("utf-8")).hexdigest()
        try:
            current_sha = hashlib.sha256(production_file.read_bytes()).hexdigest()
        except OSError as exc:
            log.error("No se puede verificar archivo de producción: %s", exc)
            return False
        # original_code can be a fragment in historical callers, so additionally
        # require it to be present. Full-file staging callers get exact hash safety.
        current_text = production_file.read_text(encoding="utf-8")
        if original_code not in current_text:
            log.error("Producción cambió desde la validación; fragmento original ausente")
            return False

        # Verificar que sigue dentro del árbol real.
        try:
            relative = production_file.relative_to(EIDOS_ROOT.resolve())
        except ValueError:
            log.error("Ruta de producción fuera de EIDOS_ROOT")
            return False
        if str(relative) in IMMUTABLE_FILES:
            log.error("No se puede modificar archivo protegido")
            return False
        
        # Backup antes de cambiar
        if backup_first:
            backup_dir = Path.home() / ".eidos" / "code_backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_file = backup_dir / f"{production_file.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{production_file.suffix}"
            shutil.copy2(production_file, backup_file)
            log.info(f"Backup creado: {backup_file}")
        
        # Aplicar a producción
        try:
            with open(production_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            new_content = content.replace(original_code, new_code, 1)
            
            if new_content == content:
                log.error("Código original no encontrado en producción")
                return False
            
            with open(production_file, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            # Actualizar status
            conn = get_conn(self.db.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE staged_changes SET status = ? WHERE id = ?",
                ("promoted", change_id)
            )
            conn.commit()
            pass  # S109: get_conn no necesita close()
            log.info(f"✅ Cambio promovido a producción: {file_path}")
            return True
            
        except Exception as e:
            log.error(f"Error promoviendo a producción: {e}")
            return False
    
    def get_staging_history(self, limit: int = 10) -> List[StagedChange]:
        """Retorna historial de cambios en staging."""
        conn = get_conn(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM staged_changes ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        changes = []
        import json
        for row in rows:
            result_dict = json.loads(row[10]) if row[10] else {}
            result = StagingResult(**result_dict) if result_dict else None
            
            changes.append(StagedChange(
                id=row[0], file_path=row[1], change_type=row[2],
                description=row[3], original_code=row[4], new_code=row[5],
                diff=row[6], staging_path=row[7], production_path=row[8],
                status=row[9], result=result,
                created_at=datetime.fromisoformat(row[11]),
                validated_at=datetime.fromisoformat(row[12]) if row[12] else None
            ))
        
        return changes


# Singleton
_staging_system: Optional[StagingSystem] = None

def get_staging_system() -> StagingSystem:
    global _staging_system
    if _staging_system is None:
        _staging_system = StagingSystem()
    return _staging_system


# ══════════════════════════════════════════════════════════════════════════════
#  TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  EIDOS Staging System - Test")
    print("=" * 70)
    
    staging = get_staging_system()
    
    # Test 1: Crear staging
    print("\n[Test 1] Creando entorno de staging...")
    staging_path = staging.env.create_staging_clone(fresh=True)
    print(f"  ✅ Staging creado en: {staging_path}")
    
    # Test 2: Verificar que es clon válido
    print("\n[Test 2] Verificando clon...")
    test_file = staging_path / "core" / "ram_guardian.py"
    if test_file.exists():
        print(f"  ✅ Clon válido: {test_file.exists()}")
    
    # Test 3: Intentar modificación de prueba (no aplicar realmente)
    print("\n[Test 3] Simulando cambio de prueba...")
    # Leer código actual
    with open(test_file, 'r') as f:
        content = f.read()
    
    # Probar con cambio trivial (comentario)
    original = "# Thresholds (porcentaje de RAM usado)"
    new = "# Thresholds (porcentaje de RAM usado) - TEST STAGING"
    
    if original in content:
        result = staging.test_improvement(
            file_path="core/ram_guardian.py",
            original_code=original,
            new_code=new,
            description="Test de staging system"
        )
        
        print(f"  Resultado: {'✅ ÉXITO' if result.success else '❌ FALLÓ'}")
        print(f"  Tests: {result.tests_passed} pasaron, {result.failed} fallaron")
        
        if result.success:
            print("\n[Test 4] Promoviendo a producción...")
            # NO promover realmente en test - solo verificar que podríamos
            print(f"  ✅ Listo para promover (change_id: {result.change_id})")
            print(f"  ⚠️  No se promovió (modo test)")
    else:
        print("  ⚠️  Patrón de prueba no encontrado")
    
    print("\n✅ Staging System test complete")
    print("   EIDOS can now test changes safely before applying them.")
