"""
Context Manager para EIDOS
Gestiona ventana de contexto y rotación
"""

import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

from .compressor import ContextCompressor

logger = logging.getLogger(__name__)

@dataclass
class ContextWindow:
    """Ventana de contexto de conversación"""
    session_key: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    tokens_used: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    last_accessed: datetime = field(default_factory=datetime.now)
    
    def touch(self):
        """Actualizar timestamp de acceso"""
        self.last_accessed = datetime.now()
    
    def add_message(self, role: str, content: str, metadata: Optional[Dict] = None):
        """Añadir mensaje a la ventana"""
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {}
        })
        self.touch()
        
        # Estimar tokens (aproximado: 4 chars ~= 1 token)
        self.tokens_used += len(content) // 4
    
    def get_messages(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Obtener mensajes, opcionalmente limitados"""
        self.touch()
        if limit:
            return self.messages[-limit:]
        return self.messages

class ContextManager:
    """
    Gestiona múltiples ventanas de contexto.
    LRU eviction cuando hay demasiadas sesiones activas.
    """
    
    def __init__(
        self,
        max_sessions: int = 128,
        max_context_per_session: int = 100,
        compressor: Optional[ContextCompressor] = None
    ):
        self.max_sessions = max_sessions
        self.max_context_per_session = max_context_per_session
        self.compressor = compressor or ContextCompressor()
        self._windows: Dict[str, ContextWindow] = {}
        
    def get_window(self, session_key: str) -> ContextWindow:
        """Obtener o crear ventana de contexto"""
        if session_key not in self._windows:
            # LRU: eliminar sesiones antiguas si estamos llenos
            if len(self._windows) >= self.max_sessions:
                self._evict_oldest()
            
            self._windows[session_key] = ContextWindow(session_key=session_key)
        
        return self._windows[session_key]
    
    def _evict_oldest(self):
        """Eliminar la sesión menos recientemente usada"""
        if not self._windows:
            return
        
        oldest_key = min(
            self._windows.keys(),
            key=lambda k: self._windows[k].last_accessed
        )
        
        window = self._windows.pop(oldest_key)
        logger.info(f"Evicted context window: {oldest_key} "
                   f"(last access: {window.last_accessed.isoformat()})")
    
    def add_to_context(
        self,
        session_key: str,
        role: str,
        content: str,
        metadata: Optional[Dict] = None
    ):
        """Añadir mensaje a contexto de sesión"""
        window = self.get_window(session_key)
        
        # Verificar si necesitamos compresión
        if self.compressor.should_compress(window.tokens_used):
            logger.info(f"Compressing context for {session_key}")
            result = self.compressor.compress(window.messages, window.tokens_used)
            
            if result.method != "none":
                # Reconstruir ventana comprimida
                new_messages = []
                if result.preserved_messages > 0:
                    # El resumen como system message
                    new_messages.append({
                        "role": "system",
                        "content": f"[Contexto previo: {result.summary}]",
                        "timestamp": datetime.now().isoformat(),
                        "metadata": {"compressed": True}
                    })
                    # Los mensajes preservados
                    new_messages.extend(window.messages[-result.preserved_messages:])
                
                window.messages = new_messages
                window.tokens_used = result.compressed_tokens
                logger.info(f"Context compressed: {result.original_tokens} -> "
                           f"{result.compressed_tokens} tokens")
        
        # Añadir nuevo mensaje
        window.add_message(role, content, metadata)
        
        # Limitar número de mensajes
        if len(window.messages) > self.max_context_per_session:
            # Remover los más antiguos (excepto system)
            system_msgs = [m for m in window.messages if m["role"] == "system"]
            other_msgs = [m for m in window.messages if m["role"] != "system"]
            
            # Mantener últimos N mensajes no-system
            keep_count = self.max_context_per_session - len(system_msgs)
            other_msgs = other_msgs[-keep_count:]
            
            window.messages = system_msgs + other_msgs
    
    def get_context(self, session_key: str) -> List[Dict[str, Any]]:
        """Obtener contexto completo de sesión"""
        window = self.get_window(session_key)
        return window.get_messages()
    
    def clear_context(self, session_key: str):
        """Limpiar contexto de sesión"""
        if session_key in self._windows:
            del self._windows[session_key]
            logger.info(f"Cleared context: {session_key}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Estadísticas del manager"""
        return {
            "active_windows": len(self._windows),
            "total_messages": sum(len(w.messages) for w in self._windows.values()),
            "total_tokens": sum(w.tokens_used for w in self._windows.values()),
            "avg_tokens_per_window": (
                sum(w.tokens_used for w in self._windows.values()) / len(self._windows)
                if self._windows else 0
            ),
        }
