"""
core/browser_manager.py — EIDOS navega el web usando Firefox con las sesiones de SER.

Usa el perfil Firefox existente (con cuentas ya logueadas).
Si necesita login → pregunta a SER qué cuenta usar.
"""
import logging
import time
import os
import hashlib
import time as t
import uuid
from core.db import get_conn
from pathlib import Path
from typing import Optional, Dict, Any, List

log = logging.getLogger("browser_manager")

# Browser state is EIDOS-owned by default. Authenticated user profiles require
# an explicit path supplied by the operator; they are never guessed or cloned.
EIDOS_HOME = Path(os.environ.get("EIDOS_HOME", str(Path.home() / ".eidos"))).expanduser()
FIREFOX_PROFILE = os.environ.get("EIDOS_FIREFOX_PROFILE", "")
EIDOS_BROWSER_PROFILE = str(EIDOS_HOME / "browser" / "firefox")
HEADLESS = False


def _get_browser():
    """Obtiene Firefox persistente con estado propiedad de EIDOS."""
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    browser = p.firefox.launch_persistent_context(
        user_data_dir=EIDOS_BROWSER_PROFILE,
        headless=HEADLESS,
        args=["--no-sandbox"],
        timeout=30000
    )
    return p, browser


def navigate(url: str, wait_for: str = "domcontentloaded",
             extract_text: bool = True) -> Dict[str, Any]:
    """
    EIDOS navega a una URL con las cuentas de SER ya logueadas.

    Args:
        url: URL a visitar
        wait_for: "domcontentloaded" | "networkidle" | "load"
        extract_text: si extraer el texto de la página

    Returns:
        {"ok": bool, "url": str, "title": str, "text": str, "screenshot": path}
    """
    p = None
    try:
        from playwright.sync_api import sync_playwright
        p = sync_playwright().start()

        browser = p.chromium.launch(headless=True)  # headless para research
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        )
        page = ctx.new_page()
        page.goto(url, wait_until=wait_for, timeout=20000)
        time.sleep(1)

        title = page.title()
        text = ""
        if extract_text:
            # Extraer texto limpio sin scripts/estilos
            text = page.evaluate("""() => {
                const els = document.querySelectorAll('script,style,nav,footer,header,aside');
                els.forEach(e => e.remove());
                return document.body ? document.body.innerText.slice(0, 5000) : '';
            }""")

        # Screenshot
        ts = int(time.time())
        shot_path = f"/tmp/eidos_browser_{ts}.png"
        page.screenshot(path=shot_path, full_page=False)

        browser.close()
        log.info("browser: navegué a %s | título: %s | texto: %d chars", url, title, len(text))

        # Guardar en brain
        _save_page_to_brain(url, title, text[:2000])

        return {"ok": True, "url": url, "title": title,
                "text": text[:3000], "screenshot": shot_path}

    except Exception as e:
        log.error("browser navigate error: %s", e)
        return {"ok": False, "url": url, "error": str(e)}
    finally:
        if p:
            try: p.stop()
            except: pass


def navigate_with_session(url: str, extract_text: bool = True) -> Dict[str, Any]:
    """
    Navega con un perfil Firefox indicado explícitamente por el operador.

    No se descubre ni reutiliza automáticamente el perfil autenticado del usuario.
    """
    p = None
    try:
        from playwright.sync_api import sync_playwright
        p = sync_playwright().start()

        if not FIREFOX_PROFILE:
            return {"ok": False, "url": url, "error": "session_profile_not_authorized"}

        ctx = p.firefox.launch_persistent_context(
            user_data_dir=FIREFOX_PROFILE,
            headless=True,  # headless para no interferir con el escritorio
            firefox_user_prefs={
                "dom.webdriver.enabled": False,
                "useAutomationExtension": False
            }
        )

        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        time.sleep(1.5)

        title = page.title()
        text = ""
        if extract_text:
            text = page.evaluate("""() => {
                const els = document.querySelectorAll('script,style,nav,footer,aside');
                els.forEach(e => e.remove());
                return document.body ? document.body.innerText.slice(0, 5000) : '';
            }""")

        ts = int(time.time())
        shot_path = f"/tmp/eidos_browser_{ts}.png"
        page.screenshot(path=shot_path)

        ctx.close()
        log.info("browser_session: %s | %s | %d chars", url, title, len(text))
        _save_page_to_brain(url, title, text[:2000])

        return {"ok": True, "url": url, "title": title,
                "text": text[:3000], "screenshot": shot_path}

    except Exception as e:
        log.error("browser_session error: %s", e)
        # Si falla con perfil (Firefox ya abierto), intentar sin perfil
        return navigate(url, extract_text=extract_text)
    finally:
        if p:
            try: p.stop()
            except: pass


def search_and_learn(query: str, max_results: int = 3) -> Dict[str, Any]:
    """
    EIDOS busca en DuckDuckGo y aprende de los resultados top.
    No necesita API key — usa DuckDuckGo directamente.
    """
    results = []
    search_url = f"https://duckduckgo.com/?q={query.replace(' ', '+')}&ia=web"

    try:
        from playwright.sync_api import sync_playwright
        p = sync_playwright().start()
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
        page = ctx.new_page()
        page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
        time.sleep(2)

        # Extraer links de resultados
        links = page.evaluate("""() => {
            const results = [];
            document.querySelectorAll('a[href^="https://"]').forEach(a => {
                const href = a.href;
                const text = a.innerText.trim();
                if (text.length > 10 && !href.includes('duckduckgo') &&
                    !href.includes('javascript') && results.length < 5) {
                    results.push({url: href, title: text});
                }
            });
            return results;
        }""")

        browser.close()
        p.stop()

        # Visitar los top resultados y extraer contenido
        for link in links[:max_results]:
            try:
                page_data = navigate(link["url"], extract_text=True)
                if page_data["ok"]:
                    results.append({
                        "url": link["url"],
                        "title": page_data["title"],
                        "content": page_data["text"][:1500]
                    })
                    log.info("search_learn: aprendí de %s", link["url"])
            except Exception:
                pass

        # Guardar todo en brain
        if results:
            _save_search_to_brain(query, results)

        return {"ok": True, "query": query, "results": results,
                "learned": len(results)}

    except Exception as e:
        log.error("search_and_learn error: %s", e)
        return {"ok": False, "query": query, "error": str(e)}


def ask_account_choice(service: str) -> str:
    """
    Cuando EIDOS necesita hacer login, pregunta a SER qué cuenta usar.
    Devuelve la cuenta elegida (o vacío si SER cancela).
    """
    # Guardar la solicitud en brain para que Colony la muestre a SER
    try:
        db = EIDOS_HOME / "evolution_brain.db"
        c = get_conn(db, timeout=3)
        now = t.time()
        c.execute(
            "INSERT OR REPLACE INTO knowledge_nodes "
            "(id,concept,definition,category,confidence,source,created_at,last_used,usage_count) "
            "VALUES (?,?,?,?,?,?,?,?,1)",
            (str(uuid.uuid4())[:16], f"browser:login_needed:{service}",
             f"EIDOS necesita hacer login en {service}. "
             f"SER: ¿qué cuenta Google quieres que use? "
             f"Dime el email y lo usaré para iniciar sesión.",
             "browser", 0.9, "browser_manager", now, now)
        )
    except Exception:
        pass

    log.info("browser: necesito login en %s — esperando respuesta de SER", service)
    return ""  # Colony mostrará la solicitud a SER en su próxima interacción


def _save_page_to_brain(url: str, title: str, text: str):
    """Guarda el contenido de una página en evolution_brain.db."""
    try:
        db = EIDOS_HOME / "evolution_brain.db"
        c = get_conn(db, timeout=3)
        node_id = hashlib.md5(f"browser:{url}".encode()).hexdigest()[:16]
        now = t.time()
        c.execute(
            "INSERT OR REPLACE INTO knowledge_nodes "
            "(id,concept,definition,category,confidence,source,created_at,last_used,usage_count) "
            "VALUES (?,?,?,?,?,?,?,?,1)",
            (node_id, f"web:{title[:60]}",
             f"URL: {url}\nTítulo: {title}\nContenido:\n{text}",
             "web_content", 0.75, "browser_manager", now, now)
        )
    except Exception:
        pass


def _save_search_to_brain(query: str, results: list):
    """Guarda los resultados de búsqueda en el brain."""
    try:
        db = EIDOS_HOME / "evolution_brain.db"
        c = get_conn(db, timeout=3)
        node_id = hashlib.md5(f"search:{query}".encode()).hexdigest()[:16]
        now = t.time()
        content = f"Búsqueda: {query}\n\n"
        for r in results:
            content += f"## {r['title']}\n{r['url']}\n{r['content'][:800]}\n\n"
        c.execute(
            "INSERT OR REPLACE INTO knowledge_nodes "
            "(id,concept,definition,category,confidence,source,created_at,last_used,usage_count) "
            "VALUES (?,?,?,?,?,?,?,?,1)",
            (node_id, f"search:{query[:60]}", content[:4000],
             "research", 0.8, "browser_manager", now, now)
        )
        log.info("brain: guardé búsqueda '%s' (%d resultados)", query, len(results))
    except Exception:
        pass
