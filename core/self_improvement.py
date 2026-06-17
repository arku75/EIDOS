#!/usr/bin/env python3
"""
EIDOS Self-Improvement Engine
==============================

EIDOS se analiza a sí mismo y propone mejoras automáticas:
1. Analiza su propio código buscando patrones mejorables
2. Detecta bugs potenciales
3. Propone refactorings
4. Evoluciona su base de conocimiento
5. (Con aprobación) Se auto-mejora

Este es el componente que permite a EIDOS EVOLUCIONAR automáticamente.
"""

import os
import re
import ast
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)

# Imports opcionales
try:
    from .knowledge_evolver import KnowledgeEvolver
    EVOLVER_AVAILABLE = True
except ImportError:
    EVOLVER_AVAILABLE = False
    logger.warning("KnowledgeEvolver no disponible")

try:
    from .detailed_logger import get_detailed_logger, ChangeType
    LOGGER_AVAILABLE = True
except ImportError:
    LOGGER_AVAILABLE = False


@dataclass
class CodeIssue:
    """Issue encontrado en código"""
    file_path: str
    line_number: int
    issue_type: str  # "complexity", "duplication", "performance", "bug", "security"
    severity: int  # 1-10
    description: str
    suggested_fix: Optional[str] = None
    code_snippet: Optional[str] = None


@dataclass
class ImprovementProposal:
    """Propuesta de mejora"""
    id: str
    timestamp: str
    proposal_type: str  # "refactor", "bugfix", "optimization", "feature"
    title: str
    description: str
    affected_files: List[str]
    estimated_impact: str  # "low", "medium", "high", "critical"
    suggested_changes: List[Dict]
    approved: bool = False
    applied: bool = False
    result: Optional[str] = None


class SelfImprovement:
    """
    Motor de auto-mejora de EIDOS

    Capacidades:
    - Análisis estático de código propio
    - Detección de code smells
    - Propuestas de refactoring
    - Evolución de knowledge base
    - Auto-corrección con aprobación
    """

    def __init__(self, eidos_root: Path = None):
        self.eidos_root = eidos_root or Path.home() / "EIDOS"
        self.core_path = self.eidos_root / "core"

        self.proposals: List[ImprovementProposal] = []
        self.analysis_results: Dict[str, Any] = {}

        # Detailed logger
        self.detailed_logger = None
        if LOGGER_AVAILABLE:
            self.detailed_logger = get_detailed_logger("self_improvement")

        logger.info("🔧 Self-Improvement Engine initialized")

    def analyze_self(self) -> Dict[str, Any]:
        """
        Análisis completo de EIDOS

        Returns:
            Diccionario con resultados del análisis
        """
        logger.info("🔍 Iniciando auto-análisis completo...")

        results = {
            'timestamp': datetime.now().isoformat(),
            'code_issues': [],
            'metrics': {},
            'suggestions': []
        }

        # 1. Analizar complejidad de código
        logger.info("   📊 Analizando complejidad...")
        complexity_issues = self._analyze_complexity()
        results['code_issues'].extend(complexity_issues)

        # 2. Detectar duplicación
        logger.info("   🔄 Detectando código duplicado...")
        duplication_issues = self._detect_duplication()
        results['code_issues'].extend(duplication_issues)

        # 3. Buscar patterns problemáticos
        logger.info("   ⚠️  Buscando anti-patterns...")
        pattern_issues = self._detect_antipatterns()
        results['code_issues'].extend(pattern_issues)

        # 4. Analizar imports y dependencias
        logger.info("   📦 Analizando dependencias...")
        dependency_issues = self._analyze_dependencies()
        results['code_issues'].extend(dependency_issues)

        # 5. Métricas generales
        logger.info("   📈 Calculando métricas...")
        results['metrics'] = self._calculate_metrics()

        # 6. Generar sugerencias
        logger.info("   💡 Generando sugerencias...")
        results['suggestions'] = self._generate_suggestions(results['code_issues'])

        self.analysis_results = results

        logger.info(f"✅ Análisis completado: {len(results['code_issues'])} issues encontrados")

        return results

    def _analyze_complexity(self) -> List[CodeIssue]:
        """Analiza complejidad ciclomática de funciones"""
        issues = []

        for py_file in self.core_path.glob("*.py"):
            try:
                with open(py_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    tree = ast.parse(content)

                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef):
                        complexity = self._calculate_cyclomatic_complexity(node)

                        if complexity > 10:  # Umbral alto
                            issues.append(CodeIssue(
                                file_path=str(py_file),
                                line_number=node.lineno,
                                issue_type="complexity",
                                severity=min(complexity // 2, 10),
                                description=f"Función '{node.name}' tiene complejidad {complexity} (>10)",
                                suggested_fix="Considerar refactorizar en funciones más pequeñas"
                            ))

            except Exception as e:
                logger.debug(f"Error analizando {py_file}: {e}")

        return issues

    def _calculate_cyclomatic_complexity(self, node: ast.FunctionDef) -> int:
        """Calcula complejidad ciclomática básica"""
        complexity = 1

        for child in ast.walk(node):
            # Incrementar por cada punto de decisión
            if isinstance(child, (ast.If, ast.While, ast.For, ast.ExceptHandler)):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1

        return complexity

    def _detect_duplication(self) -> List[CodeIssue]:
        """Detecta código duplicado"""
        issues = []
        code_blocks = defaultdict(list)

        for py_file in self.core_path.glob("*.py"):
            try:
                with open(py_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()

                # Buscar bloques de 5+ líneas similares
                for i in range(len(lines) - 5):
                    block = ''.join(lines[i:i+5]).strip()
                    if len(block) > 50:  # Solo bloques significativos
                        code_blocks[block].append((py_file, i+1))

            except Exception as e:
                logger.debug(f"Error en duplicación {py_file}: {e}")

        # Reportar duplicados
        for block, locations in code_blocks.items():
            if len(locations) > 1:
                files = ', '.join([str(loc[0].name) for loc in locations])
                issues.append(CodeIssue(
                    file_path=str(locations[0][0]),
                    line_number=locations[0][1],
                    issue_type="duplication",
                    severity=6,
                    description=f"Código duplicado en: {files}",
                    suggested_fix="Extraer a función común"
                ))

        return issues

    def _detect_antipatterns(self) -> List[CodeIssue]:
        """Detecta anti-patterns comunes"""
        issues = []

        antipatterns = [
            (r'except\s*:', "Catch genérico sin tipo", 5),
            (r'eval\(', "Uso de eval() (inseguro)", 8),
            (r'exec\(', "Uso de exec() (inseguro)", 8),
            (r'global\s+\w+', "Uso de variables globales", 4),
            (r'import\s+\*', "Import con *", 3),
        ]

        for py_file in self.core_path.glob("*.py"):
            try:
                with open(py_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    lines = content.split('\n')

                for pattern, description, severity in antipatterns:
                    for i, line in enumerate(lines, 1):
                        if re.search(pattern, line):
                            issues.append(CodeIssue(
                                file_path=str(py_file),
                                line_number=i,
                                issue_type="antipattern",
                                severity=severity,
                                description=description,
                                code_snippet=line.strip()
                            ))

            except Exception as e:
                logger.debug(f"Error en antipatterns {py_file}: {e}")

        return issues

    def _analyze_dependencies(self) -> List[CodeIssue]:
        """Analiza imports y dependencias circulares"""
        issues = []
        imports_map = {}

        for py_file in self.core_path.glob("*.py"):
            try:
                with open(py_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    tree = ast.parse(content)

                file_imports = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            file_imports.append(alias.name)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            file_imports.append(node.module)

                imports_map[py_file.name] = file_imports

                # Detectar imports no usados (básico)
                used_names = set(re.findall(r'\b\w+\b', content))
                for imp in file_imports:
                    base_name = imp.split('.')[0]
                    if base_name not in used_names and len(content) > 1000:
                        issues.append(CodeIssue(
                            file_path=str(py_file),
                            line_number=1,
                            issue_type="unused_import",
                            severity=2,
                            description=f"Import potencialmente no usado: {imp}"
                        ))

            except Exception as e:
                logger.debug(f"Error en dependencias {py_file}: {e}")

        return issues

    def _calculate_metrics(self) -> Dict[str, Any]:
        """Calcula métricas del código"""
        metrics = {
            'total_files': 0,
            'total_lines': 0,
            'total_functions': 0,
            'total_classes': 0,
            'avg_complexity': 0,
            'largest_file': {'name': '', 'lines': 0},
            'most_complex_function': {'name': '', 'complexity': 0, 'file': ''}
        }

        complexities = []

        for py_file in self.core_path.glob("*.py"):
            try:
                with open(py_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    lines = len(content.split('\n'))
                    tree = ast.parse(content)

                metrics['total_files'] += 1
                metrics['total_lines'] += lines

                if lines > metrics['largest_file']['lines']:
                    metrics['largest_file'] = {'name': py_file.name, 'lines': lines}

                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef):
                        metrics['total_functions'] += 1
                        complexity = self._calculate_cyclomatic_complexity(node)
                        complexities.append(complexity)

                        if complexity > metrics['most_complex_function']['complexity']:
                            metrics['most_complex_function'] = {
                                'name': node.name,
                                'complexity': complexity,
                                'file': py_file.name
                            }

                    elif isinstance(node, ast.ClassDef):
                        metrics['total_classes'] += 1

            except Exception as e:
                logger.debug(f"Error en métricas {py_file}: {e}")

        if complexities:
            metrics['avg_complexity'] = round(sum(complexities) / len(complexities), 2)

        return metrics

    def _generate_suggestions(self, issues: List[CodeIssue]) -> List[str]:
        """Genera sugerencias de mejora basadas en issues"""
        suggestions = []

        # Agrupar por tipo
        by_type = defaultdict(list)
        for issue in issues:
            by_type[issue.issue_type].append(issue)

        # Sugerencias por tipo
        if by_type['complexity']:
            high_complexity = [i for i in by_type['complexity'] if i.severity >= 7]
            if high_complexity:
                suggestions.append(
                    f"⚠️  {len(high_complexity)} funciones con complejidad muy alta - "
                    f"refactorizar en funciones más pequeñas"
                )

        if by_type['duplication']:
            suggestions.append(
                f"🔄 {len(by_type['duplication'])} casos de código duplicado - "
                f"extraer a funciones comunes"
            )

        if by_type['antipattern']:
            critical = [i for i in by_type['antipattern'] if i.severity >= 7]
            if critical:
                suggestions.append(
                    f"🚨 {len(critical)} anti-patterns críticos detectados - "
                    f"revisar eval/exec usage"
                )

        if by_type['unused_import']:
            suggestions.append(
                f"📦 {len(by_type['unused_import'])} imports potencialmente no usados - "
                f"limpiar para reducir dependencias"
            )

        return suggestions

    def propose_improvements(self) -> List[ImprovementProposal]:
        """
        Genera propuestas concretas de mejora

        Returns:
            Lista de propuestas de mejora
        """
        if not self.analysis_results:
            self.analyze_self()

        proposals = []
        issues = self.analysis_results.get('code_issues', [])

        # Agrupar issues críticos
        critical_issues = [i for i in issues if i.severity >= 7]

        for issue in critical_issues:
            proposal = ImprovementProposal(
                id=f"improvement_{len(proposals) + 1}",
                timestamp=datetime.now().isoformat(),
                proposal_type="bugfix" if issue.issue_type in ["bug", "security"] else "refactor",
                title=f"Fix {issue.issue_type} in {Path(issue.file_path).name}",
                description=issue.description,
                affected_files=[issue.file_path],
                estimated_impact="high" if issue.severity >= 8 else "medium",
                suggested_changes=[{
                    'file': issue.file_path,
                    'line': issue.line_number,
                    'current': issue.code_snippet or "...",
                    'suggested': issue.suggested_fix or "Manual review needed"
                }]
            )
            proposals.append(proposal)

        self.proposals.extend(proposals)

        logger.info(f"💡 Generadas {len(proposals)} propuestas de mejora")

        return proposals

    def get_improvement_report(self) -> str:
        """Genera reporte legible de mejoras"""
        if not self.analysis_results:
            self.analyze_self()

        metrics = self.analysis_results['metrics']
        issues = self.analysis_results['code_issues']
        suggestions = self.analysis_results['suggestions']

        report = []
        report.append("=" * 70)
        report.append("EIDOS SELF-IMPROVEMENT REPORT")
        report.append("=" * 70)
        report.append("")

        # Métricas
        report.append("📊 CODE METRICS:")
        report.append(f"   Files: {metrics['total_files']}")
        report.append(f"   Lines: {metrics['total_lines']:,}")
        report.append(f"   Functions: {metrics['total_functions']}")
        report.append(f"   Classes: {metrics['total_classes']}")
        report.append(f"   Avg Complexity: {metrics['avg_complexity']}")
        report.append("")

        # Largest file
        report.append(f"📄 Largest File: {metrics['largest_file']['name']} ({metrics['largest_file']['lines']} lines)")
        report.append(f"🔧 Most Complex: {metrics['most_complex_function']['name']} (complexity {metrics['most_complex_function']['complexity']})")
        report.append("")

        # Issues por tipo
        report.append("⚠️  ISSUES FOUND:")
        by_type = defaultdict(int)
        for issue in issues:
            by_type[issue.issue_type] += 1

        for issue_type, count in sorted(by_type.items(), key=lambda x: -x[1]):
            report.append(f"   {issue_type:15} {count:3}")
        report.append("")

        # Sugerencias
        report.append("💡 SUGGESTIONS:")
        for suggestion in suggestions:
            report.append(f"   {suggestion}")
        report.append("")

        report.append("=" * 70)

        return "\n".join(report)

    def evolve_knowledge(self):
        """Evoluciona la base de conocimiento"""
        if not EVOLVER_AVAILABLE:
            logger.warning("Knowledge Evolver no disponible")
            return

        logger.info("🧠 Evolucionando base de conocimiento...")

        try:
            evolver = KnowledgeEvolver()
            evolver.evolve()
            logger.info("✅ Knowledge base evolucionada")
        except Exception as e:
            logger.error(f"Error evolucionando knowledge: {e}")


# Singleton
_self_improvement: Optional[SelfImprovement] = None

def get_self_improvement() -> SelfImprovement:
    """Obtener instancia global"""
    global _self_improvement
    if _self_improvement is None:
        _self_improvement = SelfImprovement()
    return _self_improvement


if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.INFO)

    si = get_self_improvement()

    # Análisis
    results = si.analyze_self()

    # Reporte
    print(si.get_improvement_report())

    # Propuestas
    proposals = si.propose_improvements()
    print(f"\nPropuestas: {len(proposals)}")
    for p in proposals[:5]:
        print(f"  - {p.title}")
