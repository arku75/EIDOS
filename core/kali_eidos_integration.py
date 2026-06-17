"""
KALI-EIDOS Integration Module
=============================

Integración completa con la arquitectura KALI-EIDOS:
- Ollama Swap CLI para cambio dinámico de modelos
- Interfaz C++ nativa para Ollama
- Integración con ZeroClaw
"""

import os
import sys
import subprocess
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum

log = logging.getLogger("eidos.kali")


class ModelType(Enum):
    """Tipos de modelo para diferentes tareas."""
    VISION = "llava:13b"  # Para análisis visual
    CODE = "lfm2.5-1.2b-instruct:q4_0"  # Para generación de código
    CHAT = "lfm2.5-1.2b-instruct:q4_0"  # Para conversación general
    FAST = "lfm2.5-1.2b-instruct:q4_0"  # Para respuestas rápidas


@dataclass
class ModelConfig:
    """Configuración de un modelo."""
    name: str
    type: ModelType
    priority: int  # 1 = más prioritario
    memory_mb: int
    context_window: int


class OllamaSwapManager:
    """
    Gestor dinámico de modelos Ollama.
    Cambia el modelo cargado según la tarea actual.
    """
    
    def __init__(self):
        self.current_model: Optional[str] = None
        self.model_configs = {
            ModelType.VISION: ModelConfig(
                name="llava:13b",
                type=ModelType.VISION,
                priority=1,
                memory_mb=8192,
                context_window=4096
            ),
            ModelType.CODE: ModelConfig(
                name="lfm2.5-1.2b-instruct:q4_0",
                type=ModelType.CODE,
                priority=2,
                memory_mb=12288,
                context_window=32768
            ),
            ModelType.CHAT: ModelConfig(
                name="lfm2.5-1.2b-instruct:q4_0",
                type=ModelType.CHAT,
                priority=3,
                memory_mb=12288,
                context_window=32768
            ),
            ModelType.FAST: ModelConfig(
                name="lfm2.5-1.2b-instruct:q4_0",
                type=ModelType.FAST,
                priority=4,
                memory_mb=6144,
                context_window=32768
            )
        }
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        
    def check_ollama_running(self) -> bool:
        """Verifica si Ollama está ejecutándose."""
        try:
            result = subprocess.run(
                ["curl", "-s", f"{self.ollama_url}/api/tags"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def get_loaded_models(self) -> list:
        """Retorna lista de modelos cargados en memoria."""
        try:
            result = subprocess.run(
                ["curl", "-s", f"{self.ollama_url}/api/ps"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                import json
                data = json.loads(result.stdout)
                return [m.get("name", "") for m in data.get("models", [])]
        except Exception as e:
            log.warning(f"Error consultando modelos: {e}")
        return []
    
    def swap_model(self, model_type: ModelType) -> bool:
        """
        Cambia al modelo apropiado para la tarea.
        """
        config = self.model_configs.get(model_type)
        if not config:
            log.error(f"Configuración no encontrada para {model_type}")
            return False
        
        # Verificar si ya está cargado
        loaded = self.get_loaded_models()
        if config.name in loaded:
            log.debug(f"Modelo {config.name} ya cargado")
            self.current_model = config.name
            return True
        
        # Descargar/cargar modelo
        log.info(f"🔄 Swap: Cargando {config.name} ({model_type.value})")
        try:
            result = subprocess.run(
                ["ollama", "run", config.name, "--help"],
                capture_output=True,
                timeout=60
            )
            if result.returncode == 0:
                self.current_model = config.name
                log.info(f"✅ Modelo {config.name} listo")
                return True
        except Exception as e:
            log.error(f"Error cargando modelo: {e}")
        
        return False
    
    def auto_select_model(self, task_description: str) -> ModelType:
        """
        Selecciona automáticamente el mejor modelo para la tarea.
        """
        task_lower = task_description.lower()
        
        # Vision tasks
        if any(kw in task_lower for kw in ["ver", "imagen", "pantalla", "screenshot", "analizar visual"]):
            return ModelType.VISION
        
        # Code tasks
        if any(kw in task_lower for kw in ["código", "python", "script", "programar", "debug", "refactor"]):
            return ModelType.CODE
        
        # Fast tasks
        if any(kw in task_lower for kw in ["rápido", "quick", "simple", "status"]):
            return ModelType.FAST
        
        # Default
        return ModelType.CHAT
    
    def execute_with_optimal_model(self, task: str, prompt: str) -> Optional[str]:
        """
        Ejecuta una tarea con el modelo óptimo.
        """
        model_type = self.auto_select_model(task)
        
        if not self.swap_model(model_type):
            log.warning("No se pudo cambiar modelo, usando actual")
        
        # Ejecutar con Ollama
        try:
            result = subprocess.run(
                ["ollama", "run", self.current_model or model_type.value, prompt],
                capture_output=True,
                text=True,
                timeout=120
            )
            if result.returncode == 0:
                return result.stdout
        except Exception as e:
            log.error(f"Error ejecutando tarea: {e}")
        
        return None


class ZeroClawBridge:
    """
    Puente con ZeroClaw - orquestador ligero.
    """
    
    def __init__(self):
        self.enabled = False
        self.zeroclaw_path = "/opt/zeroclaw"
        
    def check_available(self) -> bool:
        """Verifica si ZeroClaw está instalado."""
        return os.path.exists(self.zeroclaw_path)
    
    def integrate(self) -> bool:
        """Integra ZeroClaw con EIDOS."""
        if not self.check_available():
            log.warning("ZeroClaw no disponible")
            return False
        
        log.info("🔗 Integrando ZeroClaw...")
        self.enabled = True
        return True


class KaliEidosIntegration:
    """
    Integración completa KALI-EIDOS.
    """
    
    def __init__(self):
        self.ollama_swap = OllamaSwapManager()
        self.zeroclaw = ZeroClawBridge()
        self.ghost_mode = os.getenv("EIDOS_MODE") == "ghost"
        
    def initialize(self) -> bool:
        """Inicializa la integración completa."""
        log.info("🚀 Inicializando KALI-EIDOS Integration...")
        
        # Verificar Ollama
        if not self.ollama_swap.check_ollama_running():
            log.error("❌ Ollama no está ejecutándose")
            return False
        
        log.info("✅ Ollama detectado")
        
        # Cargar modelo por defecto
        self.ollama_swap.swap_model(ModelType.CHAT)
        
        # Integrar ZeroClaw si está disponible
        self.zeroclaw.integrate()
        
        # Ghost protocol
        if self.ghost_mode:
            log.info("👻 Ghost Mode activo - Proceso camuflado")
        
        log.info("✅ KALI-EIDOS Integration lista")
        return True
    
    def get_status(self) -> Dict[str, Any]:
        """Retorna estado de la integración."""
        return {
            "ollama_running": self.ollama_swap.check_ollama_running(),
            "current_model": self.ollama_swap.current_model,
            "loaded_models": self.ollama_swap.get_loaded_models(),
            "zeroclaw_enabled": self.zeroclaw.enabled,
            "ghost_mode": self.ghost_mode
        }


# Singleton
_kali_integration: Optional[KaliEidosIntegration] = None

def get_kali_integration() -> KaliEidosIntegration:
    global _kali_integration
    if _kali_integration is None:
        _kali_integration = KaliEidosIntegration()
    return _kali_integration


if __name__ == "__main__":
    # Test
    kali = get_kali_integration()
    if kali.initialize():
        print("\nEstado:")
        import json
        print(json.dumps(kali.get_status(), indent=2))
    else:
        print("❌ Fallo en inicialización")
