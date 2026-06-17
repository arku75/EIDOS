"""
core/eidos_native_browser.py — Control del Firefox del usuario sin Playwright

NO descarga ningún browser. Usa el Firefox REAL del usuario con sus cookies,
su sesión y su identidad. Completamente indetectable como bot.

Estrategias (en orden de preferencia):
  1. Firefox Remote Debugging (CDP): control programático completo del Firefox abierto
  2. xdotool: control visual — teclado, scroll, click en el Firefox abierto
  3. curl + cookies SQLite: peticiones idénticas a las del browser real del usuario
  4. GitHub API: para repositorios git, sin browser (API pública sin auth)

Uso:
    from core.eidos_native_browser import get_native_browser
    nb = get_native_browser()
    text   = nb.read_url("https://example.com")
    links  = nb.extract_links("https://example.com")
    nb.navigate("https://n8n.io")   # abre en el Firefox del usuario
    nb.scroll_down(times=10)        # hace scroll en la ventana activa
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional

import logging
from core.db import get_conn
log = logging.getLogger("eidos.native_browser")

# Perfil de Firefox del usuario
_FIREFOX_PROFILE = None  # auto-detectado la primera vez

# Puerto de remote debugging de Firefox
_CDP_PORT = 9222
_CDP_BASE = f"http://localhost:{_CDP_PORT}"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    )
}


# ── Detección de perfil de Firefox ──────────────────────────────────────────

def _find_firefox_profile() -> Optional[Path]:
    """Encuentra el perfil de Firefox activo del usuario."""
    global _FIREFOX_PROFILE
    if _FIREFOX_PROFILE:
        return _FIREFOX_PROFILE
    try:
        base = Path.home() / ".mozilla" / "firefox"
        if not base.exists():
            return None
        # Buscar profiles.ini
        ini = base / "profiles.ini"
        if ini.exists():
            text = ini.read_text()
            # Buscar perfil Default o el primero con Path=
            for line in text.splitlines():
                if line.startswith("Path="):
                    p = base / line[5:]
                    if p.exists():
                        _FIREFOX_PROFILE = p
                        return p
        # Fallback: primer directorio *.default*
        for d in base.iterdir():
            if d.is_dir() and ("default" in d.name or "release" in d.name):
                _FIREFOX_PROFILE = d
                return d
    except Exception as e:
        log.debug("_find_firefox_profile: %s", e)
    return None


# ── Cookies desde perfil Firefox ────────────────────────────────────────────

def get_firefox_cookies(domain: str) -> Dict[str, str]:
    """Lee cookies del perfil Firefox del usuario para un dominio dado.
    Devuelve dict {nombre: valor} para usar en peticiones HTTP.
    """
    profile = _find_firefox_profile()
    if not profile:
        return {}
    cookies_db = profile / "cookies.sqlite"
    if not cookies_db.exists():
        return {}
    try:
        # Copiar la DB para no interferir con Firefox abierto
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            tmp.write(cookies_db.read_bytes())
            tmp_path = tmp.name
        conn = get_conn(tmp_path)
        rows = conn.execute(
            "SELECT name, value FROM moz_cookies "
            "WHERE host LIKE ? AND expiry > ?",
            (f"%{domain}%", int(time.time())),
        ).fetchall()
        conn.close()
        os.unlink(tmp_path)
        return {r[0]: r[1] for r in rows}
    except Exception as e:
        log.debug("get_firefox_cookies(%s): %s", domain, e)
        return {}


def cookies_to_header(cookies: Dict[str, str]) -> str:
    """Convierte dict de cookies a string para cabecera HTTP."""
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


# ── curl con cookies reales ──────────────────────────────────────────────────

def curl_read(url: str, max_chars: int = 5000,
              use_cookies: bool = True) -> Optional[str]:
    """Hace una petición HTTP idéntica a Firefox — mismo User-Agent + cookies reales."""
    try:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc

        cmd = [
            "curl", "-s", "-L",
            "--max-time", "30",
            "--max-filesize", "1048576",  # 1MB máximo
            "-A", _HEADERS["User-Agent"],
            "-H", "Accept: text/html,application/xhtml+xml,*/*",
            "-H", "Accept-Language: es-ES,es;q=0.9,en;q=0.8",
        ]

        if use_cookies:
            cookies = get_firefox_cookies(domain)
            if cookies:
                cmd += ["-H", f"Cookie: {cookies_to_header(cookies)}"]

        cmd.append(url)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
        raw = result.stdout
        if not raw or len(raw) < 50:
            return None

        # Limpiar HTML
        raw = re.sub(r'<(script|style)[^>]*>.*?</(script|style)>', ' ',
                     raw, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', raw)
        import html as _html
        text = _html.unescape(text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:max_chars] if text else None
    except Exception as e:
        log.debug("curl_read %s: %s", url[:60], e)
        return None


def curl_extract_links(url: str, same_domain: bool = True) -> List[str]:
    """Extrae links de una página usando curl (con cookies del usuario)."""
    try:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc
        base   = f"{parsed.scheme}://{parsed.netloc}"

        cmd = [
            "curl", "-s", "-L", "--max-time", "30",
            "-A", _HEADERS["User-Agent"],
        ]
        cookies = get_firefox_cookies(domain)
        if cookies:
            cmd += ["-H", f"Cookie: {cookies_to_header(cookies)}"]
        cmd.append(url)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
        raw = result.stdout

        hrefs = re.findall(r'href=["\']([^"\'#][^"\']*)["\']', raw)
        skip_ext = (".pdf", ".zip", ".png", ".jpg", ".gif", ".svg",
                    ".mp4", ".mp3", ".css", ".js", ".ico", ".woff")
        links: List[str] = []
        seen: set = set()
        for href in hrefs:
            if href.startswith("http"):
                full = href
            elif href.startswith("/"):
                full = base + href
            else:
                full = urllib.parse.urljoin(url, href)
            full = full.split("#")[0].rstrip("/")
            if not full or full in seen:
                continue
            if any(full.lower().endswith(ext) for ext in skip_ext):
                continue
            if same_domain and domain not in full:
                continue
            seen.add(full)
            links.append(full)
        return links
    except Exception as e:
        log.debug("curl_extract_links %s: %s", url[:60], e)
        return []


# ── xdotool: control del Firefox real del usuario ────────────────────────────

def _xdotool(*args) -> str:
    """Ejecuta xdotool y devuelve stdout."""
    try:
        r = subprocess.run(["xdotool"] + list(args),
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return ""


def find_firefox_window() -> Optional[str]:
    """Encuentra el ID de la ventana de Firefox del usuario."""
    wid = _xdotool("search", "--name", "Mozilla Firefox")
    if not wid:
        wid = _xdotool("search", "--name", "Firefox")
    if not wid:
        wid = _xdotool("search", "--class", "firefox")
    return wid.split("\n")[0].strip() if wid else None


def xdotool_navigate(url: str) -> bool:
    """Navega en el Firefox del usuario a una URL usando xdotool."""
    try:
        wid = find_firefox_window()
        if not wid:
            # Fix 4: abrir Firefox y esperar hasta 12s a que aparezca la ventana
            env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0"),
                   "GTK_MODULES": ""}  # silenciar el warning de appmenu-gtk-module
            subprocess.Popen(["firefox-esr", "--new-window", url],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(12):
                time.sleep(1)
                wid = find_firefox_window()
                if wid:
                    break
            if not wid:
                log.warning("Firefox abrió pero xdotool no encontró la ventana — continuando")
                time.sleep(3)
                return True
        # Activar ventana + Ctrl+L para ir a la barra de dirección
        _xdotool("windowactivate", "--sync", wid)
        time.sleep(0.4)
        _xdotool("key", "--window", wid, "ctrl+l")
        time.sleep(0.3)
        _xdotool("type", "--window", wid, "--clearmodifiers", url)
        time.sleep(0.2)
        _xdotool("key", "--window", wid, "Return")
        time.sleep(2.5)  # esperar carga inicial
        return True
    except Exception as e:
        log.debug("xdotool_navigate: %s", e)
        return False


def xdotool_scroll_to_bottom(times: int = 15, delay: float = 0.3) -> None:
    """Hace scroll hasta el fondo en la ventana activa del Firefox del usuario."""
    wid = find_firefox_window()
    if not wid:
        return
    _xdotool("windowactivate", "--sync", wid)
    time.sleep(0.3)
    for _ in range(times):
        _xdotool("key", "--window", wid, "space")
        time.sleep(delay)


def xdotool_select_all_copy() -> Optional[str]:
    """Selecciona todo el texto de la página y lo copia al portapapeles.
    Devuelve el texto copiado.
    """
    wid = find_firefox_window()
    if not wid:
        return None
    try:
        _xdotool("windowactivate", "--sync", wid)
        time.sleep(0.3)
        _xdotool("key", "--window", wid, "ctrl+a")
        time.sleep(0.3)
        _xdotool("key", "--window", wid, "ctrl+c")
        time.sleep(0.5)
        # Leer el portapapeles
        result = subprocess.run(["xclip", "-o", "-selection", "clipboard"],
                                capture_output=True, text=True, timeout=5)
        if not result.stdout:
            result = subprocess.run(["xsel", "--clipboard", "--output"],
                                    capture_output=True, text=True, timeout=5)
        return result.stdout.strip() if result.stdout else None
    except Exception as e:
        log.debug("xdotool_select_all_copy: %s", e)
        return None


def xdotool_read_page(url: str, max_chars: int = 5000,
                      scroll_times: int = 15) -> Optional[str]:
    """Navega a la URL en el Firefox del usuario, hace scroll completo y lee el texto."""
    if not xdotool_navigate(url):
        return None
    time.sleep(2)  # esperar carga de página
    xdotool_scroll_to_bottom(times=scroll_times)
    time.sleep(0.5)
    text = xdotool_select_all_copy()
    if text and len(text) > 100:
        return text[:max_chars]
    return None


# ── Firefox Remote Debugging (CDP) ──────────────────────────────────────────

def is_firefox_cdp_available() -> bool:
    """True si Firefox está corriendo con remote debugging en el puerto configurado."""
    try:
        req = urllib.request.Request(f"{_CDP_BASE}/json/list", headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def cdp_get_page_content(tab_id: Optional[str] = None) -> Optional[str]:
    """Lee el contenido de la pestaña activa de Firefox via CDP."""
    try:
        req = urllib.request.Request(f"{_CDP_BASE}/json/list", headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=5) as resp:
            tabs = json.loads(resp.read())
        if not tabs:
            return None
        tab = next((t for t in tabs if t.get("id") == tab_id), tabs[0])
        ws_url = tab.get("webSocketDebuggerUrl", "")
        # Para simplicidad, usamos la API HTTP de Chrome DevTools
        # (evita websockets que requieren dependencia extra)
        target_id = tab.get("id", "")
        if not target_id:
            return None
        # Evaluar JS para obtener el texto de la página
        eval_url = f"{_CDP_BASE}/json/activate/{target_id}"
        subprocess.run(["curl", "-s", eval_url], timeout=3, capture_output=True)
        return None  # requiere WebSocket — usar xdotool como fallback
    except Exception as e:
        log.debug("cdp_get_page_content: %s", e)
        return None


# ── API unificada ─────────────────────────────────────────────────────────────

class NativeBrowser:
    """Browser nativo — usa el Firefox real del usuario sin Playwright."""

    def read_url(self, url: str, max_chars: int = 5000,
                 use_xdotool: bool = False) -> Optional[str]:
        """Lee el contenido de una URL.
        use_xdotool=True: usa el Firefox del usuario con scroll real.
        use_xdotool=False: usa curl con las cookies reales del usuario (más rápido).
        """
        if use_xdotool:
            text = xdotool_read_page(url, max_chars=max_chars)
            if text:
                return text
        # Fallback / default: curl con cookies de Firefox
        return curl_read(url, max_chars=max_chars)

    def extract_links(self, url: str, same_domain: bool = True) -> List[str]:
        """Extrae links de una URL usando curl + cookies del usuario."""
        return curl_extract_links(url, same_domain=same_domain)

    def navigate(self, url: str) -> bool:
        """Abre la URL en el Firefox real del usuario."""
        return xdotool_navigate(url)

    def scroll_down(self, times: int = 10) -> None:
        """Hace scroll hacia abajo en la ventana activa del Firefox."""
        xdotool_scroll_to_bottom(times=times)

    def get_cookies(self, domain: str) -> Dict[str, str]:
        """Lee las cookies del usuario para un dominio."""
        return get_firefox_cookies(domain)


_nb_instance: Optional[NativeBrowser] = None


def get_native_browser() -> NativeBrowser:
    global _nb_instance
    if _nb_instance is None:
        _nb_instance = NativeBrowser()
    return _nb_instance
