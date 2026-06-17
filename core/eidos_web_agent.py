"""
core/eidos_web_agent.py — Automatización web FIABLE por DOM (no OCR) [S122-I]
=============================================================================
SER quiere que EIDOS use la web (Gmail, n8n, cualquier sitio) él solo y de
forma FIABLE AL 100%. El OCR de pantalla adivina píxeles y falla en formularios.
La forma fiable: controlar el navegador por su DOM con Playwright vía CDP,
usando el navegador REAL de SER (Chromium del sistema) con su perfil — así
hereda las SESIONES ya iniciadas (Gmail logueado) sin tener que hacer login
(que Google bloquea a los bots).

Capacidades:
  - connect(): lanza el Chromium del sistema con remote-debugging + perfil de
    SER y conecta Playwright por CDP. Hereda cookies/sesiones.
  - goto(url), read_text(), find_click(texto), fill(label, valor), wait(sel)
  - gmail_search(query) / gmail_open_first() / gmail_get_body(): leer correo.

Credenciales/secretos: nunca se loguean. Usa la sesión existente del perfil.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("eidos.web_agent")

CHROME_PROFILE = os.path.expanduser("~/.config/chromium")
CDP_PORT = int(os.environ.get("EIDOS_CDP_PORT", "9333"))
# Binario del Chromium del sistema (el de SER, con su perfil/sesiones)
_CHROMIUM_BINS = ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable"]


def _which(b: str) -> Optional[str]:
    return shutil.which(b)


def _find_chromium() -> Optional[str]:
    for b in _CHROMIUM_BINS:
        p = _which(b)
        if p:
            return p
    return None


class WebAgent:
    """Agente web por DOM (Playwright + CDP al Chromium real de SER)."""

    def __init__(self, profile: str = CHROME_PROFILE, port: int = CDP_PORT):
        self.profile = profile
        self.port = port
        self._proc = None
        self._pw = None
        self._browser = None
        self.page = None

    # ── Conexión ────────────────────────────────────────────────────────────
    def connect(self, headless: bool = False, fresh_profile_copy: bool = True) -> bool:
        """Lanza el Chromium del sistema con CDP + perfil de SER y conecta.

        fresh_profile_copy=True: copia el perfil a un dir temporal para NO
        interferir con el Chromium que SER tenga abierto (evita el lock) pero
        conservando sus cookies/sesiones (Gmail logueado).
        """
        chromium = _find_chromium()
        if not chromium:
            log.error("No hay Chromium del sistema")
            return False

        user_dir = self.profile
        if fresh_profile_copy:
            # Copiar solo lo necesario para heredar sesión sin lock
            tmp = Path("/tmp/eidos_chrome_profile")
            try:
                if tmp.exists():
                    shutil.rmtree(tmp, ignore_errors=True)
                (tmp / "Default").mkdir(parents=True, exist_ok=True)
                src = Path(self.profile) / "Default"
                # Copiar cookies, login data, preferencias (sesión), Local Storage
                for item in ["Cookies", "Cookies-journal", "Login Data",
                             "Preferences", "Secure Preferences", "Network",
                             "Local Storage", "Session Storage"]:
                    s = src / item
                    d = tmp / "Default" / item
                    try:
                        if s.is_dir():
                            shutil.copytree(s, d, dirs_exist_ok=True)
                        elif s.exists():
                            shutil.copy2(s, d)
                    except Exception:
                        pass
                # 'Local State' a nivel raíz (clave de cifrado de cookies)
                try:
                    shutil.copy2(Path(self.profile) / "Local State", tmp / "Local State")
                except Exception:
                    pass
                user_dir = str(tmp)
            except Exception as e:
                log.warning("copia de perfil falló (%s); uso el perfil real", e)
                user_dir = self.profile

        # Lanzar Chromium con remote debugging
        args = [chromium,
                f"--remote-debugging-port={self.port}",
                f"--user-data-dir={user_dir}",
                "--no-first-run", "--no-default-browser-check",
                "--disable-session-crashed-bubble", "--restore-last-session=false"]
        if headless:
            args.append("--headless=new")
        env = {**os.environ, "DISPLAY": ":0"}
        self._proc = subprocess.Popen(args, env=env,
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
        # Esperar a que el puerto CDP esté listo
        import urllib.request
        ok = False
        for _ in range(30):
            try:
                urllib.request.urlopen(f"http://localhost:{self.port}/json/version", timeout=2)
                ok = True
                break
            except Exception:
                time.sleep(1)
        if not ok:
            log.error("CDP no respondió en :%d", self.port)
            return False

        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(f"http://localhost:{self.port}")
        ctx = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self.page = ctx.pages[0] if ctx.pages else ctx.new_page()
        log.info("WebAgent conectado (CDP :%d, perfil %s)", self.port, user_dir)
        return True

    def close(self):
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        try:
            if self._proc:
                self._proc.terminate()
        except Exception:
            pass

    # ── Acciones DOM fiables ─────────────────────────────────────────────────
    def goto(self, url: str, wait_ms: int = 3000) -> bool:
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            self.page.wait_for_timeout(wait_ms)
            return True
        except Exception as e:
            log.warning("goto %s falló: %s", url, e)
            return False

    def read_text(self, max_chars: int = 4000) -> str:
        try:
            return (self.page.inner_text("body") or "")[:max_chars]
        except Exception:
            return ""

    def find_click(self, text: str, timeout: int = 8000) -> bool:
        """Clica el primer elemento (botón/enlace/etc) que contenga el texto."""
        for sel in [f"text={text}", f"[aria-label*='{text}' i]",
                    f"button:has-text('{text}')", f"a:has-text('{text}')",
                    f"*:has-text('{text}')"]:
            try:
                el = self.page.locator(sel).first
                el.click(timeout=timeout)
                return True
            except Exception:
                continue
        return False

    def url(self) -> str:
        try:
            return self.page.url
        except Exception:
            return ""

    # ── Gmail ────────────────────────────────────────────────────────────────
    def gmail_search(self, query: str) -> bool:
        """Abre Gmail y busca `query`. Requiere sesión iniciada en el perfil."""
        from urllib.parse import quote
        return self.goto(f"https://mail.google.com/mail/u/0/#search/{quote(query)}", wait_ms=5000)

    def gmail_open_first(self) -> bool:
        """Abre el primer email de la lista de resultados."""
        try:
            self.page.locator("tr.zA").first.click(timeout=10000)
            self.page.wait_for_timeout(3000)
            return True
        except Exception as e:
            log.warning("gmail_open_first: %s", e)
            return False

    def gmail_get_body(self) -> str:
        """Texto del email abierto."""
        for sel in ["div.a3s", "div.ii.gt", "div[role='listitem']"]:
            try:
                t = self.page.locator(sel).first.inner_text(timeout=5000)
                if t and len(t) > 20:
                    return t
            except Exception:
                continue
        return self.read_text()


def get_singleton(**kw) -> WebAgent:
    return WebAgent(**kw)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    wa = WebAgent()
    if not wa.connect():
        print("No se pudo conectar")
        sys.exit(1)
    try:
        q = sys.argv[1] if len(sys.argv) > 1 else "n8n license"
        wa.gmail_search(q)
        print("URL:", wa.url())
        print("TEXTO:", wa.read_text(800))
    finally:
        wa.close()
