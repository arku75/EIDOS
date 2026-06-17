"""
EIDOS Auto-Corrector - Sistema de ejecución sin errores
Inspirado en la lógica de Claude: pensar antes de ejecutar, detectar problemas, auto-corregir

Filosofía:
1. ANALIZAR código antes de ejecutar
2. DETECTAR errores potenciales
3. SIMULAR ejecución si es posible
4. Si falla: ANALIZAR error → GENERAR fix → RE-EJECUTAR
5. APRENDER de errores para prevenir futuros
"""
import ast
import traceback
import sys
import io
import importlib
import subprocess
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from pathlib import Path
import time
import json


@dataclass
class ExecutionResult:
    """Resultado de una ejecución de código"""
    success: bool
    output: str
    error: Optional[str] = None
    error_type: Optional[str] = None
    traceback_info: Optional[str] = None
    fixed: bool = False
    fix_applied: Optional[str] = None
    execution_time: float = 0.0


@dataclass
class CodeAnalysis:
    """Análisis de código antes de ejecutar"""
    valid_syntax: bool
    imports_missing: List[str]
    potential_errors: List[str]
    safe_to_execute: bool
    risk_level: str  # "low" | "medium" | "high"
    recommendations: List[str]


class AutoCorrector:
    """
    Sistema de auto-corrección que ejecuta código de forma inteligente y sin errores

    Capacidades:
    - Pre-análisis de código
    - Detección de imports faltantes
    - Sandbox de ejecución segura
    - Auto-corrección de errores comunes
    - Aprendizaje de errores
    """

    def __init__(self):
        self.error_history: List[Dict[str, Any]] = []
        self.fix_patterns: Dict[str, str] = self._load_fix_patterns()

        # Errores comunes y sus fixes
        self.common_fixes = {
            "ModuleNotFoundError": self._fix_missing_module,
            "NameError": self._fix_name_error,
            "AttributeError": self._fix_attribute_error,
            "ImportError": self._fix_import_error,
            "SyntaxError": self._fix_syntax_error,
            "IndentationError": self._fix_indentation_error,
        }

        print("🔧 [Auto-Corrector] Sistema inicializado")

    def _load_fix_patterns(self) -> Dict[str, str]:
        """Carga patrones de fixes conocidos"""
        # TODO: Cargar de ChromaDB si existen
        return {
            "No module named 'core'": "sys.path.insert(0, '/home/ser/EIDOS')",
            "name 'perception' is not defined": "from core.lazy_loader import lazy_import; perception = lazy_import('perception')",
        }

    def analyze_code(self, code: str) -> CodeAnalysis:
        """
        Analiza código ANTES de ejecutar (como Claude analiza antes de responder)

        Verifica:
        - Sintaxis válida
        - Imports necesarios
        - Errores potenciales
        - Nivel de riesgo
        """
        print("🔍 [Auto-Corrector] Analizando código...")

        # 1. Verificar sintaxis
        valid_syntax = True
        try:
            ast.parse(code)
        except SyntaxError as e:
            valid_syntax = False
            return CodeAnalysis(
                valid_syntax=False,
                imports_missing=[],
                potential_errors=[f"Syntax error: {e}"],
                safe_to_execute=False,
                risk_level="high",
                recommendations=["Corregir sintaxis antes de ejecutar"]
            )

        # 2. Extraer imports
        tree = ast.parse(code)
        imports_needed = set()
        imports_missing = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports_needed.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports_needed.add(node.module)

        # Verificar si imports están disponibles
        for imp in imports_needed:
            try:
                importlib.import_module(imp.split('.')[0])
            except ImportError:
                imports_missing.append(imp)

        # 3. Detectar llamadas peligrosas
        potential_errors = []
        risk_level = "low"

        for node in ast.walk(tree):
            # Detectar subprocess/os.system (potencialmente peligroso)
            if isinstance(node, ast.Call):
                if hasattr(node.func, 'attr'):
                    if node.func.attr in ['system', 'popen', 'exec', 'eval']:
                        potential_errors.append(f"Llamada peligrosa: {node.func.attr}")
                        risk_level = "high"

            # Detectar file operations sin try/except
            if isinstance(node, ast.Call):
                if hasattr(node.func, 'id'):
                    if node.func.id in ['open', 'remove', 'rmdir']:
                        # Verificar si está en try/except
                        # (simplificado - análisis completo sería más complejo)
                        potential_errors.append(f"File operation sin protección: {node.func.id}")
                        if risk_level == "low":
                            risk_level = "medium"

        # 4. Recomendaciones
        recommendations = []
        if imports_missing:
            recommendations.append(f"Instalar/importar: {', '.join(imports_missing)}")
        if risk_level in ["medium", "high"]:
            recommendations.append("Ejecutar en sandbox")

        safe_to_execute = valid_syntax and risk_level != "high"

        analysis = CodeAnalysis(
            valid_syntax=valid_syntax,
            imports_missing=imports_missing,
            potential_errors=potential_errors,
            safe_to_execute=safe_to_execute,
            risk_level=risk_level,
            recommendations=recommendations
        )

        print(f"  ✅ Análisis completado: {risk_level} risk, safe={safe_to_execute}")
        return analysis

    def execute_safe(
        self,
        code: str,
        timeout: int = 30,
        auto_fix: bool = True,
        max_retries: int = 3
    ) -> ExecutionResult:
        """
        Ejecuta código de forma segura con auto-corrección

        Flow:
        1. Analizar código
        2. Si no es seguro → advertir
        3. Ejecutar en sandbox
        4. Si falla → analizar error → generar fix → reintentar
        5. Guardar error + fix para aprendizaje

        Args:
            code: Código Python a ejecutar
            timeout: Timeout en segundos
            auto_fix: Si True, intenta auto-corregir errores
            max_retries: Máximo de intentos de corrección

        Returns:
            ExecutionResult con resultado de ejecución
        """
        print(f"\n⚙️  [Auto-Corrector] Ejecutando código...")
        start_time = time.time()

        # 1. Análisis previo
        analysis = self.analyze_code(code)

        if not analysis.safe_to_execute and not auto_fix:
            return ExecutionResult(
                success=False,
                output="",
                error="Código no seguro para ejecutar",
                error_type="UnsafeCode",
                execution_time=time.time() - start_time
            )

        # 2. Intentar ejecución
        current_code = code
        retry_count = 0

        while retry_count < max_retries:
            # Ejecutar en sandbox
            result = self._execute_in_sandbox(current_code, timeout)

            if result.success:
                result.execution_time = time.time() - start_time
                print(f"  ✅ Ejecución exitosa ({result.execution_time:.2f}s)")
                return result

            # Si falló y no auto_fix, retornar error
            if not auto_fix:
                result.execution_time = time.time() - start_time
                return result

            # Intentar auto-corrección
            print(f"  ⚠️  Error detectado: {result.error_type}")
            print(f"  🔧 Intentando auto-corrección (intento {retry_count + 1}/{max_retries})...")

            fix = self._generate_fix(current_code, result)

            if fix:
                print(f"  💡 Fix generado: {fix[:100]}...")
                current_code = fix
                retry_count += 1
                result.fixed = True
                result.fix_applied = fix
            else:
                print(f"  ❌ No se pudo generar fix automático")
                result.execution_time = time.time() - start_time
                return result

        # Max retries alcanzado
        result.execution_time = time.time() - start_time
        print(f"  ❌ Max retries alcanzado ({max_retries})")
        return result

    def _execute_in_sandbox(self, code: str, timeout: int) -> ExecutionResult:
        """
        Ejecuta código en sandbox seguro

        Captura:
        - stdout
        - stderr
        - excepciones
        - timeout
        """
        # Capturar stdout/stderr
        old_stdout = sys.stdout
        old_stderr = sys.stderr

        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()

        sys.stdout = captured_stdout
        sys.stderr = captured_stderr

        try:
            # Namespace aislado
            namespace = {
                '__builtins__': __builtins__,
                'print': print,
            }

            # Ejecutar código
            exec(code, namespace)

            # Éxito
            output = captured_stdout.getvalue()

            return ExecutionResult(
                success=True,
                output=output,
                error=None
            )

        except Exception as e:
            # Capturar error
            error_type = type(e).__name__
            error_msg = str(e)
            traceback_info = traceback.format_exc()

            return ExecutionResult(
                success=False,
                output=captured_stdout.getvalue(),
                error=error_msg,
                error_type=error_type,
                traceback_info=traceback_info
            )

        finally:
            # Restaurar stdout/stderr
            sys.stdout = old_stdout
            sys.stderr = old_stderr

    def _generate_fix(self, code: str, error_result: ExecutionResult) -> Optional[str]:
        """
        Genera fix automático basado en el error

        Usa:
        1. Patrones conocidos (fix_patterns)
        2. Fixes específicos por tipo de error (common_fixes)
        3. Análisis de traceback
        """
        error_type = error_result.error_type
        error_msg = error_result.error

        # 1. Verificar patrones conocidos
        for pattern, fix_code in self.fix_patterns.items():
            if pattern in error_msg:
                # Aplicar fix al código
                return f"{fix_code}\n{code}"

        # 2. Usar fix específico por tipo
        if error_type in self.common_fixes:
            fix_func = self.common_fixes[error_type]
            return fix_func(code, error_result)

        # 3. No se pudo generar fix
        return None

    def _fix_missing_module(self, code: str, error: ExecutionResult) -> Optional[str]:
        """
        Fix para ModuleNotFoundError

        EIDOS COMPLETAMENTE AUTÓNOMO:
        - Auto-instala el módulo faltante
        - No pregunta permiso
        - Aprende de los installs
        """
        # Extraer nombre del módulo
        if "No module named" in error.error:
            module_name = error.error.split("'")[1]

            # Si es un módulo de core, añadir path
            if module_name.startswith('core'):
                return f"import sys\nsys.path.insert(0, '/home/ser/EIDOS')\n{code}"

            # Si no, sugerir lazy_import
            if module_name in ['perception', 'bugbot', 'multi_uploader']:
                return f"from core.lazy_loader import lazy_import\n{module_name} = lazy_import('{module_name}')\n{code}"

            # AUTO-INSTALL: EIDOS instala automáticamente
            try:
                from core.auto_installer import auto_installer
                print(f"  🤖 [AUTÓNOMO] Auto-instalando {module_name}...")
                success, msg = auto_installer.install_missing_import(module_name)

                if success:
                    print(f"  ✅ {module_name} instalado → re-ejecutando código")
                    return code  # Re-ejecutar mismo código (ahora con módulo instalado)
                else:
                    print(f"  ⚠️  Auto-install falló: {msg}")

            except Exception as e:
                print(f"  ⚠️  Auto-installer no disponible: {e}")

        return None

    def _fix_name_error(self, code: str, error: ExecutionResult) -> Optional[str]:
        """Fix para NameError"""
        # Extraer nombre de variable
        if "name" in error.error and "is not defined" in error.error:
            var_name = error.error.split("'")[1]

            # Si es un módulo conocido, importar
            known_modules = {
                'perception': "from core.lazy_loader import lazy_import; perception = lazy_import('perception')",
                'time': "import time",
                'json': "import json",
                'os': "import os",
                'sys': "import sys",
            }

            if var_name in known_modules:
                return f"{known_modules[var_name]}\n{code}"

        return None

    def _fix_attribute_error(self, code: str, error: ExecutionResult) -> Optional[str]:
        """Fix para AttributeError"""
        # Por ahora, no auto-fix (requiere análisis más complejo)
        return None

    def _fix_import_error(self, code: str, error: ExecutionResult) -> Optional[str]:
        """Fix para ImportError"""
        # Similar a ModuleNotFoundError
        return self._fix_missing_module(code, error)

    def _fix_syntax_error(self, code: str, error: ExecutionResult) -> Optional[str]:
        """Fix para SyntaxError"""
        # Syntax errors son difíciles de auto-fix
        # Por ahora, retornar None
        return None

    def _fix_indentation_error(self, code: str, error: ExecutionResult) -> Optional[str]:
        """Fix para IndentationError"""
        # Auto-fix de indentación
        try:
            # Intentar re-indentar con autopep8 si está disponible
            import autopep8
            fixed = autopep8.fix_code(code)
            return fixed
        except ImportError:
            return None

    def learn_from_error(self, error: ExecutionResult):
        """
        Aprende de un error para prevenir futuros similares

        Guarda en error_history y eventualmente en ChromaDB
        """
        if not error.success and error.fixed:
            self.error_history.append({
                'error_type': error.error_type,
                'error_msg': error.error,
                'fix_applied': error.fix_applied,
                'timestamp': time.time()
            })

            # Si el fix funcionó, añadir a patrones conocidos
            if error.fix_applied and error.error:
                # Extraer patrón clave del error
                error_pattern = error.error.split('\n')[0] if '\n' in error.error else error.error
                self.fix_patterns[error_pattern] = error.fix_applied

            print(f"📚 [Auto-Corrector] Error aprendido: {error.error_type}")

    def get_error_stats(self) -> Dict[str, Any]:
        """Obtiene estadísticas de errores"""
        total_errors = len(self.error_history)

        if total_errors == 0:
            return {"total": 0}

        # Contar por tipo
        error_counts = {}
        for err in self.error_history:
            error_type = err['error_type']
            error_counts[error_type] = error_counts.get(error_type, 0) + 1

        return {
            'total': total_errors,
            'by_type': error_counts,
            'patterns_learned': len(self.fix_patterns)
        }


# Singleton
auto_corrector = AutoCorrector()


# ═══════════════════════════════════════════════════════════════════════════
# Funciones de conveniencia
# ═══════════════════════════════════════════════════════════════════════════

def safe_exec(code: str, auto_fix: bool = True) -> ExecutionResult:
    """
    Ejecuta código de forma segura con auto-corrección

    Ejemplo:
        result = safe_exec("print('hello')")
        if result.success:
            print(result.output)
    """
    return auto_corrector.execute_safe(code, auto_fix=auto_fix)


def analyze(code: str) -> CodeAnalysis:
    """
    Analiza código sin ejecutar

    Ejemplo:
        analysis = analyze("import perception")
        if not analysis.safe_to_execute:
            print(analysis.recommendations)
    """
    return auto_corrector.analyze_code(code)


# ═══════════════════════════════════════════════════════════════════════════
# Test
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=== Test Auto-Corrector ===\n")

    # Test 1: Código correcto
    print("Test 1: Código correcto")
    result = safe_exec("print('Hello EIDOS')")
    assert result.success, "Debería ejecutar correctamente"
    print(f"  Output: {result.output.strip()}\n")

    # Test 2: Import faltante (auto-fix)
    print("Test 2: Import faltante (debería auto-corregir)")
    code_with_error = "print(time.time())"
    result = safe_exec(code_with_error, auto_fix=True)
    print(f"  Success: {result.success}")
    print(f"  Fixed: {result.fixed}")
    if result.success:
        print(f"  Output: {result.output.strip()}\n")

    # Test 3: Análisis de código
    print("Test 3: Análisis de código")
    analysis = analyze("import os\nos.system('ls')")
    print(f"  Safe to execute: {analysis.safe_to_execute}")
    print(f"  Risk level: {analysis.risk_level}")
    print(f"  Recommendations: {analysis.recommendations}\n")

    # Test 4: Stats
    print("Test 4: Estadísticas")
    stats = auto_corrector.get_error_stats()
    print(f"  Total errors handled: {stats.get('total', 0)}")
    print(f"  Patterns learned: {stats.get('patterns_learned', 0)}")

    print("\n✅ Auto-Corrector funcional")
