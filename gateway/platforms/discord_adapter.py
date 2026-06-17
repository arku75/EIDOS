"""
Discord Adapter para EIDOS Gateway
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

class DiscordAdapter(BasePlatformAdapter):
    """Adapter para Discord usando WebSocket Gateway"""
    
    API_BASE = "https://discord.com/api/v10"
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.platform_name = "discord"
        self.bot_token = config.get("bot_token", "")
        self.allowed_users = config.get("allowed_users", [])
        self.session: Optional[aiohttp.ClientSession] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._session_id: Optional[str] = None
        
    async def start(self) -> bool:
        if not HAS_AIOHTTP:
            logger.error("aiohttp requerido para Discord")
            return False
        
        if not self.bot_token:
            logger.error("DISCORD_BOT_TOKEN no configurado")
            return False
        
        headers = {
            "Authorization": f"Bot {self.bot_token}",
            "Content-Type": "application/json",
        }
        self.session = aiohttp.ClientSession(headers=headers)
        
        # Obtener gateway URL
        gateway_info = await self._api_call("gateway/bot")
        if not gateway_info.get("url"):
            logger.error("Failed to get Discord gateway URL")
            return False
        
        logger.info(f"✅ Discord adapter started (shards: {gateway_info.get('shards', 1)})")
        
        self._running = True
        self._ws_task = asyncio.create_task(
            self._websocket_loop(gateway_info["url"])
        )
        return True
    
    async def stop(self):
        self._running = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self.session:
            await self.session.close()
        logger.info("Discord adapter stopped")
    
    async def _api_call(self, endpoint: str, **kwargs) -> Dict:
        """Llamar a Discord API"""
        if not self.session:
            return {}
        
        url = f"{self.API_BASE}/{endpoint}"
        
        try:
            method = kwargs.pop("_method", "GET")
            if method == "POST":
                async with self.session.post(url, json=kwargs) as resp:
                    return await resp.json() if resp.content_type == "application/json" else {}
            else:
                async with self.session.get(url) as resp:
                    return await resp.json() if resp.content_type == "application/json" else {}
        except Exception as e:
            logger.error(f"Discord API error: {e}")
            return {}
    
    async def _websocket_loop(self, gateway_url: str):
        """WebSocket connection loop"""
        ws_url = f"{gateway_url}?v=10&encoding=json"
        
        while self._running:
            try:
                async with self.session.ws_connect(ws_url) as ws:
                    logger.info("Discord WebSocket connected")
                    
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = msg.json()
                            await self._process_gateway_event(ws, data)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                            
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                await asyncio.sleep(5)
    
    async def _process_gateway_event(self, ws, data: Dict):
        """Procesar evento del gateway"""
        op = data.get("op")
        
        if op == 10:  # Hello
            interval = data["d"]["heartbeat_interval"] / 1000
            asyncio.create_task(self._heartbeat(ws, interval))
            
            # Identify
            await ws.send_json({
                "op": 2,
                "d": {
                    "token": self.bot_token,
                    "intents": 512 + 1024,  # GUILD_MESSAGES + GUILD_MEMBERS
                    "properties": {
                        "os": "linux",
                        "browser": "EIDOS",
                        "device": "EIDOS"
                    }
                }
            })
        
        elif op == 0:  # Dispatch
            event_type = data.get("t")
            if event_type == "MESSAGE_CREATE":
                await self._process_message(data["d"])
            elif event_type == "READY":
                user = data["d"]["user"]
                logger.info(f"✅ Discord bot ready: {user['username']}#{user.get('discriminator', '0')}")
    
    async def _heartbeat(self, ws, interval: float):
        """Enviar heartbeats"""
        while self._running:
            await asyncio.sleep(interval)
            try:
                await ws.send_json({"op": 1, "d": None})
            except:
                break
    
    async def _process_message(self, msg_data: Dict):
        """Procesar mensaje de Discord"""
        # Ignorar mensajes del bot
        if msg_data.get("author", {}).get("bot"):
            return
        
        author = msg_data.get("author", {})
        channel = msg_data.get("channel_id", "")
        
        # Verificar usuario permitido
        user_id = author.get("id")
        if self.allowed_users and str(user_id) not in self.allowed_users:
            return
        
        msg = PlatformMessage(
            message_id=msg_data["id"],
            text=msg_data.get("content", ""),
            sender_id=user_id,
            sender_name=author.get("username", "Unknown"),
            chat_id=channel,
            chat_name=msg_data.get("guild_id", "DM"),
            chat_type="dm" if not msg_data.get("guild_id") else "channel",
            thread_id=msg_data.get("thread", {}).get("id") if msg_data.get("thread") else None,
            platform="discord",
            raw_data=msg_data,
        )
        
        await self._handle_message(msg)
    
    async def send_message(self, response: PlatformResponse) -> bool:
        """Enviar mensaje a Discord"""
        text = self.truncate_message(response.text, 2000)
        
        result = await self._api_call(
            f"channels/{response.chat_id}/messages",
            _method="POST",
            content=text,
        )
        
        return bool(result.get("id"))
    
    async def send_typing_indicator(self, chat_id: str):
        """Typing indicator no es crítico para Discord"""
        pass

from .base import register_adapter
register_adapter("discord", DiscordAdapter)
