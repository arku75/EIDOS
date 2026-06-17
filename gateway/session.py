"""
Session Management para EIDOS Gateway
Adaptado de Hermes Agent - Session management with PII redaction
"""

import hashlib
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set
from enum import Enum

logger = logging.getLogger(__name__)

class Platform(Enum):
    """Plataformas soportadas por EIDOS Gateway"""
    LOCAL = "local"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    SLACK = "slack"
    MATRIX = "matrix"
    MATTERMOST = "mattermost"
    WHATSAPP = "whatsapp"
    SIGNAL = "signal"
    BLUEBUBBLES = "bluebubbles"
    QQ = "qq"
    WEBHOOK = "webhook"
    EMAIL = "email"

@dataclass
class SessionSource:
    """Describe el origen de un mensaje"""
    platform: Platform
    chat_id: str
    chat_name: Optional[str] = None
    chat_type: str = "dm"  # dm, group, channel, thread
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    thread_id: Optional[str] = None
    chat_topic: Optional[str] = None
    is_bot: bool = False
    
    @property
    def description(self) -> str:
        if self.platform == Platform.LOCAL:
            return "CLI terminal"
        
        parts = []
        if self.chat_type == "dm":
            parts.append(f"DM con {self.user_name or self.user_id or 'user'}")
        elif self.chat_type == "group":
            parts.append(f"group: {self.chat_name or self.chat_id}")
        elif self.chat_type == "channel":
            parts.append(f"channel: {self.chat_name or self.chat_id}")
        else:
            parts.append(self.chat_name or self.chat_id)
        
        if self.thread_id:
            parts.append(f"thread: {self.thread_id}")
        
        return ", ".join(parts)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform.value,
            "chat_id": self.chat_id,
            "chat_name": self.chat_name,
            "chat_type": self.chat_type,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "thread_id": self.thread_id,
            "chat_topic": self.chat_topic,
            "is_bot": self.is_bot,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionSource":
        return cls(
            platform=Platform(data.get("platform", "local")),
            chat_id=str(data.get("chat_id", "")),
            chat_name=data.get("chat_name"),
            chat_type=data.get("chat_type", "dm"),
            user_id=data.get("user_id"),
            user_name=data.get("user_name"),
            thread_id=data.get("thread_id"),
            chat_topic=data.get("chat_topic"),
            is_bot=data.get("is_bot", False),
        )

@dataclass
class SessionContext:
    """Contexto completo de una sesión"""
    source: SessionSource
    connected_platforms: List[Platform] = field(default_factory=list)
    session_key: str = ""
    session_id: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source.to_dict(),
            "connected_platforms": [p.value for p in self.connected_platforms],
            "session_key": self.session_key,
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "metadata": self.metadata,
        }

class SessionManager:
    """Gestiona sesiones persistentes para EIDOS Gateway"""
    
    def __init__(self, sessions_dir: Optional[Path] = None):
        self.sessions_dir = sessions_dir or Path.home() / ".eidos" / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._cache: Dict[str, SessionContext] = {}
        
    def _get_session_path(self, session_key: str) -> Path:
        """Sanitize session key para uso seguro en filesystem"""
        safe_key = hashlib.sha256(session_key.encode()).hexdigest()[:16]
        return self.sessions_dir / f"{safe_key}.json"
    
    def get_or_create_session(
        self,
        source: SessionSource,
        session_key: Optional[str] = None
    ) -> SessionContext:
        """Obtener o crear sesión para una fuente"""
        if session_key is None:
            session_key = f"{source.platform.value}:{source.chat_id}"
            if source.thread_id:
                session_key += f":{source.thread_id}"
        
        with self._lock:
            # Check cache
            if session_key in self._cache:
                session = self._cache[session_key]
                session.updated_at = datetime.now()
                return session
            
            # Try load from disk
            session_path = self._get_session_path(session_key)
            if session_path.exists():
                try:
                    data = json.loads(session_path.read_text(encoding="utf-8"))
                    session = self._load_session_from_dict(data)
                    session.session_key = session_key
                    self._cache[session_key] = session
                    return session
                except Exception as e:
                    logger.warning(f"Failed to load session {session_key}: {e}")
            
            # Create new session
            session = SessionContext(
                source=source,
                session_key=session_key,
                session_id=str(uuid.uuid4())[:8],
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            self._cache[session_key] = session
            self._save_session(session)
            return session
    
    def _load_session_from_dict(self, data: Dict) -> SessionContext:
        """Reconstruir SessionContext desde dict"""
        source = SessionSource.from_dict(data.get("source", {}))
        platforms = [Platform(p) for p in data.get("connected_platforms", [])]
        
        return SessionContext(
            source=source,
            connected_platforms=platforms,
            session_key=data.get("session_key", ""),
            session_id=data.get("session_id", ""),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None,
            metadata=data.get("metadata", {}),
        )
    
    def _save_session(self, session: SessionContext):
        """Persistir sesión a disco"""
        session_path = self._get_session_path(session.session_key)
        try:
            session_path.write_text(
                json.dumps(session.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"Failed to save session {session.session_key}: {e}")
    
    def build_context_prompt(self, session: SessionContext, redact_pii: bool = True) -> str:
        """Construir prompt de contexto dinámico para el agente"""
        lines = ["## Contexto de Sesión EIDOS", ""]
        
        src = session.source
        platform_name = src.platform.value.title()
        
        if src.platform == Platform.LOCAL:
            lines.append(f"**Fuente:** {platform_name} (terminal local)")
        else:
            if redact_pii and src.user_name:
                lines.append(f"**Fuente:** {platform_name} - DM con usuario")
            else:
                lines.append(f"**Fuente:** {platform_name} ({src.description})")
        
        if src.chat_topic:
            lines.append(f"**Tema:** {src.chat_topic}")
        
        # Notas específicas por plataforma
        if src.platform == Platform.TELEGRAM:
            lines.extend(["", "**Notas Telegram:** Respuestas soportan Markdown."])
        elif src.platform == Platform.DISCORD:
            lines.extend(["", "**Notas Discord:** Menciones con <@user_id>."])
        
        # Plataformas conectadas
        if session.connected_platforms:
            platforms = [p.value for p in session.connected_platforms if p != Platform.LOCAL]
            if platforms:
                lines.extend(["", f"**Plataformas conectadas:** {', '.join(platforms)}"])
        
        return "\n".join(lines)
    
    def reset_session(self, session_key: str):
        """Resetear una sesión (limpiar contexto)"""
        with self._lock:
            if session_key in self._cache:
                del self._cache[session_key]
            
            session_path = self._get_session_path(session_key)
            if session_path.exists():
                session_path.unlink()
                logger.info(f"Session reset: {session_key}")

# Singleton instance
_session_manager: Optional[SessionManager] = None

def get_session_manager() -> SessionManager:
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager
