"""
EIDOS Configuration System - Sistema de Configuración Centralizado
==================================================================
Gestiona todas las configuraciones de EIDOS desde un único punto.
"""
from __future__ import annotations

import os
import json
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Any, Optional
from pathlib import Path

# ============================
# PATHS - Rutas del sistema
# ============================
EIDOS_ROOT = Path(__file__).parent.parent.parent.resolve()
EIDOS_HOME = Path.home() / ".eidos"

# Crear directorios necesarios
EIDOS_HOME.mkdir(exist_ok=True)
(EIDOS_HOME / "videos").mkdir(exist_ok=True)
(EIDOS_HOME / "frames").mkdir(exist_ok=True)
(EIDOS_HOME / "logs").mkdir(exist_ok=True)
(EIDOS_HOME / "objectives").mkdir(exist_ok=True)
(EIDOS_HOME / "knowledge").mkdir(exist_ok=True)

CONFIG_FILE = EIDOS_HOME / "config.json"

# ============================
# DATACLASSES - Configuración
# ============================

@dataclass
class PathsConfig:
    """Configuración de rutas del sistema"""
    root: str = str(EIDOS_ROOT)
    home: str = str(EIDOS_HOME)
    videos: str = str(EIDOS_HOME / "videos")
    frames: str = str(EIDOS_HOME / "frames")
    logs: str = str(EIDOS_HOME / "logs")
    objectives: str = str(EIDOS_HOME / "objectives")
    knowledge: str = str(EIDOS_HOME / "knowledge")

@dataclass
class ModelConfig:
    """Configuración de modelos de Ollama"""
    text_generation: str = "deepseek-r1:14b"
    vision_fast: str = "moondream:latest"
    vision_detailed: str = "moondream:latest"
    code_generation: str = "lfm2.5-1.2b-instruct:q4_0"
    embeddings: str = "nomic-embed-text"
    tool_call: str = "deepseek-r1:14b"

@dataclass
class VoiceConfig:
    """Configuración del sistema de voz (Bark - postponed)"""
    enabled: bool = False
    model: str = "bark"
    language: str = "es"  # Spanish por defecto
    speaker: str = "v2/es_speaker_6"  # Voz masculina española
    use_small: bool = True  # Usar modelo pequeño para RAM
    output_dir: str = str(EIDOS_HOME / "voice_output")

@dataclass
class WindowConfig:
    """Configuración del window focus manager (postponed)"""
    enabled: bool = False
    focus_mode: str = "window"  # "window" o "screen"
    target_apps: List[str] = field(default_factory=lambda: ["VSCode", "Terminal", "Browser"])
    capture_fps: int = 1  # Capturas por segundo

@dataclass
class AutonomyConfig:
    """Configuración del modo LIBRE (autonomía)"""
    enabled: bool = False
    mode: str = "PLAN"  # PLAN, EDIT, PLAN+EDIT, LIBRE
    auto_objectives: bool = False  # Crear objetivos propios
    max_autonomous_actions: int = 10  # Límite de acciones sin input
    observation_learning: bool = False  # Aprender observando

@dataclass
class SystemConfig:
    """Configuración del sistema"""
    language: str = "es"  # Idioma principal
    typo_tolerance: bool = True  # Tolerar errores de escritura
    low_ram_mode: bool = True  # Optimización para RAM baja
    max_ram_mb: int = 2048  # Límite de RAM en MB
    log_level: str = "INFO"  # DEBUG, INFO, WARNING, ERROR
    auto_cleanup: bool = True  # Limpieza automática de archivos temporales

@dataclass
class MoltbookConfig:
    """Configuración de moltbook.com connector (postponed)"""
    enabled: bool = False
    url: str = "https://moltbook.com"
    learning_mode: bool = False
    auto_share_ideas: bool = False

@dataclass
class BrowserConfig:
    """Configuración del navegador autónomo"""
    enabled: bool = True
    browser_type: str = "playwright"  # playwright o selenium
    headless: bool = True  # Sin ventana visible
    auto_research: bool = True  # Investigar automáticamente
    allowed_domains: List[str] = field(default_factory=lambda: [
        "*.wikipedia.org", "*.github.com", "*.stackoverflow.com",
        "*.python.org", "*.mozilla.org", "*.w3.org"
    ])

@dataclass
class SecurityConfig:
    """Configuración del sistema de seguridad y ethical hacking"""
    enabled: bool = True
    auto_learn_courses: bool = True
    max_course_size_mb: int = 50
    require_permission_for_actions: bool = True
    sandbox_mode: bool = False
    allowed_targets: List[str] = field(default_factory=lambda: ["localhost", "127.0.0.1", "::1", "*.local"])
    forbidden_actions: List[str] = field(default_factory=lambda: [
        "unauthorized_access", "data_theft", "service_disruption_public",
        "credential_harvesting_bulk", "malware_distribution"
    ])
    require_permission: List[str] = field(default_factory=lambda: [
        "network_scanning", "exploitation_attempts", "credential_testing"
    ])

@dataclass
class EidosConfig:
    """Configuración completa de EIDOS"""
    paths: PathsConfig = field(default_factory=PathsConfig)
    models: ModelConfig = field(default_factory=ModelConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    window: WindowConfig = field(default_factory=WindowConfig)
    autonomy: AutonomyConfig = field(default_factory=AutonomyConfig)
    system: SystemConfig = field(default_factory=SystemConfig)
    moltbook: MoltbookConfig = field(default_factory=MoltbookConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)

    def to_dict(self) -> Dict[str, Any]:
        """Convierte la configuración a diccionario"""
        return {
            "paths": asdict(self.paths),
            "models": asdict(self.models),
            "voice": asdict(self.voice),
            "window": asdict(self.window),
            "autonomy": asdict(self.autonomy),
            "system": asdict(self.system),
            "moltbook": asdict(self.moltbook),
            "security": asdict(self.security),
            "browser": asdict(self.browser),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EidosConfig':
        """Crea configuración desde diccionario"""
        return cls(
            paths=PathsConfig(**data.get("paths", {})),
            models=ModelConfig(**data.get("models", {})),
            voice=VoiceConfig(**data.get("voice", {})),
            window=WindowConfig(**data.get("window", {})),
            autonomy=AutonomyConfig(**data.get("autonomy", {})),
            system=SystemConfig(**data.get("system", {})),
            moltbook=MoltbookConfig(**data.get("moltbook", {})),
            security=SecurityConfig(**data.get("security", {})),
            browser=BrowserConfig(**data.get("browser", {})),
        )

    def save(self, path: Optional[Path] = None):
        """Guarda la configuración a archivo JSON"""
        target = path or CONFIG_FILE
        with open(target, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"[CONFIG] ✅ Configuración guardada en {target}")

    @classmethod
    def load(cls, path: Optional[Path] = None) -> 'EidosConfig':
        """Carga la configuración desde archivo JSON"""
        target = path or CONFIG_FILE
        if not target.exists():
            print(f"[CONFIG] ⚠️  No existe {target}, usando configuración por defecto")
            config = cls()
            config.save()
            return config

        with open(target, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"[CONFIG] ✅ Configuración cargada desde {target}")
        return cls.from_dict(data)

# ============================
# SINGLETON - Instancia global
# ============================

_config_instance: Optional[EidosConfig] = None

def get_config() -> EidosConfig:
    """Obtiene la instancia global de configuración"""
    global _config_instance
    if _config_instance is None:
        _config_instance = EidosConfig.load()
    return _config_instance

def reload_config() -> EidosConfig:
    """Recarga la configuración desde disco"""
    global _config_instance
    _config_instance = EidosConfig.load()
    return _config_instance

def save_config(config: Optional[EidosConfig] = None):
    """Guarda la configuración actual"""
    global _config_instance
    target = config or _config_instance
    if target is None:
        target = EidosConfig()
        _config_instance = target
    target.save()

# ============================
# UTILIDADES
# ============================

def update_config(**kwargs):
    """Actualiza configuración con valores específicos

    Ejemplo:
        update_config(autonomy_enabled=True, autonomy_mode="LIBRE")
    """
    config = get_config()

    for key, value in kwargs.items():
        parts = key.split('_', 1)
        if len(parts) != 2:
            continue

        section, field = parts
        if hasattr(config, section):
            section_obj = getattr(config, section)
            if hasattr(section_obj, field):
                setattr(section_obj, field, value)
                print(f"[CONFIG] ✏️  {section}.{field} = {value}")

    save_config(config)

def get_model(task_type: str) -> str:
    """Obtiene el modelo para un tipo de tarea específico"""
    config = get_config()
    model_map = {
        "text": config.models.text_generation,
        "vision_fast": config.models.vision_fast,
        "vision_detailed": config.models.vision_detailed,
        "code": config.models.code_generation,
        "embeddings": config.models.embeddings,
        "tool": config.models.tool_call,
    }
    return model_map.get(task_type, config.models.text_generation)

def get_path(path_type: str) -> str:
    """Obtiene una ruta específica del sistema"""
    config = get_config()
    return getattr(config.paths, path_type, str(EIDOS_HOME))

# ============================
# TESTING
# ============================

if __name__ == "__main__":
    print("=== EIDOS Configuration System Test ===\n")

    # Crear configuración por defecto
    config = EidosConfig()
    print("✅ Configuración creada")

    # Guardar
    config.save()
    print("✅ Configuración guardada")

    # Cargar
    loaded = EidosConfig.load()
    print("✅ Configuración cargada")

    # Usar singleton
    cfg = get_config()
    print(f"\n📋 Modo actual: {cfg.autonomy.mode}")
    print(f"📋 Idioma: {cfg.system.language}")
    print(f"📋 Low RAM mode: {cfg.system.low_ram_mode}")
    print(f"📋 Modelo de texto: {cfg.models.text_generation}")

    # Actualizar
    update_config(autonomy_enabled=True, autonomy_mode="LIBRE")

    # Verificar
    cfg_reloaded = reload_config()
    print(f"\n✅ Modo después de actualizar: {cfg_reloaded.autonomy.mode}")
    print(f"✅ Autonomía habilitada: {cfg_reloaded.autonomy.enabled}")

    print("\n🎯 Test completado exitosamente")
