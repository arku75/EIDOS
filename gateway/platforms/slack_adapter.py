"""
Slack Adapter para EIDOS Gateway
Socket Mode con WebSocket
"""

import asyncio
import json
import logging
from typing import Dict, Any, Optional
from .base import BasePlatformAdapter, PlatformMessage, PlatformResponse

try:
    import aiohttp
    import aiohttp.web
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

logger = logging.getLogger(__name__)

class SlackAdapter(BasePlatformAdapter):
    """Adapter para Slack usando Socket Mode"""
    
    API_BASE = "https://slack.com/api"
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.platform_name = "slack"
        self.bot_token = config.get("bot_token", "")
        self.app_token = config.get("app_token", "")
        self.session: Optional[aiohttp.ClientSession] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._socket_url: Optional[str] = None
        
    async def start(self) -> bool:
        if not HAS_AIOHTTP:
            logger.error("aiohttp requerido para Slack")
            return False
        
        if not self.bot_token or not self.app_token:
            logger.error("SLACK_BOT_TOKEN y SLACK_APP_TOKEN requeridos")
            return False
        
        headers = {
            "Authorization": f"Bearer {self.bot_token}",
            "Content-Type": "application/json",
        }
        self.session = aiohttp.ClientSession(headers=headers)
        
        # Obtener WebSocket URL (Socket Mode)
        ws_url = await self._get_socket_url()
        if not ws_url:
            logger.error("Failed to get Slack Socket Mode URL")
            return False
        
        self._socket_url = ws_url
        logger.info("✅ Slack adapter started (Socket Mode)")
        
        self._running = True
        self._ws_task = asyncio.create_task(self._websocket_loop(ws_url))
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
        logger.info("Slack adapter stopped")
    
    async def _api_call(self, method: str, **params) -> Dict:
        """Llamar a Slack API"""
        if not self.session:
            return {"ok": False}
        
        url = f"{self.API_BASE}/{method}"
        
        try:
            async with self.session.post(url, json=params) as resp:
                return await resp.json()
        except Exception as e:
            logger.error(f"Slack API error: {e}")
            return {"ok": False, "error": str(e)}
    
    async def _get_socket_url(self) -> Optional[str]:
        """Obtener URL de WebSocket para Socket Mode"""
        # Usar app token para Socket Mode
        headers = {"Authorization": f"Bearer {self.app_token}"}
        
        try:
            async with self.session.post(
                "https://slack.com/api/apps.connections.open",
                headers=headers
            ) as resp:
                data = await resp.json()
                if data.get("ok"):
                    return data.get("url")
                logger.error(f"Socket Mode error: {data}")
        except Exception as e:
            logger.error(f"Failed to open Socket Mode: {e}")
        
        return None
    
    async def _websocket_loop(self, ws_url: str):
        """WebSocket connection para Socket Mode"""
        reconnect_delay = 1
        
        while self._running:
            try:
                async with self.session.ws_connect(ws_url) as ws:
                    logger.info("Slack WebSocket connected")
                    reconnect_delay = 1  # Reset on success
                    
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._process_socket_message(ws, json.loads(msg.data))
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                            
            except Exception as e:
                logger.error(f"Slack WebSocket error: {e}")
            
            if self._running:
                logger.info(f"Reconnecting in {reconnect_delay}s...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)
                
                # Refresh socket URL
                ws_url = await self._get_socket_url()
                if not ws_url:
                    break
    
    async def _process_socket_message(self, ws, envelope: Dict):
        """Procesar mensaje del envelope de Socket Mode"""
        # Acknowledge
        if envelope.get("envelope_id"):
            await ws.send_str(json.dumps({"envelope_id": envelope["envelope_id"]}))
        
        payload = envelope.get("payload", {})
        event_type = payload.get("type")
        
        if event_type == "event_callback":
            event = payload.get("event", {})
            await self._process_event(event)
    
    async def _process_event(self, event: Dict):
        """Procesar evento de Slack"""
        event_type = event.get("type")
        
        if event_type == "message":
            # Ignorar mensajes del bot
            if event.get("bot_id") or event.get("subtype"):
                return
            
            text = event.get("text", "")
            channel = event.get("channel", "")
            user = event.get("user", "")
            ts = event.get("ts", "")
            thread_ts = event.get("thread_ts")
            
            # Solo responder a menciones o DMs
            if not self._should_respond(event):
                return
            
            msg = PlatformMessage(
                message_id=ts,
                text=text,
                sender_id=user,
                sender_name=await self._get_user_name(user),
                chat_id=channel,
                chat_name=await self._get_channel_name(channel),
                chat_type="dm" if event.get("channel_type") == "im" else "channel",
                thread_id=thread_ts,
                platform="slack",
                raw_data=event,
            )
            
            await self._handle_message(msg)
    
    def _should_respond(self, event: Dict) -> bool:
        """Determinar si debemos responder a este evento"""
        text = event.get("text", "")
        channel_type = event.get("channel_type", "")
        
        # Siempre responder en DMs
        if channel_type == "im":
            return True
        
        # En canales, solo responder a menciones
        # TODO: Detectar @bot mention
        return "@EIDOS" in text or "@eidos" in text
    
    async def _get_user_name(self, user_id: str) -> str:
        """Obtener nombre de usuario"""
        # Cache simple
        result = await self._api_call("users.info", user=user_id)
        if result.get("ok"):
            user = result.get("user", {})
            return user.get("real_name") or user.get("name", user_id)
        return user_id
    
    async def _get_channel_name(self, channel_id: str) -> str:
        """Obtener nombre de canal"""
        # Cache simple
        if channel_id.startswith("D"):  # DM
            return "DM"
        
        result = await self._api_call("conversations.info", channel=channel_id)
        if result.get("ok"):
            channel = result.get("channel", {})
            return channel.get("name", channel_id)
        return channel_id
    
    async def send_message(self, response: PlatformResponse) -> bool:
        """Enviar mensaje a Slack"""
        text = self.truncate_message(response.text, 3000)
        
        params = {
            "channel": response.chat_id,
            "text": text,
        }
        
        if response.thread_id:
            params["thread_ts"] = response.thread_id
        
        result = await self._api_call("chat.postMessage", **params)
        
        if not result.get("ok"):
            logger.error(f"Slack send failed: {result}")
        
        return result.get("ok", False)
    
    async def send_typing_indicator(self, chat_id: str):
        """No-op para Slack (no tiene typing indicator)"""
        pass

from .base import register_adapter
register_adapter("slack", SlackAdapter)
