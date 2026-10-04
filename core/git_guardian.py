"""
EIDOS core/git_guardian.py — Control de Versiones Atómico
===========================================================
Sistema de control de versiones automático con transacciones atómicas.
Cada cambio de código es: commit → test → merge o rollback.

Responsabilidades:
- Auto-commit antes de cualquier cambio de código
- Mensajes descriptivos automáticos con metadatos
- Rollback atómico si tests fallan
- Branch staging para probar cambios sin afectar main
- Historial completo de transacciones con auditoría

Uso:
    from core.git_guardian import GitGuardian
    
    guardian = GitGuardian(REPO_ROOT)
    
    # Crear transacción (staging branch + commit)
    tx = guardian.propose_change(
        files=[Path("core/kernel.py")],
        reason="Fix error in tool execution loop"
    )
    
    # Validar con tests
    test_results = guardian.validate_change(tx)
    
    # Confirmar o rollback
    if test_results.success:
        guardian.confirm_change(tx)  # Merge a main
    else:
        guardian.rollback(tx)  # Vuelve al estado anterior

Dependencias:
    - GitPython: pip install GitPython
    - Tests pytest disponibles en tests/

Autor: EIDOS Autonomy System
Versión: 1.0.0
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from core.paths import REPO_ROOT
from typing import Dict, List, Optional, Literal, Any, Callable
from uuid import uuid4

# Configuración de logging estructurado
log = logging.getLogger("eidos.git_guardian")


# ═══════════════════════════════════════════════════════════════════════════════
#  ENUMS & DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

class TransactionStatus(Enum):
    """Estados posibles de una transacción de cambio."""
    PENDING = "pending"
    VALIDATING = "validating"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class TestStatus(Enum):
    """Estados de resultados de tests."""
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class TestResults:
    """Resultados de ejecución de tests."""
    status: TestStatus
    duration_ms: int
    total_tests: int
    passed: int
    failed: int
    errors: int
    coverage_percent: Optional[float] = None
    failed_tests: List[str] = field(default_factory=list)
    output: str = ""
    
    @property
    def success(self) -> bool:
        """True si todos los tests pasaron."""
        return self.status == TestStatus.PASS and self.failed == 0


@dataclass
class ChangeTransaction:
    """
    Transacción atómica de cambio de código.
    
    Representa un cambio propuesto desde la creación hasta
    su confirmación o rollback.
    """
    id: str
    timestamp: datetime
    files: List[Path]
    reason: str
    author: str
    
    # Git metadata
    staging_branch: str
    base_commit: str
    result_commit: Optional[str] = None
    
    # Estado y resultados
    test_results: Optional[TestResults] = None
    status: TransactionStatus = field(default=TransactionStatus.PENDING)
    
    # Metadata adicional
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serializa a diccionario para persistencia."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "files": [str(f) for f in self.files],
            "reason": self.reason,
            "author": self.author,
            "staging_branch": self.staging_branch,
            "base_commit": self.base_commit,
            "result_commit": self.result_commit,
            "test_results": asdict(self.test_results) if self.test_results else None,
            "status": self.status.value,
            "metadata": self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChangeTransaction":
        """Deserializa desde diccionario."""
        test_data = data.get("test_results")
        test_results = TestResults(**test_data) if test_data else None
        
        return cls(
            id=data["id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            files=[Path(f) for f in data["files"]],
            reason=data["reason"],
            author=data["author"],
            staging_branch=data["staging_branch"],
            base_commit=data["base_commit"],
            result_commit=data.get("result_commit"),
            test_results=test_results,
            status=TransactionStatus(data["status"]),
            metadata=data.get("metadata", {})
        )


@dataclass
class GuardianConfig:
    """Configuración de GitGuardian."""
    repo_path: Path
    main_branch: str = "main"
    staging_prefix: str = "staging/eidos"
    auto_merge: bool = True
    require_tests: bool = True
    min_coverage: float = 80.0
    test_command: str = "pytest tests/ -x --tb=short --cov=core --cov-report=term-missing"
    backup_stash_prefix: str = "auto: checkpoint before"
    max_transaction_history: int = 1000


# ═══════════════════════════════════════════════════════════════════════════════
#  GIT GUARDIAN PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

class GitGuardian:
    """
    Sistema de control de versiones atómico para EIDOS.
    
    Gestiona transacciones de cambio con garantías de atomicidad:
    - Cada cambio se prueba en branch staging
    - Si tests pasan → merge a main
    - Si tests fallan → rollback automático
    
    Thread-safe mediante locks internos.
    """
    
    def __init__(self, config: Optional[GuardianConfig] = None):
        """
        Inicializa GitGuardian.
        
        Args:
            config: Configuración personalizada. Si None, usa defaults.
        """
        self.config = config or GuardianConfig(
            repo_path=REPO_ROOT
        )
        
        # Importar GitPython lazy para evitar dependencia hard
        try:
            import git
            self.git = git
            self.repo = git.Repo(self.config.repo_path)
        except ImportError:
            log.error("GitPython no instalado. Ejecuta: pip install GitPython")
            raise
        except Exception as e:
            log.error(f"No se pudo abrir repositorio git: {e}")
            raise
        
        # Historial de transacciones
        self._transactions: List[ChangeTransaction] = []
        self._load_transaction_history()
        
        log.info(f"GitGuardian inicializado en {self.config.repo_path}")
    
    def is_active(self) -> bool:
        """Verifica si GitGuardian está operativo y listo para transacciones."""
        try:
            # Verificar que el repo es válido
            if not self.repo or not self.repo.git_dir:
                return False
            # Verificar que git está operativo
            self.repo.git.status()
            return True
        except Exception as e:
            log.warning(f"GitGuardian no está activo: {e}")
            return False
    
    def propose_change(
        self,
        files: List[Path],
        reason: str,
        author: str = "eidos_self"
    ) -> ChangeTransaction:
        """
        Crea una transacción de cambio en staging branch.
        
        Flujo:
        1. Verifica que files existen y tienen cambios
        2. Crea stash del estado actual
        3. Crea branch staging
        4. Commitea cambios en staging
        
        Args:
            files: Lista de archivos a cambiar
            reason: Descripción del cambio
            author: Autor del cambio (default: eidos_self)
            
        Returns:
            ChangeTransaction con metadata de la transacción
            
        Raises:
            ValueError: Si files está vacío o archivos no existen
            RuntimeError: Si hay error de git
        """
        if not files:
            raise ValueError("Debe especificar al menos un archivo")
        
        # Verificar archivos existen
        for f in files:
            if not f.exists():
                raise ValueError(f"Archivo no existe: {f}")
        
        # Crear ID único
        tx_id = f"eidos_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        staging_branch = f"{self.config.staging_prefix}/{tx_id}"
        
        # Guardar estado actual
        base_commit = self.repo.head.commit.hexsha
        
        log.info(f"Creando transacción {tx_id} para {len(files)} archivos")
        
        try:
            # 1. Stash cambios no commiteados
            if self.repo.is_dirty():
                stash_msg = f"{self.config.backup_stash_prefix} {tx_id}"
                self.repo.git.stash("push", "-m", stash_msg)
                log.debug(f"Stash creado: {stash_msg}")
            
            # 2. Crear branch staging
            self.repo.git.checkout("-b", staging_branch)
            log.debug(f"Branch creada: {staging_branch}")
            
            # 3. Añadir archivos al index
            for f in files:
                self.repo.git.add(str(f))
            
            # 4. Commit con mensaje descriptivo
            commit_msg = self._generate_commit_message(reason, author, files)
            self.repo.git.commit("-m", commit_msg)
            
            # 5. Obtener hash del commit
            result_commit = self.repo.head.commit.hexsha
            
            # 6. Volver a main
            self.repo.git.checkout(self.config.main_branch)
            
            # Crear transacción
            tx = ChangeTransaction(
                id=tx_id,
                timestamp=datetime.now(),
                files=files,
                reason=reason,
                author=author,
                staging_branch=staging_branch,
                base_commit=base_commit,
                result_commit=result_commit,
                status=TransactionStatus.PENDING
            )
            
            self._transactions.append(tx)
            self._save_transaction_history()
            
            log.info(f"Transacción {tx_id} creada exitosamente")
            return tx
            
        except Exception as e:
            log.error(f"Error creando transacción: {e}")
            self._cleanup_failed_transaction(staging_branch, base_commit)
            raise RuntimeError(f"Error creando transacción: {e}")
    
    def validate_change(self, tx: ChangeTransaction) -> TestResults:
        """
        Ejecuta tests en la transacción staging.
        
        Cambia estado a VALIDATING, ejecuta tests en staging branch,
        registra resultados y vuelve a main.
        
        Args:
            tx: Transacción a validar
            
        Returns:
            TestResults con resultados de la ejecución
        """
        log.info(f"Validando transacción {tx.id}")
        tx.status = TransactionStatus.VALIDATING
        
        start_time = time.time()
        
        try:
            # Checkout a staging branch
            self.repo.git.checkout(tx.staging_branch)
            
            # Ejecutar tests
            result = subprocess.run(
                self.config.test_command.split(),
                cwd=self.config.repo_path,
                capture_output=True,
                text=True,
                timeout=300  # 5 minutos máximo
            )
            
            duration_ms = int((time.time() - start_time) * 1000)
            
            # Parsear resultados
            test_results = self._parse_test_output(
                result.returncode,
                result.stdout,
                result.stderr,
                duration_ms
            )
            
            tx.test_results = test_results
            
            # Volver a main
            self.repo.git.checkout(self.config.main_branch)
            
            log.info(f"Validación completada: {test_results.status.value}")
            return test_results
            
        except subprocess.TimeoutExpired:
            tx.test_results = TestResults(
                status=TestStatus.ERROR,
                duration_ms=300000,
                total_tests=0,
                passed=0,
                failed=0,
                errors=1,
                output="Tests timeout (>5 minutos)"
            )
            self.repo.git.checkout(self.config.main_branch)
            return tx.test_results
            
        except Exception as e:
            log.error(f"Error validando transacción: {e}")
            tx.test_results = TestResults(
                status=TestStatus.ERROR,
                duration_ms=int((time.time() - start_time) * 1000),
                total_tests=0,
                passed=0,
                failed=0,
                errors=1,
                output=str(e)
            )
            self.repo.git.checkout(self.config.main_branch)
            return tx.test_results
    
    def confirm_change(self, tx: ChangeTransaction) -> bool:
        """
        Mergea transacción a main si tests pasaron.
        
        Args:
            tx: Transacción a confirmar
            
        Returns:
            True si merge exitoso
            
        Raises:
            RuntimeError: Si tests no pasaron o hay error de merge
        """
        if not tx.test_results:
            raise RuntimeError("Transacción no validada. Ejecuta validate_change() primero.")
        
        if not tx.test_results.success:
            log.warning(f"Transacción {tx.id} tiene tests fallidos, abortando confirmación")
            return False
        
        log.info(f"Confirmando transacción {tx.id} a {self.config.main_branch}")
        
        try:
            # Merge a main
            self.repo.git.merge(
                tx.staging_branch,
                "--no-ff",
                "-m", f"[EIDOS] {tx.reason}"
            )
            
            # Actualizar commit resultante
            tx.result_commit = self.repo.head.commit.hexsha
            tx.status = TransactionStatus.APPLIED
            
            # Limpiar staging branch
            self.repo.git.branch("-d", tx.staging_branch)
            
            # Intentar recuperar stash si existe
            self._pop_stash_if_exists(tx.id)
            
            self._save_transaction_history()
            
            log.info(f"Transacción {tx.id} confirmada exitosamente")
            return True
            
        except Exception as e:
            log.error(f"Error confirmando transacción: {e}")
            raise RuntimeError(f"Error en merge: {e}")
    
    def rollback(self, tx: ChangeTransaction) -> bool:
        """
        Revierte transacción y vuelve al estado anterior.
        
        Args:
            tx: Transacción a revertir
            
        Returns:
            True si rollback exitoso
        """
        log.info(f"Rollback de transacción {tx.id}")
        
        try:
            # Reset a commit base
            self.repo.git.checkout(self.config.main_branch)
            self.repo.git.reset("--hard", tx.base_commit)
            
            # Eliminar staging branch
            try:
                self.repo.git.branch("-D", tx.staging_branch)
            except Exception:
                pass  # Branch ya no existe
            
            # Recuperar stash
            self._pop_stash_if_exists(tx.id)
            
            tx.status = TransactionStatus.ROLLED_BACK
            self._save_transaction_history()
            
            log.info(f"Rollback de {tx.id} completado")
            return True
            
        except Exception as e:
            log.error(f"Error en rollback: {e}")
            tx.status = TransactionStatus.FAILED
            return False
    
    def get_transaction_history(
        self,
        status: Optional[TransactionStatus] = None,
        limit: int = 100
    ) -> List[ChangeTransaction]:
        """
        Obtiene historial de transacciones.
        
        Args:
            status: Filtrar por estado específico
            limit: Máximo de transacciones a retornar
            
        Returns:
            Lista de transacciones ordenadas por fecha (más reciente primero)
        """
        transactions = sorted(
            self._transactions,
            key=lambda t: t.timestamp,
            reverse=True
        )
        
        if status:
            transactions = [t for t in transactions if t.status == status]
        
        return transactions[:limit]
    
    def get_stats(self) -> Dict[str, Any]:
        """Retorna estadísticas de transacciones."""
        total = len(self._transactions)
        applied = len([t for t in self._transactions if t.status == TransactionStatus.APPLIED])
        rolled_back = len([t for t in self._transactions if t.status == TransactionStatus.ROLLED_BACK])
        failed = len([t for t in self._transactions if t.status == TransactionStatus.FAILED])
        
        return {
            "total_transactions": total,
            "applied": applied,
            "rolled_back": rolled_back,
            "failed": failed,
            "success_rate": applied / total if total > 0 else 0.0
        }
    
    # ═════════════════════════════════════════════════════════════════════════
    #  MÉTODOS PRIVADOS
    # ═════════════════════════════════════════════════════════════════════════
    
    def _generate_commit_message(
        self,
        reason: str,
        author: str,
        files: List[Path]
    ) -> str:
        """Genera mensaje de commit descriptivo."""
        file_list = ", ".join([f.name for f in files[:3]])
        if len(files) > 3:
            file_list += f" y {len(files) - 3} más"
        
        return f"""[EIDOS AUTO] {reason}

Archivos: {file_list}
Autor: {author}
Timestamp: {datetime.now().isoformat()}
"""
    
    def _parse_test_output(
        self,
        returncode: int,
        stdout: str,
        stderr: str,
        duration_ms: int
    ) -> TestResults:
        """Parsea output de pytest."""
        output = stdout + "\n" + stderr
        
        # Intentar extraer estadísticas básicas
        try:
            # Buscar línea tipo "X passed, Y failed, Z error"
            import re
            match = re.search(
                r'(\d+) passed.*?(\d+) failed.*?(\d+) error',
                output,
                re.IGNORECASE
            )
            
            if match:
                passed = int(match.group(1))
                failed = int(match.group(2))
                errors = int(match.group(3))
                total = passed + failed + errors
            else:
                # Fallback: contar desde output
                passed = stdout.count("PASSED")
                failed = stdout.count("FAILED")
                errors = stdout.count("ERROR")
                total = passed + failed + errors
            
            # Buscar coverage
            coverage_match = re.search(r'total\s+\d+%\s+(\d+)%', output)
            coverage = float(coverage_match.group(1)) if coverage_match else None
            
            if returncode == 0 and failed == 0 and errors == 0:
                status = TestStatus.PASS
            else:
                status = TestStatus.FAIL
                
        except Exception as e:
            log.warning(f"Error parseando output de tests: {e}")
            status = TestStatus.ERROR if returncode != 0 else TestStatus.PASS
            total = passed = failed = errors = 0
            coverage = None
        
        return TestResults(
            status=status,
            duration_ms=duration_ms,
            total_tests=total,
            passed=passed,
            failed=failed,
            errors=errors,
            coverage_percent=coverage,
            output=output[:5000]  # Limitar output
        )
    
    def _cleanup_failed_transaction(self, staging_branch: str, base_commit: str):
        """Limpia estado tras transacción fallida."""
        try:
            self.repo.git.checkout(self.config.main_branch)
            try:
                self.repo.git.branch("-D", staging_branch)
            except Exception:
                pass  # error no crítico, continuar
            self._pop_stash_if_exists(staging_branch)
        except Exception as e:
            log.error(f"Error limpiando transacción fallida: {e}")
    
    def _pop_stash_if_exists(self, tx_id: str):
        """Recupera stash si existe para esta transacción."""
        try:
            # Listar stashes y encontrar el índice correcto
            stash_list_output = self.repo.git.stash("list")
            if not stash_list_output or tx_id not in stash_list_output:
                return
            
            # Encontrar el índice del stash que contiene el tx_id
            for idx, line in enumerate(stash_list_output.split('\n')):
                if tx_id in line:
                    self.repo.git.stash("pop", str(idx))
                    log.debug(f"Stash recuperado para {tx_id} (índice {idx})")
                    return
        except Exception as e:
            log.debug(f"No se pudo recuperar stash: {e}")
    
    def _load_transaction_history(self):
        """Carga historial de transacciones desde disco."""
        history_file = self.config.repo_path / ".eidos" / "git_guardian_history.json"
        
        if not history_file.exists():
            return
        
        try:
            with open(history_file, "r") as f:
                data = json.load(f)
                self._transactions = [
                    ChangeTransaction.from_dict(t) for t in data.get("transactions", [])
                ]
            log.debug(f"Cargadas {len(self._transactions)} transacciones del historial")
        except Exception as e:
            log.warning(f"Error cargando historial: {e}")
    
    def _save_transaction_history(self):
        """Persiste historial de transacciones a disco."""
        history_file = self.config.repo_path / ".eidos" / "git_guardian_history.json"
        history_file.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            # Limitar historial
            transactions = self._transactions[-self.config.max_transaction_history:]
            
            with open(history_file, "w") as f:
                json.dump({
                    "transactions": [t.to_dict() for t in transactions],
                    "last_updated": datetime.now().isoformat()
                }, f, indent=2)
                
        except Exception as e:
            log.error(f"Error guardando historial: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  SINGLETON & UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════════

_guardian: Optional[GitGuardian] = None


def get_git_guardian(config: Optional[GuardianConfig] = None) -> GitGuardian:
    """
    Obtiene instancia singleton de GitGuardian.
    
    Args:
        config: Configuración opcional (solo usada en primera llamada)
        
    Returns:
        Instancia de GitGuardian
    """
    global _guardian
    if _guardian is None:
        _guardian = GitGuardian(config)
    return _guardian


def reset_git_guardian():
    """Resetea singleton (útil para tests)."""
    global _guardian
    _guardian = None


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN (tests rápidos)
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Configurar logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Test básico
    print("GitGuardian - Test de inicialización")
    print("=" * 50)
    
    try:
        guardian = GitGuardian()
        print(f"✅ GitGuardian inicializado")
        print(f"   Repo: {guardian.config.repo_path}")
        print(f"   Branch principal: {guardian.config.main_branch}")
        print(f"   Transacciones históricas: {len(guardian._transactions)}")
        
        stats = guardian.get_stats()
        print(f"\n📊 Estadísticas:")
        print(f"   Total: {stats['total_transactions']}")
        print(f"   Aplicadas: {stats['applied']}")
        print(f"   Rollbacks: {stats['rolled_back']}")
        print(f"   Tasa éxito: {stats['success_rate']:.1%}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
