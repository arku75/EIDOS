"""
EIDOS 7-Layer Context Compressor — Compresión de contexto en 7 capas
Inspirado en ClawRouter: dedup, whitespace, dictionary, paths, JSON, observation, codebook

Target: 30-50% reducción de tokens sin perder información semántica.
"""

import re
import json
import hashlib
import time
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any
from collections import Counter
from dataclasses import dataclass, field

# ─── Constantes ──────────────────────────────────────────────────────────────

EIDOS_DIR = Path.home() / ".eidos"
CODEBOOK_PATH = EIDOS_DIR / "compression_codebook.json"

# ─── Diccionario de compresión ───────────────────────────────────────────────

DICTIONARY = {
    # Python keywords
    "function": "fn", "return": "ret", "import": "imp",
    "class": "cls", "self": "slf", "None": "∅",
    "True": "⊤", "False": "⊥", "print": "pr",
    "except": "exc", "Exception": "Exc", "raise": "rse",
    "lambda": "λ", "yield": "yld", "async": "asc",
    "await": "awt", "finally": "fnl", "continue": "cnt",
    # Common programming terms
    "response": "resp", "request": "req", "message": "msg",
    "error": "err", "warning": "wrn", "success": "suc",
    "config": "cfg", "configuration": "cfg",
    "database": "db", "parameter": "param", "argument": "arg",
    "directory": "dir", "file": "f", "path": "p",
    "initialize": "init", "execute": "exec", "process": "proc",
    "result": "res", "output": "out", "input": "inp",
    "string": "str", "integer": "int", "boolean": "bool",
    "dictionary": "dict", "list": "lst", "tuple": "tpl",
    "timestamp": "ts", "datetime": "dt",
    "connection": "conn", "session": "sess",
    "memory": "mem", "buffer": "buf", "cache": "cch",
    "default": "def", "maximum": "max", "minimum": "min",
    "description": "desc", "information": "info",
    "application": "app", "service": "svc",
    "authentication": "auth", "authorization": "authz",
    "environment": "env", "variable": "var",
    "template": "tmpl", "document": "doc",
    "command": "cmd", "status": "sts",
}

# Reverse dictionary for decompression
REVERSE_DICT = {v: k for k, v in DICTIONARY.items()}

# ─── Dataclass ───────────────────────────────────────────────────────────────

@dataclass
class CompressionStats:
    original_tokens: int = 0
    compressed_tokens: int = 0
    ratio: float = 0.0
    per_layer: Dict[str, Dict] = field(default_factory=dict)
    total_time_ms: float = 0.0

# ─── Compressor ──────────────────────────────────────────────────────────────

class SevenLayerCompressor:
    """
    Compresión de contexto en 7 capas independientes.

    Capas:
    1. Deduplication — elimina contenido duplicado/similar
    2. Whitespace normalization — normaliza espacios y saltos de línea
    3. Dictionary encoding — reemplaza tokens frecuentes con abreviaciones
    4. Path shortening — acorta rutas de archivos repetidas
    5. JSON compaction — comprime JSON inline
    6. Observation compression — resume outputs largos de tools
    7. Dynamic codebook — aprende patrones del contexto actual
    """

    def __init__(self):
        EIDOS_DIR.mkdir(parents=True, exist_ok=True)
        self.codebook = self._load_codebook()
        self._path_map = {}
        self._path_counter = 0

    def _load_codebook(self) -> Dict[str, str]:
        """Carga codebook dinámico"""
        if CODEBOOK_PATH.exists():
            try:
                return json.loads(CODEBOOK_PATH.read_text())
            except Exception:
                pass  # error no crítico, continuar
        return {}

    def _save_codebook(self):
        """Guarda codebook dinámico"""
        try:
            CODEBOOK_PATH.write_text(json.dumps(self.codebook, indent=2))
        except Exception:
            pass  # error no crítico, continuar
    def _count_tokens(self, text: str) -> int:
        """Estimación rápida de tokens (palabras + puntuación)"""
        return len(re.findall(r'\S+', text))

    # ─── Capa 1: Deduplication ───────────────────────────────────────────

    def _layer_dedup(self, text: str) -> str:
        """Elimina líneas duplicadas o muy similares"""
        lines = text.split('\n')
        seen_hashes = set()
        result = []

        for line in lines:
            # Normalizar para comparación
            normalized = re.sub(r'\s+', ' ', line.strip().lower())
            if not normalized:
                result.append(line)
                continue

            h = hashlib.md5(normalized.encode()).hexdigest()[:12]
            if h not in seen_hashes:
                seen_hashes.add(h)
                result.append(line)
            # Si es duplicado, lo saltamos silenciosamente

        return '\n'.join(result)

    # ─── Capa 2: Whitespace Normalization ────────────────────────────────

    def _layer_whitespace(self, text: str) -> str:
        """Normaliza whitespace sin perder estructura"""
        # Múltiples líneas vacías → máximo 1
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Tabs → 2 espacios
        text = re.sub(r'\t', '  ', text)
        # Trailing whitespace
        text = re.sub(r'[ \t]+\n', '\n', text)
        # Múltiples espacios (pero no al inicio de línea)
        lines = []
        for line in text.split('\n'):
            indent = len(line) - len(line.lstrip())
            content = re.sub(r'  +', ' ', line.lstrip())
            lines.append(' ' * min(indent, 8) + content)
        return '\n'.join(lines)

    # ─── Capa 3: Dictionary Encoding ─────────────────────────────────────

    def _layer_dictionary(self, text: str) -> str:
        """Reemplaza tokens frecuentes con abreviaciones"""
        # Solo reemplazar palabras completas, case-sensitive para keywords
        for word, abbr in DICTIONARY.items():
            # No reemplazar dentro de strings/paths
            pattern = r'(?<![/\w])' + re.escape(word) + r'(?![/\w])'
            text = re.sub(pattern, f'«{abbr}»', text)
        return text

    def _layer_dictionary_decode(self, text: str) -> str:
        """Decodifica abreviaciones del diccionario"""
        for abbr, word in REVERSE_DICT.items():
            text = text.replace(f'«{abbr}»', word)
        return text

    # ─── Capa 4: Path Shortening ─────────────────────────────────────────

    def _layer_paths(self, text: str) -> str:
        """Acorta rutas de archivos repetidas"""
        # Encontrar rutas comunes
        paths = re.findall(r'(?:/[\w.-]+){3,}', text)
        if not paths:
            return text

        # Contar frecuencia de prefijos
        prefix_count = Counter()
        for p in paths:
            parts = p.split('/')
            for i in range(3, len(parts)):
                prefix = '/'.join(parts[:i]) + '/'
                prefix_count[prefix] += 1

        # Reemplazar prefijos frecuentes (aparecen 2+ veces)
        for prefix, count in prefix_count.most_common(10):
            if count >= 2 and len(prefix) > 15:
                if prefix not in self._path_map:
                    self._path_counter += 1
                    # Crear abreviación legible
                    parts = prefix.rstrip('/').split('/')
                    short = '~' + '/'.join(p[0] for p in parts[-3:] if p) + '/'
                    self._path_map[prefix] = short

                text = text.replace(prefix, self._path_map[prefix])

        return text

    # ─── Capa 5: JSON Compaction ─────────────────────────────────────────

    def _layer_json(self, text: str) -> str:
        """Comprime JSON/dicts inline"""
        def compact_json_match(m):
            try:
                obj = json.loads(m.group(0))
                return json.dumps(obj, separators=(',', ':'))
            except (json.JSONDecodeError, ValueError):
                return m.group(0)

        # Encontrar bloques JSON (entre {} o [])
        # Patrón simple para JSON con whitespace
        text = re.sub(
            r'\{[^{}]*\}',
            compact_json_match,
            text
        )
        return text

    # ─── Capa 6: Observation Compression ─────────────────────────────────

    def _layer_observation(self, text: str) -> str:
        """Resume outputs largos de tools manteniendo info clave"""
        lines = text.split('\n')
        if len(lines) <= 30:
            return text

        result = []
        in_output_block = False
        output_lines = []

        for line in lines:
            # Detectar bloques de output largo
            if any(marker in line.lower() for marker in
                   ['output:', 'stdout:', 'result:', '```']):
                if output_lines and len(output_lines) > 20:
                    # Comprimir: primeras 5 + stats + últimas 5
                    result.extend(output_lines[:5])
                    result.append(f"  ... [{len(output_lines) - 10} líneas omitidas] ...")
                    result.extend(output_lines[-5:])
                    output_lines = []
                in_output_block = not in_output_block
                result.append(line)
                continue

            if in_output_block:
                output_lines.append(line)
            else:
                result.append(line)

        # Flush remaining
        if output_lines and len(output_lines) > 20:
            result.extend(output_lines[:5])
            result.append(f"  ... [{len(output_lines) - 10} líneas omitidas] ...")
            result.extend(output_lines[-5:])
        else:
            result.extend(output_lines)

        return '\n'.join(result)

    # ─── Capa 7: Dynamic Codebook ────────────────────────────────────────

    def _layer_codebook(self, text: str) -> str:
        """Aprende patrones del contexto actual y crea codebook dinámico"""
        # Encontrar n-gramas frecuentes (2-4 palabras)
        words = re.findall(r'\b\w+\b', text)
        if len(words) < 50:
            return text

        # Bigrams y trigrams frecuentes
        for n in [3, 2]:
            ngrams = Counter()
            for i in range(len(words) - n + 1):
                gram = ' '.join(words[i:i+n])
                if len(gram) > 10:  # Solo n-gramas largos
                    ngrams[gram] += 1

            for gram, count in ngrams.most_common(20):
                if count >= 3 and len(gram) > 12:
                    code = f"§{hashlib.md5(gram.encode()).hexdigest()[:4]}"
                    if gram not in self.codebook:
                        self.codebook[gram] = code
                    text = text.replace(gram, self.codebook[gram])

        self._save_codebook()
        return text

    # ─── API Principal ───────────────────────────────────────────────────

    def compress(self, text: str, layers: List[int] = None) -> Tuple[str, CompressionStats]:
        """
        Comprime texto aplicando las capas seleccionadas.

        Args:
            text: Texto a comprimir
            layers: Lista de capas a aplicar (1-7). Default: todas.

        Returns:
            (texto_comprimido, estadísticas)
        """
        start = time.time()
        stats = CompressionStats()
        stats.original_tokens = self._count_tokens(text)

        if layers is None:
            layers = [1, 2, 3, 4, 5, 6, 7]

        layer_functions = {
            1: ("deduplication", self._layer_dedup),
            2: ("whitespace", self._layer_whitespace),
            3: ("dictionary", self._layer_dictionary),
            4: ("path_shortening", self._layer_paths),
            5: ("json_compaction", self._layer_json),
            6: ("observation", self._layer_observation),
            7: ("dynamic_codebook", self._layer_codebook),
        }

        for layer_num in sorted(layers):
            if layer_num not in layer_functions:
                continue

            name, func = layer_functions[layer_num]
            before = self._count_tokens(text)
            text = func(text)
            after = self._count_tokens(text)

            stats.per_layer[name] = {
                "before": before,
                "after": after,
                "saved": before - after,
                "ratio": 1 - (after / max(before, 1))
            }

        stats.compressed_tokens = self._count_tokens(text)
        stats.ratio = 1 - (stats.compressed_tokens / max(stats.original_tokens, 1))
        stats.total_time_ms = (time.time() - start) * 1000

        return text, stats

    def decompress(self, text: str) -> str:
        """
        Decompresión best-effort.
        Revierte dictionary encoding y codebook.
        """
        # Revertir dictionary
        text = self._layer_dictionary_decode(text)

        # Revertir codebook
        reverse_codebook = {v: k for k, v in self.codebook.items()}
        for code, gram in reverse_codebook.items():
            text = text.replace(code, gram)

        # Revertir paths
        reverse_paths = {v: k for k, v in self._path_map.items()}
        for short, full in reverse_paths.items():
            text = text.replace(short, full)

        return text

    def get_stats_summary(self, stats: CompressionStats) -> str:
        """Resumen legible de estadísticas"""
        lines = [
            f"📦 Compresión: {stats.original_tokens} → {stats.compressed_tokens} tokens "
            f"({stats.ratio:.1%} reducción, {stats.total_time_ms:.1f}ms)",
            ""
        ]
        for name, data in stats.per_layer.items():
            saved = data["saved"]
            ratio = data["ratio"]
            bar = "█" * int(ratio * 20) + "░" * (20 - int(ratio * 20))
            lines.append(f"  L{list(stats.per_layer.keys()).index(name)+1} {name:20s} {bar} {ratio:.1%} (-{saved})")

        return '\n'.join(lines)


# ─── Test ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Test 7-Layer Compressor ===\n")

    comp = SevenLayerCompressor()

    # Texto de prueba con contenido típico de EIDOS
    test_text = """
The function called initialize_database will create a new connection
to the database and return the configuration dictionary with all the
default parameter values set.

The function called initialize_database will create a new connection
to the database and return the configuration dictionary with all the
default parameter values set.

import os
import sys
from pathlib import Path

def initialize():
    config = {
        "database":    "eidos",
        "host":    "localhost",
        "port":    5432,
        "timeout":    30
    }
    return config


File: core/kernel.py
File: core/agent.py
File: core/autonomous.py
File: core/tools.py
File: core/memory.py

Output:
Line 1 of output
Line 2 of output
Line 3 of output
Line 4 of output
Line 5 of output
Line 6 of output
Line 7 of output
Line 8 of output
Line 9 of output
Line 10 of output
Line 11 of output
Line 12 of output
Line 13 of output
Line 14 of output
Line 15 of output
Line 16 of output
Line 17 of output
Line 18 of output
Line 19 of output
Line 20 of output
Line 21 of output
Line 22 of output
Line 23 of output
Line 24 of output
Line 25 of output

The function process_request will handle the authentication
and return a response message with the result information.
The application service needs the configuration environment
variable to initialize the database connection session.
The function process_request will handle the authentication
and return a response message with the result information.
"""

    compressed, stats = comp.compress(test_text)
    print(comp.get_stats_summary(stats))

    print(f"\n--- Original ({stats.original_tokens} tokens) ---")
    print(test_text[:200] + "...")
    print(f"\n--- Comprimido ({stats.compressed_tokens} tokens) ---")
    print(compressed[:200] + "...")

    # Test decompresión
    decompressed = comp.decompress(compressed)
    print(f"\n--- Decomprimido ---")
    print(decompressed[:200] + "...")

    # Test capas individuales
    print("\n\n=== Test capas individuales ===")
    for layer in range(1, 8):
        _, s = comp.compress(test_text, layers=[layer])
        print(f"  Capa {layer}: {s.ratio:.1%} reducción")

    print("\n✅ 7-Layer Compressor funcional")
