"""
EIDOS Action Executor — el primer latido (S115).

El SISTEMA NERVIOSO que faltaba: conecta la intención (router) con los órganos
que YA existen (browser_native, screen_scanner, OCR) y cierra el bucle
acción → percepción → memoria → respuesta.

Esto es lo que convierte a EIDOS de "loro que describe" en "agente que actúa".

Flujo del bucle (sin LLM):
  1. extract_action(texto)  → slots {action, target, url, report_vision}
  2. execute_action(slots)  → abre navegador/app, navega
  3. perceive()             → captura pantalla + OCR (tesseract)
  4. remember()             → guarda lo percibido como nodo en el grafo
  5. respond()              → genera respuesta NLG con lo que vio

ZERO LLM. ZERO VLM. ZERO API key. OCR = leer texto, no "ver" imágenes (honesto).
Construido sobre piezas existentes: browser_native, screen_scanner, grafo.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse as _up
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("eidos.action")

BRAIN_DB = str(Path.home() / ".eidos" / "evolution_brain.db")

# ── Vocabulario de acciones (sin LLM: sinónimos → verbo canónico) ─────────────
_OPEN_VERBS = (
    "abre", "abrir", "abreme", "ábreme", "lanza", "lanzar", "inicia", "iniciar",
    "ve a", "entra en", "entra a", "entra", "navega a", "navega", "vete a",
    "open", "go to", "launch", "start", "visita",
)
_SEARCH_VERBS = ("busca", "buscar", "búscame", "search", "googlea", "investiga en")

# Apps/sitios conocidos → URL (ampliable; EIDOS aprende más con el tiempo)
_KNOWN_TARGETS = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "telegram": "https://web.telegram.org",
    "wikipedia": "https://es.wikipedia.org",
    "kali": "https://www.kali.org",
    "duckduckgo": "https://duckduckgo.com",
    "chatgpt": "https://chat.openai.com",
}

# Motores/fuentes de búsqueda → plantilla de URL con la query. Antes solo se
# construía para google/youtube y DuckDuckGo se quedaba en la home SIN buscar
# (bug: EIDOS abría el buscador y se paraba). Mapa AMPLIO → EIDOS puede buscar y
# aprender en muchísimas fuentes (general, conocimiento, código, docs, académico,
# seguridad, paquetes, aprendizaje). La URL es lo que se ABRE visible para SER;
# la lectura/síntesis real la hace research_now (que busca por su cuenta).
_SEARCH_ENGINES = {
    # ── Buscadores generales ───────────────────────────────────────────────
    "duckduckgo":      "https://duckduckgo.com/?q={q}",
    "google":          "https://www.google.com/search?q={q}",
    "bing":            "https://www.bing.com/search?q={q}",
    "brave":           "https://search.brave.com/search?q={q}",
    "startpage":       "https://www.startpage.com/sp/search?query={q}",
    "ecosia":          "https://www.ecosia.org/search?q={q}",
    "qwant":           "https://www.qwant.com/?q={q}",
    "yandex":          "https://yandex.com/search/?text={q}",
    "baidu":           "https://www.baidu.com/s?wd={q}",
    "mojeek":          "https://www.mojeek.com/search?q={q}",
    "searx":           "https://searx.be/search?q={q}",
    "you":             "https://you.com/search?q={q}",
    "perplexity":      "https://www.perplexity.ai/search?q={q}",
    "marginalia":      "https://search.marginalia.nu/search?query={q}",
    # ── Conocimiento / enciclopedias ───────────────────────────────────────
    "wikipedia":       "https://es.wikipedia.org/w/index.php?search={q}",
    "wikipedia_en":    "https://en.wikipedia.org/w/index.php?search={q}",
    "wiktionary":      "https://es.wiktionary.org/w/index.php?search={q}",
    "wikidata":        "https://www.wikidata.org/w/index.php?search={q}",
    "wolframalpha":    "https://www.wolframalpha.com/input?i={q}",
    "britannica":      "https://www.britannica.com/search?query={q}",
    "archive":         "https://archive.org/search?query={q}",
    # ── Académico / papers ─────────────────────────────────────────────────
    "scholar":         "https://scholar.google.com/scholar?q={q}",
    "arxiv":           "https://arxiv.org/search/?query={q}&searchtype=all",
    "pubmed":          "https://pubmed.ncbi.nlm.nih.gov/?term={q}",
    "semanticscholar": "https://www.semanticscholar.org/search?q={q}",
    "paperswithcode":  "https://paperswithcode.com/search?q={q}",
    "gutenberg":       "https://www.gutenberg.org/ebooks/search/?query={q}",
    # ── Código / desarrollo ────────────────────────────────────────────────
    "github":          "https://github.com/search?q={q}&type=repositories",
    "gitlab":          "https://gitlab.com/search?search={q}",
    "sourcegraph":     "https://sourcegraph.com/search?q={q}",
    "grep":            "https://grep.app/search?q={q}",
    "stackoverflow":   "https://stackoverflow.com/search?q={q}",
    "stackexchange":   "https://stackexchange.com/search?q={q}",
    # ── Documentación / aprender a programar ───────────────────────────────
    "mdn":             "https://developer.mozilla.org/en-US/search?q={q}",
    "devdocs":         "https://devdocs.io/#q={q}",
    "readthedocs":     "https://readthedocs.org/search/?q={q}",
    "geeksforgeeks":   "https://www.geeksforgeeks.org/?s={q}",
    "freecodecamp":    "https://www.freecodecamp.org/news/search/?query={q}",
    # ── Comunidades / Q&A ──────────────────────────────────────────────────
    "reddit":          "https://www.reddit.com/search/?q={q}",
    "hackernews":      "https://hn.algolia.com/?q={q}",
    "quora":           "https://www.quora.com/search?q={q}",
    "medium":          "https://medium.com/search?q={q}",
    "devto":           "https://dev.to/search?q={q}",
    # ── Vídeo / cursos ─────────────────────────────────────────────────────
    "youtube":         "https://www.youtube.com/results?search_query={q}",
    "coursera":        "https://www.coursera.org/search?query={q}",
    "edx":             "https://www.edx.org/search?q={q}",
    "khanacademy":     "https://www.khanacademy.org/search?page_search_query={q}",
    # ── Seguridad / Kali (dominio de SER) ──────────────────────────────────
    "exploitdb":       "https://www.exploit-db.com/search?q={q}",
    "cve":             "https://cve.mitre.org/cgi-bin/cvekey.cgi?keyword={q}",
    "nvd":             "https://nvd.nist.gov/vuln/search/results?query={q}&search_type=all",
    "manpages":        "https://manpages.debian.org/jump?q={q}",
    "gtfobins":        "https://gtfobins.github.io/#{q}",
    # ── Paquetes / repos de software ───────────────────────────────────────
    "pypi":            "https://pypi.org/search/?q={q}",
    "npm":             "https://www.npmjs.com/search?q={q}",
    "crates":          "https://crates.io/search?q={q}",
    "dockerhub":       "https://hub.docker.com/search?q={q}",
    "pkgs":            "https://pkgs.org/search/?q={q}",
    "repology":        "https://repology.org/projects/?search={q}",
    # ── IA / modelos / datos ───────────────────────────────────────────────
    "huggingface":     "https://huggingface.co/search/full-text?q={q}",
    "kaggle":          "https://www.kaggle.com/search?q={q}",
    # ── Traducción / idiomas ───────────────────────────────────────────────
    "linguee":         "https://www.linguee.com/espanol-ingles/search?query={q}",
}

# Alias hablados → clave canónica de _SEARCH_ENGINES (multi-palabra y variantes).
_ENGINE_ALIASES = {
    "ddg": "duckduckgo", "duck duck go": "duckduckgo",
    "google scholar": "scholar", "scholar google": "scholar",
    "stack overflow": "stackoverflow", "stack-overflow": "stackoverflow",
    "stack exchange": "stackexchange",
    "hacker news": "hackernews", "hn": "hackernews",
    "exploit-db": "exploitdb", "exploit db": "exploitdb", "exploitdb": "exploitdb",
    "man": "manpages", "manpage": "manpages", "man pages": "manpages",
    "wiki": "wikipedia", "wikipedia inglés": "wikipedia_en",
    "wikipedia ingles": "wikipedia_en", "english wikipedia": "wikipedia_en",
    "hugging face": "huggingface", "papers with code": "paperswithcode",
    "dev.to": "devto", "geeks for geeks": "geeksforgeeks", "gfg": "geeksforgeeks",
    "wolfram": "wolframalpha", "wolfram alpha": "wolframalpha",
    "semantic scholar": "semanticscholar", "khan academy": "khanacademy",
    "khan": "khanacademy", "free code camp": "freecodecamp",
    "docker hub": "dockerhub", "docker": "dockerhub",
    "read the docs": "readthedocs", "you.com": "you", "grep.app": "grep",
    "archive.org": "archive", "internet archive": "archive",
    "national vulnerability database": "nvd",
}
# Pistas de "búscalo en internet/la web" sin nombrar motor → DuckDuckGo por defecto.
_WEB_HINTS = ("internet", "la web", "en web", "buscador", "en línea", "en linea",
              "online", "la red")

# Señales de que SER quiere que EIDOS reporte lo que percibe
_REPORT_SIGNALS = (
    "dime qué ves", "dime que ves", "qué ves", "que ves", "qué hay", "que hay",
    "léelo", "lee", "describe", "qué aparece", "que aparece", "dime qué",
    "qué dice", "que dice", "muéstrame", "report", "what do you see",
)


def _extract_url(text: str) -> Optional[str]:
    """Extrae una URL explícita o un dominio del texto."""
    m = re.search(r"https?://[^\s]+", text)
    if m:
        return m.group(0)
    # dominio suelto tipo "ejemplo.com"
    m = re.search(r"\b([a-z0-9-]+\.[a-z]{2,}(?:/[^\s]*)?)\b", text.lower())
    if m and "." in m.group(1):
        return "https://" + m.group(1)
    return None


def extract_action(text: str) -> Dict[str, Any]:
    """Slot-filling determinista: de lenguaje natural a una acción estructurada."""
    low = text.lower()
    slots: Dict[str, Any] = {
        "action": None, "target": None, "url": None, "engine": None,
        "report_vision": False, "query": None, "raw": text,
    }

    # ¿Pide reportar lo que ve?
    slots["report_vision"] = any(sig in low for sig in _REPORT_SIGNALS)

    # 1. URL explícita
    url = _extract_url(text)
    if url:
        slots["action"] = "open_url"
        slots["url"] = url
        slots["target"] = url
        return slots

    has_open = any(v in low for v in _OPEN_VERBS)
    has_search = any(v in low for v in _SEARCH_VERBS)

    # 2a. Buscador NOMBRADO: "busca X en <motor>" (incl. alias multi-palabra).
    #     El motor es lo que va tras "en/in". Se prueba el nombre más largo
    #     primero ("google scholar" gana a "google") y con límites de palabra
    #     ("man" no casa con "alemania"). EIDOS busca DE VERDAD ahí, no se para.
    if has_search:
        _eng_tokens = sorted(
            set(_SEARCH_ENGINES.keys()) | set(_ENGINE_ALIASES.keys()),
            key=len, reverse=True)
        for tok in _eng_tokens:
            eng = _ENGINE_ALIASES.get(tok, tok)
            if eng not in _SEARCH_ENGINES:
                continue
            if re.search(r"(?:\ben\b|\bin\b)\s+" + re.escape(tok) + r"\b", low):
                q = _extract_query(text, tok)
                if q:
                    slots["action"] = "search"
                    slots["engine"] = eng
                    slots["target"] = eng
                    slots["query"] = q
                    slots["url"] = _SEARCH_ENGINES[eng].format(q=_up.quote_plus(q))
                    return slots

    # 2. Target conocido (youtube, google...) con verbo de apertura
    for name, target_url in _KNOWN_TARGETS.items():
        if name in low:
            slots["target"] = name
            slots["url"] = target_url
            slots["action"] = "open_url"
            # ¿Búsqueda en un buscador? "busca X en duckduckgo/google/youtube/bing"
            if has_search and name in _SEARCH_ENGINES:
                q = _extract_query(text, name)
                if q:
                    slots["query"] = q
                    slots["engine"] = name
                    slots["url"] = _SEARCH_ENGINES[name].format(q=_up.quote_plus(q))
                    slots["action"] = "search"
            return slots

    # 2b. "busca X en internet / en la web" sin nombrar motor → DuckDuckGo por defecto
    if has_search and any(h in low for h in _WEB_HINTS):
        q = _extract_query(text, "internet")
        if q:
            slots["action"] = "search"
            slots["engine"] = "duckduckgo"
            slots["query"] = q
            slots["url"] = _SEARCH_ENGINES["duckduckgo"].format(q=_up.quote_plus(q))
            return slots

    # 3. Verbo de apertura + algo (app local)
    if has_open:
        slots["action"] = "open_app"
        # Tomar la palabra significativa tras el verbo
        m = re.search(r"(?:abre|abrir|lanza|inicia|open|launch)\s+(?:el|la|mi)?\s*([a-z0-9_-]{2,})", low)
        if m:
            slots["target"] = m.group(1)
        return slots

    return slots  # action=None → no es una acción


_QUERY_VERBS = r"(?:busca|buscar|búscame|buscame|search|googlea|investiga|infórmate|informate)"


def _extract_query(text: str, site: str) -> Optional[str]:
    """De 'busca gatos en youtube' → 'gatos'."""
    low = text.lower()
    m = re.search(_QUERY_VERBS + r"\s+(.+?)\s+(?:en|in)\s+" + re.escape(site), low)
    if m:
        return m.group(1).strip()
    m = re.search(_QUERY_VERBS + r"\s+(.+)", low)
    if m:
        q = m.group(1).strip()
        for stop in (" en youtube", " en google", " en duckduckgo", " en bing",
                     " en internet", " en la web", " en el buscador", " en línea",
                     " en linea", " online", " en la red", " y dime", " y muéstrame"):
            q = q.replace(stop, "")
        return q.strip() or None
    return None


def _ocr_screenshot(image_path: str) -> str:
    """Lee el texto visible en una captura (tesseract). OCR = leer, no 'ver'."""
    try:
        import pytesseract
        from PIL import Image
        txt = pytesseract.image_to_string(Image.open(image_path), lang="spa+eng")
        # Limpiar: líneas con contenido real
        lineas = [l.strip() for l in txt.splitlines() if len(l.strip()) > 2]
        # Quitar líneas que son puro ruido (símbolos sueltos)
        lineas = [l for l in lineas if re.search(r"[a-zA-Záéíóúñ0-9]{3,}", l)]
        return "\n".join(lineas[:40])
    except Exception as e:
        log.debug("OCR falló: %s", e)
        return ""


def _find_firefox_content_wid() -> Optional[str]:
    """WID de la ventana de CONTENIDO de Firefox (título 'Mozilla Firefox'),
    no el contenedor. Capturable con import aunque esté detrás."""
    try:
        import subprocess
        r = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=4)
        candidatos = []
        for line in r.stdout.splitlines():
            if "firefox" in line.lower() or "mozilla" in line.lower():
                wid = line.split()[0]
                # Preferir la que dice "Mozilla Firefox" (ventana de contenido)
                if "mozilla firefox" in line.lower():
                    return wid
                candidatos.append(wid)
        return candidatos[0] if candidatos else None
    except Exception:
        return None


def perceive(wid: Optional[str] = None) -> Dict[str, Any]:
    """Captura la ventana de Firefox y lee su texto. El 'ver' honesto de EIDOS (OCR).

    Usa `import -window <wid>` que captura la ventana DIRECTAMENTE del servidor X,
    aunque esté detrás — sin pelear con el focus-stealing-prevention del WM.
    """
    result = {"ok": False, "text": "", "title": "", "screenshot": ""}
    try:
        import subprocess
        from core import browser_native, screen_scanner

        time.sleep(2.0)  # dejar que la página termine de cargar/repintar
        content_wid = _find_firefox_content_wid()
        if wid:
            try:
                result["title"] = browser_native.get_page_title(wid)
            except Exception:
                pass

        shot = ""
        if content_wid:
            # Captura por WID (no requiere foco) con ImageMagick import
            out = str(Path.home() / ".eidos" / "screenshots" / f"perc_{int(time.time())}.png")
            r = subprocess.run(["import", "-window", content_wid, out],
                               capture_output=True, timeout=10)
            if r.returncode == 0 and Path(out).exists():
                shot = out

        # Fallback: pantalla completa si la captura por ventana falló
        if not shot:
            shot = screen_scanner.take_screenshot()

        if shot:
            result["screenshot"] = shot
            result["text"] = _ocr_screenshot(shot)
            result["ok"] = bool(result["text"])
    except Exception as e:
        log.warning("perceive falló: %s", e)
        result["error"] = str(e)
    return result


def _remember(concept: str, text: str, source: str = "screen_perception"):
    """Guarda lo percibido como conocimiento nuevo en el grafo (cierra el bucle)."""
    if not text:
        return
    try:
        from core.db import get_conn
        with get_conn(BRAIN_DB) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_nodes "
                "(concept, definition, category, source, confidence) VALUES (?,?,?,?,?)",
                (concept[:120], text[:1000], "perception", source, 0.7),
            )
            conn.commit()
        log.info("action: percepción guardada en grafo (%s)", concept[:50])
    except Exception as e:
        log.debug("no se pudo guardar percepción: %s", e)


def _research_topic(query: str, timeout: float = 15.0) -> str:
    """LEE y SINTETIZA de verdad sobre `query` (no solo abrir el buscador).
    Usa research_now (lee fuentes web + concluye + persiste al grafo). Devuelve
    la síntesis o "". Así EIDOS ya NO abre la búsqueda y se para: aprende de ella."""
    q = (query or "").strip()
    if not q:
        return ""
    try:
        from core.eidos_active_research import research_now
        r = research_now(q, timeout=timeout, persist=True, prefer_remote=True)
        if isinstance(r, dict):
            return (r.get("definition") or "").strip()
    except Exception as e:  # noqa: BLE001
        log.debug("research en search falló: %s", e)
    return ""


def _open_browser(url: str) -> bool:
    """Abre la URL en el navegador (visible) sin xdotool/foco. True si lanzó."""
    try:
        import subprocess
        from shutil import which
        ff = which("firefox-esr") or which("firefox") or which("chromium")
        if not ff:
            return False
        subprocess.Popen(
            [ff, "--new-tab", url] if "firefox" in ff else [ff, url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("abrir navegador falló: %s", e)
        return False


def execute_action(slots: Dict[str, Any]) -> Dict[str, Any]:
    """Ejecuta la acción extraída usando los órganos existentes."""
    result = {"ok": False, "did": "", "perception": None}
    action = slots.get("action")

    # SEGURIDAD (S116): respetar el freno de emergencia de SER
    try:
        from core.eidos_input_safety import is_frozen
        if is_frozen():
            result["error"] = "freno de emergencia activo (SER congeló el control)"
            return result
    except Exception:
        pass

    if action == "search":
        # 1) Abrir el buscador VISIBLE (SER ve la búsqueda) + 2) LEER y concluir.
        url = slots.get("url")
        query = slots.get("query") or ""
        engine = slots.get("engine") or "duckduckgo"
        opened = _open_browser(url) if url else False
        research = _research_topic(query)
        result["ok"] = True
        result["research"] = research
        result["did"] = (
            f"Busqué «{query}» en {engine}"
            + (" (abrí el buscador en el navegador)" if opened else "")
            + (" y leí los resultados." if research else "."))
        if research:
            _remember(f"búsqueda «{query}» ({engine})", research, source=f"search:{engine}")
        return result

    if action == "open_url":
        url = slots.get("url")
        try:
            import subprocess
            from shutil import which
            # Navegar abriendo la URL DIRECTAMENTE como argumento del navegador.
            # No usa xdotool/foco → robusto frente a focus-stealing del WM.
            ff = which("firefox-esr") or which("firefox") or which("chromium")
            if not ff:
                result["error"] = "No encuentro un navegador instalado"
                return result
            subprocess.Popen([ff, "--new-tab", url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            result["ok"] = True
            result["did"] = f"Abrí el navegador en {slots.get('target') or url}"
            time.sleep(5)  # dejar que cargue la página (red + render)
            # Percibir si SER lo pidió (o siempre, para aprender)
            if slots.get("report_vision") or True:
                perc = perceive()  # busca la ventana de contenido de Firefox sola
                result["perception"] = perc
                if perc.get("ok"):
                    _remember(
                        f"visto en {slots.get('target') or url}: {perc.get('title','')}"[:120],
                        perc["text"],
                    )
        except Exception as e:
            result["error"] = str(e)
        return result

    if action == "open_app":
        target = slots.get("target", "")
        try:
            import subprocess
            from shutil import which
            if not which(target):
                result["error"] = f"No tengo '{target}' instalado o no sé abrirlo aún"
                return result
            subprocess.Popen([target])
            result["ok"] = True
            result["did"] = f"Lancé la aplicación {target}"
            time.sleep(2)
            if slots.get("report_vision"):
                perc = perceive()
                result["perception"] = perc
        except Exception as e:
            result["error"] = str(e)
        return result

    result["error"] = "no reconocí una acción ejecutable"
    return result


def handle_action(text: str) -> Optional[str]:
    """
    Punto de entrada: si el texto es una acción, la ejecuta y devuelve la
    respuesta de EIDOS. Si NO es una acción, devuelve None (deja pasar al
    pipeline normal de conocimiento).
    """
    slots = extract_action(text)
    if not slots.get("action"):
        return None

    log.info("action: ejecutando %s → %s", slots["action"], slots.get("target") or slots.get("url"))
    res = execute_action(slots)

    if not res.get("ok"):
        return (f"Intenté hacerlo pero no pude: {res.get('error','error desconocido')}. "
                f"¿Me ayudas a entender qué falló?")

    partes = [res.get("did", "Hecho.")]
    # Búsqueda: mostrar lo que EIDOS APRENDIÓ de los resultados (no solo abrir).
    if res.get("research"):
        partes.append("\nLo que aprendí:\n" + res["research"].strip())
    perc = res.get("perception") or {}
    if perc.get("ok") and perc.get("text"):
        # Mostrar lo que LEYÓ en pantalla (honesto: es texto, no visión)
        lineas = [l for l in perc["text"].splitlines()[:8] if l.strip()]
        if perc.get("title"):
            partes.append(f"La página es: «{perc['title']}».")
        partes.append("Leo en pantalla:\n" + "\n".join(f"  · {l}" for l in lineas))
        partes.append("(Nota: leo el texto visible, no interpreto imágenes ni vídeo.)")
    elif slots.get("report_vision"):
        partes.append("Abrí la página pero no pude leer texto claro en pantalla "
                      "(puede ser contenido gráfico o estar cargando).")

    return "\n".join(partes)


# ── Singleton-style helpers ──────────────────────────────────────────────────
def is_action(text: str) -> bool:
    """¿Este texto es una orden de acción ejecutable?"""
    return extract_action(text).get("action") is not None
