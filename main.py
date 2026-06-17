"""
EIDOS Gateway Integration - Entry Point Principal
"""

import asyncio
import logging
import sys
from pathlib import Path

# Setup path
sys.path.insert(0, str(Path(__file__).parent))

from config.env_loader import load_eidos_dotenv, ensure_eidos_home
from config.loader import load_config
from gateway.run import GatewayRunner, GatewayConfig
from gateway.cron.scheduler import get_scheduler
# [JUBILADO S126] api/server.py movido a api/server.py.dead
# from api.server import get_api_server
from webhook.server import get_webhook_server
from utils.security import get_security_checker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

class EidosIntegratedSystem:
    """
    Sistema EIDOS integrado con todos los componentes de Hermes.
    """
    
    def __init__(self):
        self.config = load_config()
        self.gateway = None
        # [JUBILADO S126] api/server.py → .dead
        # self.api_server = None
        self.webhook_server = None
        self.scheduler = None
        self.security = get_security_checker()
        
    async def start(self):
        """Iniciar todos los servicios"""
        logger.info("🚀 Starting EIDOS Integrated System...")
        
        # Asegurar directorios
        ensure_eidos_home()
        
        # 1. Iniciar Cron Scheduler
        if self.config.get("cron", {}).get("enabled", True):
            self.scheduler = get_scheduler()
            self.scheduler.start(interval=60)
            logger.info("✅ Cron scheduler started")
        
        # 2. Iniciar Gateway
        gw_config = self.config.get("gateway", {})
        if gw_config.get("enabled", True):
            gateway_config = GatewayConfig(
                port=gw_config.get("port", 18789),
                host=gw_config.get("host", "127.0.0.1"),
                enabled_platforms=gw_config.get("enabled_platforms", ["local"]),
                platform_configs=self.config.get("platforms", {})
            )
            
            # Crear y configurar gateway runner
            self.gateway = GatewayRunner(gateway_config)
            
            # Iniciar en background
            asyncio.create_task(self._run_gateway())
            logger.info("✅ Gateway starting...")
        
        # 3. Iniciar API Server [JUBILADO S126] api/server.py → .dead
        # api_config = self.config.get("api", {})
        # if api_config.get("enabled", False):
        #     self.api_server = get_api_server(
        #         host=api_config.get("host", "127.0.0.1"),
        #         port=api_config.get("port", 8642),
        #         api_key=api_config.get("api_key"),
        #         model_name=api_config.get("model_name", "eidos")
        #     )
        #     await self.api_server.start()
        #     logger.info("✅ API server started")
        logger.info("⏭️  API server skipped (jubilado S126)")
        
        # 4. Iniciar Webhook Server
        webhook_config = self.config.get("webhook", {})
        if webhook_config.get("enabled", False):
            self.webhook_server = get_webhook_server(
                host=webhook_config.get("host", "127.0.0.1"),
                port=webhook_config.get("port", 8644),
                secret=webhook_config.get("secret")
            )
            await self.webhook_server.start()
            logger.info("✅ Webhook server started")
        
        logger.info("🎉 EIDOS Integrated System fully operational!")
        
        # Mantener running
        while True:
            await asyncio.sleep(1)
    
    async def _run_gateway(self):
        """Ejecutar gateway"""
        try:
            await self.gateway.run()
        except Exception as e:
            logger.error(f"Gateway error: {e}")
    
    async def stop(self):
        """Detener todos los servicios"""
        logger.info("🛑 Stopping EIDOS Integrated System...")
        
        if self.scheduler:
            self.scheduler.stop()
        
        if self.gateway:
            self.gateway.stop()
        
        # [JUBILADO S126] api/server.py → .dead
        # if self.api_server:
        #     await self.api_server.stop()
        
        if self.webhook_server:
            await self.webhook_server.stop()
        
        logger.info("✅ System stopped")

async def main():
    """Entry point principal"""
    system = EidosIntegratedSystem()
    
    try:
        await system.start()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        await system.stop()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        await system.stop()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
