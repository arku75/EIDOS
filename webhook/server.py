"""
Webhook Server para integración GitHub/GitLab/etc
"""

import hashlib
import hmac
import json
import logging
from typing import Dict, Any, Optional, Callable

try:
    from aiohttp import web
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False
    web = None

logger = logging.getLogger(__name__)

class WebhookServer:
    """
    Webhook server para recibir eventos externos.
    Soporta GitHub, GitLab, y webhooks genéricos.
    """
    
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8644,
        secret: Optional[str] = None
    ):
        self.host = host
        self.port = port
        self.secret = secret
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._handlers: Dict[str, Callable] = {}
        
    async def start(self) -> bool:
        """Iniciar webhook server"""
        if not HAS_AIOHTTP:
            logger.error("aiohttp requerido para webhook server")
            return False
        
        self._app = web.Application()
        
        # Routes
        self._app.router.add_post("/webhook/{provider}", self._handle_webhook)
        self._app.router.add_get("/webhook/health", self._health)
        
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        
        logger.info(f"✅ Webhook Server started on {self.host}:{self.port}")
        return True
    
    async def stop(self):
        """Detener webhook server"""
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()
        logger.info("Webhook Server stopped")
    
    def register_handler(self, provider: str, handler: Callable):
        """Registrar handler para un provider"""
        self._handlers[provider] = handler
        logger.info(f"Registered webhook handler: {provider}")
    
    async def _health(self, request):
        """Health check"""
        return web.json_response({"status": "ok"})
    
    async def _handle_webhook(self, request):
        """Manejar webhook entrante"""
        provider = request.match_info.get("provider", "generic")
        
        try:
            body = await request.read()
            
            # Verificar firma si hay secret
            if self.secret:
                signature = request.headers.get("X-Hub-Signature-256", "")
                if not self._verify_signature(body, signature):
                    return web.json_response(
                        {"error": "Invalid signature"},
                        status=401
                    )
            
            # Parsear payload
            try:
                payload = json.loads(body)
            except:
                payload = {"raw": body.decode('utf-8', errors='replace')}
            
            # Enriquecer con headers
            event = {
                "provider": provider,
                "payload": payload,
                "headers": dict(request.headers),
                "event_type": request.headers.get("X-GitHub-Event", "unknown")
            }
            
            # Llamar handler
            handler = self._handlers.get(provider)
            if handler:
                try:
                    result = await handler(event)
                    return web.json_response({"ok": True, "result": result})
                except Exception as e:
                    logger.error(f"Handler error: {e}")
                    return web.json_response({"ok": False, "error": str(e)})
            else:
                logger.warning(f"No handler for provider: {provider}")
                return web.json_response({"ok": True, "note": "no handler"})
                
        except Exception as e:
            logger.error(f"Webhook error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    def _verify_signature(self, body: bytes, signature: str) -> bool:
        """Verificar HMAC signature"""
        if not signature.startswith("sha256="):
            return False
        
        expected = signature[7:]
        computed = hmac.new(
            self.secret.encode(),
            body,
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(expected, computed)

# Singleton
_server: Optional[WebhookServer] = None

def get_webhook_server(**kwargs) -> WebhookServer:
    global _server
    if _server is None:
        _server = WebhookServer(**kwargs)
    return _server
