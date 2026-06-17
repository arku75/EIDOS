#!/usr/bin/env python3
"""
EIDOS Coding Agent - CLI especializado para tareas de código
Inspirado en Kimi Code CLI de Kimi K2.5

Uso:
    eidos-code optimize <file>    # Optimiza un archivo
    eidos-code test [--coverage]  # Ejecuta tests
    eidos-code refactor <pattern> # Refactoriza usando patrón
    eidos-code review <file/dir>  # Code review completo
    eidos-code explain <file>     # Explica código
    eidos-code fix <file>         # Auto-fix de bugs detectados
    eidos-code generate <desc>    # Genera código desde descripción
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add EIDOS to path
EIDOS_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(EIDOS_ROOT))


class CodingAgent:
    """
    Agente especializado para tareas de código.
    
    Features:
    - Multi-step planning para tareas complejas
    - Análisis estático integrado
    - Refactoring automático
    - Testing y coverage
    - Code review con Ollama
    """
    
    def __init__(self):
        self.ollama_available = self._check_ollama()
        self.model = "lfm2.5-thinking:1.2b"  # Modelo por defecto para código
        self.verbose = False
        
    def _check_ollama(self) -> bool:
        """Verifica si Ollama está disponible"""
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def _call_ollama(self, prompt: str, system: str = "", model: str = None) -> str:
        """Llama a Ollama para generación de código"""
        model = model or self.model
        
        if not self.ollama_available:
            return "[Error: Ollama no disponible]"
        
        try:
            import requests
            
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            
            response = requests.post(
                'http://localhost:11434/api/chat',
                json={
                    'model': model,
                    'messages': messages,
                    'stream': False,
                    'options': {
                        'temperature': 0.2,
                        'num_ctx': 4096
                    }
                },
                timeout=120
            )
            
            if response.status_code == 200:
                return response.json()['message']['content']
            else:
                return f"[Error: {response.status_code}]"
        except Exception as e:
            return f"[Error: {e}]"
    
    def _read_file(self, filepath: str) -> str:
        """Lee un archivo de forma segura"""
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except Exception as e:
            return f"[Error leyendo archivo: {e}]"
    
    def _write_file(self, filepath: str, content: str):
        """Escribe un archivo"""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
    
    def _detect_language(self, filepath: str) -> str:
        """Detecta el lenguaje por extensión"""
        ext = Path(filepath).suffix.lower()
        
        lang_map = {
            '.py': 'python',
            '.js': 'javascript',
            '.ts': 'typescript',
            '.jsx': 'jsx',
            '.tsx': 'tsx',
            '.rs': 'rust',
            '.go': 'go',
            '.java': 'java',
            '.c': 'c',
            '.cpp': 'cpp',
            '.h': 'c',
            '.hpp': 'cpp',
            '.rb': 'ruby',
            '.php': 'php',
            '.swift': 'swift',
            '.kt': 'kotlin',
            '.scala': 'scala',
        }
        
        return lang_map.get(ext, 'text')
    
    def _get_git_diff(self) -> str:
        """Obtiene diff de git si está disponible"""
        try:
            result = subprocess.run(
                ['git', 'diff', '--cached'],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.stdout
        except Exception:
            return ""
    
    # ─── COMANDOS PRINCIPALES ───────────────────────────────────────────────
    
    def cmd_optimize(self, filepath: str, inline: bool = False) -> Dict:
        """
        Optimiza un archivo de código.
        
        Pipeline:
        1. Analizar código actual
        2. Identificar optimizaciones
        3. Generar código optimizado
        4. Mostrar diff
        """
        print(f"🔧 Optimizando: {filepath}")
        
        code = self._read_file(filepath)
        if code.startswith("[Error"):
            return {"success": False, "error": code}
        
        lang = self._detect_language(filepath)
        
        system_prompt = f"""Eres un experto en optimización de código {lang}.
Tu tarea es optimizar el código proporcionado para:
1. Mejor performance (tiempo de ejecución)
2. Menor uso de memoria
3. Mejor legibilidad
4. Mejores prácticas del lenguaje

Responde SOLO con el código optimizado, sin explicaciones."""

        prompt = f"Optimiza este código {lang}:\n\n```\n{code}\n```"
        
        optimized = self._call_ollama(prompt, system_prompt)
        
        # Extraer código de la respuesta
        optimized = self._extract_code(optimized)
        
        if inline:
            # Backup y escribir
            backup_path = f"{filepath}.backup"
            self._write_file(backup_path, code)
            self._write_file(filepath, optimized)
            print(f"   💾 Backup: {backup_path}")
            print(f"   ✅ Optimizado in-place")
        else:
            # Guardar en archivo temporal
            output_path = f"{filepath}.optimized"
            self._write_file(output_path, optimized)
            print(f"   💾 Guardado en: {output_path}")
        
        # Estadísticas
        original_lines = len(code.split('\n'))
        optimized_lines = len(optimized.split('\n'))
        
        return {
            "success": True,
            "original_lines": original_lines,
            "optimized_lines": optimized_lines,
            "output_path": output_path if not inline else filepath
        }
    
    def cmd_test(self, coverage: bool = False, target: str = None) -> Dict:
        """
        Ejecuta tests del proyecto.
        
        Detecta el framework de testing y ejecuta.
        """
        print("🧪 Ejecutando tests...")
        
        # Detectar proyecto
        if Path("pytest.ini").exists() or Path("setup.py").exists():
            # Python project
            return self._run_python_tests(coverage, target)
        elif Path("package.json").exists():
            # Node project
            return self._run_node_tests(coverage, target)
        elif Path("Cargo.toml").exists():
            # Rust project
            return self._run_rust_tests(target)
        elif Path("go.mod").exists():
            # Go project
            return self._run_go_tests(target)
        else:
            return {"success": False, "error": "No se detectó framework de testing"}
    
    def _run_python_tests(self, coverage: bool, target: str) -> Dict:
        """Ejecuta tests de Python"""
        cmd = ["python3", "-m", "pytest"]
        
        if coverage:
            cmd.extend(["--cov", "--cov-report=term-missing"])
        
        if target:
            cmd.append(target)
        else:
            cmd.append("tests/")
        
        cmd.extend(["-v", "--tb=short"])
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            print(result.stdout)
            if result.stderr:
                print(result.stderr)
            
            return {
                "success": result.returncode == 0,
                "returncode": result.returncode,
                "tests_passed": result.returncode == 0
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _run_node_tests(self, coverage: bool, target: str) -> Dict:
        """Ejecuta tests de Node.js"""
        cmd = ["npm", "test"]
        
        if coverage:
            cmd = ["npm", "run", "test:coverage"]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            print(result.stdout)
            return {
                "success": result.returncode == 0,
                "returncode": result.returncode
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _run_rust_tests(self, target: str) -> Dict:
        """Ejecuta tests de Rust"""
        cmd = ["cargo", "test"]
        if target:
            cmd.append(target)
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            print(result.stdout)
            return {
                "success": result.returncode == 0,
                "returncode": result.returncode
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _run_go_tests(self, target: str) -> Dict:
        """Ejecuta tests de Go"""
        cmd = ["go", "test", "-v"]
        if target:
            cmd.append(target)
        else:
            cmd.append("./...")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            print(result.stdout)
            return {
                "success": result.returncode == 0,
                "returncode": result.returncode
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def cmd_refactor(self, pattern: str, target: str = None) -> Dict:
        """
        Refactoriza código usando un patrón.
        
        Patrones soportados:
        - extract-function
        - rename-variable
        - add-types
        - simplify-conditionals
        - remove-duplication
        """
        print(f"🔄 Refactorizando con patrón: {pattern}")
        
        if not target:
            # Detectar archivos relevantes
            target = self._detect_main_file()
        
        if not target or not Path(target).exists():
            return {"success": False, "error": f"Target no encontrado: {target}"}
        
        code = self._read_file(target)
        lang = self._detect_language(target)
        
        patterns_desc = {
            "extract-function": "Extrae bloques de código repetidos en funciones reutilizables",
            "rename-variable": "Mejora nombres de variables para mayor claridad",
            "add-types": "Agrega type hints/annotations donde falten",
            "simplify-conditionals": "Simplifica condicionales complejos",
            "remove-duplication": "Elimina código duplicado",
        }
        
        desc = patterns_desc.get(pattern, f"Aplica refactorización: {pattern}")
        
        system_prompt = f"""Eres un experto en refactoring de código {lang}.
Tarea: {desc}

Responde SOLO con el código refactorizado, sin explicaciones."""

        prompt = f"Refactoriza este código {lang}:\n\n```\n{code}\n```"
        
        refactored = self._call_ollama(prompt, system_prompt)
        refactored = self._extract_code(refactored)
        
        output_path = f"{target}.refactored"
        self._write_file(output_path, refactored)
        
        print(f"   💾 Guardado en: {output_path}")
        
        return {
            "success": True,
            "pattern": pattern,
            "output_path": output_path
        }
    
    def cmd_review(self, target: str) -> Dict:
        """
        Realiza code review completo.
        
        Análisis:
        - Bugs potenciales
        - Code smells
        - Security issues
        - Performance issues
        - Style issues
        """
        print(f"👁️  Code Review: {target}")
        
        if Path(target).is_dir():
            # Review de directorio
            return self._review_directory(target)
        else:
            # Review de archivo
            return self._review_file(target)
    
    def _review_file(self, filepath: str) -> Dict:
        """Review de un archivo"""
        code = self._read_file(filepath)
        lang = self._detect_language(filepath)
        
        system_prompt = f"""Eres un experto senior en code review de {lang}.
Realiza un análisis completo buscando:

1. 🐛 Bugs potenciales
2. 🔒 Problemas de seguridad
3. ⚡ Problemas de performance
4. 📝 Code smells
5. 📖 Problemas de legibilidad
6. ✅ Mejores prácticas

Formato de respuesta:
- Puntuación general (1-10)
- Issues encontrados (categorizados)
- Recomendaciones específicas
- Ejemplos de mejora"""

        prompt = f"Revisa este código {lang}:\n\n```\n{code[:3000]}\n```"
        
        review = self._call_ollama(prompt, system_prompt, model="lfm2.5-thinking:1.2b")
        
        print("\n" + "=" * 60)
        print(review)
        print("=" * 60)
        
        # Guardar review
        review_path = f"{filepath}.review.md"
        self._write_file(review_path, f"# Code Review: {filepath}\n\n{review}")
        print(f"\n   💾 Review guardado en: {review_path}")
        
        return {
            "success": True,
            "filepath": filepath,
            "review_path": review_path
        }
    
    def _review_directory(self, dirpath: str) -> Dict:
        """Review de un directorio"""
        print(f"   📁 Analizando directorio: {dirpath}")
        
        # Encontrar archivos relevantes
        files = []
        for ext in ['*.py', '*.js', '*.ts', '*.rs', '*.go', '*.java']:
            files.extend(Path(dirpath).rglob(ext))
        
        # Limitar a los 5 más importantes
        files = files[:5]
        
        reviews = []
        for f in files:
            print(f"   📄 Revisando: {f}")
            result = self._review_file(str(f))
            if result["success"]:
                reviews.append(result)
        
        return {
            "success": True,
            "files_reviewed": len(reviews),
            "reviews": reviews
        }
    
    def cmd_explain(self, filepath: str, line_start: int = None, line_end: int = None) -> Dict:
        """
        Explica código en lenguaje natural.
        
        Puede explicar:
        - Archivo completo
        - Función específica
        - Rango de líneas
        """
        print(f"📖 Explicando: {filepath}")
        
        code = self._read_file(filepath)
        lang = self._detect_language(filepath)
        
        # Si hay rango, extraer solo esa parte
        if line_start is not None:
            lines = code.split('\n')
            line_end = line_end or line_start + 20
            code = '\n'.join(lines[line_start-1:line_end])
            print(f"   Líneas {line_start}-{line_end}")
        
        system_prompt = f"""Eres un experto docente de {lang}.
Explica el código proporcionado de forma clara y didáctica:

1. Qué hace el código (propósito general)
2. Cómo funciona (lógica paso a paso)
3. Conceptos clave involucrados
4. Ejemplo de uso (si aplica)

Usa lenguaje claro y ejemplos cuando sea útil."""

        prompt = f"Explica este código {lang}:\n\n```\n{code[:2000]}\n```"
        
        explanation = self._call_ollama(prompt, system_prompt, model="lfm2.5-thinking:1.2b")
        
        print("\n" + "=" * 60)
        print(explanation)
        print("=" * 60)
        
        return {
            "success": True,
            "explanation": explanation
        }
    
    def cmd_fix(self, filepath: str, dry_run: bool = False) -> Dict:
        """
        Detecta y corrige bugs automáticamente.
        
        Pasos:
        1. Analizar código buscando bugs
        2. Proponer fixes
        3. Aplicar si no es dry-run
        """
        print(f"🔨 Auto-fix: {filepath}")
        
        code = self._read_file(filepath)
        lang = self._detect_language(filepath)
        
        system_prompt = f"""Eres un experto en debugging de {lang}.
Tu tarea es:
1. Identificar bugs, errores o problemas en el código
2. Proponer fixes para cada issue encontrado
3. Responder con el código corregido completo

Responde SOLO con el código corregido, sin explicaciones."""

        prompt = f"Corrige bugs en este código {lang}:\n\n```\n{code}\n```"
        
        fixed = self._call_ollama(prompt, system_prompt)
        fixed = self._extract_code(fixed)
        
        if dry_run:
            # Mostrar diff
            print("\n   🔍 Cambios propuestos:")
            print("   [Preview del código corregido]")
            print(f"   {len(fixed)} caracteres")
        else:
            # Backup y aplicar
            backup_path = f"{filepath}.backup"
            self._write_file(backup_path, code)
            self._write_file(filepath, fixed)
            print(f"   💾 Backup: {backup_path}")
            print(f"   ✅ Corregido in-place")
        
        return {
            "success": True,
            "dry_run": dry_run,
            "original_size": len(code),
            "fixed_size": len(fixed)
        }
    
    def cmd_generate(self, description: str, output_file: str = None, lang: str = "python") -> Dict:
        """
        Genera código desde descripción natural.
        
        Ejemplo:
        "Genera una función de ordenamiento quicksort con type hints"
        """
        print(f"✨ Generando código: {description}")
        
        system_prompt = f"""Eres un experto programador {lang}.
Genera código limpio, eficiente y bien documentado.

Requisitos:
1. Código funcional y completo
2. Mejores prácticas del lenguaje
3. Documentación/docstrings
4. Manejo de errores básico
5. Ejemplo de uso (si aplica)

Responde SOLO con el código, sin explicaciones adicionales."""

        prompt = f"Genera código {lang} para: {description}"
        
        code = self._call_ollama(prompt, system_prompt)
        code = self._extract_code(code)
        
        if output_file:
            self._write_file(output_file, code)
            print(f"   💾 Guardado en: {output_file}")
        else:
            print("\n" + "=" * 60)
            print(code)
            print("=" * 60)
        
        return {
            "success": True,
            "language": lang,
            "lines": len(code.split('\n')),
            "output_file": output_file
        }
    
    # ─── HELPERS ─────────────────────────────────────────────
    
    def _extract_code(self, text: str) -> str:
        """Extrae código de respuesta de LLM"""
        import re
        
        # Buscar bloques de código
        pattern = r'```(?:\w+)?\n(.*?)\n```'
        matches = re.findall(pattern, text, re.DOTALL)
        
        if matches:
            return matches[0]
        
        # Si no hay bloques, devolver todo
        return text
    
    def _detect_main_file(self) -> Optional[str]:
        """Detecta el archivo principal del proyecto"""
        candidates = [
            "main.py", "app.py", "index.js", "main.rs", "main.go",
            "lib.rs", "mod.rs", "src/main.py", "src/main.rs"
        ]
        
        for c in candidates:
            if Path(c).exists():
                return c
        
        # Buscar cualquier archivo de código
        for ext in ['*.py', '*.js', '*.rs']:
            files = list(Path('.').glob(ext))
            if files:
                return str(files[0])
        
        return None


# ─── CLI ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="EIDOS Coding Agent - CLI para tareas de código",
        prog="eidos-code"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Comandos disponibles")
    
    # optimize
    p_opt = subparsers.add_parser("optimize", help="Optimiza un archivo")
    p_opt.add_argument("file", help="Archivo a optimizar")
    p_opt.add_argument("--inline", action="store_true", help="Sobreescribir archivo")
    
    # test
    p_test = subparsers.add_parser("test", help="Ejecuta tests")
    p_test.add_argument("--coverage", action="store_true", help="Con cobertura")
    p_test.add_argument("--target", help="Target específico")
    
    # refactor
    p_ref = subparsers.add_parser("refactor", help="Refactoriza código")
    p_ref.add_argument("pattern", help="Patrón de refactoring")
    p_ref.add_argument("--target", help="Archivo/directorio objetivo")
    
    # review
    p_rev = subparsers.add_parser("review", help="Code review")
    p_rev.add_argument("target", help="Archivo o directorio a revisar")
    
    # explain
    p_exp = subparsers.add_parser("explain", help="Explica código")
    p_exp.add_argument("file", help="Archivo a explicar")
    p_exp.add_argument("--lines", help="Rango de líneas (ej: 10-20)")
    
    # fix
    p_fix = subparsers.add_parser("fix", help="Auto-fix de bugs")
    p_fix.add_argument("file", help="Archivo a corregir")
    p_fix.add_argument("--dry-run", action="store_true", help="Solo preview")
    
    # generate
    p_gen = subparsers.add_parser("generate", help="Genera código")
    p_gen.add_argument("description", help="Descripción de lo que generar")
    p_gen.add_argument("--output", "-o", help="Archivo de salida")
    p_gen.add_argument("--lang", default="python", help="Lenguaje (default: python)")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    agent = CodingAgent()
    
    # Ejecutar comando
    if args.command == "optimize":
        result = agent.cmd_optimize(args.file, args.inline)
    elif args.command == "test":
        result = agent.cmd_test(args.coverage, args.target)
    elif args.command == "refactor":
        result = agent.cmd_refactor(args.pattern, args.target)
    elif args.command == "review":
        result = agent.cmd_review(args.target)
    elif args.command == "explain":
        line_start = None
        line_end = None
        if args.lines:
            parts = args.lines.split('-')
            line_start = int(parts[0])
            line_end = int(parts[1]) if len(parts) > 1 else None
        result = agent.cmd_explain(args.file, line_start, line_end)
    elif args.command == "fix":
        result = agent.cmd_fix(args.file, args.dry_run)
    elif args.command == "generate":
        result = agent.cmd_generate(args.description, args.output, args.lang)
    else:
        parser.print_help()
        sys.exit(1)
    
    # Resultado
    if result.get("success"):
        sys.exit(0)
    else:
        print(f"❌ Error: {result.get('error', 'Unknown error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
