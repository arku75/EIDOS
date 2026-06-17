"""
EIDOS Cleanup System - Sistema de Limpieza Automática
======================================================
Gestiona el espacio en disco eliminando archivos temporales y aprendidos.

Filosofía:
- Si EIDOS ya "aprendió" un archivo, lo borra para liberar espacio
- Guarda solo la esencia/conocimiento extraído
- Mantiene el sistema limpio automáticamente

⚠️ PROTECCIÓN CRÍTICA:
- NUNCA borra archivos del usuario (pre-existentes)
- SOLO borra archivos que EIDOS mismo descargó
- Usa directorio de descargas específico: ~/.eidos/downloads/
"""
from __future__ import annotations

import sys
import os
import shutil
import hashlib
import json
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime

# Añadir root al path
EIDOS_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(EIDOS_ROOT))

from core.eidos_config import get_config

# ============================
# DATA MODELS
# ============================

@dataclass
class LearnedFile:
    """Archivo que ya fue aprendido"""
    file_path: str
    file_hash: str  # MD5 hash del archivo
    learned_at: float
    knowledge_extracted: str  # Resumen de lo aprendido
    original_size_mb: float
    can_delete: bool = True

@dataclass
class CleanupStats:
    """Estadísticas de limpieza"""
    files_cleaned: int
    space_freed_mb: float
    files_kept: int
    duration_seconds: float

# ============================
# CLEANUP MANAGER
# ============================

class CleanupManager:
    """
    Gestiona limpieza automática de archivos.

    Reglas:
    1. Si EIDOS "aprendió" un archivo → guardar knowledge → borrar archivo
    2. Archivos temporales (frames, cache) → borrar automáticamente
    3. Videos/audio generados → mantener solo los últimos N
    4. Mantener siempre configuración y objetivos

    🛡️ PROTECCIÓN:
    - SOLO borra archivos en directorios de EIDOS:
      * ~/.eidos/downloads/  (lo que EIDOS descarga)
      * ~/.eidos/frames/     (temporales)
      * ~/.eidos/videos/     (generados)
      * ~/.eidos/voice_output/ (generados)
    - NUNCA toca archivos del usuario en:
      * /home/ser/ (TODO el directorio del usuario está protegido)
      * Cualquier archivo fuera de ~/.eidos/
    """

    def __init__(self):
        self.config = get_config()

        # Directorio de conocimiento aprendido
        self.learned_dir = Path(self.config.paths.knowledge) / "learned_files"
        self.learned_dir.mkdir(parents=True, exist_ok=True)

        # Archivo de registro de aprendidos
        self.learned_registry = self.learned_dir / "learned_files.jsonl"

        # 🆕 Directorio de descargas de EIDOS (solo aquí se borra)
        self.downloads_dir = Path.home() / ".eidos" / "downloads"
        self.downloads_dir.mkdir(parents=True, exist_ok=True)

        # 🆕 Directorios seguros para borrar (solo lo que EIDOS generó)
        self.safe_delete_dirs = [
            self.downloads_dir,
            Path(self.config.paths.frames),
            Path(self.config.paths.videos),
            Path(self.config.paths.home) / "voice_output",
            Path(self.config.paths.home) / "voice_cache",
            Path(self.config.paths.home) / "window_captures",
        ]

        # Configuración de limpieza
        self.auto_cleanup = self.config.system.auto_cleanup

        print(f"[CLEANUP] 🧹 Cleanup Manager inicializado")
        print(f"[CLEANUP]    Auto-cleanup: {self.auto_cleanup}")
        print(f"[CLEANUP]    🛡️  Protección: Solo borra archivos en {len(self.safe_delete_dirs)} dirs de EIDOS")

    def _is_safe_to_delete(self, file_path: Path) -> bool:
        """
        🛡️ PROTECCIÓN CRÍTICA: Verifica si es seguro borrar un archivo.

        SOLO permite borrar archivos en directorios de EIDOS:
        - ~/.eidos/downloads/
        - ~/.eidos/frames/
        - ~/.eidos/videos/
        - ~/.eidos/voice_output/
        - ~/.eidos/voice_cache/
        - ~/.eidos/window_captures/

        NUNCA borra archivos del usuario en:
        - /home/ser/ (TODO el directorio del usuario)
        - Cualquier archivo fuera de ~/.eidos/
        """
        file_path = file_path.resolve()  # Ruta absoluta

        # 🛡️ PROTECCIÓN 1: Verificar que NO esté en /home/ser/ (excepto ~/.eidos/)
        user_home = Path.home()  # /home/ser
        eidos_home = user_home / ".eidos"

        # Si el archivo está en /home/ser/ pero NO en /home/ser/.eidos/, PROTEGIDO
        try:
            # Intentar obtener ruta relativa desde /home/ser/
            rel_from_user = file_path.relative_to(user_home)
            # Si la ruta NO empieza con .eidos, está en directorio del usuario → PROTEGIDO
            if not str(rel_from_user).startswith(".eidos"):
                return False  # ❌ NO borrar - es archivo del usuario
        except ValueError:
            # No está en /home/ser/, continuar verificando
            pass

        # 🛡️ PROTECCIÓN 2: Verificar si el archivo está en algún directorio seguro
        for safe_dir in self.safe_delete_dirs:
            try:
                safe_dir = safe_dir.resolve()
                # Si el archivo está dentro de un directorio seguro
                if file_path.is_relative_to(safe_dir):
                    return True  # ✅ Borrar - está en directorio de EIDOS
            except (ValueError, AttributeError):
                # is_relative_to no existe en Python 3.8, usar otro método
                try:
                    file_path.relative_to(safe_dir)
                    return True  # ✅ Borrar - está en directorio de EIDOS
                except ValueError:
                    continue

        return False  # ❌ NO borrar - no está en ningún directorio seguro

    def mark_as_learned(
        self,
        file_path: Path,
        knowledge: str,
        can_delete: bool = True
    ) -> LearnedFile:
        """
        Marca un archivo como "aprendido" y opcionalmente lo borra.

        🛡️ PROTECCIÓN: Solo borra archivos en directorios de EIDOS,
                       NUNCA archivos del usuario.

        Args:
            file_path: Path al archivo
            knowledge: Conocimiento extraído (resumen, texto, etc.)
            can_delete: Si True, borra el archivo después (si es seguro)

        Returns:
            LearnedFile con metadata
        """
        if not file_path.exists():
            print(f"[CLEANUP] ⚠️  Archivo no existe: {file_path}")
            return None

        print(f"[CLEANUP] 📚 Marcando como aprendido: {file_path.name}")

        # Calcular hash del archivo
        file_hash = self._calculate_hash(file_path)

        # Tamaño original
        size_mb = file_path.stat().st_size / (1024 * 1024)

        # Crear registro
        learned = LearnedFile(
            file_path=str(file_path),
            file_hash=file_hash,
            learned_at=time.time(),
            knowledge_extracted=knowledge,
            original_size_mb=size_mb,
            can_delete=can_delete
        )

        # Guardar en registro
        self._save_learned(learned)

        # 🛡️ PROTECCIÓN: Verificar si es seguro borrar
        if can_delete and self.auto_cleanup:
            if self._is_safe_to_delete(file_path):
                try:
                    file_path.unlink()
                    print(f"[CLEANUP] 🗑️  Archivo borrado ({size_mb:.2f} MB liberados)")
                except Exception as e:
                    print(f"[CLEANUP] ⚠️  No se pudo borrar: {e}")
            else:
                print(f"[CLEANUP] 🛡️  PROTEGIDO: Archivo del usuario, NO se borra")
                print(f"[CLEANUP]    (Solo se borran archivos en ~/.eidos/downloads/)")

        return learned

    def _calculate_hash(self, file_path: Path) -> str:
        """Calcula MD5 hash de un archivo"""
        md5 = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                md5.update(chunk)
        return md5.hexdigest()

    def _save_learned(self, learned: LearnedFile):
        """Guarda registro de archivo aprendido"""
        try:
            with open(self.learned_registry, 'a') as f:
                record = {
                    "file_path": learned.file_path,
                    "file_hash": learned.file_hash,
                    "learned_at": learned.learned_at,
                    "knowledge_extracted": learned.knowledge_extracted[:500],  # Primeros 500 chars
                    "original_size_mb": learned.original_size_mb,
                    "deleted": learned.can_delete and self.auto_cleanup
                }
                f.write(json.dumps(record) + '\n')
        except Exception as e:
            print(f"[CLEANUP] ⚠️  Error guardando registro: {e}")

    def is_already_learned(self, file_path: Path) -> bool:
        """Verifica si un archivo ya fue aprendido"""
        if not file_path.exists():
            return False

        file_hash = self._calculate_hash(file_path)

        if not self.learned_registry.exists():
            return False

        try:
            with open(self.learned_registry, 'r') as f:
                for line in f:
                    if line.strip():
                        record = json.loads(line)
                        if record.get("file_hash") == file_hash:
                            return True
        except Exception:
            pass  # error no crítico, continuar
        return False

    def cleanup_temp_files(self) -> CleanupStats:
        """
        Limpia archivos temporales.

        Borra:
        - Frames en ~/.eidos/frames/
        - Caché de voz en ~/.eidos/voice_cache/
        - Archivos .tmp
        """
        print(f"[CLEANUP] 🧹 Limpiando archivos temporales...")

        start_time = time.time()
        files_cleaned = 0
        space_freed = 0.0

        # Directorios temporales
        temp_dirs = [
            Path(self.config.paths.home) / "frames",
            Path(self.config.paths.home) / "voice_cache"
        ]

        for temp_dir in temp_dirs:
            if temp_dir.exists():
                for file in temp_dir.glob("*"):
                    if file.is_file():
                        try:
                            size = file.stat().st_size / (1024 * 1024)
                            file.unlink()
                            files_cleaned += 1
                            space_freed += size
                        except Exception as e:
                            print(f"[CLEANUP] ⚠️  Error borrando {file.name}: {e}")

        duration = time.time() - start_time

        stats = CleanupStats(
            files_cleaned=files_cleaned,
            space_freed_mb=space_freed,
            files_kept=0,
            duration_seconds=duration
        )

        print(f"[CLEANUP] ✅ Limpieza completada:")
        print(f"[CLEANUP]    Archivos borrados: {files_cleaned}")
        print(f"[CLEANUP]    Espacio liberado: {space_freed:.2f} MB")
        print(f"[CLEANUP]    Tiempo: {duration:.2f}s")

        return stats

    def cleanup_old_videos(self, keep_latest: int = 10) -> CleanupStats:
        """
        Limpia videos viejos, mantiene solo los últimos N.

        Args:
            keep_latest: Número de videos a mantener
        """
        print(f"[CLEANUP] 🎬 Limpiando videos viejos (mantener últimos {keep_latest})...")

        start_time = time.time()
        files_cleaned = 0
        space_freed = 0.0

        videos_dir = Path(self.config.paths.videos)
        if not videos_dir.exists():
            return CleanupStats(0, 0.0, 0, 0.0)

        # Obtener todos los videos ordenados por fecha
        videos = sorted(
            videos_dir.glob("*.mp4"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        # Mantener solo los últimos N
        videos_to_delete = videos[keep_latest:]

        for video in videos_to_delete:
            try:
                size = video.stat().st_size / (1024 * 1024)
                video.unlink()
                files_cleaned += 1
                space_freed += size
                print(f"[CLEANUP] 🗑️  Borrado: {video.name} ({size:.2f} MB)")
            except Exception as e:
                print(f"[CLEANUP] ⚠️  Error: {e}")

        duration = time.time() - start_time

        stats = CleanupStats(
            files_cleaned=files_cleaned,
            space_freed_mb=space_freed,
            files_kept=len(videos) - len(videos_to_delete),
            duration_seconds=duration
        )

        print(f"[CLEANUP] ✅ Videos limpiados: {files_cleaned}")
        print(f"[CLEANUP]    Espacio liberado: {space_freed:.2f} MB")
        print(f"[CLEANUP]    Videos mantenidos: {stats.files_kept}")

        return stats

    def cleanup_old_captures(self, keep_latest: int = 20) -> CleanupStats:
        """
        Limpia capturas de ventanas viejas.

        Args:
            keep_latest: Número de capturas a mantener
        """
        print(f"[CLEANUP] 📸 Limpiando capturas viejas (mantener últimas {keep_latest})...")

        start_time = time.time()
        files_cleaned = 0
        space_freed = 0.0

        captures_dir = Path(self.config.paths.home) / "window_captures"
        if not captures_dir.exists():
            return CleanupStats(0, 0.0, 0, 0.0)

        # Obtener todas las capturas ordenadas por fecha
        captures = sorted(
            captures_dir.glob("*.png"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        # Mantener solo las últimas N
        captures_to_delete = captures[keep_latest:]

        for capture in captures_to_delete:
            try:
                size = capture.stat().st_size / (1024 * 1024)
                capture.unlink()
                files_cleaned += 1
                space_freed += size
            except Exception as e:
                print(f"[CLEANUP] ⚠️  Error: {e}")

        duration = time.time() - start_time

        stats = CleanupStats(
            files_cleaned=files_cleaned,
            space_freed_mb=space_freed,
            files_kept=len(captures) - len(captures_to_delete),
            duration_seconds=duration
        )

        print(f"[CLEANUP] ✅ Capturas limpiadas: {files_cleaned}")
        print(f"[CLEANUP]    Espacio liberado: {space_freed:.2f} MB")

        return stats

    def full_cleanup(self) -> Dict[str, CleanupStats]:
        """
        Limpieza completa del sistema.

        Returns:
            Dict con estadísticas de cada tipo de limpieza
        """
        print(f"[CLEANUP] 🧹 LIMPIEZA COMPLETA DEL SISTEMA")
        print(f"[CLEANUP] " + "="*60)

        results = {}

        # 1. Archivos temporales
        results['temp_files'] = self.cleanup_temp_files()

        # 2. Videos viejos
        results['old_videos'] = self.cleanup_old_videos(keep_latest=10)

        # 3. Capturas viejas
        results['old_captures'] = self.cleanup_old_captures(keep_latest=20)

        # Resumen total
        total_files = sum(r.files_cleaned for r in results.values())
        total_space = sum(r.space_freed_mb for r in results.values())

        print(f"\n[CLEANUP] " + "="*60)
        print(f"[CLEANUP] 📊 RESUMEN TOTAL:")
        print(f"[CLEANUP]    Archivos borrados: {total_files}")
        print(f"[CLEANUP]    Espacio liberado: {total_space:.2f} MB")
        print(f"[CLEANUP] " + "="*60)

        return results

    def get_disk_usage(self) -> Dict[str, float]:
        """Obtiene uso de disco de EIDOS"""
        eidos_home = Path(self.config.paths.home)

        usage = {}

        # Calcular tamaño de cada directorio
        directories = {
            "videos": "videos",
            "frames": "frames",
            "window_captures": "window_captures",
            "voice_output": "voice_output",
            "voice_cache": "voice_cache",
            "knowledge": "knowledge",
            "logs": "logs"
        }

        for name, dir_name in directories.items():
            dir_path = eidos_home / dir_name
            if dir_path.exists():
                total_size = sum(
                    f.stat().st_size
                    for f in dir_path.rglob("*")
                    if f.is_file()
                )
                usage[name] = total_size / (1024 * 1024)  # MB
            else:
                usage[name] = 0.0

        # Total
        usage['total'] = sum(usage.values())

        return usage

# ============================
# SINGLETON
# ============================

_cleanup_manager: Optional[CleanupManager] = None

def get_cleanup_manager() -> CleanupManager:
    """Obtiene instancia singleton"""
    global _cleanup_manager
    if _cleanup_manager is None:
        _cleanup_manager = CleanupManager()
    return _cleanup_manager

# ============================
# TESTING
# ============================

if __name__ == "__main__":
    print("=== EIDOS Cleanup System Test ===\n")

    manager = get_cleanup_manager()

    # Ver uso de disco actual
    print("\n📊 Uso de disco actual:")
    usage = manager.get_disk_usage()
    for name, size_mb in usage.items():
        print(f"   {name}: {size_mb:.2f} MB")

    # Test de marcar como aprendido
    print("\n📚 Test: Marcar archivo como aprendido")
    test_file = Path("/tmp/test_learned.txt")
    test_file.write_text("Contenido de prueba para EIDOS")

    learned = manager.mark_as_learned(
        test_file,
        knowledge="Este es un archivo de prueba. Contenido aprendido: test",
        can_delete=True
    )

    if learned:
        print(f"✅ Archivo marcado como aprendido")
        print(f"   Hash: {learned.file_hash}")
        print(f"   Tamaño: {learned.original_size_mb:.4f} MB")

    # Verificar si ya fue aprendido
    if not test_file.exists():
        print(f"✅ Archivo fue borrado automáticamente")

    # Limpieza de temporales
    print("\n🧹 Test: Limpieza de archivos temporales")
    stats = manager.cleanup_temp_files()

    print("\n🎯 Test completado")
