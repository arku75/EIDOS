"""
core/eidos_playwright.py — Agente de Navegación Activa para EIDOS
Permite a EIDOS interactuar visualmente y dinámicamente con las páginas web.
"""
import os
import time
import logging
from typing import Optional

log = logging.getLogger("eidos.playwright")

class PlaywrightAgent:
    def __init__(self, headless: bool = False):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def start(self):
        if self.playwright is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
            self.playwright = sync_playwright().start()
            
            display = os.environ.get("DISPLAY", ":0")
            os.environ["DISPLAY"] = display
            
            # Sincronizar perfil de Firefox del usuario a una carpeta temporal para usar sus sesiones
            import subprocess
            import glob
            profile_dir = "/tmp/eidos_firefox_profile"
            user_profiles = glob.glob(os.path.expanduser("~/.mozilla/firefox/*.default-esr")) + glob.glob(os.path.expanduser("~/.mozilla/firefox/*.default"))
            if user_profiles:
                source_profile = user_profiles[0]
                subprocess.run(f"rm -rf {profile_dir} && rsync -a --copy-links --exclude 'lock' --exclude '.parentlock' {source_profile}/ {profile_dir}/", shell=True)
            
            # Usar Firefox de Playwright con el perfil clonado
            self.browser = self.playwright.firefox.launch_persistent_context(
                user_data_dir=profile_dir if user_profiles else "",
                headless=False,
                viewport={'width': 1920, 'height': 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            # persistent_context ya devuelve un BrowserContext, y puede tener páginas abiertas
            self.context = self.browser
            pages = self.context.pages
            self.page = pages[0] if pages else self.context.new_page()
            
            log.info("Playwright Agent Firefox iniciado (headless=%s)", self.headless)
        except Exception as e:
            log.error("Error iniciando Playwright: %s (¿Falta 'playwright install firefox'?)", e)
            self.stop()

    def stop(self):
        try:
            if self.context: self.context.close()
            if self.browser: self.browser.close()
            if self.playwright: self.playwright.stop()
        except Exception:
            pass
        self.context = None
        self.browser = None
        self.playwright = None
        self.page = None

    def goto(self, url: str, wait_time: int = 2) -> bool:
        if not self.page:
            self.start()
        if not self.page:
            return False
        try:
            self.page.goto(url, timeout=30000, wait_until="domcontentloaded")
            time.sleep(wait_time)
            if not self.headless:
                os.system("wmctrl -a 'Firefox' 2>/dev/null || wmctrl -a 'firefox' 2>/dev/null")
            return True
        except Exception as e:
            log.error("Error goto %s: %s", url, e)
            return False

    def scroll(self, pixels: int = 800):
        if not self.page: return
        try:
            self.page.evaluate(f"window.scrollBy(0, {pixels});")
            time.sleep(1)
        except Exception:
            pass

    def extract_text(self, max_chars: int = 5000) -> str:
        if not self.page: return ""
        try:
            text = self.page.evaluate("document.body.innerText")
            if not text: return ""
            return text[:max_chars]
        except Exception:
            return ""

    def click(self, text_or_selector: str) -> bool:
        if not self.page: return False
        try:
            # 1. Intentar por texto (case-insensitive parcial)
            locator = self.page.get_by_text(text_or_selector, exact=False).first
            if locator.count() > 0:
                locator.click(timeout=5000)
                time.sleep(2)
                return True
            # 2. Intentar como selector CSS puro
            self.page.click(text_or_selector, timeout=5000)
            time.sleep(2)
            return True
        except Exception as e:
            log.debug("No se pudo clicar '%s': %s", text_or_selector, e)
            return False

_agent = None

def get_playwright_agent(headless: bool = False) -> PlaywrightAgent:
    global _agent
    if _agent is None:
        _agent = PlaywrightAgent(headless=headless)
        _agent.start()
    return _agent
