"""
WhatsApp Adapter para EIDOS Gateway
Usa whatsapp-web.js vía Node.js bridge o API externa
"""

import asyncio
import json
import logging
import subprocess
from typing import Dict, Any, Optional
from .base import BasePlatformAdapter, PlatformMessage, PlatformResponse

logger = logging.getLogger(__name__)

class WhatsAppAdapter(BasePlatformAdapter):
    """
    Adapter para WhatsApp.
    Soporta múltiples backends: whatsapp-web.js, Evolution API, etc.
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.platform_name = "whatsapp"
        self.backend = config.get("backend", "evolution")  # evolution, baileys, etc.
        self.api_url = config.get("api_url", "")
        self.api_key = config.get("api_key", "")
        self.instance = config.get("instance", "eidos")
        self._polling_task: Optional[asyncio.Task] = None
        self._last_message_id: str = ""
        
    async def start(self) -> bool:
        if not self.api_url:
            logger.error("WHATSAPP_API_URL requerido")
            return False
        
        if self.backend == "evolution":
            logger.info(f"✅ WhatsApp adapter started (Evolution API: {self.api_url})")
            self._running = True
            self._polling_task = asyncio.create_task(self._poll_evolution())
            return True
        else:
            logger.error(f"Backend no soportado: {self.backend}")
            return False
    
    async def stop(self):
        self._running = False
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
        logger.info("WhatsApp adapter stopped")
    
    async def _poll_evolution(self):
        """Polling para Evolution API"""
        import aiohttp
        
        async with aiohttp.ClientSession() as session:
            headers = {"apikey": self.api_key} if self.api_key else {}
            
            while self._running:
                try:
                    url = f"{self.api_url}/message/findMessages/{self.instance}"
                    
                    async with session.post(url, headers=headers, json={}) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            messages = data if isinstance(data, list) else []
                            
                            for msg in messages:
                                await self._process_evolution_message(msg)
                    
                    await asyncio.sleep(5)
                    
                except Exception as e:
                    logger.error(f"WhatsApp poll error: {e}")
                    await asyncio.sleep(10)
    
    async def _process_evolution_message(self, msg: Dict):
        """Procesar mensaje de Evolution API"""
        msg_id = msg.get("key", {}).get("id", "")
        
        # Evitar duplicados
        if msg_id == self._last_message_id:
            return
        self._last_message_id = msg_id
        
        # Ignorar mensajes enviados por nosotros
        if msg.get("key", {}).get("fromMe"):
            return
        
        content = msg.get("message", {})
        conversation = content.get("conversation", "")
        
        if not conversation:
            return
        
        remote_jid = msg.get("key", {}).get("remoteJid", "")
        push_name = msg.get("pushName", "Unknown")
        
        # Determinar tipo de chat
        chat_type = "group" if "@g.us" in remote_jid else "dm"
        
        platform_msg = PlatformMessage(
            message_id=msg_id,
            text=conversation,
            sender_id=remote_jid.split("@")[0],
            sender_name=push_name,
            chat_id=remote_jid,
            chat_name=push_name if chat_type == "dm" else "Group",
            chat_type=chat_type,
            thread_id=None,
            platform="whatsapp",
            raw_data=msg,
        )
        
        await self._handle_message(platform_msg)
    
    async def send_message(self, response: PlatformResponse) -> bool:
        """Enviar mensaje por WhatsApp"""
        if self.backend == "evolution":
            return await self._send_evolution(response)
        return False
    
    async def _send_evolution(self, response: PlatformResponse) -> bool:
        """Enviar usando Evolution API"""
        import aiohttp
        
        try:
            url = f"{self.api_url}/message/sendText/{self.instance}"
            headers = {"apikey": self.api_key} if self.api_key else {}
            
            payload = {
                "number": response.chat_id,
                "text": response.text,
                "delay": 1000,
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    return resp.status == 201
                    
        except Exception as e:
            logger.error(f"WhatsApp send error: {e}")
            return False
    
    async def send_typing_indicator(self, chat_id: str):
        """No implementado para Evolution"""
        pass

from .base import register_adapter
register_adapter("whatsapp", WhatsAppAdapter)
