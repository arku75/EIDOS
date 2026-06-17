"""
core/browser_native.py — EIDOS controla el Firefox REAL que ya está abierto.

SER tiene Firefox ESR corriendo con sus sesiones activas.
EIDOS NO abre un Firefox nuevo — usa el que ya está en pantalla via:
  1. xdotool → controla la ventana existente
  2. Vision loop → ve la pantalla, entiende qué hay, actúa
  3. Si Firefox no está abierto → lo abre con el perfil de SER

JAMÁS usa el modo "headless" ni aparece como Playwright al sitio.
"""
import subprocess, time, logging, json, os, re
from pathlib import Path
from typing import Dict, Any, Optional, List

log = logging.getLogger("browser_native")

FIREFOX_PROFILE = str(Path.home() / ".mozilla/firefox/yii3m1ky.default-esr")
EIDOS_HOME      = Path.home() / ".eidos"
DOWNLOADS_DIR   = EIDOS_HOME / "books"
SCREENSHOTS_DIR = EIDOS_HOME / "screenshots"


# ── Gestión de la ventana Firefox ─────────────────────────────────────────────

def get_firefox_wid() -> Optional[str]:
    """Busca el WID del Firefox ESR ya abierto — xdotool primero, wmctrl fallback."""
    # xdotool: devuelve decimal, fiable en Kali
    try:
        r = subprocess.run(['xdotool', 'search', '--name', 'Firefox'],
                           capture_output=True, text=True, timeout=5)
        if r.stdout.strip():
            return r.stdout.strip().split('\n')[0]
        r2 = subprocess.run(['xdotool', 'search', '--name', 'Mozilla Firefox'],
                            capture_output=True, text=True, timeout=5)
        if r2.stdout.strip():
            return r2.stdout.strip().split('\n')[0]
    except Exception:
        pass
    # fallback: wmctrl (devuelve hex)
    try:
        r = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            if "firefox" in line.lower():
                return line.split()[0]
    except Exception:
        pass
    return None


def open_firefox_if_needed() -> Optional[str]:
    """
    Si Firefox no está abierto, lo lanza con el perfil de SER.
    Retorna el WID cuando está listo.
    """
    wid = get_firefox_wid()
    if wid:
        log.info(f"Firefox ya abierto: WID={wid}")
        return wid

    log.info("Firefox no está abierto. Lanzando con perfil de SER...")
    subprocess.Popen([
        "firefox-esr",
        "--profile", FIREFOX_PROFILE,
        "--new-window", "about:blank"
    ])

    # Esperar a que aparezca en wmctrl
    for _ in range(20):
        time.sleep(1)
        wid = get_firefox_wid()
        if wid:
            time.sleep(2)  # esperar carga completa
            return wid

    log.error("No se pudo abrir Firefox")
    return None


def focus_firefox(wid: str) -> bool:
    """Enfoca la ventana de Firefox."""
    r = subprocess.run(["wmctrl", "-ia", wid], capture_output=True)
    time.sleep(0.3)
    return r.returncode == 0


def navigate_to(url: str, wid: str = None) -> Dict[str, Any]:
    """
    Navega a una URL usando el Firefox real de SER.
    Usa xdotool para escribir en la barra de direcciones — sin Playwright.
    """
    if not wid:
        wid = open_firefox_if_needed()
    if not wid:
        return {"ok": False, "error": "Firefox no disponible"}

    focus_firefox(wid)
    time.sleep(0.3)

    # Ctrl+L → abre la barra de direcciones
    subprocess.run(["xdotool", "key", "--window", wid, "ctrl+l"])
    time.sleep(0.4)

    # Borrar lo que haya y escribir la URL
    subprocess.run(["xdotool", "key", "--window", wid, "ctrl+a"])
    time.sleep(0.1)
    subprocess.run(["xdotool", "type", "--window", wid, "--clearmodifiers", url])
    time.sleep(0.2)
    subprocess.run(["xdotool", "key", "--window", wid, "Return"])

    time.sleep(3)  # Esperar carga
    log.info(f"Navegado a: {url}")
    return {"ok": True, "url": url, "wid": wid}


def get_page_title(wid: str) -> str:
    """Obtiene el título de la página leyendo el título de la ventana."""
    r = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith(wid):
            parts = line.split(None, 3)
            if len(parts) >= 4:
                return parts[3].replace(" - Mozilla Firefox", "").strip()
    return ""


def take_screenshot(wid: str = None, filename: str = None) -> Optional[Path]:
    """
    Toma screenshot del Firefox real (no headless).
    Usa scrot de la ventana enfocada.
    """
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    fname = filename or f"firefox_{int(time.time())}.png"
    out   = SCREENSHOTS_DIR / fname

    if wid:
        subprocess.run(["wmctrl", "-ia", wid])
        time.sleep(0.3)
        r = subprocess.run(["scrot", "-z", "--window", wid, str(out)],
                           capture_output=True)
        if r.returncode == 0 and out.exists():
            return out

    # Fallback: screenshot completo de escritorio
    r = subprocess.run(["scrot", "-z", str(out)], capture_output=True)
    return out if out.exists() else None


# ── Vision sobre Firefox real ──────────────────────────────────────────────────

def see_firefox(question: str, wid: str = None) -> Dict[str, Any]:
    """
    EIDOS toma screenshot del Firefox real y lo analiza con llama3.2-vision.
    Entiende qué hay en la página, qué botones ve, qué hay que hacer.
    """
    if not wid:
        wid = get_firefox_wid()

    screenshot = take_screenshot(wid)
    if not screenshot:
        return {"ok": False, "error": "No se pudo tomar screenshot"}

    # Analizar con vision
    try:
        from core.vision_loop import see_screen
        result = see_screen(question, wid=wid, fast=False)
        return {"ok": True, "vision": result, "screenshot": str(screenshot)}
    except Exception as e:
        # Fallback directo a ollama vision
        prompt = f"Eres EIDOS. Estás mirando el Firefox del usuario. {question}\n¿Qué ves en la pantalla? ¿Qué pasos dar?"
        r = subprocess.run(
            ["ollama", "run", "moondream:latest",
             f"[img]{screenshot}[/img]\n{prompt}"],
            capture_output=True, text=True, timeout=60
        )
        return {"ok": True, "vision": r.stdout.strip()[:1000], "screenshot": str(screenshot)}


# ── Descarga de libros usando Firefox REAL ─────────────────────────────────────

def download_from_zlibrary_native(book_url: str, title: str) -> Optional[Path]:
    """
    Descarga un libro de z-library usando el Firefox REAL de SER.

    Flujo con vision:
      1. Abrir/enfocar Firefox
      2. Navegar a la URL del libro
      3. Ver la página (screenshot + vision) → encontrar botón descarga
      4. Hacer click en el enlace /dl/
      5. Esperar archivo en ~/Downloads o directorio configurado
    """
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

    wid = open_firefox_if_needed()
    if not wid:
        return None

    # Navegar a la página del libro
    navigate_to(book_url, wid)
    time.sleep(3)

    # Ver la página con vision
    vision = see_firefox(
        f"¿Hay un botón de descarga de PDF o EPUB? ¿Cuál es el enlace de descarga? Busca texto como 'PDF', 'EPUB', 'Download', 'Descargar'.",
        wid
    )
    log.info(f"Vision del libro: {vision.get('vision', '')[:200]}")

    # Buscar y hacer click en el enlace de descarga via JavaScript
    # (xdotool no puede acceder al DOM, usamos el address bar para ejecutar JS)
    js_find_dl = """javascript:(function(){
        var links = document.querySelectorAll('a');
        var dlLink = null;
        for(var l of links){
            if(l.href.includes('/dl/') || l.textContent.toLowerCase().includes('pdf')){
                dlLink = l.href; break;
            }
        }
        if(dlLink) location.href=dlLink;
        else alert('No encontré enlace de descarga');
    })()"""

    # Ejecutar el bookmarklet en la barra de direcciones
    subprocess.run(["xdotool", "key", "--window", wid, "ctrl+l"])
    time.sleep(0.4)
    subprocess.run(["xdotool", "type", "--window", wid, "--clearmodifiers",
                    js_find_dl.replace("\n", " ")])
    time.sleep(0.2)
    subprocess.run(["xdotool", "key", "--window", wid, "Return"])
    time.sleep(5)  # Esperar descarga

    # Ver si apareció el diálogo de descarga de Firefox
    screenshot = take_screenshot(wid)
    if screenshot:
        log.info(f"Screenshot post-click: {screenshot}")

    # Buscar el archivo descargado en ~/Downloads o DOWNLOADS_DIR
    dl_dirs = [
        Path.home() / "Downloads",
        Path.home() / "Descargas",
        DOWNLOADS_DIR,
    ]

    for dl_dir in dl_dirs:
        if dl_dir.exists():
            recent = sorted(dl_dir.glob("*.pdf") | dl_dir.glob("*.epub") if False else
                           list(dl_dir.glob("*.pdf")) + list(dl_dir.glob("*.epub")),
                           key=lambda f: f.stat().st_mtime, reverse=True)
            if recent and (time.time() - recent[0].stat().st_mtime) < 60:
                log.info(f"Libro descargado: {recent[0]}")
                return recent[0]

    log.warning("No se encontró archivo descargado")
    return None


# ── Método mixto: Playwright con flags antidetección ──────────────────────────

def download_undetected(url: str, output_dir: Path) -> Optional[Path]:
    """
    Descarga usando Playwright con Firefox + flags para no ser detectado como bot.

    Diferencias vs headless normal:
      - headless=False → ventana visible (mismo que usuario real)
      - Usa el perfil real de SER (cookies, sesión)
      - Desactiva webdriver flag via about:config
      - Usa stealth prefs de Firefox
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    script = f"""
import asyncio, sys
from playwright.async_api import async_playwright
from pathlib import Path

STEALTH_PREFS = {{
    "dom.webdriver.enabled": False,
    "useAutomationExtension": False,
    "privacy.resistFingerprinting": False,
    "network.http.referer.spoofSource": True,
    "general.useragent.override": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
}}

async def download():
    async with async_playwright() as p:
        browser = await p.firefox.launch_persistent_context(
            user_data_dir="{FIREFOX_PROFILE}",
            headless=False,
            downloads_path="{output_dir}",
            accept_downloads=True,
            firefox_user_prefs=STEALTH_PREFS,
            viewport={{"width": 1920, "height": 1080}},
            locale="es-ES",
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()

        # Eliminar rastros de webdriver
        await page.add_init_script('''
            Object.defineProperty(navigator, "webdriver", {{get: () => undefined}});
            Object.defineProperty(navigator, "plugins", {{get: () => [1, 2, 3]}});
        ''')

        # Encontrar enlace de descarga
        await page.goto("{url}", timeout=30000)
        await page.wait_for_timeout(3000)

        # Buscar enlace /dl/ directo
        dl_href = await page.evaluate('''() => {{
            for (const a of document.querySelectorAll("a")) {{
                if (a.href.includes("/dl/")) return a.href;
            }}
            return null;
        }}''')

        if not dl_href:
            print("NO_DL_LINK")
            await browser.close()
            return

        # Navegar directo al link de descarga
        try:
            async with page.expect_download(timeout=90000) as dl_info:
                await page.goto(dl_href, wait_until="commit", timeout=30000)
            dl = await dl_info.value
            save_path = "{output_dir}/" + dl.suggested_filename
            await dl.save_as(save_path)
            print(save_path)
        except Exception as e:
            print("FAILED: " + str(e), file=sys.stderr)
            print("FAILED")
        finally:
            await browser.close()

asyncio.run(download())
"""
    tmp = Path("/tmp/zlib_undetected.py")
    tmp.write_text(script)

    r = subprocess.run(["python3", str(tmp)],
                       capture_output=True, text=True, timeout=120)
    output = r.stdout.strip()

    if output and not output.startswith("FAILED") and not output.startswith("NO_DL") and Path(output).exists():
        return Path(output)

    # Buscar por fecha
    files = sorted(output_dir.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
    if files and (time.time() - files[0].stat().st_mtime) < 120:
        return files[0]

    return None
