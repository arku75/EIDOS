"""
Base Platform Adapter para EIDOS Gateway
Adaptado de Hermes - Interfaz común para todas las plataformas
"""

import asyncio
import logging
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class PlatformMessage:
    """Mensaje entrante de cualquier plataforma"""
    message_id: str
    text: str
    sender_id: str
    sender_name: Optional[str] = None
    chat_id: str = ""
    chat_name: Optional[str] = None
    chat_type: str = "dm"  # dm, group, channel, thread
    thread_id: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    platform: str = ""
    raw_data: Optional[Dict] = None
    attachments: List[Dict] = field(default_factory=list)
    reply_to: Optional[str] = None

@dataclass
class PlatformResponse:
    """Respuesta a enviar a una plataforma"""
    text: str
    chat_id: str
    thread_id: Optional[str] = None
    reply_to_message_id: Optional[str] = None
    attachments: List[Dict] = field(default_factory=list)
    platform_specific: Dict[str, Any] = field(default_factory=dict)

class BasePlatformAdapter(ABC):
    """
    Interfaz base para todos los adapters de plataforma.
    Cada plataforma (Telegram, Discord, etc.) implementa esta interfaz.
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.platform_name = self.__class__.__name__.lower().replace("adapter", "")
        self._running = False
        self._message_handler: Optional[Callable[[PlatformMessage], None]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        
    @property
    def is_running(self) -> bool:
        return self._running
    
    def set_message_handler(self, handler: Callable[[PlatformMessage], None]):
        """Registrar callback para mensajes entrantes"""
        self._message_handler = handler
        
    async def _handle_message(self, message: PlatformMessage):
        """Internal: llamar al handler registrado"""
        if self._message_handler:
            try:
                if asyncio.iscoroutinefunction(self._message_handler):
                    await self._message_handler(message)
                else:
                    self._message_handler(message)
            except Exception as e:
                logger.error(f"Error handling message: {e}")
        else:
            logger.warning(f"No message handler registered for {self.platform_name}")
    
    @abstractmethod
    async def start(self) -> bool:
        """Iniciar el adapter y conectar a la plataforma"""
        pass
    
    @abstractmethod
    async def stop(self):
        """Detener el adapter y desconectar"""
        pass
    
    @abstractmethod
    async def send_message(self, response: PlatformResponse) -> bool:
        """Enviar mensaje a la plataforma"""
        pass
    
    @abstractmethod
    async def send_typing_indicator(self, chat_id: str):
        """Mostrar indicador de 'escribiendo...'"""
        pass
    
    def truncate_message(self, text: str, max_length: int = 4096) -> str:
        """Truncar mensaje a longitud máxima segura"""
        if len(text) <= max_length:
            return text
        
        # Dejar espacio para indicador de truncado
        truncate_marker = "\n\n[...mensaje truncado]"
        available = max_length - len(truncate_marker)
        
        # Cortar en límite de línea si es posible
        truncated = text[:available]
        last_newline = truncated.rfind('\n')
        if last_newline > available * 0.8:
            truncated = truncated[:last_newline]
        
        return truncated + truncate_marker
    
    def escape_markdown(self, text: str, platform: str = "telegram") -> str:
        """Escapar caracteres especiales de Markdown según plataforma"""
        if platform == "telegram":
            # Telegram usa MarkdownV2
            chars_to_escape = r'_*[]()~`>#+-=|{}.!'
            for char in chars_to_escape:
                text = text.replace(char, f"\\{char}")
        elif platform == "discord":
            # Discord usa Markdown estándar
            chars_to_escape = r'*_`~'
            for char in chars_to_escape:
                text = text.replace(char, f"\\{char}")
        return text
    
    def format_code_block(self, code: str, language: str = "", platform: str = "telegram") -> str:
        """Formatear bloque de código según plataforma"""
        if platform == "telegram":
            return f"```{language}\n{code}\n```"
        elif platform == "discord":
            return f"```{language}\n{code}\n```"
        else:
            return f"```{language}\n{code}\n```"

class LocalAdapter(BasePlatformAdapter):
    """
    Adapter para CLI local - modo terminal interactivo.
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.platform_name = "local"
        
    async def start(self) -> bool:
        self._running = True
        logger.info("Local adapter started (CLI mode)")
        return True
        
    async def stop(self):
        self._running = False
        logger.info("Local adapter stopped")
        
    async def send_message(self, response: PlatformResponse) -> bool:
        """Imprimir mensaje a stdout"""
        print(f"\n[🤖 EIDOS] {response.text}\n")
        return True
        
    async def send_typing_indicator(self, chat_id: str):
        """No-op para CLI"""
        pass

# Registry de adapters
_adapter_registry: Dict[str, type] = {}

def register_adapter(platform: str, adapter_class: type):
    """Registrar un adapter para una plataforma"""
    _adapter_registry[platform] = adapter_class
    logger.debug(f"Registered adapter: {platform}")

def get_adapter(platform: str, config: Dict[str, Any]) -> Optional[BasePlatformAdapter]:
    """Obtener instancia de adapter para una plataforma"""
    adapter_class = _adapter_registry.get(platform)
    if adapter_class:
        return adapter_class(config)
    return None

def list_available_adapters() -> List[str]:
    """Listar plataformas disponibles"""
    return list(_adapter_registry.keys())

# Registrar local
register_adapter("local", LocalAdapter)
