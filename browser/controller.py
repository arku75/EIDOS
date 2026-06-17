"""
Browser Controller - CDP para EIDOS
Control de Chrome/Chromium para scraping y automatización
"""

import asyncio
import base64
import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from urllib.parse import urljoin, urlparse

try:
    import aiohttp
    import aiohttp.web
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

logger = logging.getLogger(__name__)

class BrowserController:
    """
    Controlador de navegador via Chrome DevTools Protocol (CDP).
    Soporta Chrome/Chromium local o remoto.
    """
    
    def __init__(
        self,
        cdp_url: Optional[str] = None,
        headless: bool = True,
        window_size: tuple = (1280, 720)
    ):
        self.cdp_url = cdp_url
        self.headless = headless
        self.window_size = window_size
        self._chrome_process: Optional[subprocess.Popen] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_url: Optional[str] = None
        self._page_id: Optional[str] = None
        
    async def start(self) -> bool:
        """Iniciar navegador y conectar CDP"""
        if not HAS_AIOHTTP:
            logger.error("aiohttp requerido para browser")
            return False
        
        # Si no hay CDP URL, iniciar Chrome local
        if not self.cdp_url:
            if not await self._start_local_chrome():
                return False
        
        self._session = aiohttp.ClientSession()
        
        # Obtener WebSocket URL
        if not await self._get_ws_url():
            return False
        
        logger.info("✅ Browser controller started")
        return True
    
    async def stop(self):
        """Detener navegador"""
        if self._session:
            await self._session.close()
        
        if self._chrome_process:
            self._chrome_process.terminate()
            try:
                self._chrome_process.wait(timeout=5)
            except:
                self._chrome_process.kill()
        
        logger.info("Browser controller stopped")
    
    async def _start_local_chrome(self) -> bool:
        """Iniciar Chrome local con remote debugging"""
        port = 9222
        
        chrome_cmd = [
            "google-chrome" if self._find_chrome() == "google-chrome" else "chromium",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        
        if self.headless:
            chrome_cmd.append("--headless=new")
        
        chrome_cmd.append("about:blank")
        
        try:
            self._chrome_process = subprocess.Popen(
                chrome_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Esperar a que Chrome esté listo
            await asyncio.sleep(2)
            
            self.cdp_url = f"http://localhost:{port}"
            return True
            
        except Exception as e:
            logger.error(f"Failed to start Chrome: {e}")
            return False
    
    def _find_chrome(self) -> str:
        """Encontrar ejecutable de Chrome/Chromium"""
        for binary in ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]:
            try:
                subprocess.run(["which", binary], capture_output=True, check=True)
                return binary
            except:
                continue
        return "google-chrome"
    
    async def _get_ws_url(self) -> bool:
        """Obtener URL de WebSocket del debugger"""
        try:
            async with self._session.get(f"{self.cdp_url}/json/version") as resp:
                if resp.status != 200:
                    return False
            
            async with self._session.get(f"{self.cdp_url}/json/list") as resp:
                pages = await resp.json()
                if pages:
                    self._ws_url = pages[0].get("webSocketDebuggerUrl")
                    return True
                    
        except Exception as e:
            logger.error(f"Failed to get WS URL: {e}")
        
        return False
    
    async def navigate(self, url: str) -> bool:
        """Navegar a URL"""
        try:
            async with self._session.get(
                f"{self.cdp_url}/json/new?{url}"
            ) as resp:
                return resp.status == 200
        except Exception as e:
            logger.error(f"Navigation failed: {e}")
            return False
    
    async def get_content(self) -> str:
        """Obtener contenido de la página"""
        try:
            # Usar CDP Runtime.evaluate
            payload = {
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {"expression": "document.documentElement.outerHTML"}
            }
            
            async with self._session.ws_connect(self._ws_url) as ws:
                await ws.send_str(json.dumps(payload))
                
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if "result" in data:
                            result = data["result"].get("result", {})
                            return result.get("value", "")
                        break
                        
        except Exception as e:
            logger.error(f"Get content failed: {e}")
        
        return ""
    
    async def screenshot(self, full_page: bool = False) -> Optional[bytes]:
        """Tomar screenshot"""
        try:
            method = "Page.captureScreenshot"
            
            async with self._session.ws_connect(self._ws_url) as ws:
                await ws.send_str(json.dumps({"id": 1, "method": method, "params": {}}))
                
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if "result" in data:
                            screenshot_data = data["result"].get("data", "")
                            return base64.b64decode(screenshot_data)
                        break
                        
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
        
        return None
    
    async def click(self, selector: str) -> bool:
        """Hacer click en elemento"""
        try:
            js = f'document.querySelector("{selector}").click()'
            
            async with self._session.ws_connect(self._ws_url) as ws:
                await ws.send_str(json.dumps({
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": js}
                }))
                return True
                
        except Exception as e:
            logger.error(f"Click failed: {e}")
            return False
    
    async def type_text(self, selector: str, text: str) -> bool:
        """Escribir texto en input"""
        try:
            js = f'''
                const el = document.querySelector("{selector}");
                el.focus();
                el.value = "{text}";
                el.dispatchEvent(new Event("input"));
                el.dispatchEvent(new Event("change"));
            '''
            
            async with self._session.ws_connect(self._ws_url) as ws:
                await ws.send_str(json.dumps({
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": js}
                }))
                return True
                
        except Exception as e:
            logger.error(f"Type failed: {e}")
            return False
    
    async def scroll_to(self, y: int):
        """Scroll a posición Y"""
        js = f"window.scrollTo(0, {y})"
        await self._evaluate_js(js)
    
    async def _evaluate_js(self, expression: str) -> Any:
        """Evaluar JavaScript en la página"""
        try:
            async with self._session.ws_connect(self._ws_url) as ws:
                await ws.send_str(json.dumps({
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": expression, "returnByValue": True}
                }))
                
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        return data.get("result", {}).get("result", {}).get("value")
                        
        except Exception as e:
            logger.error(f"JS evaluation failed: {e}")
        
        return None

# Singleton
_controller: Optional[BrowserController] = None

def get_browser_controller(**kwargs) -> BrowserController:
    global _controller
    if _controller is None:
        _controller = BrowserController(**kwargs)
    return _controller
