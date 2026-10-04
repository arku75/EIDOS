"""
EIDOS Learning System - Sistema de Aprendizaje Multi-Formato
=============================================================
EIDOS puede aprender de múltiples formatos y automáticamente limpiar después.

Formatos soportados:
- Videos de YouTube (youtube-dl/yt-dlp)
- Archivos .vsix (VS Code extensions)
- Archivos .zip (código fuente)
- PDFs (documentos)
- Código fuente (Python, JS, etc.)
- Imágenes (con CLIP Vision)
- Audio (transcripción con Whisper)
- Markdown/texto

Workflow:
1. EIDOS descarga/recibe archivo
2. Extrae conocimiento (según formato)
3. Guarda conocimiento en KB
4. CONSERVA la fuente por defecto para procedencia y reproducción
"""
from __future__ import annotations

import sys
import os
import subprocess
import zipfile
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass
from datetime import datetime

# Añadir root al path
EIDOS_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(EIDOS_ROOT))

from core.eidos_config import get_config
from core.cleanup_system import get_cleanup_manager

# ============================
# DATA MODELS
# ============================

@dataclass
class LearnedKnowledge:
    """Conocimiento extraído de un archivo"""
    source_file: str
    source_type: str  # youtube, vsix, zip, pdf, code, etc.
    knowledge: str  # Conocimiento extraído
    metadata: Dict[str, Any]
    learned_at: float
    original_size_mb: float

# ============================
# LEARNING SYSTEM
# ============================

class LearningSystem:
    """
    Sistema de aprendizaje multi-formato.

    Puede aprender de múltiples fuentes y conserva los originales por defecto.
    """

    def __init__(self):
        self.config = get_config()
        self.cleanup = get_cleanup_manager()

        # Directorio de aprendizaje
        self.learning_dir = Path(self.config.paths.knowledge) / "learning"
        self.learning_dir.mkdir(parents=True, exist_ok=True)

        # Directorio temporal para descargas
        self.temp_dir = Path(self.config.paths.home) / "temp_learning"
        self.temp_dir.mkdir(exist_ok=True)

        # Verificar herramientas
        self._check_tools()

        print(f"[LEARNING] 📚 Learning System inicializado")

    def _check_tools(self):
        """Verifica herramientas disponibles"""
        self.tools = {
            "yt-dlp": self._command_exists("yt-dlp"),
            "youtube-dl": self._command_exists("youtube-dl"),
            "unzip": self._command_exists("unzip"),
            "pdftotext": self._command_exists("pdftotext"),
        }

        for tool, available in self.tools.items():
            status = "✅" if available else "⚠️"
            print(f"[LEARNING] {status} {tool}")

    def _command_exists(self, command: str) -> bool:
        """Verifica si un comando existe"""
        try:
            subprocess.run(
                ["which", command],
                capture_output=True,
                check=True
            )
            return True
        except Exception:
            return False

    # ========== YOUTUBE ==========

    def learn_from_youtube(
        self,
        url: str,
        download_video: bool = False
    ) -> Optional[LearnedKnowledge]:
        """
        Aprende de un video de YouTube.

        Args:
            url: URL del video
            download_video: Si False, solo descarga subtítulos/metadata

        Returns:
            LearnedKnowledge con lo aprendido
        """
        print(f"[LEARNING] 🎬 Aprendiendo de YouTube: {url}")

        if not (self.tools["yt-dlp"] or self.tools["youtube-dl"]):
            print(f"[LEARNING] ⚠️  youtube-dl/yt-dlp no disponible")
            return None

        try:
            ytdl = "yt-dlp" if self.tools["yt-dlp"] else "youtube-dl"

            # Obtener metadata
            cmd_info = [
                ytdl,
                "--dump-json",
                "--no-download",
                url
            ]

            result = subprocess.run(cmd_info, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"[LEARNING] ❌ Error obteniendo metadata")
                return None

            metadata = json.loads(result.stdout)

            # Extraer conocimiento básico
            knowledge = f"""
Video de YouTube aprendido:
- Título: {metadata.get('title', 'N/A')}
- Canal: {metadata.get('uploader', 'N/A')}
- Descripción: {metadata.get('description', 'N/A')[:500]}...
- Duración: {metadata.get('duration', 0)} segundos
- Vistas: {metadata.get('view_count', 0)}
""".strip()

            # Si hay subtítulos, descargarlos
            if not download_video:
                subtitle_file = self.temp_dir / f"subtitles_{int(datetime.now().timestamp())}.srt"

                cmd_subs = [
                    ytdl,
                    "--write-sub",
                    "--write-auto-sub",
                    "--sub-lang", "es,en",
                    "--skip-download",
                    "-o", str(subtitle_file.with_suffix("")),
                    url
                ]

                subprocess.run(cmd_subs, capture_output=True)

                # Buscar archivo de subtítulos generado
                srt_files = list(self.temp_dir.glob("subtitles_*.srt"))
                if srt_files:
                    with open(srt_files[0], 'r', encoding='utf-8', errors='ignore') as f:
                        subtitles = f.read()
                        knowledge += f"\n\nSubtítulos:\n{subtitles[:2000]}..."  # Primeros 2000 chars

                    # Limpiar subtítulos
                    for srt in srt_files:
                        srt.unlink()

            learned = LearnedKnowledge(
                source_file=url,
                source_type="youtube",
                knowledge=knowledge,
                metadata=metadata,
                learned_at=datetime.now().timestamp(),
                original_size_mb=0.0  # No guardamos el video
            )

            self._save_knowledge(learned)

            print(f"[LEARNING] ✅ Video de YouTube aprendido")
            return learned

        except Exception as e:
            print(f"[LEARNING] ❌ Error: {e}")
            import traceback
            traceback.print_exc()
            return None

    # ========== VSIX (VS Code Extensions) ==========

    def learn_from_vsix(self, vsix_path: Path) -> Optional[LearnedKnowledge]:
        """
        Aprende de una extensión de VS Code (.vsix).

        Los .vsix son archivos ZIP con metadata de la extensión.
        """
        print(f"[LEARNING] 📦 Aprendiendo de VSIX: {vsix_path.name}")

        if not vsix_path.exists():
            print(f"[LEARNING] ❌ Archivo no existe")
            return None

        try:
            # Los .vsix son ZIP files
            with zipfile.ZipFile(vsix_path, 'r') as zip_file:
                # Buscar package.json
                if 'extension/package.json' in zip_file.namelist():
                    with zip_file.open('extension/package.json') as f:
                        package = json.load(f)

                        knowledge = f"""
Extensión de VS Code aprendida:
- Nombre: {package.get('name', 'N/A')}
- Display Name: {package.get('displayName', 'N/A')}
- Versión: {package.get('version', 'N/A')}
- Descripción: {package.get('description', 'N/A')}
- Publisher: {package.get('publisher', 'N/A')}
- Categorías: {', '.join(package.get('categories', []))}
- Keywords: {', '.join(package.get('keywords', []))}

Capabilities:
"""

                        # Comandos
                        if 'contributes' in package:
                            contributes = package['contributes']

                            if 'commands' in contributes:
                                knowledge += f"\nComandos ({len(contributes['commands'])}):\n"
                                for cmd in contributes['commands'][:5]:  # Primeros 5
                                    knowledge += f"  - {cmd.get('title', 'N/A')}\n"

                            if 'languages' in contributes:
                                langs = [l.get('id', 'N/A') for l in contributes['languages']]
                                knowledge += f"\nLenguajes soportados: {', '.join(langs)}\n"

                        learned = LearnedKnowledge(
                            source_file=str(vsix_path),
                            source_type="vsix",
                            knowledge=knowledge.strip(),
                            metadata=package,
                            learned_at=datetime.now().timestamp(),
                            original_size_mb=vsix_path.stat().st_size / (1024 * 1024)
                        )

                        self._save_knowledge(learned)

                        # Registrar aprendizaje sin destruir la evidencia fuente.
                        self.cleanup.mark_as_learned(
                            vsix_path,
                            knowledge=knowledge[:500],
                            can_delete=False
                        )

                        print(f"[LEARNING] ✅ VSIX aprendido; fuente conservada")
                        return learned

        except Exception as e:
            print(f"[LEARNING] ❌ Error: {e}")
            return None

    # ========== ZIP (Código fuente) ==========

    def learn_from_zip(self, zip_path: Path) -> Optional[LearnedKnowledge]:
        """
        Aprende de un archivo ZIP (código fuente, proyecto, etc.).
        """
        print(f"[LEARNING] 📦 Aprendiendo de ZIP: {zip_path.name}")

        if not zip_path.exists():
            print(f"[LEARNING] ❌ Archivo no existe")
            return None

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_file:
                files = zip_file.namelist()

                # Contar tipos de archivos
                extensions = {}
                for file in files:
                    if not file.endswith('/'):  # No directorios
                        ext = Path(file).suffix or "no_ext"
                        extensions[ext] = extensions.get(ext, 0) + 1

                # Buscar archivos importantes
                important = []
                for name in ['README.md', 'README.txt', 'package.json', 'setup.py', 'requirements.txt']:
                    if name in files or f"*/{name}" in str(files):
                        matching = [f for f in files if f.endswith(name)]
                        if matching:
                            important.extend(matching)

                knowledge = f"""
Archivo ZIP aprendido:
- Nombre: {zip_path.name}
- Total archivos: {len(files)}
- Archivos por tipo: {dict(list(extensions.items())[:10])}  # Top 10

Archivos importantes encontrados:
"""

                # Leer archivos importantes
                for imp_file in important[:3]:  # Primeros 3
                    try:
                        with zip_file.open(imp_file) as f:
                            content = f.read(2000).decode('utf-8', errors='ignore')  # Primeros 2000 bytes
                            knowledge += f"\n--- {imp_file} ---\n{content}\n"
                    except Exception:
                        pass  # error no crítico, continuar
                learned = LearnedKnowledge(
                    source_file=str(zip_path),
                    source_type="zip",
                    knowledge=knowledge.strip(),
                    metadata={"files": files[:100], "extensions": extensions},  # Primeros 100 archivos
                    learned_at=datetime.now().timestamp(),
                    original_size_mb=zip_path.stat().st_size / (1024 * 1024)
                )

                self._save_knowledge(learned)

                # Registrar aprendizaje sin destruir la evidencia fuente.
                self.cleanup.mark_as_learned(
                    zip_path,
                    knowledge=knowledge[:500],
                    can_delete=False
                )

                print(f"[LEARNING] ✅ ZIP aprendido; fuente conservada")
                return learned

        except Exception as e:
            print(f"[LEARNING] ❌ Error: {e}")
            return None

    # ========== PDF ==========

    def learn_from_pdf(self, pdf_path: Path) -> Optional[LearnedKnowledge]:
        """Aprende de un PDF"""
        print(f"[LEARNING] 📄 Aprendiendo de PDF: {pdf_path.name}")

        if not pdf_path.exists():
            return None

        if not self.tools["pdftotext"]:
            print(f"[LEARNING] ⚠️  pdftotext no disponible")
            return None

        try:
            # Extraer texto del PDF
            result = subprocess.run(
                ["pdftotext", str(pdf_path), "-"],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                text = result.stdout[:5000]  # Primeros 5000 chars

                learned = LearnedKnowledge(
                    source_file=str(pdf_path),
                    source_type="pdf",
                    knowledge=text,
                    metadata={"filename": pdf_path.name},
                    learned_at=datetime.now().timestamp(),
                    original_size_mb=pdf_path.stat().st_size / (1024 * 1024)
                )

                self._save_knowledge(learned)

                # Registrar aprendizaje sin destruir la evidencia fuente.
                self.cleanup.mark_as_learned(pdf_path, text[:500], can_delete=False)

                print(f"[LEARNING] ✅ PDF aprendido; fuente conservada")
                return learned

        except Exception as e:
            print(f"[LEARNING] ❌ Error: {e}")
            return None

    # ========== SAVE KNOWLEDGE ==========

    def _save_knowledge(self, learned: LearnedKnowledge):
        """Guarda conocimiento aprendido en la KB"""
        kb_file = self.learning_dir / f"learned_{learned.source_type}.jsonl"

        try:
            with open(kb_file, 'a') as f:
                record = {
                    "source_file": learned.source_file,
                    "source_type": learned.source_type,
                    "knowledge": learned.knowledge,
                    "metadata": learned.metadata,
                    "learned_at": learned.learned_at,
                    "original_size_mb": learned.original_size_mb
                }
                f.write(json.dumps(record) + '\n')

            print(f"[LEARNING] 💾 Conocimiento guardado en KB")

        except Exception as e:
            print(f"[LEARNING] ⚠️  Error guardando: {e}")

    # ========== SEARCH ==========

    def search_learned(self, query: str, source_type: Optional[str] = None) -> List[Dict]:
        """
        Busca en conocimiento aprendido.

        Args:
            query: Texto a buscar
            source_type: Filtrar por tipo (youtube, vsix, zip, pdf, etc.)

        Returns:
            Lista de conocimientos que coinciden
        """
        results = []
        query_lower = query.lower()

        # Buscar en todos los archivos de KB
        for kb_file in self.learning_dir.glob("learned_*.jsonl"):
            # Filtrar por tipo si se especifica
            if source_type:
                if source_type not in kb_file.name:
                    continue

            try:
                with open(kb_file, 'r') as f:
                    for line in f:
                        if line.strip():
                            record = json.loads(line)

                            # Buscar en knowledge
                            if query_lower in record["knowledge"].lower():
                                results.append(record)

            except Exception:
                pass  # error no crítico, continuar
        return results

# ============================
# SINGLETON
# ============================

_learning_system: Optional[LearningSystem] = None

def get_learning_system() -> LearningSystem:
    """Obtiene instancia singleton"""
    global _learning_system
    if _learning_system is None:
        _learning_system = LearningSystem()
    return _learning_system

# ============================
# TESTING
# ============================

if __name__ == "__main__":
    print("=== EIDOS Learning System Test ===\n")

    learning = get_learning_system()

    # Test de búsqueda (si hay algo aprendido)
    print("\n🔍 Buscando 'youtube' en conocimiento...")
    results = learning.search_learned("youtube")
    print(f"   Resultados: {len(results)}")

    print("\n🎯 Test completado")
