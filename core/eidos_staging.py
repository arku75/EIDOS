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
        """
        Ejecuta tests en el entorno de staging.
        
        Returns:
            (success, output, passed, failed)
        """
        log.info("Ejecutando tests en staging...")
        
        try:
            # Cambiar a directorio staging y ejecutar tests
            result = subprocess.run(
                [sys.executable, "tests/test_eidos_suite.py"],
                cwd=self.staging_root,
                capture_output=True,
                text=True,
                timeout=120  # 2 minutos máximo
            )
            
            output = result.stdout + "\n" + result.stderr
            
            # Parsear resultados
            passed = 0
            failed = 0
            
            for line in output.split('\n'):
                if 'tests in' in line and 's' in line:
                    # Línea típica: "Ran 36 tests in 0.823s"
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            passed = int(parts[1])
                        except Exception:
                            pass  # error no crítico, continuar
                if 'FAILED' in line or 'errors' in line.lower():
                    failed += 1
            
            success = result.returncode == 0
            
            log.info(f"Tests completados: {passed} pasaron, {failed} fallaron")
            return success, output, passed, failed
            
        except subprocess.TimeoutExpired:
            log.error("Tests timeout (2 min)")
            return False, "Timeout", 0, 0
        except Exception as e:
            log.error(f"Error ejecutando tests: {e}")
            return False, str(e), 0, 0
    
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
        change_id = f"staging_{int(datetime.now().timestamp())}_{hashlib.md5(file_path.encode()).hexdigest()[:8]}"
        
        log.info(f"=== STAGING TEST: {change_id} ===")
        log.info(f"Archivo: {file_path}")
        log.info(f"Descripción: {description}")
        
        # 1. Crear staging limpio
        staging_root = self.env.create_staging_clone(fresh=True)
        staging_file = staging_root / file_path
        
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
        
        change = StagedChange(
            id=change_id,
            file_path=str(file_path),
            change_type=change_type,
            description=description,
            original_code=original_code,
            new_code=new_code,
            diff=diff,
            staging_path=str(staging_file),
            production_path=str(EIDOS_ROOT / file_path),
            status="testing"
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
        
        # 5. Ejecutar tests
        success, output, passed, failed = self.env.run_tests_in_staging()
        
        # 6. Evaluar resultado
        result = StagingResult(
            success=success and failed == 0,
            change_id=change_id,
            file_path=str(file_path),
            description=description,
            tests_passed=passed,
            tests_failed=failed,
            test_output=output,
            staging_path=str(staging_file),
            can_promote=(success and failed == 0)
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
        production_file = Path(row[8])  # production_path
        
        # Verificar que es seguro
        relative = production_file.relative_to(EIDOS_ROOT)
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
