"""
Matrix Adapter para EIDOS Gateway
"""

import asyncio
import json
import logging
from typing import Dict, Any, Optional
from .base import BasePlatformAdapter, PlatformMessage, PlatformResponse

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

logger = logging.getLogger(__name__)

class MatrixAdapter(BasePlatformAdapter):
    """Adapter para Matrix usando Client-Server API"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.platform_name = "matrix"
        self.homeserver = config.get("homeserver", "")
        self.access_token = config.get("access_token", "")
        self.user_id = config.get("user_id", "")
        self.device_id = config.get("device_id", "EIDOS_GATEWAY")
        self.session: Optional[aiohttp.ClientSession] = None
        self._sync_task: Optional[asyncio.Task] = None
        self._next_batch: Optional[str] = None
        
    async def start(self) -> bool:
        if not HAS_AIOHTTP:
            logger.error("aiohttp requerido para Matrix")
            return False
        
        if not all([self.homeserver, self.access_token, self.user_id]):
            logger.error("MATRIX_HOMESERVER, MATRIX_ACCESS_TOKEN, MATRIX_USER_ID requeridos")
            return False
        
        headers = {"Authorization": f"Bearer {self.access_token}"}
        self.session = aiohttp.ClientSession(headers=headers)
        
        # Verificar conexión
        whoami = await self._api_call("account/whoami")
        if whoami.get("user_id") != self.user_id:
            logger.error(f"Matrix auth failed: {whoami}")
            return False
        
        logger.info(f"✅ Matrix adapter started: {self.user_id}")
        
        self._running = True
        self._sync_task = asyncio.create_task(self._sync_loop())
        return True
    
    async def stop(self):
        self._running = False
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        if self.session:
            await self.session.close()
        logger.info("Matrix adapter stopped")
    
    async def _api_call(self, endpoint: str, method: str = "GET", **params) -> Dict:
        """Llamar a Matrix API"""
        if not self.session:
            return {}
        
        url = f"{self.homeserver}/_matrix/client/v3/{endpoint}"
        
        try:
            if method == "POST":
                async with self.session.post(url, json=params) as resp:
                    return await resp.json() if resp.status < 300 else {}
            else:
                async with self.session.get(url, params=params) as resp:
                    return await resp.json() if resp.status < 300 else {}
        except Exception as e:
            logger.error(f"Matrix API error: {e}")
            return {}
    
    async def _sync_loop(self):
        """Sync loop para recibir eventos"""
        while self._running:
            try:
                params = {"timeout": 30000}  # 30s long polling
                if self._next_batch:
                    params["since"] = self._next_batch
                
                sync_data = await self._api_call("sync", **params)
                
                if not sync_data:
                    await asyncio.sleep(5)
                    continue
                
                self._next_batch = sync_data.get("next_batch")
                
                # Procesar mensajes
                await self._process_sync(sync_data)
                
            except Exception as e:
                logger.error(f"Matrix sync error: {e}")
                await asyncio.sleep(5)
    
    async def _process_sync(self, sync_data: Dict):
        """Procesar datos de sync"""
        rooms = sync_data.get("rooms", {}).get("join", {})
        
        for room_id, room_data in rooms.items():
            timeline = room_data.get("timeline", {})
            
            for event in timeline.get("events", []):
                if event.get("type") == "m.room.message":
                    await self._process_message(room_id, event)
    
    async def _process_message(self, room_id: str, event: Dict):
        """Procesar mensaje de Matrix"""
        sender = event.get("sender", "")
        
        # Ignorar mensajes propios
        if sender == self.user_id:
            return
        
        content = event.get("content", {})
        msgtype = content.get("msgtype", "")
        
        if msgtype != "m.text":
            return
        
        body = content.get("body", "")
        event_id = event.get("event_id", "")
        
        # Extrair displayname del sender
        sender_name = sender.split(":")[0].lstrip("@")
        
        msg = PlatformMessage(
            message_id=event_id,
            text=body,
            sender_id=sender,
            sender_name=sender_name,
            chat_id=room_id,
            chat_name=room_id.split(":")[0].lstrip("!"),
            chat_type="room",
            thread_id=None,
            platform="matrix",
            raw_data=event,
        )
        
        await self._handle_message(msg)
    
    async def send_message(self, response: PlatformResponse) -> bool:
        """Enviar mensaje a Matrix"""
        txn_id = f"eidos_{int(asyncio.get_event_loop().time() * 1000)}"
        
        content = {
            "msgtype": "m.text",
            "body": response.text,
        }
        
        endpoint = f"rooms/{response.chat_id}/send/m.room.message/{txn_id}"
        result = await self._api_call(endpoint, method="POST", **content)
        
        return "event_id" in result
    
    async def send_typing_indicator(self, chat_id: str):
        """Enviar typing indicator"""
        await self._api_call(
            f"rooms/{chat_id}/typing/{self.user_id}",
            method="POST",
            typing=True,
            timeout=30000
        )

from .base import register_adapter
register_adapter("matrix", MatrixAdapter)
