"""
Telegram Adapter para EIDOS Gateway
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from .base import BasePlatformAdapter, PlatformMessage, PlatformResponse

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

logger = logging.getLogger(__name__)

class TelegramAdapter(BasePlatformAdapter):
    """Adapter para Telegram Bot API"""
    
    API_BASE = "https://api.telegram.org/bot"
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.platform_name = "telegram"
        self.bot_token = config.get("bot_token", "")
        self.allowed_users = config.get("allowed_users", [])
        self.session: Optional[aiohttp.ClientSession] = None
        self._last_update_id = 0
        self._polling_task: Optional[asyncio.Task] = None
        
    async def start(self) -> bool:
        if not HAS_AIOHTTP:
            logger.error("aiohttp requerido para Telegram")
            return False
        
        if not self.bot_token:
            logger.error("TELEGRAM_BOT_TOKEN no configurado")
            return False
        
        self.session = aiohttp.ClientSession()
        
        # Verificar bot
        me = await self._api_call("getMe")
        if not me.get("ok"):
            logger.error(f"Failed to connect to Telegram: {me}")
            return False
        
        bot_info = me.get("result", {})
        logger.info(f"✅ Telegram bot connected: @{bot_info.get('username')}")
        
        self._running = True
        self._polling_task = asyncio.create_task(self._polling_loop())
        return True
    
    async def stop(self):
        self._running = False
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
        if self.session:
            await self.session.close()
        logger.info("Telegram adapter stopped")
    
    async def _api_call(self, method: str, **params) -> Dict:
        """Llamar a Telegram Bot API"""
        if not self.session:
            return {"ok": False, "error": "No session"}
        
        url = f"{self.API_BASE}{self.bot_token}/{method}"
        
        try:
            async with self.session.post(url, json=params) as resp:
                return await resp.json()
        except Exception as e:
            logger.error(f"API call failed: {e}")
            return {"ok": False, "error": str(e)}
    
    async def _polling_loop(self):
        """Long polling para updates"""
        while self._running:
            try:
                updates = await self._api_call(
                    "getUpdates",
                    offset=self._last_update_id + 1,
                    timeout=30
                )
                
                if updates.get("ok"):
                    for update in updates.get("result", []):
                        self._last_update_id = update["update_id"]
                        await self._process_update(update)
                        
            except Exception as e:
                logger.error(f"Polling error: {e}")
                await asyncio.sleep(5)
    
    async def _process_update(self, update: Dict):
        """Procesar update de Telegram"""
        message = update.get("message")
        if not message:
            return
        
        # Verificar usuario permitido
        user_id = message.get("from", {}).get("id")
        if self.allowed_users and str(user_id) not in self.allowed_users:
            logger.warning(f"Ignored message from unauthorized user: {user_id}")
            return
        
        # Extraer información
        chat = message.get("chat", {})
        from_user = message.get("from", {})
        
        msg = PlatformMessage(
            message_id=str(message["message_id"]),
            text=message.get("text", ""),
            sender_id=str(from_user.get("id", "")),
            sender_name=from_user.get("first_name", "Unknown"),
            chat_id=str(chat.get("id", "")),
            chat_name=chat.get("title") or chat.get("username", "Private"),
            chat_type="dm" if chat.get("type") == "private" else "group",
            thread_id=None,  # Telegram usa message_thread_id
            platform="telegram",
            raw_data=update,
        )
        
        await self._handle_message(msg)
    
    async def send_message(self, response: PlatformResponse) -> bool:
        """Enviar mensaje a Telegram"""
        text = self.truncate_message(response.text, 4096)
        
        params = {
            "chat_id": response.chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        
        if response.reply_to_message_id:
            params["reply_to_message_id"] = int(response.reply_to_message_id)
        
        result = await self._api_call("sendMessage", **params)
        return result.get("ok", False)
    
    async def send_typing_indicator(self, chat_id: str):
        """Enviar acción 'typing'"""
        await self._api_call("sendChatAction", chat_id=chat_id, action="typing")

# Registrar adapter
from .base import register_adapter
register_adapter("telegram", TelegramAdapter)
