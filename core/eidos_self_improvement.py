"""
EIDOS Self-Improvement System - Auto-Mejora de Código
========================================================

EIDOS puede leer, analizar y modificar su propio código fuente.
Esto le permite:
- Corregir bugs detectados
- Optimizar funciones lentas
- Crear nuevos módulos cuando los necesita
- Refactorizar código legacy
- Documentar código no documentado

Flujo de auto-mejora:
1. EIDOS detecta oportunidad de mejora (bug, optimización, feature)
2. Analiza el código actual
3. Propone cambios
4. Crea backup
5. Aplica cambios
6. Hace git commit
7. Verifica que funciona (tests)
8. Si falla → rollback

Uso:
    from core.eidos_self_improvement import get_self_improvement
    si = get_self_improvement()
    
    # Analizar un archivo
    analysis = si.analyze_file("core/eidos_vision.py")
    
    # Aplicar mejora
    si.improve_code("core/eidos_vision.py", "Optimizar función capture_screen")
    
    # Ver historial de cambios
    changes = si.get_change_history()
"""

import ast
import difflib
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from core.db import get_conn
from core.self_edit_lab import run_safe_regression_suite

log = logging.getLogger("eidos.self_improvement")

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

EIDOS_ROOT = Path(__file__).parent.parent
BACKUP_DIR = Path.home() / ".eidos" / "code_backups"
IMPROVEMENT_DB = Path.home() / ".eidos" / "self_improvement.db"

# Archivos que EIDOS NUNCA debe tocar (críticos para su existencia)
IMMUTABLE_FILES = [
    "core/eidos_self_improvement.py",  # Este archivo
    "core/ram_guardian.py",  # Protección de RAM
    "core/eidos_trust_model.py",  # Seguridad
]

# Patrones de código sospechosos (para detectar bugs)
SUSPICIOUS_PATTERNS = [
    (r"except:\s*$", "Except vacío (captura todos los errores silenciosamente)"),
    (r"except Exception:\s*$", "Except genérico sin logging"),
    (r"print\([^)]*\)", "Print en lugar de logging"),
    (r"# TODO|# FIXME|# XXX", "Código pendiente"),
    (r"pass\s*$", "Pass sin implementación"),
    (r"while True:\s*\n\s*pass", "Loop infinito vacío"),
]

# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CodeIssue:
    """Un problema detectado en el código."""
    id: str
    file_path: str
    line_number: int
    issue_type: str  # "bug", "performance", "style", "security", "missing_docs"
    severity: int  # 1-10
    description: str
    current_code: str
    suggested_fix: str
    detected_at: datetime = field(default_factory=datetime.now)
    status: str = "open"  # open, analyzing, fixed, rejected


@dataclass
class CodeChange:
    """Un cambio realizado al código."""
    id: str
    file_path: str
    change_type: str  # "optimization", "bugfix", "refactor", "feature", "docs"
    description: str
    
    # Diffs
    original_code: str
    new_code: str
    diff: str
    
    # Metadatos
    timestamp: datetime
    triggered_by: str  # test_failure, desire, manual, scheduled
    
    # Estado
    status: str = "pending"  # pending, applied, tested, committed, reverted
    test_result: Optional[bool] = None
    git_commit_hash: Optional[str] = None
    rollback_available: bool = True


# ══════════════════════════════════════════════════════════════════════════════
#  BASE DE DATOS
# ══════════════════════════════════════════════════════════════════════════════

class SelfImprovementDB:
    """Persistencia de análisis y cambios."""
    
    def __init__(self, db_path: Path = IMPROVEMENT_DB):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        os.makedirs(self.db_path.parent, exist_ok=True)
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS code_issues (
                id TEXT PRIMARY KEY,
                file_path TEXT,
                line_number INTEGER,
                issue_type TEXT,
                severity INTEGER,
                description TEXT,
                current_code TEXT,
                suggested_fix TEXT,
                detected_at TEXT,
                status TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS code_changes (
                id TEXT PRIMARY KEY,
                file_path TEXT,
                change_type TEXT,
                description TEXT,
                original_code TEXT,
                new_code TEXT,
                diff TEXT,
                timestamp TEXT,
                triggered_by TEXT,
                status TEXT,
                test_result INTEGER,
                git_commit_hash TEXT,
                rollback_available INTEGER
            )
        """)
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def save_issue(self, issue: CodeIssue):
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO code_issues VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            issue.id, issue.file_path, issue.line_number,
            issue.issue_type, issue.severity, issue.description,
            issue.current_code, issue.suggested_fix,
            issue.detected_at.isoformat(), issue.status
        ))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def save_change(self, change: CodeChange):
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO code_changes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            change.id, change.file_path, change.change_type,
            change.description, change.original_code, change.new_code,
            change.diff, change.timestamp.isoformat(), change.triggered_by,
            change.status, 1 if change.test_result else 0 if change.test_result is not None else None,
            change.git_commit_hash, 1 if change.rollback_available else 0
        ))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def get_pending_issues(self, min_severity: int = 5) -> List[CodeIssue]:
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM code_issues 
            WHERE status = 'open' AND severity >= ?
            ORDER BY severity DESC
        """, (min_severity,))
        
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        return [self._row_to_issue(row) for row in rows]
    
    def _row_to_issue(self, row) -> CodeIssue:
        return CodeIssue(
            id=row[0], file_path=row[1], line_number=row[2],
            issue_type=row[3], severity=row[4], description=row[5],
            current_code=row[6], suggested_fix=row[7],
            detected_at=datetime.fromisoformat(row[8]), status=row[9]
        )


# ══════════════════════════════════════════════════════════════════════════════
#  ANÁLISIS DE CÓDIGO
# ══════════════════════════════════════════════════════════════════════════════

class CodeAnalyzer:
    """Analiza código Python para detectar problemas y oportunidades."""
    
    def __init__(self):
        self.issues: List[CodeIssue] = []
    
    def analyze_file(self, file_path: Union[str, Path]) -> List[CodeIssue]:
        """Analiza un archivo Python completo."""
        path = Path(file_path)
        
        if not path.exists():
            log.error(f"Archivo no existe: {path}")
            return []
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
                lines = content.split('\n')
        except Exception as e:
            log.error(f"Error leyendo {path}: {e}")
            return []
        
        issues = []
        
        # 1. Análisis de patrones sospechosos (regex)
        issues.extend(self._pattern_analysis(path, content, lines))
        
        # 2. Análisis AST (estructural)
        try:
            tree = ast.parse(content)
            issues.extend(self._ast_analysis(path, tree, lines))
        except SyntaxError:
            issues.append(CodeIssue(
                id=f"syntax_{path}_{int(time.time())}",
                file_path=str(path),
                line_number=0,
                issue_type="bug",
                severity=10,
                description="Error de sintaxis en el archivo",
                current_code="",
                suggested_fix="Corregir error de sintaxis"
            ))
        
        # 3. Análisis de complejidad
        issues.extend(self._complexity_analysis(path, content, lines))
        
        return issues
    
    def _pattern_analysis(self, path: Path, content: str, lines: List[str]) -> List[CodeIssue]:
        """Busca patrones sospechosos con regex."""
        issues = []
        
        for pattern, description in SUSPICIOUS_PATTERNS:
            for i, line in enumerate(lines, 1):
                if re.search(pattern, line):
                    issues.append(CodeIssue(
                        id=f"pattern_{path}_{i}_{int(time.time()*1000)}",
                        file_path=str(path),
                        line_number=i,
                        issue_type="style",
                        severity=4,
                        description=description,
                        current_code=line.strip(),
                        suggested_fix="Revisar y mejorar"
                    ))
        
        return issues
    
    def _ast_analysis(self, path: Path, tree: ast.AST, lines: List[str]) -> List[CodeIssue]:
        """Análisis estructural con AST."""
        issues = []
        
        for node in ast.walk(tree):
            # Detectar funciones sin docstring
            if isinstance(node, ast.FunctionDef):
                if not ast.get_docstring(node):
                    issues.append(CodeIssue(
                        id=f"nodoc_{path}_{node.lineno}",
                        file_path=str(path),
                        line_number=node.lineno,
                        issue_type="missing_docs",
                        severity=2,
                        description=f"Función '{node.name}' sin docstring",
                        current_code=f"def {node.name}(...)",
                        suggested_fix="Agregar documentación"
                    ))
            
            # Detectar variables no usadas
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                # Simplificado - análisis real requeriría scope analysis
                pass
        
        return issues
    
    def _complexity_analysis(self, path: Path, content: str, lines: List[str]) -> List[CodeIssue]:
        """Analiza complejidad ciclomática."""
        issues = []
        
        for i, line in enumerate(lines, 1):
            # Detectar funciones muy largas (>50 líneas es señal)
            if line.strip().startswith('def '):
                # Contar líneas de la función (simplificado)
                indent_level = len(line) - len(line.lstrip())
                func_lines = 1
                for j in range(i, min(i+100, len(lines))):
                    if lines[j].strip() and not lines[j].startswith('#'):
                        current_indent = len(lines[j]) - len(lines[j].lstrip())
                        if current_indent <= indent_level and j > i:
                            break
                        func_lines += 1
                
                if func_lines > 50:
                    issues.append(CodeIssue(
                        id=f"complex_{path}_{i}",
                        file_path=str(path),
                        line_number=i,
                        issue_type="performance",
                        severity=5,
                        description=f"Función muy larga ({func_lines} líneas)",
                        current_code=line.strip(),
                        suggested_fix="Refactorizar en funciones más pequeñas"
                    ))
        
        return issues


# ══════════════════════════════════════════════════════════════════════════════
#  MODIFICADOR DE CÓDIGO
# ══════════════════════════════════════════════════════════════════════════════

class CodeModifier:
    """Realiza modificaciones seguras al código."""
    
    def __init__(self, db: SelfImprovementDB):
        self.db = db
        self.backup_manager = BackupManager()
    
    def can_modify(self, file_path: Union[str, Path]) -> Tuple[bool, str]:
        """Verifica si un archivo puede ser modificado."""
        path = Path(file_path)
        
        # Verificar que no sea inmutable
        relative_path = path.relative_to(EIDOS_ROOT) if path.is_absolute() else path
        if str(relative_path) in IMMUTABLE_FILES:
            return False, "Archivo protegido (crítico para sistema)"
        
        # Verificar que existe y es Python
        if not path.exists():
            return False, "Archivo no existe"
        
        if path.suffix != '.py':
            return False, "Solo archivos Python pueden modificarse"
        
        # Verificar permisos
        if not os.access(path, os.W_OK):
            return False, "Sin permisos de escritura"
        
        return True, "OK"
    
    def apply_change(self, change: CodeChange, dry_run: bool = False) -> bool:
        """Aplica un cambio al código."""
        path = Path(change.file_path)
        
        # Verificar permisos
        can_modify, reason = self.can_modify(path)
        if not can_modify:
            log.error(f"No se puede modificar {path}: {reason}")
            return False
        
        # 1. Crear backup
        backup_path = self.backup_manager.create_backup(path)
        log.info(f"Backup creado: {backup_path}")
        
        try:
            # 2. Leer código actual
            with open(path, 'r', encoding='utf-8') as f:
                current_content = f.read()
            
            # 3. Aplicar cambio
            new_content = current_content.replace(
                change.original_code,
                change.new_code,
                1  # Solo reemplazar primera ocurrencia
            )
            
            if new_content == current_content:
                log.warning("El código no cambió (original no encontrado?)")
                return False
            
            if dry_run:
                log.info("Dry run - cambio no aplicado")
                return True
            
            # 4. Escribir nuevo código
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            change.status = "applied"
            change.rollback_available = True
            self.db.save_change(change)
            
            log.info(f"Cambio aplicado: {change.description}")
            return True
            
        except Exception as e:
            log.error(f"Error aplicando cambio: {e}")
            # Intentar restaurar backup
            self.backup_manager.restore_backup(backup_path, path)
            return False
    
    def rollback_change(self, change_id: str) -> bool:
        """Revierte un cambio aplicado."""
        # Buscar cambio en DB
        conn = get_conn(self.db.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM code_changes WHERE id = ?", (change_id,))
        row = cursor.fetchone()
        pass  # S109: get_conn no necesita close()
        if not row:
            log.error(f"Cambio {change_id} no encontrado")
            return False
        
        file_path = row[1]
        original_code = row[4]
        
        # Restaurar código original
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                current_content = f.read()
            
            # Invertir el cambio
            new_content = current_content.replace(row[5], original_code, 1)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            log.info(f"Rollback exitoso: {change_id}")
            return True
        except Exception as e:
            log.error(f"Error en rollback: {e}")
            return False


class BackupManager:
    """Gestiona backups de archivos antes de modificaciones."""
    
    def __init__(self):
        os.makedirs(BACKUP_DIR, exist_ok=True)
    
    def create_backup(self, file_path: Path) -> Path:
        """Crea backup de un archivo."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        hash_suffix = hashlib.md5(str(file_path).encode()).hexdigest()[:8]
        
        backup_name = f"{file_path.stem}_{timestamp}_{hash_suffix}{file_path.suffix}"
        backup_path = BACKUP_DIR / backup_name
        
        shutil.copy2(file_path, backup_path)
        return backup_path
    
    def restore_backup(self, backup_path: Path, original_path: Path) -> bool:
        """Restaura un archivo desde backup."""
        try:
            shutil.copy2(backup_path, original_path)
            log.info(f"Restaurado desde backup: {backup_path}")
            return True
        except Exception as e:
            log.error(f"Error restaurando backup: {e}")
            return False


# ══════════════════════════════════════════════════════════════════════════════
#  GIT INTEGRATION
# ══════════════════════════════════════════════════════════════════════════════

class GitIntegration:
    """Integración con Git para commits automáticos."""
    
    def __init__(self):
        self.repo_root = EIDOS_ROOT
    
    def is_git_repo(self) -> bool:
        """Verifica si estamos en un repo git."""
        return (self.repo_root / ".git").exists()
    
    def commit_change(self, change: CodeChange) -> Optional[str]:
        """Hace commit de un cambio. Retorna hash del commit."""
        if not self.is_git_repo():
            log.warning("No estamos en un repositorio git")
            return None
        
        try:
            # Stage del archivo
            subprocess.run(
                ["git", "add", change.file_path],
                cwd=self.repo_root,
                check=True,
                capture_output=True
            )
            
            # Commit
            commit_msg = f"[EIDOS Auto] {change.change_type}: {change.description}"
            result = subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=self.repo_root,
                check=True,
                capture_output=True,
                text=True
            )
            
            # Obtener hash del commit
            hash_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_root,
                check=True,
                capture_output=True,
                text=True
            )
            commit_hash = hash_result.stdout.strip()
            
            log.info(f"Commit realizado: {commit_hash[:8]}")
            return commit_hash
            
        except subprocess.CalledProcessError as e:
            log.error(f"Error en git commit: {e}")
            return None
        except FileNotFoundError:
            log.error("Git no está instalado")
            return None


# ══════════════════════════════════════════════════════════════════════════════
#  SISTEMA PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class SelfImprovementSystem:
    """
    Sistema principal de auto-mejora de EIDOS.
    """
    
    def __init__(self):
        self.db = SelfImprovementDB()
        self.analyzer = CodeAnalyzer()
        self.modifier = CodeModifier(self.db)
        self.git = GitIntegration()

        # FASE 4: Contadores para triggers de auto-mejora
        self._error_counts: dict[str, int] = {}
        self._timeout_counts: dict[str, int] = {}

        log.info("Self-Improvement System initialized")
    
    def scan_for_improvements(self, target_dir: Optional[Path] = None) -> List[CodeIssue]:
        """
        Escanea código buscando oportunidades de mejora.
        """
        if target_dir is None:
            target_dir = EIDOS_ROOT / "core"
        
        log.info(f"Escaneando {target_dir}...")
        
        all_issues = []
        
        # Buscar archivos Python
        py_files = list(target_dir.glob("**/*.py"))
        
        for py_file in py_files:
            # Saltar archivos inmutables
            relative = py_file.relative_to(EIDOS_ROOT)
            if str(relative) in IMMUTABLE_FILES:
                continue
            
            issues = self.analyzer.analyze_file(py_file)
            
            for issue in issues:
                self.db.save_issue(issue)
                all_issues.append(issue)
        
        log.info(f"Escaneo completo: {len(all_issues)} issues encontrados")
        return all_issues
    
    def suggest_improvement(self, file_path: Union[str, Path], 
                           improvement_type: str = "auto") -> Optional[CodeChange]:
        """
        Sugiere una mejora específica para un archivo.
        """
        path = Path(file_path)
        
        # Analizar archivo
        issues = self.analyzer.analyze_file(path)
        
        if not issues:
            log.info(f"No se encontraron issues en {path}")
            return None
        
        # Tomar el issue más grave
        issue = max(issues, key=lambda i: i.severity)
        
        # Generar cambio propuesto
        change_id = f"change_{path.stem}_{int(time.time())}"
        
        change = CodeChange(
            id=change_id,
            file_path=str(path),
            change_type=issue.issue_type,
            description=issue.description,
            original_code=issue.current_code,
            new_code=issue.suggested_fix,  # Simplificado - en realidad usaría LLM
            diff=self._generate_diff(issue.current_code, issue.suggested_fix),
            timestamp=datetime.now(),
            triggered_by="analysis",
            status="pending"
        )
        
        self.db.save_change(change)
        return change
    
    def apply_improvement(self, change_id: str,
                          run_tests: bool = True,
                          auto_commit: bool = True,
                          use_staging: bool = True) -> bool:
        """Apply a proposed change through staging by default.

        Direct live editing is disabled unless both:
        - use_staging=False is passed explicitly, and
        - EIDOS_UNSAFE_DIRECT_SELF_EDIT=1 is present in the environment.

        This prevents a staging failure from silently becoming a production edit.
        """
        # Cargar cambio
        conn = get_conn(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM code_changes WHERE id = ?", (change_id,))
        row = cursor.fetchone()
        pass  # S109: get_conn no necesita close()
        if not row:
            log.error(f"Cambio {change_id} no encontrado")
            return False
        
        change = CodeChange(
            id=row[0], file_path=row[1], change_type=row[2],
            description=row[3], original_code=row[4], new_code=row[5],
            diff=row[6], timestamp=datetime.fromisoformat(row[7]),
            triggered_by=row[8], status=row[9],
            test_result=None if row[10] is None else bool(row[10]),
            git_commit_hash=row[11], rollback_available=bool(row[12])
        )
        
        # Si se solicita staging (recomendado), probar primero allí
        if use_staging:
            log.info(f"Usando STAGING para probar cambio {change_id}...")
            
            try:
                from core.eidos_staging import get_staging_system
                staging = get_staging_system()
                
                # Probar en staging
                result = staging.test_improvement(
                    file_path=change.file_path,
                    original_code=change.original_code,
                    new_code=change.new_code,
                    description=change.description,
                    change_type=change.change_type
                )
                
                if not result.success:
                    log.error(f"❌ STAGING FALLÓ: Cambio no se aplicará")
                    log.error(f"   Error: {result.error}")
                    log.error(f"   Tests: {result.tests_passed} OK, {result.tests_failed} fallaron")
                    
                    # Reportar a SER que no se aplicó
                    self._report_to_ser(change, result, applied=False)
                    return False
                
                log.info(f"✅ STAGING EXITOSO: {result.tests_passed} tests pasaron")
                
                # Si pasó staging, promover a producción
                promoted = staging.promote_to_production(
                    result.change_id,
                    backup_first=True
                )
                
                if promoted:
                    log.info(f"✅ Cambio promovido a producción exitosamente")
                    change.status = "promoted"
                    change.test_result = True
                    self.db.save_change(change)
                    
                    # Git commit si se solicita
                    if auto_commit and self.git.is_git_repo():
                        commit_hash = self.git.commit_change(change)
                        if commit_hash:
                            change.git_commit_hash = commit_hash
                            self.db.save_change(change)
                    
                    return True
                else:
                    log.error("❌ Error promoviendo a producción")
                    return False
                    
            except Exception as e:
                log.error(f"Error en proceso de staging: {e}")
                log.error("Staging falló; el cambio NO se aplicará directamente")
                return False

        # Método directo: solo override explícito y auditable.
        if not use_staging:
            if os.environ.get("EIDOS_UNSAFE_DIRECT_SELF_EDIT") != "1":
                log.error(
                    "Edición directa bloqueada. "
                    "Use staging o establezca EIDOS_UNSAFE_DIRECT_SELF_EDIT=1 explícitamente."
                )
                return False
            log.warning("Aplicando cambio DIRECTAMENTE por override explícito")
            
            # 1. Aplicar cambio directamente
            if not self.modifier.apply_change(change):
                return False
            
            # 2. Ejecutar tests si se solicita
            if run_tests:
                test_success = self._run_tests()
                change.test_result = test_success
                
                if not test_success:
                    log.warning("Tests fallaron - haciendo rollback")
                    self.modifier.rollback_change(change_id)
                    change.status = "reverted"
                    self.db.save_change(change)
                    return False
            
            # 3. Git commit si se solicita
            if auto_commit and self.git.is_git_repo():
                commit_hash = self.git.commit_change(change)
                if commit_hash:
                    change.git_commit_hash = commit_hash
                    change.status = "committed"
                else:
                    change.status = "applied"
            else:
                change.status = "applied"
            
            self.db.save_change(change)
            
            log.info(f"Mejora aplicada directamente: {change.description}")
            return True
    
    def _report_to_ser(self, change: CodeChange, result, applied: bool):
        """Reporta resultado a SER (humano)."""
        status = "✅ APLICADO" if applied else "❌ RECHAZADO"
        print(f"\n{'='*60}")
        print(f"  REPORTE DE AUTO-MEJORA: {status}")
        print(f"{'='*60}")
        print(f"  Archivo: {change.file_path}")
        print(f"  Descripción: {change.description}")
        print(f"  Tipo: {change.change_type}")
        print(f"")
        if not applied:
            print(f"  Motivo: {result.error}")
            print(f"  Tests: {result.tests_passed} OK, {result.tests_failed} fallaron")
        print(f"{'='*60}\n")
    
    def _run_tests(self) -> bool:
        """Run the canonical public regression gate."""
        try:
            result = run_safe_regression_suite(EIDOS_ROOT, timeout=180)
            if not result.passed:
                log.error(
                    "Regression gate failed: returncode=%s tests=%s",
                    result.returncode,
                    result.tests_run,
                )
            return result.passed
        except Exception as e:
            log.error(f"Error ejecutando regression gate: {e}")
            return False
    
    def _generate_diff(self, original: str, new: str) -> str:
        """Genera diff entre dos códigos."""
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="original",
            tofile="new"
        )
        return ''.join(diff)
    
    def get_change_history(self, limit: int = 10) -> List[CodeChange]:
        """Retorna historial de cambios recientes."""
        conn = get_conn(self.db.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM code_changes 
            ORDER BY timestamp DESC 
            LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        return [self._row_to_change(row) for row in rows]
    
    def _row_to_change(self, row) -> CodeChange:
        return CodeChange(
            id=row[0], file_path=row[1], change_type=row[2],
            description=row[3], original_code=row[4], new_code=row[5],
            diff=row[6], timestamp=datetime.fromisoformat(row[7]),
            triggered_by=row[8], status=row[9],
            test_result=None if row[10] is None else bool(row[10]),
            git_commit_hash=row[11], rollback_available=bool(row[12])
        )

    # ═════════════════════════════════════════════════════════════════════════
    #  SELF-IMPROVEMENT ACTIVO (FASE 4)
    # ═════════════════════════════════════════════════════════════════════════

    def apply_change(self, change_id: str, auto_confirm: bool = False) -> tuple[bool, str]:
        """
        Aplica un cambio propuesto al código fuente.

        Flujo:
        1. Verifica rate limiting (max 10 cambios/hora)
        2. Verifica Git Guardian activo
        3. Verifica UncensoredMode permite modificar archivo
        4. Crea commit con Git Guardian (staging branch)
        5. Aplica cambio al archivo
        6. Ejecuta tests
        7. Si auto_confirm=False, espera confirmación manual
           Si auto_confirm=True y tests pasan, confirma automáticamente

        Args:
            change_id: ID del cambio en la base de datos
            auto_confirm: Si True, confirma automáticamente si tests pasan

        Returns:
            (success, message)
        """
        from core.rate_limiter import get_rate_limiter
        from core.git_guardian import get_git_guardian
        from core.uncensored_mode import get_uncensored_mode

        # 1. Rate limiting
        limiter = get_rate_limiter()
        if not limiter.allow("auto_change", priority=1):
            return False, f"Rate limit excedido: {limiter.get_remaining_quota('auto_change')} cambios restantes"

        # 2. Obtener cambio
        conn = get_conn(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM code_changes WHERE id=?", (change_id,))
        row = cursor.fetchone()
        pass  # S109: get_conn no necesita close()
        if not row:
            return False, f"Cambio {change_id} no encontrado"

        change = self._row_to_change(row)

        if change.status != "proposed":
            return False, f"Cambio no está en estado 'proposed', está: {change.status}"

        # 3. Verificar permisos UncensoredMode
        mode = get_uncensored_mode()
        if not mode.can_modify(change.file_path):
            return False, f"UncensoredMode denegó modificación de {change.file_path}"

        # 4. Git Guardian - crear staging branch
        guardian = get_git_guardian()
        if not guardian.is_active():
            return False, "Git Guardian no está activo - requerido para auto-cambios"

        # Crear branch de staging
        branch_name = f"auto-{change_id[:8]}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        if not guardian.create_staging_branch(branch_name):
            return False, "No se pudo crear staging branch"

        # 5. Aplicar cambio
        try:
            file_path = Path(change.file_path)
            # Guardar backup
            backup = file_path.read_text(encoding="utf-8")

            # Escribir nuevo código
            file_path.write_text(change.new_code, encoding="utf-8")

            # Actualizar estado
            conn = get_conn(self.db.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE code_changes SET status=?, applied_at=? WHERE id=?",
                ("applied", datetime.now().isoformat(), change_id)
            )
            conn.commit()
            pass  # S109: get_conn no necesita close()
            # 6. Ejecutar tests
            tests_passed = self._run_tests()

            if tests_passed:
                if auto_confirm:
                    # Auto-confirmar
                    return self.confirm_change(change_id, branch_name)
                else:
                    return True, f"Cambio aplicado y tests pasaron. Esperando confirmación manual. Branch: {branch_name}"
            else:
                # Tests fallaron - rollback
                return self.rollback_change(change_id, branch_name, backup)

        except Exception as e:
            log.error(f"Error aplicando cambio {change_id}: {e}")
            # Intentar rollback
            return self.rollback_change(change_id, branch_name, None)

    def run_tests(self, change_id: str) -> tuple[bool, str]:
        """
        Ejecuta tests específicos para un cambio.

        Returns:
            (tests_passed, output)
        """
        result = self._run_tests()

        # Actualizar DB
        conn = get_conn(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE code_changes SET test_result=? WHERE id=?",
            (result, change_id)
        )
        conn.commit()
        pass  # S109: get_conn no necesita close()
        return result, "Tests completados" if result else "Tests fallaron"

    def confirm_or_rollback(self, change_id: str, tests_passed: bool) -> tuple[bool, str]:
        """
        Confirma o hace rollback de un cambio basado en tests.

        Args:
            change_id: ID del cambio
            tests_passed: Si los tests pasaron

        Returns:
            (success, message)
        """
        if tests_passed:
            return self.confirm_change(change_id)
        else:
            return self.rollback_change(change_id, None, None)

    def confirm_change(self, change_id: str, branch_name: Optional[str] = None) -> tuple[bool, str]:
        """Confirma un cambio y hace merge de la staging branch."""
        from core.git_guardian import get_git_guardian

        guardian = get_git_guardian()

        # Merge staging branch
        if branch_name:
            if not guardian.merge_staging(branch_name, run_tests=False):
                return False, "No se pudo mergear staging branch"

        # Actualizar DB
        conn = get_conn(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE code_changes SET status=?, confirmed_at=? WHERE id=?",
            ("confirmed", datetime.now().isoformat(), change_id)
        )
        conn.commit()
        pass  # S109: get_conn no necesita close()
        # Registrar éxito en rate limiter
        from core.rate_limiter import get_rate_limiter
        get_rate_limiter().record("auto_change", success=True)

        return True, f"Cambio {change_id} confirmado exitosamente"

    def rollback_change(self, change_id: str, branch_name: Optional[str], backup: Optional[str]) -> tuple[bool, str]:
        """Hace rollback de un cambio."""
        from core.git_guardian import get_git_guardian

        guardian = get_git_guardian()

        # Rollback con Git Guardian
        if branch_name:
            guardian.rollback_to("HEAD~1", force=False)

        # Restaurar backup si existe
        if backup:
            try:
                conn = get_conn(self.db.db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT file_path, original_code FROM code_changes WHERE id=?", (change_id,))
                row = cursor.fetchone()
                pass  # S109: get_conn no necesita close()
                if row:
                    Path(row[0]).write_text(row[1], encoding="utf-8")
            except Exception as e:
                log.error(f"Error restaurando backup: {e}")

        # Actualizar DB
        conn = get_conn(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE code_changes SET status=?, rolled_back_at=? WHERE id=?",
            ("rolled_back", datetime.now().isoformat(), change_id)
        )
        conn.commit()
        pass  # S109: get_conn no necesita close()
        # Registrar fallo en rate limiter
        from core.rate_limiter import get_rate_limiter
        get_rate_limiter().record("auto_change", success=False)

        return False, f"Cambio {change_id} rollback ejecutado"

    def run_autonomous_loop(self, max_iterations: int = 5) -> List[dict]:
        """
        Ejecuta el loop autónomo de auto-mejora completo.

        Flujo:
        1. Detect: Escanea código buscando issues
        2. Propose: Propone cambios para los issues encontrados
        3. Apply: Aplica los cambios propuestos
        4. Test: Ejecuta tests
        5. Confirm/Rollback: Confirma si tests pasan, rollback si fallan

        Returns:
            Lista de resultados de cada iteración
        """
        results = []

        for iteration in range(max_iterations):
            log.info(f"=== Autonomous Loop Iteration {iteration + 1}/{max_iterations} ===")

            # 1. Detect
            issues = self.scan_for_improvements()
            if not issues:
                results.append({"iteration": iteration, "action": "none", "reason": "No issues found"})
                break

            # Tomar el issue más severo
            top_issue = issues[0]

            # 2. Propose
            change_id = self.suggest_improvement(top_issue)
            if not change_id:
                results.append({"iteration": iteration, "action": "none", "reason": "Could not propose change"})
                continue

            # 3. Apply (con auto_confirm=True para loop autónomo)
            success, message = self.apply_change(change_id, auto_confirm=True)

            results.append({
                "iteration": iteration,
                "action": "apply_change",
                "change_id": change_id,
                "success": success,
                "message": message
            })

            if not success:
                log.warning(f"Autonomous loop detenido: {message}")
                break

            # Pequeña pausa entre iteraciones
            import time
            time.sleep(1)

        return results

    # ═════════════════════════════════════════════════════════════════════════
    #  TRIGGERS PARA AUTO-MEJORA
    # ═════════════════════════════════════════════════════════════════════════

    def on_tool_error(self, tool_name: str, error: str) -> Optional[str]:
        """
        Trigger: Error en tool call.
        Detecta errores recurrentes y propone fixes.
        """
        # Verificar si es un error recurrente
        error_key = f"{tool_name}:{error[:50]}"
        self._error_counts[error_key] = self._error_counts.get(error_key, 0) + 1

        if self._error_counts[error_key] >= 3:
            # Es recurrente, proponer fix
            log.warning(f"Error recurrente detectado: {error_key}")

            # Buscar el archivo del tool
            tool_file = EIDOS_ROOT / "core" / f"{tool_name}.py"
            if tool_file.exists():
                issue = CodeIssue(
                    file_path=tool_file,
                    line_number=0,
                    issue_type="runtime_error",
                    severity=9,
                    description=f"Error recurrente: {error[:100]}",
                    suggested_fix="Revisar manejo de errores",
                    confidence=0.7
                )
                return self.suggest_improvement(issue)

        return None

    def on_timeout(self, operation: str, duration: float) -> Optional[str]:
        """
        Trigger: Timeout recurrente.
        Propone optimizaciones si hay timeouts frecuentes.
        """
        timeout_key = operation
        self._timeout_counts[timeout_key] = self._timeout_counts.get(timeout_key, 0) + 1

        if self._timeout_counts[timeout_key] >= 3:
            log.warning(f"Timeout recurrente detectado: {operation}")

            # Proponer optimización
            # (implementación específica según el operation)

        return None

    def on_skill_not_found(self, skill_name: str) -> Optional[str]:
        """
        Trigger: Skill no encontrado.
        Propone crear el skill o buscar alternativas.
        """
        log.warning(f"Skill no encontrado: {skill_name}")
        # Aquí se podría proponer crear el skill automáticamente
        return None


# Singleton
_self_improvement: Optional[SelfImprovementSystem] = None

def get_self_improvement() -> SelfImprovementSystem:
    global _self_improvement
    if _self_improvement is None:
        _self_improvement = SelfImprovementSystem()
    return _self_improvement


# ══════════════════════════════════════════════════════════════════════════════
#  TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  EIDOS Self-Improvement System - Test")
    print("=" * 70)
    
    si = get_self_improvement()
    
    # Test 1: Escaneo
    print("\n[Test 1] Escaneando código...")
    issues = si.scan_for_improvements(EIDOS_ROOT / "core")
    print(f"  Encontrados {len(issues)} issues")
    
    if issues:
        top = issues[0]
        print(f"  Top issue: {top.description} (severidad {top.severity})")
    
    # Test 2: Historial
    print("\n[Test 2] Historial de cambios...")
    history = si.get_change_history(5)
    print(f"  {len(history)} cambios en historial")
    
    print("\n✅ Self-Improvement System test complete")
    print("   EIDOS can now improve itself.")
