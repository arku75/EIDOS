#!/usr/bin/env python3
"""
EIDOS core/knowledge_db.py — Knowledge Database
================================================
Sistema de base de conocimiento que APRENDE de TODO.

Cuando usas EIDOS en VSCode (o cualquier IDE):
1. EIDOS observa TODO tu código
2. Identifica lenguajes, librerías, patrones
3. Guarda en knowledge database
4. Sincroniza con otras instancias EIDOS
5. Crece continuamente

Knowledge estructura:
~/.eidos/knowledge/
├── languages/
│   ├── rust.json          # Sintaxis, patterns, best practices
│   ├── python.json
│   ├── go.json
│   └── ...
├── libraries/
│   ├── tokio.json         # Rust async
│   ├── django.json        # Python web
│   └── ...
├── patterns/
│   ├── architecture.json  # Patrones arquitectónicos
│   ├── design_patterns.json
│   └── ...
└── sync/
    └── sync_manifest.json # Qué compartir con otros EIDOS

Uso:
    from core.knowledge_db import KnowledgeDB

    kb = KnowledgeDB()

    # Observar código
    kb.observe_file("/path/to/main.rs")

    # EIDOS detecta automáticamente:
    # - Lenguaje: Rust
    # - Librerías: tokio, serde
    # - Patrones: async/await, error handling

    # Sincronizar con otro EIDOS
    kb.sync_with("192.168.1.100:8000")
"""
from __future__ import annotations

import json
import re
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Set


# ══════════════════════════════════════════════════════════════════════════════
# Configuración
# ══════════════════════════════════════════════════════════════════════════════

KNOWLEDGE_DIR = Path.home() / ".eidos" / "knowledge"
LANGUAGES_DIR = KNOWLEDGE_DIR / "languages"
LIBRARIES_DIR = KNOWLEDGE_DIR / "libraries"
PATTERNS_DIR = KNOWLEDGE_DIR / "patterns"
SYNC_DIR = KNOWLEDGE_DIR / "sync"

SYNC_MANIFEST = SYNC_DIR / "sync_manifest.json"


# ══════════════════════════════════════════════════════════════════════════════
# Tipos de Datos
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class LanguageKnowledge:
    """Conocimiento sobre un lenguaje."""
    name: str
    extensions: List[str]
    keywords: Set[str] = field(default_factory=set)
    syntax_patterns: Dict[str, List[str]] = field(default_factory=dict)
    best_practices: List[str] = field(default_factory=list)
    common_errors: Dict[str, str] = field(default_factory=dict)
    files_observed: int = 0
    last_updated: str = ""


@dataclass
class LibraryKnowledge:
    """Conocimiento sobre una librería/framework."""
    name: str
    language: str
    import_patterns: List[str] = field(default_factory=list)
    common_usage: List[str] = field(default_factory=list)
    documentation_urls: List[str] = field(default_factory=list)
    examples: List[Dict] = field(default_factory=list)
    times_seen: int = 0
    last_updated: str = ""


@dataclass
class CodePattern:
    """Patrón de código identificado."""
    name: str
    description: str
    language: str
    pattern_code: str
    category: str  # "architecture", "design", "idiom", etc.
    times_seen: int = 0
    confidence: float = 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Language Detectors
# ══════════════════════════════════════════════════════════════════════════════

LANGUAGE_SIGNATURES = {
    "yaml": {
        "extensions": [".yaml", ".yml"],
        "keywords": {"version", "services", "image", "container", "deployment", "pipeline"},
        "patterns": {
            "docker_service": r"services:\s*\n\s+\w+:",
            "kubernetes_kind": r"kind:\s*(\w+)",
            "ci_pipeline": r"(?:stages|jobs):\s*\n"
        },
        "config_types": {
            "docker-compose": ["services", "networks", "volumes"],
            "kubernetes": ["apiVersion", "kind", "metadata"],
            "github-actions": ["on", "jobs", "steps"],
            "gitlab-ci": ["stages", "before_script"],
            "ansible": ["hosts", "tasks", "roles"]
        }
    },
    "rust": {
        "extensions": [".rs"],
        "keywords": {"fn", "let", "mut", "impl", "trait", "struct", "enum", "match"},
        "patterns": {
            "function": r"fn\s+\w+",
            "struct": r"struct\s+\w+",
            "impl": r"impl\s+\w+"
        }
    },
    "python": {
        "extensions": [".py"],
        "keywords": {"def", "class", "import", "from", "async", "await", "lambda"},
        "patterns": {
            "function": r"def\s+\w+",
            "class": r"class\s+\w+",
            "import": r"^(?:from\s+[\w.]+\s+)?import\s+"
        }
    },
    "go": {
        "extensions": [".go"],
        "keywords": {"func", "package", "import", "type", "struct", "interface", "chan"},
        "patterns": {
            "function": r"func\s+\w+",
            "struct": r"type\s+\w+\s+struct"
        }
    },
    "javascript": {
        "extensions": [".js", ".jsx", ".ts", ".tsx"],
        "keywords": {"function", "const", "let", "var", "class", "async", "await"},
        "patterns": {
            "function": r"function\s+\w+|const\s+\w+\s*=\s*\(",
            "class": r"class\s+\w+"
        }
    },
    "c": {
        "extensions": [".c", ".h"],
        "keywords": {"int", "void", "char", "struct", "typedef", "static"},
        "patterns": {
            "function": r"\w+\s+\w+\s*\([^)]*\)\s*\{",
            "struct": r"struct\s+\w+"
        }
    },
    "cpp": {
        "extensions": [".cpp", ".hpp", ".cc", ".h"],
        "keywords": {"class", "namespace", "template", "public", "private", "virtual"},
        "patterns": {
            "class": r"class\s+\w+",
            "namespace": r"namespace\s+\w+"
        }
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# Knowledge Database
# ══════════════════════════════════════════════════════════════════════════════

class KnowledgeDB:
    """
    Base de conocimiento que aprende de TODO.

    EIDOS observa código y extrae conocimiento automáticamente.
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self._ensure_dirs()

        # Caches en memoria
        self.languages: Dict[str, LanguageKnowledge] = {}
        self.libraries: Dict[str, LibraryKnowledge] = {}
        self.patterns: List[CodePattern] = []

        # Cargar conocimiento existente
        self._load_existing()

    def _ensure_dirs(self):
        """Crea directorios necesarios."""
        for dir_path in [LANGUAGES_DIR, LIBRARIES_DIR, PATTERNS_DIR, SYNC_DIR]:
            dir_path.mkdir(parents=True, exist_ok=True)

    def _log(self, msg: str):
        """Log con prefijo."""
        if self.verbose:
            print(f"📚 [KnowledgeDB] {msg}")

    def _load_existing(self):
        """Carga conocimiento existente desde disco."""
        # Cargar lenguajes
        for lang_file in LANGUAGES_DIR.glob("*.json"):
            try:
                with open(lang_file) as f:
                    data = json.load(f)
                    lang = LanguageKnowledge(**data)
                    # Convert list back to set for keywords
                    if isinstance(lang.keywords, list):
                        lang.keywords = set(lang.keywords)
                    self.languages[lang.name] = lang
            except Exception:
                pass  # error no crítico, continuar
        # Cargar librerías
        for lib_file in LIBRARIES_DIR.glob("*.json"):
            try:
                with open(lib_file) as f:
                    data = json.load(f)
                    lib = LibraryKnowledge(**data)
                    self.libraries[lib.name] = lib
            except Exception:
                pass  # error no crítico, continuar
        if self.languages or self.libraries:
            self._log(f"Cargado: {len(self.languages)} lenguajes, {len(self.libraries)} librerías")

    def observe_file(self, file_path: str | Path) -> Dict[str, Any]:
        """
        Observa un archivo y extrae conocimiento.

        Args:
            file_path: Ruta al archivo

        Returns:
            Dict con conocimiento extraído
        """
        file_path = Path(file_path)

        if not file_path.exists():
            return {"error": "File not found"}

        self._log(f"Observando: {file_path.name}")

        # Detectar lenguaje
        language = self._detect_language(file_path)

        if not language:
            return {"error": "Language not detected"}

        # Leer contenido
        try:
            content = file_path.read_text(errors="ignore")
        except Exception as e:
            return {"error": str(e)}

        # Extraer conocimiento
        knowledge = {
            "file": str(file_path),
            "language": language,
            "libraries": self._extract_libraries(content, language),
            "patterns": self._extract_patterns(content, language),
            "functions": self._extract_functions(content, language),
            "complexity_score": self._estimate_complexity(content)
        }

        # Actualizar knowledge database
        self._update_language_knowledge(language, content, file_path)
        self._update_library_knowledge(knowledge["libraries"], language)

        # Guardar
        self._save_knowledge()

        return knowledge

    def _detect_language(self, file_path: Path) -> Optional[str]:
        """Detecta el lenguaje de un archivo."""
        extension = file_path.suffix

        for lang, sig in LANGUAGE_SIGNATURES.items():
            if extension in sig["extensions"]:
                return lang

        return None

    def _extract_libraries(self, content: str, language: str) -> List[str]:
        """Extrae librerías/imports del código."""
        libraries = []

        if language == "python":
            # import x, from x import y
            imports = re.findall(r'(?:from\s+([\w.]+)\s+)?import\s+([\w., ]+)', content)
            for from_part, import_part in imports:
                if from_part:
                    libraries.append(from_part.split('.')[0])
                for imp in import_part.split(','):
                    libraries.append(imp.strip().split()[0])

        elif language == "rust":
            # use crate::module
            uses = re.findall(r'use\s+([\w:]+)', content)
            libraries.extend([u.split('::')[0] for u in uses])

        elif language == "go":
            # import "package"
            imports = re.findall(r'import\s+(?:"([^"]+)"|`([^`]+)`)', content)
            libraries.extend([i[0] or i[1] for i in imports])

        elif language in ["javascript", "typescript"]:
            # import x from 'y', require('y')
            imports = re.findall(r'(?:import.*?from\s+|require\s*\()\s*[\'"]([^\'"]+)', content)
            libraries.extend(imports)

        return list(set(libraries))  # Unique

    def _extract_patterns(self, content: str, language: str) -> List[str]:
        """Identifica patrones de código."""
        patterns = []

        # Patrones comunes
        if "async" in content and "await" in content:
            patterns.append("async/await")

        if language == "rust":
            if "Result<" in content and "?" in content:
                patterns.append("error_handling_with_question_mark")
            if ".iter()" in content or ".into_iter()" in content:
                patterns.append("iterator_pattern")

        elif language == "python":
            if "with " in content:
                patterns.append("context_manager")
            if "@" in content and "def " in content:
                patterns.append("decorator")

        return patterns

    def _extract_functions(self, content: str, language: str) -> int:
        """Cuenta funciones en el código."""
        sig = LANGUAGE_SIGNATURES.get(language, {})
        func_pattern = sig.get("patterns", {}).get("function")

        if func_pattern:
            return len(re.findall(func_pattern, content, re.MULTILINE))

        return 0

    def _estimate_complexity(self, content: str) -> int:
        """Estima complejidad del código (ciclomática simplificada)."""
        # Contar keywords que aumentan complejidad
        complexity_keywords = ["if", "else", "elif", "for", "while", "match", "case", "catch", "&&", "||"]

        score = 1  # Base
        for keyword in complexity_keywords:
            score += content.count(keyword)

        return min(score, 100)  # Cap at 100

    def _update_language_knowledge(self, language: str, content: str, file_path: Path):
        """Actualiza conocimiento sobre un lenguaje."""
        if language not in self.languages:
            sig = LANGUAGE_SIGNATURES.get(language, {})
            self.languages[language] = LanguageKnowledge(
                name=language,
                extensions=sig.get("extensions", []),
                keywords=set(sig.get("keywords", [])),
                last_updated=datetime.now().isoformat()
            )

        lang_kb = self.languages[language]
        lang_kb.files_observed += 1
        lang_kb.last_updated = datetime.now().isoformat()

    def _update_library_knowledge(self, libraries: List[str], language: str):
        """Actualiza conocimiento sobre librerías."""
        for lib in libraries:
            if lib not in self.libraries:
                self.libraries[lib] = LibraryKnowledge(
                    name=lib,
                    language=language,
                    last_updated=datetime.now().isoformat()
                )

            self.libraries[lib].times_seen += 1
            self.libraries[lib].last_updated = datetime.now().isoformat()

    def _save_knowledge(self):
        """Guarda conocimiento a disco."""
        # Guardar lenguajes
        for lang in self.languages.values():
            file_path = LANGUAGES_DIR / f"{lang.name}.json"
            data = asdict(lang)
            # Convert set to list for JSON
            data['keywords'] = list(data['keywords'])
            with open(file_path, 'w') as f:
                json.dump(data, f, indent=2)

        # Guardar librerías
        for lib in self.libraries.values():
            file_path = LIBRARIES_DIR / f"{lib.name}.json"
            with open(file_path, 'w') as f:
                json.dump(asdict(lib), f, indent=2)

    def get_stats(self) -> Dict[str, Any]:
        """Obtiene estadísticas de la knowledge database."""
        return {
            "languages": {
                name: {
                    "files_observed": lang.files_observed,
                    "keywords_count": len(lang.keywords)
                }
                for name, lang in self.languages.items()
            },
            "libraries": {
                name: {
                    "language": lib.language,
                    "times_seen": lib.times_seen
                }
                for name, lib in self.libraries.items()
            },
            "total_languages": len(self.languages),
            "total_libraries": len(self.libraries)
        }

    def export_for_sync(self) -> Dict[str, Any]:
        """
        Exporta conocimiento para sincronización con otros EIDOS.

        Returns:
            Dict serializable con todo el conocimiento
        """
        return {
            "languages": {
                name: {**asdict(lang), "keywords": list(lang.keywords)}
                for name, lang in self.languages.items()
            },
            "libraries": {
                name: asdict(lib)
                for name, lib in self.libraries.items()
            },
            "export_timestamp": datetime.now().isoformat()
        }

    def import_from_sync(self, knowledge_data: Dict[str, Any]):
        """
        Importa conocimiento de otro EIDOS.

        Args:
            knowledge_data: Dict exportado de otro EIDOS
        """
        self._log("Importando conocimiento de otro EIDOS...")

        # Merge lenguajes
        for lang_name, lang_data in knowledge_data.get("languages", {}).items():
            if lang_name in self.languages:
                # Merge keywords
                existing = self.languages[lang_name]
                existing.keywords.update(set(lang_data.get("keywords", [])))
                existing.files_observed += lang_data.get("files_observed", 0)
            else:
                # Nuevo lenguaje
                lang_data_copy = lang_data.copy()
                lang_data_copy['keywords'] = set(lang_data.get('keywords', []))
                self.languages[lang_name] = LanguageKnowledge(**lang_data_copy)

        # Merge librerías
        for lib_name, lib_data in knowledge_data.get("libraries", {}).items():
            if lib_name in self.libraries:
                self.libraries[lib_name].times_seen += lib_data.get("times_seen", 0)
            else:
                self.libraries[lib_name] = LibraryKnowledge(**lib_data)

        # Guardar
        self._save_knowledge()

        self._log(f"✅ Conocimiento importado")


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_kb: Optional[KnowledgeDB] = None

def get_knowledge_db() -> KnowledgeDB:
    """Obtiene la instancia singleton de KnowledgeDB."""
    global _kb
    if _kb is None:
        _kb = KnowledgeDB()
    return _kb


# ══════════════════════════════════════════════════════════════════════════════
# CLI Testing
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    kb = get_knowledge_db()

    print(f"\n{'═' * 70}")
    print(f"EIDOS KNOWLEDGE DATABASE")
    print(f"{'═' * 70}\n")

    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        print(f"Observando archivo: {file_path}\n")

        knowledge = kb.observe_file(file_path)

        if "error" in knowledge:
            print(f"❌ Error: {knowledge['error']}")
        else:
            print(f"Lenguaje: {knowledge['language']}")
            print(f"Librerías: {', '.join(knowledge['libraries']) if knowledge['libraries'] else 'ninguna'}")
            print(f"Patrones: {', '.join(knowledge['patterns']) if knowledge['patterns'] else 'ninguno'}")
            print(f"Funciones: {knowledge['functions']}")
            print(f"Complejidad: {knowledge['complexity_score']}")

    print(f"\n{'─' * 70}")
    print(f"ESTADÍSTICAS")
    print(f"{'─' * 70}\n")

    stats = kb.get_stats()
    print(f"Lenguajes aprendidos: {stats['total_languages']}")
    print(f"Librerías aprendidas: {stats['total_libraries']}")

    if stats['languages']:
        print(f"\nLenguajes:")
        for lang, data in stats['languages'].items():
            print(f"  • {lang:15s} → {data['files_observed']} archivos observados")

    if stats['libraries']:
        print(f"\nLibrerías Top 10:")
        sorted_libs = sorted(
            stats['libraries'].items(),
            key=lambda x: x[1]['times_seen'],
            reverse=True
        )[:10]

        for lib, data in sorted_libs:
            print(f"  • {lib:20s} ({data['language']}) → visto {data['times_seen']} veces")

    print(f"\n{'═' * 70}\n")
