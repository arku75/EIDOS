"""
Channel Directory para EIDOS Gateway
Gestiona mapping de canales y nombres
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

class ChannelDirectory:
    """
    Directorio de canales para resolver nombres a IDs.
    Usado por cron jobs y comandos de mensajería.
    """
    
    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or Path.home() / ".eidos" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "channel_directory.json"
        self._channels: Dict[str, Dict[str, Any]] = {}
        self._load_cache()
        
    def _load_cache(self):
        """Cargar cache desde disco"""
        if self.cache_file.exists():
            try:
                self._channels = json.loads(self.cache_file.read_text())
            except Exception as e:
                logger.error(f"Error loading channel directory: {e}")
                self._channels = {}
    
    def _save_cache(self):
        """Guardar cache a disco"""
        try:
            self.cache_file.write_text(
                json.dumps(self._channels, indent=2, ensure_ascii=False)
            )
        except Exception as e:
            logger.error(f"Error saving channel directory: {e}")
    
    def update_channel(
        self,
        platform: str,
        channel_id: str,
        name: str,
        channel_type: str = "channel",
        metadata: Optional[Dict] = None
    ):
        """Actualizar o añadir canal al directorio"""
        key = f"{platform}:{channel_id}"
        
        self._channels[key] = {
            "platform": platform,
            "channel_id": channel_id,
            "name": name,
            "type": channel_type,
            "metadata": metadata or {}
        }
        
        # Índice por nombre
        name_key = f"{platform}:{name.lower()}"
        if name_key not in self._channels:
            self._channels[name_key] = {"_ref": key}
        
        self._save_cache()
    
    def resolve_channel(self, platform: str, identifier: str) -> Optional[str]:
        """
        Resolver identificador de canal a ID real.
        Soporta: IDs directos, nombres, menciones.
        """
        # Si ya es un ID válido
        key = f"{platform}:{identifier}"
        if key in self._channels and "_ref" not in self._channels[key]:
            return identifier
        
        # Buscar por nombre
        name_key = f"{platform}:{identifier.lower()}"
        if name_key in self._channels:
            entry = self._channels[name_key]
            if "_ref" in entry:
                ref_key = entry["_ref"]
                return self._channels[ref_key]["channel_id"]
        
        return None
    
    def get_channel_info(self, platform: str, channel_id: str) -> Optional[Dict]:
        """Obtener información de un canal"""
        key = f"{platform}:{channel_id}"
        return self._channels.get(key)
    
    def list_channels(self, platform: Optional[str] = None) -> List[Dict]:
        """Listar canales, opcionalmente filtrados por plataforma"""
        channels = []
        for key, data in self._channels.items():
            if "_ref" in data:
                continue
            if platform and data.get("platform") != platform:
                continue
            channels.append(data)
        return channels
    
    def remove_channel(self, platform: str, channel_id: str):
        """Eliminar canal del directorio"""
        key = f"{platform}:{channel_id}"
        if key in self._channels:
            del self._channels[key]
            self._save_cache()

# Singleton
_directory: Optional[ChannelDirectory] = None

def get_channel_directory() -> ChannelDirectory:
    global _directory
    if _directory is None:
        _directory = ChannelDirectory()
    return _directory

def resolve_channel_name(platform: str, identifier: str) -> Optional[str]:
    """Función helper para resolver canales"""
    return get_channel_directory().resolve_channel(platform, identifier)
