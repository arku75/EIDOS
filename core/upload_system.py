"""
EIDOS Auto-Upload System
=========================
Sistema para subir videos a plataformas automáticamente.

Platforms:
- YouTube
- TikTok
- Instagram
"""
from __future__ import annotations

import sys
import time
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

# Añadir root al path
EIDOS_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(EIDOS_ROOT))

from core.eidos_config import get_config

# ============================
# PLATFORM DEFINITIONS
# ============================

class Platform(Enum):
    """Plataformas de video"""
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"

@dataclass
class UploadSpec:
    """Especificación de upload"""
    video_path: Path
    title: str
    description: str
    tags: List[str]
    platform: Platform
    privacy: str = "public"  # public, private, unlisted
    category: Optional[str] = None
    thumbnail: Optional[Path] = None

@dataclass
class UploadResult:
    """Resultado de upload"""
    success: bool
    platform: Platform
    video_id: Optional[str]
    url: Optional[str]
    uploaded_at: float
    error: Optional[str] = None

# ============================
# UPLOAD MANAGER
# ============================

class UploadManager:
    """
    Gestiona uploads a diferentes plataformas.

    Nota: Esta es una implementación base. Para producción
    se requiere configurar APIs y credenciales de cada plataforma.
    """

    def __init__(self):
        self.config = get_config()

        # Directorio de logs de uploads
        self.logs_dir = Path(self.config.paths.logs) / "uploads"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        # Archivo de historial
        self.history_file = self.logs_dir / "upload_history.jsonl"

        print(f"[UPLOAD] 📤 Upload Manager inicializado")

    def upload(self, spec: UploadSpec) -> UploadResult:
        """
        Sube video a plataforma especificada.

        Args:
            spec: UploadSpec con configuración

        Returns:
            UploadResult con resultado del upload
        """
        print(f"[UPLOAD] 📤 Subiendo a {spec.platform.value}: {spec.title}")

        try:
            # Validar que el video existe
            if not spec.video_path.exists():
                return UploadResult(
                    success=False,
                    platform=spec.platform,
                    video_id=None,
                    url=None,
                    uploaded_at=time.time(),
                    error=f"Video no encontrado: {spec.video_path}"
                )

            # Delegar a handler específico de plataforma
            if spec.platform == Platform.YOUTUBE:
                result = self._upload_youtube(spec)
            elif spec.platform == Platform.TIKTOK:
                result = self._upload_tiktok(spec)
            elif spec.platform == Platform.INSTAGRAM:
                result = self._upload_instagram(spec)
            else:
                result = UploadResult(
                    success=False,
                    platform=spec.platform,
                    video_id=None,
                    url=None,
                    uploaded_at=time.time(),
                    error=f"Plataforma no soportada: {spec.platform}"
                )

            # Guardar en historial
            self._save_to_history(spec, result)

            return result

        except Exception as e:
            print(f"[UPLOAD] ❌ Error: {e}")
            return UploadResult(
                success=False,
                platform=spec.platform,
                video_id=None,
                url=None,
                uploaded_at=time.time(),
                error=str(e)
            )

    def _upload_youtube(self, spec: UploadSpec) -> UploadResult:
        """
        Upload a YouTube.

        Requiere: youtube-upload CLI tool o google-api-python-client

        Install: pip install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib
        """
        print(f"[UPLOAD] 🎬 Uploading to YouTube...")

        # TODO: Implementar upload real con YouTube Data API v3
        # Requiere OAuth2 credentials y configuración

        # Por ahora, simulación
        print(f"[UPLOAD] ⚠️  YouTube API no configurada - simulando upload")

        return UploadResult(
            success=True,
            platform=Platform.YOUTUBE,
            video_id="simulated_video_id",
            url=f"https://youtube.com/watch?v=simulated_video_id",
            uploaded_at=time.time(),
            error=None
        )

    def _upload_tiktok(self, spec: UploadSpec) -> UploadResult:
        """
        Upload a TikTok.

        Requiere: TikTok API credentials
        """
        print(f"[UPLOAD] 🎵 Uploading to TikTok...")

        # TODO: Implementar upload real con TikTok API
        print(f"[UPLOAD] ⚠️  TikTok API no configurada - simulando upload")

        return UploadResult(
            success=True,
            platform=Platform.TIKTOK,
            video_id="simulated_tiktok_id",
            url=f"https://tiktok.com/@user/video/simulated_tiktok_id",
            uploaded_at=time.time(),
            error=None
        )

    def _upload_instagram(self, spec: UploadSpec) -> UploadResult:
        """
        Upload a Instagram (Reels).

        Requiere: Instagram Graph API credentials
        """
        print(f"[UPLOAD] 📸 Uploading to Instagram...")

        # TODO: Implementar upload real con Instagram Graph API
        print(f"[UPLOAD] ⚠️  Instagram API no configurada - simulando upload")

        return UploadResult(
            success=True,
            platform=Platform.INSTAGRAM,
            video_id="simulated_insta_id",
            url=f"https://instagram.com/p/simulated_insta_id",
            uploaded_at=time.time(),
            error=None
        )

    def _save_to_history(self, spec: UploadSpec, result: UploadResult):
        """Guarda upload en historial"""
        try:
            history_entry = {
                "timestamp": result.uploaded_at,
                "platform": spec.platform.value,
                "title": spec.title,
                "video_path": str(spec.video_path),
                "success": result.success,
                "video_id": result.video_id,
                "url": result.url,
                "error": result.error
            }

            with open(self.history_file, 'a') as f:
                f.write(json.dumps(history_entry) + '\n')

        except Exception as e:
            print(f"[UPLOAD] ⚠️  Error guardando historial: {e}")

    def get_upload_history(self, limit: int = 100) -> List[Dict]:
        """Obtiene historial de uploads"""
        if not self.history_file.exists():
            return []

        history = []
        with open(self.history_file, 'r') as f:
            for line in f:
                if line.strip():
                    history.append(json.loads(line))

        return history[-limit:]  # Retornar más recientes

    def get_stats(self) -> Dict[str, Any]:
        """Obtiene estadísticas de uploads"""
        history = self.get_upload_history()

        if not history:
            return {"total": 0}

        total = len(history)
        successful = sum(1 for h in history if h["success"])
        failed = total - successful

        by_platform = {}
        for entry in history:
            platform = entry["platform"]
            by_platform[platform] = by_platform.get(platform, 0) + 1

        return {
            "total": total,
            "successful": successful,
            "failed": failed,
            "success_rate": (successful / total * 100) if total > 0 else 0,
            "by_platform": by_platform
        }

# ============================
# BATCH UPLOADER
# ============================

class BatchUploader:
    """Sube múltiples videos en batch"""

    def __init__(self):
        self.manager = UploadManager()

    def upload_batch(
        self,
        specs: List[UploadSpec],
        delay_between: int = 60
    ) -> List[UploadResult]:
        """
        Sube múltiples videos con delay entre cada uno.

        Args:
            specs: Lista de UploadSpec
            delay_between: Segundos entre uploads

        Returns:
            Lista de UploadResult
        """
        print(f"[BATCH] 📤 Uploading batch of {len(specs)} videos")

        results = []

        for i, spec in enumerate(specs, 1):
            print(f"\n[BATCH] ═══ Video {i}/{len(specs)} ═══")

            result = self.manager.upload(spec)
            results.append(result)

            if result.success:
                print(f"[BATCH] ✅ Upload exitoso: {result.url}")
            else:
                print(f"[BATCH] ❌ Upload falló: {result.error}")

            # Esperar entre uploads (respetar rate limits)
            if i < len(specs):
                print(f"[BATCH] ⏸️  Esperando {delay_between}s...")
                time.sleep(delay_between)

        successful = sum(1 for r in results if r.success)
        print(f"\n[BATCH] ✅ Batch completado: {successful}/{len(specs)} exitosos")

        return results

# ============================
# SINGLETON
# ============================

_upload_manager: Optional[UploadManager] = None

def get_upload_manager() -> UploadManager:
    """Obtiene instancia singleton"""
    global _upload_manager
    if _upload_manager is None:
        _upload_manager = UploadManager()
    return _upload_manager

# ============================
# TESTING
# ============================

if __name__ == "__main__":
    print("=== EIDOS Upload System Test ===\n")

    manager = get_upload_manager()

    # Crear video de prueba (path ficticio)
    test_video = Path("/tmp/test_video.mp4")

    # Crear spec de upload
    spec = UploadSpec(
        video_path=test_video,
        title="Test Video - EIDOS Auto Upload",
        description="Video generado automáticamente por EIDOS AI",
        tags=["ai", "automation", "eidos"],
        platform=Platform.YOUTUBE,
        privacy="unlisted"
    )

    print(f"📋 Upload Spec:")
    print(f"   Platform: {spec.platform.value}")
    print(f"   Title: {spec.title}")
    print(f"   Tags: {', '.join(spec.tags)}")

    # Nota: Como el video no existe, fallará
    print(f"\n📤 Attempting upload...")
    result = manager.upload(spec)

    if result.success:
        print(f"✅ Upload exitoso!")
        print(f"   URL: {result.url}")
    else:
        print(f"❌ Upload falló: {result.error}")

    # Ver estadísticas
    print(f"\n📊 Upload Stats:")
    stats = manager.get_stats()
    for key, value in stats.items():
        print(f"   {key}: {value}")

    print("\n🎯 Test completado")
