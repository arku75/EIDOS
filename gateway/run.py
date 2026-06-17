"""
EIDOS Gateway Runner
Adaptado de Hermes - Servicio de gateway multi-canal
"""

import asyncio
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Any, Set
from dataclasses import dataclass, field
from datetime import datetime

# Setup logging
logger = logging.getLogger(__name__)

from .session import SessionManager, get_session_manager, SessionSource, Platform
from .platforms.base import BasePlatformAdapter, PlatformMessage, get_adapter

# Colony singleton (lazy, thread-safe)
_colony = None
_colony_lock = threading.Lock()

def _get_colony():
    global _colony
    if _colony is None:
        with _colony_lock:
            if _colony is None:
                try:
                    from core.colony_community import ColonyCommunity
                except ImportError:
                    from ..core.colony_community import ColonyCommunity
                _colony = ColonyCommunity()
    return _colony


@dataclass
class GatewayConfig:
    """Configuración del Gateway EIDOS"""
    port: int = 18789
    host: str = "127.0.0.1"
    enabled_platforms: List[str] = field(default_factory=list)
    platform_configs: Dict[str, Dict] = field(default_factory=dict)
    session_timeout: int = 3600  # 1 hora
    max_concurrent_sessions: int = 128
    log_level: str = "info"
    
    @classmethod
    def from_dict(cls, data: Dict) -> "GatewayConfig":
        return cls(
            port=data.get("port", 18789),
            host=data.get("host", "127.0.0.1"),
            enabled_platforms=data.get("enabled_platforms", ["local"]),
            platform_configs=data.get("platform_configs", {}),
            session_timeout=data.get("session_timeout", 3600),
            max_concurrent_sessions=data.get("max_concurrent_sessions", 128),
            log_level=data.get("log_level", "info"),
        )

class GatewayRunner:
    """
    Gateway principal de EIDOS - Gestiona múltiples plataformas y sesiones.
    """
    
    def __init__(self, config: Optional[GatewayConfig] = None):
        self.config = config or GatewayConfig()
        self.session_manager = get_session_manager()
        self.adapters: Dict[str, BasePlatformAdapter] = {}
        self._running = False
        self._stop_event = threading.Event()
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        
    @property
    def is_running(self) -> bool:
        return self._running
    
    def _setup_signal_handlers(self):
        """Configurar handlers para señales de terminación"""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            self._stop_event.set()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    async def _handle_incoming_message(self, message: PlatformMessage, platform: str):
        """Procesar mensaje entrante de cualquier plataforma"""
        try:
            # Crear/obtener sesión
            source = SessionSource(
                platform=Platform(platform),
                chat_id=message.chat_id,
                chat_name=message.chat_name,
                chat_type=message.chat_type,
                user_id=message.sender_id,
                user_name=message.sender_name,
                thread_id=message.thread_id,
            )
            
            session = self.session_manager.get_or_create_session(
                source=source,
                session_key=f"{platform}:{message.chat_id}:{message.thread_id or 'main'}"
            )
            
            logger.info(f"Message from {platform}: {message.sender_name} - {message.text[:50]}...")

            if message.sender_name.lower() == "ser":
                prompt = f"[Mensaje desde {platform} de SER]: {message.text}"
            else:
                prompt = f"[Mensaje desde {platform} de {message.sender_name}]: {message.text}"

            try:
                loop = asyncio.get_event_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, _get_colony().deliberate, prompt),
                    timeout=60,
                )
                response_text = result.get("response") or result.get("text") or str(result)
            except asyncio.TimeoutError:
                response_text = "Colony no respondió a tiempo (timeout 60s)"
            except Exception as colony_err:
                logger.error(f"Colony error: {colony_err}")
                response_text = f"Error de Colony: {colony_err}"
            
            # Enviar respuesta
            from .platforms.base import PlatformResponse
            response = PlatformResponse(
                text=response_text,
                chat_id=message.chat_id,
                thread_id=message.thread_id,
                reply_to_message_id=message.message_id,
            )
            
            adapter = self.adapters.get(platform)
            if adapter:
                await adapter.send_message(response)
                
        except Exception as e:
            logger.error(f"Error handling message from {platform}: {e}")
    
    async def _init_adapters(self) -> bool:
        """Inicializar todos los adapters configurados"""
        success = False
        
        for platform in self.config.enabled_platforms:
            try:
                platform_config = self.config.platform_configs.get(platform, {})
                adapter = get_adapter(platform, platform_config)
                
                if adapter is None:
                    logger.warning(f"No adapter available for platform: {platform}")
                    continue
                
                # Setup message handler
                async def handler(msg, plat=platform):
                    await self._handle_incoming_message(msg, plat)
                
                adapter.set_message_handler(handler)
                
                # Start adapter
                if await adapter.start():
                    self.adapters[platform] = adapter
                    logger.info(f"✅ Adapter started: {platform}")
                    success = True
                else:
                    logger.error(f"❌ Failed to start adapter: {platform}")
                    
            except Exception as e:
                logger.error(f"❌ Error initializing {platform}: {e}")
                
        return success
    
    async def _shutdown_adapters(self):
        """Detener todos los adapters"""
        for platform, adapter in list(self.adapters.items()):
            try:
                await adapter.stop()
                logger.info(f"Stopped adapter: {platform}")
            except Exception as e:
                logger.error(f"Error stopping {platform}: {e}")
        
        self.adapters.clear()
    
    async def run(self) -> bool:
        """
        Ejecutar el gateway hasta que se reciba señal de parada.
        Returns True si ejecutó correctamente, False si hubo error crítico.
        """
        logger.info("🚀 Starting EIDOS Gateway...")
        
        self._setup_signal_handlers()
        self._main_loop = asyncio.get_running_loop()
        
        # Inicializar adapters
        if not await self._init_adapters():
            logger.error("No adapters could be started")
            return False
        
        self._running = True
        logger.info(f"✅ Gateway running on {self.config.host}:{self.config.port}")
        logger.info(f"Enabled platforms: {list(self.adapters.keys())}")
        
        # Main loop - esperar señal de parada
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(1)
                
                # Cleanup de sesiones expiradas cada 60s
                # TODO: Implementar cleanup
                
        except asyncio.CancelledError:
            logger.info("Main loop cancelled")
        finally:
            self._running = False
            await self._shutdown_adapters()
            logger.info("🛑 Gateway stopped")
        
        return True
    
    def stop(self):
        """Solicitar parada del gateway"""
        logger.info("Stop requested")
        self._stop_event.set()

# Global instance
_gateway_runner: Optional[GatewayRunner] = None

def get_gateway() -> Optional[GatewayRunner]:
    """Obtener instancia global del gateway"""
    return _gateway_runner

async def start_gateway(config: Optional[GatewayConfig] = None) -> bool:
    """
    Iniciar el gateway de EIDOS.
    
    Usage:
        python -m eidos.gateway.run
    """
    global _gateway_runner
    
    if config is None:
        # Cargar config desde archivo
        config_path = Path.home() / ".eidos" / "gateway.yaml"
        if config_path.exists():
            try:
                import yaml
                with open(config_path) as f:
                    data = yaml.safe_load(f)
                config = GatewayConfig.from_dict(data)
            except Exception as e:
                logger.warning(f"Could not load config: {e}, using defaults")
                config = GatewayConfig()
        else:
            config = GatewayConfig()
    
    _gateway_runner = GatewayRunner(config)
    
    try:
        return await _gateway_runner.run()
    except Exception as e:
        logger.error(f"Gateway crashed: {e}")
        return False

if __name__ == "__main__":
    # Configurar logging básico
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )
    
    # Iniciar
    success = asyncio.run(start_gateway())
    sys.exit(0 if success else 1)
