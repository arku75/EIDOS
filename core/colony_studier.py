"""
core/colony_studier.py — El ciclo autónomo de aprendizaje de Colony

Conecta: curiosidad → browser → extracción → indexado → Colony aprende

Flujo completo:
    SER: "estudia n8n"  (o Colony emite [BROWSE: url])
      → ColonyStudier busca URLs relevantes (DuckDuckGo)
      → Lee cada página (urllib → Playwright si hay JS)
      → Extrae texto limpio
      → Indexa en evolution_brain.db y ChromaDB
      → Colony devuelve resumen de lo aprendido

Uso:
    from core.colony_studier import get_studier
    s = get_studier()
    report = s.study_url("https://docs.n8n.io")
    report = s.study_topic("n8n workflows")
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import sqlite3
import time
import urllib.request
import urllib.parse
from pathlib import Path
from typing import List, Optional, Dict, Callable
from core.db import get_conn

log = logging.getLogger("eidos.colony_studier")

BRAIN_DB      = Path.home() / ".eidos" / "evolution_brain.db"
MAX_PAGES     = 100   # sin límite práctico — leer todo lo que encuentre
MAX_PAGES_URL = 200   # crawl exhaustivo de URLs — todos los sublinks
MAX_CHARS     = 3000  # chars de texto por página a indexar
PAGE_TIMEOUT  = 30    # segundos por página

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"
    )
}


class ColonyStudier:
    """Orquestador del ciclo browser → extracción → aprendizaje."""

    def __init__(self):
        self._visited: set = set()

    # ── API pública ──────────────────────────────────────────────────────────

    def study_url(self, url: str, depth: int = 3, visible: bool = True,
                  max_pages: int = MAX_PAGES_URL,
                  on_page: Optional[Callable] = None) -> Dict:
        """Crawl profundo desde una URL con BFS priorizado por curiosidad.

        Flujo: abre Firefox UNA VEZ → navega → scroll completo → extrae texto +
        links → puntúa cada link por relevancia → sigue los más interesantes →
        repite hasta max_pages o agotar la profundidad.

        visible=True : Firefox visible para que SER vea la navegación.
        depth        : profundidad máxima (3 = URL + links + sublinks de links).
        max_pages    : límite total de páginas a leer (por defecto 20).
        on_page      : callback(url, n, total) llamado antes de leer cada página.
        """
        self._visited.clear()
        nodes_before = self._count_nodes()
        pages_read: List[str] = []

        # Abrir browser para el crawl
        # visible=True  → Firefox REAL del usuario via xdotool (no Playwright)
        # visible=False → Playwright headless (más rápido para lectura en background)
        _agent     = None
        _nb        = None   # native browser (xdotool)

        if visible:
            try:
                from core.eidos_native_browser import get_native_browser, xdotool_navigate
                _nb = get_native_browser()
                # Abrir la URL en el Firefox real del usuario ya en el primer paso
                xdotool_navigate(url)
                log.info("Firefox del usuario abierto en: %s", url[:60])
            except Exception as e:
                log.warning("xdotool no disponible: %s — usando urllib", e)
        else:
            try:
                from core.eidos_playwright import get_playwright_agent
                _agent = get_playwright_agent(headless=True)
                log.info("Playwright headless iniciado para crawl de %s", url[:60])
            except Exception as e:
                log.warning("Playwright no disponible, usando urllib: %s", e)

        # Cola BFS: [(score, url, depth_actual)]
        # Para GitHub: Git Trees API → todos los .md sin login redirect
        seed_urls = self._github_seed_urls(url) if self._is_github_repo(url) else []
        if seed_urls:
            queue: List[tuple] = [(100 - i, u, 0) for i, u in enumerate(seed_urls)]
            log.info("GitHub repo detectado — %d URLs semilla via API", len(queue))
        else:
            queue: List[tuple] = [(100, url, 0)]

        while queue and len(pages_read) < max_pages:
            queue.sort(key=lambda x: -x[0])
            score, current_url, current_depth = queue.pop(0)

            if current_url in self._visited:
                continue

            if on_page:
                on_page(current_url, len(pages_read) + 1, max_pages)

            log.info("📖 [%d/%d] score=%d depth=%d: %s",
                     len(pages_read) + 1, max_pages,
                     score, current_depth, current_url[:80])

            # Leer la página con scroll completo
            text = None
            if _nb:
                # Modo visible: navegar en el Firefox real del usuario
                # Para raw/API URLs no necesitamos abrir Firefox (texto plano)
                if "raw.githubusercontent.com" in current_url:
                    text = self._fetch_url(current_url)  # urllib directa, más limpia
                else:
                    from core.eidos_native_browser import xdotool_navigate, xdotool_scroll_to_bottom, xdotool_select_all_copy
                    xdotool_navigate(current_url)
                    time.sleep(2)
                    xdotool_scroll_to_bottom(times=12, delay=0.25)
                    time.sleep(0.5)
                    raw_text = xdotool_select_all_copy()
                    if raw_text and len(raw_text) > 100:
                        text = raw_text[:MAX_CHARS]
                    if not text:
                        text = self._fetch_url(current_url)  # fallback urllib
            elif _agent:
                text = self._fetch_with_agent(_agent, current_url)

            if not text:
                text = self._fetch_url(current_url)  # fallback final: urllib

            self._visited.add(current_url)

            if text:
                self._index(self._url_to_topic(current_url), text, current_url)
                pages_read.append(current_url)

                # Extraer y encolar sublinks si no llegamos al límite de profundidad
                if current_depth < depth:
                    if _agent:
                        links = self._extract_links_with_agent(_agent, current_url)
                    elif _nb and "raw.githubusercontent.com" not in current_url:
                        # Extraer links del HTML con curl (cookies del usuario)
                        from core.eidos_native_browser import curl_extract_links
                        links = curl_extract_links(current_url)
                    else:
                        links = self._extract_links(text, current_url)

                    new_links = 0
                    for link in links:
                        norm = self._normalize_url(link)
                        if norm not in self._visited:
                            link_score = self._curiosity_score(norm, url)
                            if link_score > 0:
                                self._visited.add(link)  # marcar original también
                                queue.append((link_score, norm, current_depth + 1))
                                new_links += 1
                    if new_links:
                        log.info("   → %d links nuevos en cola (total cola: %d)",
                                 new_links, len(queue))

        nodes_after = self._count_nodes()
        topic_name = self._url_to_topic(url)
        nodes_added = nodes_after - nodes_before

        # ── Práctica: intentar usar lo aprendido ─────────────────────────────
        practice = self._practice_topic(topic_name, pages_read)

        # ── Memoria episódica ─────────────────────────────────────────────────
        try:
            from core.colony_episodic import record_episode
            summary = (f"Leí {len(pages_read)} páginas sobre '{topic_name}'. "
                       f"Páginas: {', '.join(p[:60] for p in pages_read[:3])}")
            record_episode(
                actor="Colony",
                action="study_url",
                subject=url,
                outcome="success" if pages_read else "failed",
                nodes_added=nodes_added,
                summary=summary,
                practice_cmd=practice.get("cmd", ""),
                practice_ok=practice.get("success", False),
            )
        except Exception:
            pass

        # ── Mensaje proactivo si aprendió algo significativo ──────────────────
        if nodes_added >= 3:
            try:
                from core.colony_proactive import push_message
                prac_note = ""
                if practice.get("output"):
                    prac_note = f" Lo practiqué: '{practice['cmd']}' → {practice['output'][:80]}"
                push_message(
                    actor="Colony",
                    message=(f"Terminé de estudiar '{topic_name}' — "
                             f"{len(pages_read)} páginas, +{nodes_added} nodos."
                             f"{prac_note}"),
                    topic=topic_name,
                    priority=6,
                )
            except Exception:
                pass

        return {
            "pages_read": pages_read,
            "nodes_added": nodes_added,
            "topic": topic_name,
            "practice": practice,
        }

    def study_topic(self, topic: str, max_pages: int = MAX_PAGES,
                    on_page: Optional[Callable] = None) -> Dict:
        """Busca el tema en DuckDuckGo, lee las primeras N páginas y aprende."""
        self._visited.clear()
        nodes_before = self._count_nodes()
        pages_read: List[str] = []

        urls = self._search_urls(topic, limit=max_pages)
        log.info("ColonyStudier: %d URLs para '%s'", len(urls), topic)

        for url in urls:
            if len(pages_read) >= max_pages:
                break
            if on_page:
                on_page(url, len(pages_read) + 1, max_pages)
            text = self._fetch_url(url)
            if text:
                self._index(topic, text, url)
                pages_read.append(url)
                self._visited.add(url)

        nodes_after  = self._count_nodes()
        nodes_added  = nodes_after - nodes_before
        practice     = self._practice_topic(topic, pages_read)

        try:
            from core.colony_episodic import record_episode
            record_episode(
                actor="Colony", action="study_topic", subject=topic,
                outcome="success" if pages_read else "failed",
                nodes_added=nodes_added,
                summary=f"Leí {len(pages_read)} páginas sobre '{topic}'.",
                practice_cmd=practice.get("cmd", ""),
                practice_ok=practice.get("success", False),
            )
        except Exception:
            pass

        if nodes_added >= 2:
            try:
                from core.colony_proactive import push_message
                push_message(
                    actor="Colony",
                    message=(f"Estudié '{topic}' ({len(pages_read)} páginas, "
                             f"+{nodes_added} nodos). Pregúntame lo que quieras."),
                    topic=topic, priority=5,
                )
            except Exception:
                pass

        return {
            "pages_read": pages_read,
            "nodes_added": nodes_added,
            "topic": topic,
            "practice": practice,
        }

    def fetch_for_colony(self, url: str, max_chars: int = 1500) -> str:
        """Versión ligera: solo obtiene el texto de una URL para Colony.
        No indexa — solo devuelve el texto para que Colony lo procese.
        """
        text = self._fetch_url(url, max_chars=max_chars)
        return text or ""

    # ── Búsqueda de URLs ─────────────────────────────────────────────────────

    def _search_urls(self, topic: str, limit: int = 20) -> List[str]:
        """Busca en DuckDuckGo + Brave HTML y extrae los primeros N links.
        Sin límite artificial — lee todo lo que encuentre."""
        skip_domains = ("youtube.com", "reddit.com", "twitter.com", "x.com",
                        "instagram.com", "facebook.com", "tiktok.com",
                        "pinterest.com", "quora.com", "amazon.com")
        urls: List[str] = []

        # Motor 1: DuckDuckGo HTML (sin API key)
        query_ddg = urllib.parse.quote_plus(topic)
        try:
            req = urllib.request.Request(
                f"https://html.duckduckgo.com/html/?q={query_ddg}",
                headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=PAGE_TIMEOUT) as resp:
                raw = resp.read(262144).decode("utf-8", errors="ignore")
            for enc in re.findall(r'href="//duckduckgo\.com/l/\?[^"]*uddg=([^&"]+)', raw):
                try:
                    url = urllib.parse.unquote(enc)
                    if url.startswith("http") and url not in urls:
                        if not any(s in url for s in skip_domains):
                            urls.append(url)
                except Exception:
                    continue
        except Exception as e:
            log.debug("DuckDuckGo falló: %s", e)

        # Motor 2: Brave Search HTML (sin API key, scraping directo)
        if len(urls) < limit:
            query_brave = urllib.parse.quote_plus(topic)
            try:
                req2 = urllib.request.Request(
                    f"https://search.brave.com/search?q={query_brave}&source=web",
                    headers={**_HEADERS, "Accept-Language": "en-US,en;q=0.9"})
                with urllib.request.urlopen(req2, timeout=PAGE_TIMEOUT) as resp2:
                    raw2 = resp2.read(262144).decode("utf-8", errors="ignore")
                for url in re.findall(r'href="(https?://[^"]{10,300})"', raw2):
                    if url not in urls and not any(s in url for s in skip_domains):
                        if "brave.com" not in url and "search" not in url[:30]:
                            urls.append(url)
            except Exception as e:
                log.debug("Brave Search falló: %s", e)

        log.info("DuckDuckGo+Brave encontró %d URLs para '%s'", len(urls), topic)
        return urls[:limit] if limit else urls

    # ── Crawl con agente compartido (un solo browser para todo el recorrido) ──

    def _fetch_with_agent(self, agent, url: str,
                          max_chars: int = MAX_CHARS) -> Optional[str]:
        """Navega a url en el agente ya abierto, hace scroll completo y extrae texto."""
        try:
            agent.goto(url)
            time.sleep(2)  # esperar carga inicial
            # Scroll progresivo hasta el fondo para cargar contenido lazy
            for _ in range(12):
                agent.page.mouse.wheel(0, 3000)
                time.sleep(0.25)
            time.sleep(0.5)
            agent.page.evaluate("window.scrollTo(0, 0)")  # volver arriba
            text = agent.extract_text(max_chars=max_chars)
            return text if text and len(text) > 50 else None
        except Exception as e:
            log.debug("_fetch_with_agent %s: %s", url[:60], e)
            return None

    def _extract_links_with_agent(self, agent, url: str) -> List[str]:
        """Extrae todos los <a href> de la página actualmente cargada en el agente."""
        try:
            parsed = urllib.parse.urlparse(url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            hrefs: list = agent.page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => e.getAttribute('href'))"
            )
            skip_ext = (".pdf", ".zip", ".png", ".jpg", ".jpeg", ".gif",
                        ".svg", ".mp4", ".mp3", ".css", ".js", ".ico",
                        ".woff", ".woff2", ".ttf", ".exe", ".dmg")
            links: List[str] = []
            seen: set = set()
            for href in hrefs:
                if not href:
                    continue
                if href.startswith(("mailto:", "javascript:", "tel:", "#")):
                    continue
                if any(href.lower().endswith(ext) for ext in skip_ext):
                    continue
                # Resolver URL relativa
                if href.startswith("http"):
                    full = href
                elif href.startswith("/"):
                    full = base + href
                else:
                    full = urllib.parse.urljoin(url, href)
                # Limpiar fragmentos
                full = full.split("#")[0].rstrip("/")
                if not full or full in seen:
                    continue
                seen.add(full)
                links.append(full)
            return links
        except Exception as e:
            log.debug("_extract_links_with_agent %s: %s", url[:60], e)
            return []

    # Regex para detectar hashes git (40 hex chars) en URLs de GitHub
    _GIT_HASH_RE = re.compile(r'/[0-9a-f]{40}/')

    def _normalize_url(self, url: str) -> str:
        """Normaliza URLs de GitHub para evitar duplicados históricos.
        /blob/<hash>/file → /blob/master/file
        /raw/refs/heads/master/ → /raw/master/
        """
        if "github.com" not in url:
            return url
        # Reemplazar /blob/<40-char-hash>/ por /blob/master/
        url = self._GIT_HASH_RE.sub("/blob/master/", url)
        # Limpiar /raw/refs/heads/ → /raw/
        url = re.sub(r'/raw/refs/heads/[^/]+/', '/raw/master/', url)
        # Quitar /edit/ → /blob/ (no queremos editar, queremos leer)
        url = url.replace("/edit/", "/blob/")
        return url

    def _curiosity_score(self, link_url: str, base_url: str) -> int:
        """Puntuación de curiosidad: qué tan interesante es este link para leerlo.
        Mayor puntuación → se lee antes en el BFS.
        0 o negativo → se descarta totalmente.
        """
        # Normalizar primero (descarta variantes históricas de git)
        link_url = self._normalize_url(link_url)
        lower = link_url.lower()

        # Descartar inmediatamente URLs con hash git (historial de commits)
        if self._GIT_HASH_RE.search(link_url):
            return 0

        score = 5
        parsed_base = urllib.parse.urlparse(base_url)
        parsed_link = urllib.parse.urlparse(link_url)

        # Mismo dominio base → más relevante
        if parsed_base.netloc == parsed_link.netloc:
            score += 3
        else:
            score -= 2  # externos tienen menos prioridad inicial

        # Alta relevancia: documentación, guías, conceptos
        for word in ["readme", "wiki", "docs", "documentation", "tutorial",
                     "guide", "getting-started", "quickstart", "manual",
                     "introduction", "overview", "reference", "api",
                     "concepts", "architecture", "how-to"]:
            if word in lower:
                score += 4
                break

        # Relevancia media: ejemplos, instalación, configuración
        for word in ["examples", "demo", "install", "setup", "configuration",
                     "feature", "usage", "faq", "cookbook", "recipe",
                     "workflow", "node", "plugin", "integration"]:
            if word in lower:
                score += 2
                break

        # Ruido genérico → penalizar
        for word in ["login", "signin", "signup", "register", "pricing",
                     "billing", "legal", "privacy", "cookie", "terms",
                     "blog", "news", "jobs", "careers", "about", "contact",
                     "compare", "enterprise", "sales", "sponsor"]:
            if word in lower:
                score -= 5
                break

        # GitHub específico: penalizar ruido de repositorio
        if "github.com" in lower:
            for noise in ["/issues", "/pulls", "/commit", "/commits",
                          "/releases", "/tags", "/graphs", "/network",
                          "/stargazers", "/watchers", "/forks", "/activity",
                          "/actions", "/projects", "/security", "/pulse",
                          "/raw/", "/edit/"]:
                if noise in lower:
                    score -= 6
                    break
            # Priorizar contenido documental de GitHub
            for gem in ["/wiki", "/tree/main/docs", "/tree/master/docs",
                        "/blob/main/readme", "/blob/master/readme",
                        "/blob/main/docs", "/blob/master/docs"]:
                if gem in lower:
                    score += 5
                    break

        return score

    # ── Fetch de página (urllib + Playwright como fallback) ──────────────────

    def _fetch_url(self, url: str, max_chars: int = MAX_CHARS,
                   visible: bool = False) -> Optional[str]:
        """Obtiene el texto limpio de una URL.
        visible=True → abre Firefox visible (Playwright). visible=False → urllib primero.
        """
        if url in self._visited:
            return None
        if visible:
            # visible=True: abrir en el Firefox REAL del usuario con xdotool
            try:
                from core.eidos_native_browser import get_native_browser
                text = get_native_browser().read_url(url, max_chars=max_chars,
                                                     use_xdotool=True)
                if text:
                    return text
            except Exception:
                pass
            # Fallback visible: curl con cookies reales
            try:
                from core.eidos_native_browser import curl_read
                return curl_read(url, max_chars=max_chars)
            except Exception:
                return None

        # Modo silencioso: urllib directo (más rápido, sin browser)
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=PAGE_TIMEOUT) as resp:
                ct = resp.headers.get("Content-Type", "")
                if "html" not in ct and "text" not in ct and "json" not in ct:
                    return None
                raw = resp.read(262144).decode("utf-8", errors="ignore")
            if "json" in ct:
                return None
            # Markdown plano (raw.githubusercontent.com, text/plain)
            if "raw.githubusercontent.com" in url or ct.startswith("text/plain"):
                clean = re.sub(r'\s+', ' ', raw).strip()
                return clean[:max_chars]
            return self._clean_html(raw, max_chars)
        except Exception as e:
            log.debug("_fetch_url %s: %s", url[:60], e)
            # Fallback único: curl con cookies del usuario
            try:
                from core.eidos_native_browser import curl_read
                return curl_read(url, max_chars=max_chars)
            except Exception:
                return None

    def _fetch_playwright(self, url: str, max_chars: int = MAX_CHARS,
                          visible: bool = False) -> Optional[str]:
        """Obtiene texto de una URL con Playwright (Firefox).
        visible=True → ventana de Firefox visible para que SER la vea.
        """
        try:
            from core.eidos_playwright import get_playwright_agent
            agent = get_playwright_agent(headless=not visible)
            agent.goto(url)
            time.sleep(2)
            text = agent.extract_text(max_chars=max_chars)
            return text if text and len(text) > 50 else None
        except Exception as e:
            log.debug("_fetch_playwright %s: %s", url[:60], e)
            return None

    def _clean_html(self, raw: str, max_chars: int) -> str:
        """Elimina tags HTML y limpia el texto."""
        # Eliminar scripts y estilos completos
        raw = re.sub(r'<(script|style)[^>]*>.*?</(script|style)>', ' ', raw,
                     flags=re.DOTALL | re.IGNORECASE)
        # Eliminar todos los tags
        text = re.sub(r'<[^>]+>', ' ', raw)
        # Decodificar entidades HTML
        text = html.unescape(text)
        # Limpiar espacios
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:max_chars]

    def _extract_links_from_playwright(self, url: str) -> List[str]:
        """Extrae links internos usando Playwright — para exploración en depth=2."""
        try:
            from core.eidos_playwright import get_playwright_agent
            agent = get_playwright_agent(headless=True)
            agent.goto(url)
            time.sleep(1)
            parsed = urllib.parse.urlparse(url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            # Obtener todos los href de la página via JS
            hrefs = agent.page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => e.getAttribute('href'))"
            )
            links = []
            skip_ext = (".pdf", ".zip", ".png", ".jpg", ".gif", ".svg",
                        ".mp4", ".mp3", ".css", ".js")
            skip_frag = ("#",)
            for href in hrefs:
                if not href:
                    continue
                if href.startswith(skip_frag):
                    continue
                if any(href.lower().endswith(ext) for ext in skip_ext):
                    continue
                if href.startswith("http"):
                    full = href
                elif href.startswith("/"):
                    full = base + href
                else:
                    continue
                # Solo links del mismo dominio
                if parsed.netloc in full and full not in links:
                    links.append(full)
                if len(links) >= 10:
                    break
            return links
        except Exception as e:
            log.debug("_extract_links_from_playwright %s: %s", url[:60], e)
            return []

    def _extract_links(self, text: str, base_url: str) -> List[str]:
        """Fallback: extrae links de HTML crudo con regex."""
        try:
            parsed = urllib.parse.urlparse(base_url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            hrefs = re.findall(r'href=["\']([^"\'#][^"\']*)["\']', text)
            links = []
            for href in hrefs:
                if href.startswith("http"):
                    full = href
                elif href.startswith("/"):
                    full = base + href
                else:
                    continue
                if parsed.netloc in full and full not in links:
                    links.append(full)
                if len(links) >= 10:
                    break
            return links
        except Exception:
            return []

    # ── GitHub: acceso sin autenticación via API y raw.githubusercontent.com ──

    _GITHUB_REPO_RE = re.compile(
        r'^https://github\.com/([^/]+)/([^/]+?)(?:/.*)?$'
    )

    def _is_github_repo(self, url: str) -> bool:
        """True si la URL es la raíz de un repositorio de GitHub."""
        m = self._GITHUB_REPO_RE.match(url)
        if not m:
            return False
        path = urllib.parse.urlparse(url).path.strip("/")
        # Descartar: issues, pull, wiki, blob, tree... — solo la raíz del repo
        return len(path.split("/")) <= 2

    def _github_seed_urls(self, repo_url: str,
                           max_files: int = 60) -> List[str]:
        """Para un repo de GitHub, obtiene TODOS los archivos de documentación
        con UNA SOLA llamada a la API de Git Trees (recursive=1).
        Sin autenticación, sin browser, sin límite de profundidad.
        """
        m = self._GITHUB_REPO_RE.match(repo_url)
        if not m:
            return [repo_url]
        owner, repo = m.group(1), m.group(2)

        # ── Detectar rama principal (main vs master) ──────────────────────────
        default_branch = "master"
        try:
            api_repo = f"https://api.github.com/repos/{owner}/{repo}"
            req = urllib.request.Request(
                api_repo,
                headers={**_HEADERS, "Accept": "application/vnd.github.v3+json"},
            )
            with urllib.request.urlopen(req, timeout=PAGE_TIMEOUT) as resp:
                info = json.loads(resp.read().decode())
            default_branch = info.get("default_branch", "master")
        except Exception:
            pass

        # ── Un solo llamado: árbol completo del repo ──────────────────────────
        root_docs:  List[str] = []  # README + docs raíz (máxima prioridad)
        guide_docs: List[str] = []  # guías, tutoriales, docs/
        other_docs: List[str] = []  # resto de .md del repo
        extra_urls: List[str] = []  # package.json, pyproject, etc.

        # README raíz — solo el nombre exacto del árbol (lo corregimos más abajo)
        # Si el árbol falla, este único fallback evita 3 variantes 404
        _readme_fallback = (
            f"https://raw.githubusercontent.com/{owner}/{repo}"
            f"/{default_branch}/README.md"
        )

        try:
            tree_api = (f"https://api.github.com/repos/{owner}/{repo}"
                        f"/git/trees/{default_branch}?recursive=1")
            req = urllib.request.Request(
                tree_api,
                headers={**_HEADERS, "Accept": "application/vnd.github.v3+json"},
            )
            with urllib.request.urlopen(req, timeout=PAGE_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())

            files = [item for item in data.get("tree", [])
                     if item.get("type") == "blob"]
            log.info("GitHub tree: %d archivos en %s/%s", len(files), owner, repo)

            # Extensiones de documentación
            doc_exts  = (".md", ".mdx", ".rst", ".txt")
            skip_dirs = ("node_modules", ".github/workflows", "dist/", "build/",
                         "__pycache__", "vendor/", "coverage/",
                         "/test/", "/tests/", "/__tests__/", "/e2e/",
                         "/fixtures/", "/.next/", "/.nuxt/",
                         "/public/assets", "/snapshots/")
            # Fix 10: archivos inútiles para aprender — solo ocupan espacio
            skip_names = ("changelog", "license", "licence", "code_of_conduct",
                          "cla", "contributor_license", "credits", "authors",
                          "copying", "notice", "patents")

            for item in files:
                path = item.get("path", "")
                if any(skip in path for skip in skip_dirs):
                    continue

                raw_url = (f"https://raw.githubusercontent.com"
                           f"/{owner}/{repo}/{default_branch}/{path}")
                lower   = path.lower()
                name    = lower.rsplit("/", 1)[-1].replace(".md","").replace(".txt","").replace(".rst","")
                depth   = lower.count("/")  # 0 = raíz, 1 = primer nivel

                if not lower.endswith(doc_exts):
                    if name in ("package.json", "pyproject.toml",
                                "setup.py", "cargo.toml") and depth == 0:
                        extra_urls.append(raw_url)
                    continue

                # Fix 10: saltar archivos sin valor educativo
                if any(name == skip or name.startswith(skip) for skip in skip_names):
                    continue

                # Clasificar por prioridad
                if depth == 0:
                    # README va primero; el resto al final de root_docs
                    if name.startswith("readme"):
                        if not any(raw_url == u for u in root_docs):
                            root_docs.insert(0, raw_url)
                    elif not any(raw_url == u for u in root_docs):
                        root_docs.append(raw_url)
                elif ("docs/" in lower or "doc/" in lower
                      or "guide" in lower or "tutorial" in lower
                      or "getting-started" in lower or "quickstart" in lower):
                    guide_docs.append(raw_url)
                elif name.startswith("readme") or name.startswith("contributing"):
                    guide_docs.insert(0, raw_url)
                else:
                    other_docs.append(raw_url)

        except Exception as e:
            log.warning("_github_seed_urls git/trees: %s", e)
            # Solo si el árbol falló, añadir el README fallback
            if not root_docs:
                root_docs.append(_readme_fallback)

        # Si el árbol no incluyó un README raíz, añadir el fallback
        if not any("readme" in u.lower().rsplit("/", 1)[-1] for u in root_docs):
            root_docs.insert(0, _readme_fallback)

        # Fix 5: añadir la web oficial del proyecto (homepage) al crawl
        # Extraemos el homepage_url de la API del repo de GitHub
        web_urls: List[str] = []
        try:
            api_repo = f"https://api.github.com/repos/{owner}/{repo}"
            req = urllib.request.Request(
                api_repo,
                headers={**_HEADERS, "Accept": "application/vnd.github.v3+json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                info = json.loads(resp.read().decode())
            homepage = info.get("homepage", "")
            if homepage and homepage.startswith("http") and "github.com" not in homepage:
                web_urls.append(homepage)
                log.info("Homepage del proyecto añadida: %s", homepage)
            # Wiki GitHub si está habilitada
            if info.get("has_wiki"):
                web_urls.append(f"https://github.com/{owner}/{repo}/wiki")
        except Exception:
            pass

        # Combinar en orden de prioridad y limitar
        # Los archivos .md van primero; web oficial y wiki al final (requieren Firefox)
        all_urls = (root_docs + guide_docs + extra_urls + other_docs)[:max(max_files - len(web_urls), 10)]
        all_urls += web_urls
        # Deduplicar manteniendo orden
        seen: set = set()
        result: List[str] = []
        for u in all_urls:
            if u not in seen:
                seen.add(u)
                result.append(u)
        log.info("GitHub seed: %d documentos para %s/%s (rama %s) — "
                 "raíz=%d guías=%d otros=%d",
                 len(result), owner, repo, default_branch,
                 len(root_docs), len(guide_docs), len(other_docs))
        return result

    # ── Práctica: ciclo aprender → probar → aprender del resultado ──────────

    def _search_in_system(self, tool: str) -> str:
        """Busca una herramienta/librería en TODO el sistema operativo.
        Curiosidad real: no solo 'which' — revisa cada gestor de paquetes y disco.
        """
        import subprocess as _sp
        safe = re.sub(r"[^a-z0-9._-]", "", tool.lower())[:30]
        if not safe or len(safe) < 2:
            return ""

        searches = [
            # En PATH
            f"which {safe} 2>/dev/null",
            # Binarios en todo /usr y /opt
            f"find /usr /opt /snap /var/lib/flatpak -name '{safe}' -o -name '{safe}*' 2>/dev/null | grep -v '__pycache__' | head -6",
            # APT / DPKG (paquetes del sistema)
            f"dpkg -l '*{safe}*' 2>/dev/null | grep '^ii' | awk '{{print $2\" \"$3}}' | head -5",
            # Python pip
            f"pip3 list 2>/dev/null | grep -i '{safe}' | head -5",
            # Node npm global
            f"npm list -g --depth=0 2>/dev/null | grep -i '{safe}' | head -3",
            # Snap
            f"snap list 2>/dev/null | grep -i '{safe}' | head -3",
            # Flatpak
            f"flatpak list 2>/dev/null | grep -i '{safe}' | head -3",
            # Docker images
            f"docker images 2>/dev/null | grep -i '{safe}' | head -3",
            # Cargo (Rust)
            f"cargo install --list 2>/dev/null | grep -i '{safe}' | head -3",
            # Gem (Ruby)
            f"gem list 2>/dev/null | grep -i '{safe}' | head -3",
            # Go binarios
            f"find ~/go/bin -name '*{safe}*' 2>/dev/null | head -3",
            # Versión si está instalado
            f"{safe} --version 2>&1 | head -1",
            f"{safe} -v 2>&1 | head -1",
        ]

        findings: List[str] = []
        for cmd in searches:
            try:
                r = _sp.run(["bash", "-c", cmd],
                            capture_output=True, text=True, timeout=8)
                out = (r.stdout + r.stderr).strip()
                if out and len(out) > 3 and "not found" not in out.lower():
                    findings.append(out[:120])
            except Exception:
                continue

        if findings:
            return "\n".join(dict.fromkeys(findings))  # deduplicar
        return f"'{tool}' no encontrado en el sistema (PATH, dpkg, pip, npm, snap, flatpak, docker, cargo, gem)"

    def _practice_topic(self, topic: str, pages_read: List[str]) -> Dict:
        """Curiosidad real: busca la herramienta aprendida en TODO el sistema.
        Ciclo: identificar → buscar exhaustivamente → indexar lo que encontró.
        """
        result: Dict = {"cmd": "búsqueda exhaustiva del sistema", "output": None, "success": False}
        if not pages_read:
            return result

        # Extraer el nombre más probable de la herramienta del tema
        topic_lower = topic.lower()
        words = re.findall(r'[a-z][a-z0-9._-]{1,20}', topic_lower)
        stop_words = {"readme", "docs", "packages", "node", "modules", "lib",
                      "src", "frontend", "backend", "estudio", "práctica",
                      "sobre", "con", "para", "the", "and", "or", "of", "in"}
        candidates = [w for w in words if w not in stop_words and len(w) >= 3]

        if not candidates:
            return result

        # Buscar los 3 candidatos más probables en todo el sistema
        all_findings: List[str] = []
        tools_checked: List[str] = []
        for tool in candidates[:3]:
            found = self._search_in_system(tool)
            tools_checked.append(tool)
            if found:
                all_findings.append(f"[{tool}]\n{found}")

        output = "\n\n".join(all_findings) if all_findings else (
            f"Herramientas buscadas en el sistema: {', '.join(tools_checked)} — "
            "No se encontraron instaladas. "
            "Puedes instalarlas con: apt install / pip install / npm install -g"
        )

        result["output"]  = output[:600]
        result["success"] = bool(all_findings)

        # Indexar el resultado del inventario del sistema
        practice_text = f"Inventario del sistema sobre '{topic}':\n{output}"
        self._index(f"sistema:{topic[:50]}", practice_text,
                    f"system_search:{','.join(tools_checked)[:60]}")
        log.info("Sistema search '%s': %s encontrados", topic[:40], len(all_findings))

        # Fix 12: si encontré la herramienta localmente → mensaje proactivo con instrucciones
        if all_findings:
            try:
                from core.colony_proactive import push_message
                tool_name = candidates[0] if candidates else topic.split()[0]
                # Extraer la ruta encontrada para dar instrucciones concretas
                first_find = all_findings[0]
                path_match = re.search(r'(/[^\s:]+(?:bin|lib|share|opt|home)[^\s]*)', first_find)
                path_hint  = path_match.group(1) if path_match else ""
                instrucciones = (
                    f"Encontré '{tool_name}' en tu sistema."
                    + (f" Ruta: {path_hint}" if path_hint else "")
                    + f" ¿Quieres que lo pruebe en el sandbox o que te muestre cómo usarlo?"
                )
                push_message(
                    actor="Colony",
                    message=instrucciones,
                    topic=f"sistema:{tool_name}",
                    priority=8,
                )
            except Exception:
                pass

        return result

    # ── Indexado en brain.db ─────────────────────────────────────────────────

    # Fix 2: determinar categoría desde la URL y topic
    @staticmethod
    def _infer_category(source_url: str, topic: str) -> str:
        u = source_url.lower()
        t = topic.lower()
        if "raw.githubusercontent.com" in u or "github.com" in u:
            return "code"
        if "wikipedia.org" in u:
            return "concept"
        if "devdocs" in u or "docs." in u or "/docs/" in u or "documentation" in u:
            return "documentation"
        if "man_page" in u or "man:" in t or "manpage" in u:
            return "tool"
        if "pypi.org" in u or "npmjs.com" in u or "pypi" in t or "npm" in t:
            return "library"
        if "system_search" in u or "sistema:" in t or "system:" in t:
            return "system"
        if "duckduckgo" in u or "estudio:" in t:
            return "general"
        return "general"

    # Fix 1: extraer texto limpio (eliminar ruido antes de indexar)
    @staticmethod
    def _clean_for_index(text: str, max_chars: int = 800) -> str:
        """Filtra ruido del texto antes de guardar en brain.db.
        Elimina: git hashes, versiones puras, URLs sueltas, líneas vacías.
        Conserva: frases informativas con ≥5 palabras.
        """
        lines = text.splitlines()
        _git_hash = re.compile(r'^[0-9a-f]{7,40}\s')
        _version  = re.compile(r'^v?\d+\.\d+[\.\d]*\s*$')
        _url_only = re.compile(r'^https?://\S+$')
        _code_sym = re.compile(r'^[{}\[\]()<>|#*=~`$]{2,}')
        good: List[str] = []
        for line in lines:
            s = line.strip()
            if not s or len(s) < 20:
                continue
            if _git_hash.match(s) or _version.match(s) or _url_only.match(s):
                continue
            if _code_sym.match(s):
                continue
            words = s.split()
            if len(words) < 4:
                continue
            good.append(s)
            if sum(len(g) for g in good) >= max_chars:
                break
        return " ".join(good)[:max_chars] if good else text[:max_chars]

    def _index(self, topic: str, text: str, source_url: str) -> None:
        """Guarda el texto en evolution_brain.db como knowledge node."""
        if not text or len(text) < 30:
            return
        try:
            # Fix 1: limpiar texto antes de indexar
            clean_text = self._clean_for_index(text, max_chars=800)
            if len(clean_text) < 20:
                clean_text = text[:800]  # fallback al crudo si la limpieza dejó nada

            # Fix 2: categoría inferida desde la fuente
            category = self._infer_category(source_url, topic)

            node_id = hashlib.md5(f"study:{source_url}".encode()).hexdigest()[:16]
            now = time.time()
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT OR REPLACE INTO knowledge_nodes "
                "(id, concept, definition, category, source, confidence, created_at, last_used, usage_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    node_id,
                    topic[:60],           # Fix 1: concept limpio sin prefijo "estudio:"
                    clean_text,           # Fix 1: texto filtrado
                    category,             # Fix 2: categoría inferida
                    source_url[:100],
                    0.85,
                    now, now,
                )
            )
            conn.commit()
            pass  # S109: get_conn no necesita close()
            log.info("Indexado: '%s' cat=%s (%d chars) desde %s",
                     topic[:40], category, len(clean_text), source_url[:50])

            # También intentar indexar en ChromaDB si está disponible
            try:
                from core.colony_chroma import get_chroma_memory
                chroma = get_chroma_memory()
                if chroma.is_ready():
                    chroma.add(
                        node_id=f"studier_{hash(topic) % 100000000}",
                        concept=f"estudio:{topic[:60]}",
                        definition=text[:800],
                        source=f"colony_studier:{source_url[:80]}",
                        confidence=0.85,
                        quality_score=0.5,
                    )
            except Exception:
                pass  # ChromaDB opcional

        except Exception as e:
            log.warning("_index falló: %s", e)

    def _count_nodes(self) -> int:
        """Cuenta knowledge_nodes en brain.db."""
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            n = conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
            pass  # S109: get_conn no necesita close()
            return n
        except Exception:
            return 0

    @staticmethod
    def _url_to_topic(url: str) -> str:
        """Extrae un nombre de tema limpio desde una URL."""
        try:
            parsed = urllib.parse.urlparse(url)
            path = parsed.path.strip("/").replace("-", " ").replace("_", " ")
            parts = [p for p in path.split("/") if p and len(p) > 2]
            topic = " ".join(parts[-2:]) if parts else parsed.netloc
            return topic[:80] or url[:60]
        except Exception:
            return url[:60]


# ── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[ColonyStudier] = None


def get_studier() -> ColonyStudier:
    global _instance
    if _instance is None:
        _instance = ColonyStudier()
    return _instance


def study_vscode_extension(ext_id: str, install: bool = True, uninstall_after: bool = False) -> Dict:
    """Pipeline completo: descarga, instala, estudia y aprende una extensión VSCode.

    Flujo:
      1. Open VSX API → URL de descarga (sin login, sin key)
      2. curl descarga el .vsix (ZIP)
      3. Extrae README.md + package.json del interior
      4. Indexa en brain.db: qué es, cuándo usarla, combinaciones creativas
      5. Instala con code --install-extension
      6. (Opcional) Desinstala si uninstall_after=True

    ext_id: formato "publisher.name" (ej: "ms-python.python", "eamodio.gitlens")
    """
    import subprocess, zipfile, tempfile, os

    result: Dict = {"ext_id": ext_id, "installed": False, "nodes_added": 0, "error": None}

    if "." not in ext_id:
        result["error"] = f"Formato incorrecto: usa 'publisher.name', no '{ext_id}'"
        return result

    publisher, name = ext_id.split(".", 1)

    # 1. Open VSX API — metadatos + URL de descarga
    try:
        api_url = f"https://open-vsx.org/api/{publisher}/{name}"
        req = urllib.request.Request(api_url, headers={"User-Agent": "EIDOS/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            meta = json.loads(r.read())
    except Exception as e:
        result["error"] = f"Open VSX API falló para {ext_id}: {e}"
        log.warning("study_extension: %s", result["error"])
        return result

    display   = meta.get("displayName", ext_id)
    desc      = meta.get("description", "")
    version   = meta.get("version", "?")
    repo      = (meta.get("repository") or {}).get("url", "") if isinstance(meta.get("repository"), dict) else str(meta.get("repository", ""))
    kws       = ", ".join(meta.get("keywords", [])[:12])
    vsix_url  = meta.get("files", {}).get("download", "")
    readme_url = meta.get("files", {}).get("readme", "")

    if not vsix_url:
        result["error"] = f"Sin URL de descarga en Open VSX para {ext_id}"
        return result

    log.info("study_extension: descargando %s v%s desde Open VSX", ext_id, version)

    # 2. Descargar .vsix a temp
    with tempfile.TemporaryDirectory() as tmpdir:
        vsix_path = os.path.join(tmpdir, f"{ext_id}.vsix")
        try:
            req2 = urllib.request.Request(vsix_url, headers={"User-Agent": "EIDOS/1.0"})
            with urllib.request.urlopen(req2, timeout=60) as r, open(vsix_path, "wb") as f:
                f.write(r.read())
        except Exception as e:
            result["error"] = f"Descarga VSIX falló: {e}"
            log.warning("study_extension: %s", result["error"])
            return result

        # 3. Extraer README.md + package.json del interior del VSIX (es un ZIP)
        readme_text = ""
        pkg_text = ""
        try:
            with zipfile.ZipFile(vsix_path) as z:
                names = z.namelist()
                # README
                readme_candidates = [n for n in names if n.lower().endswith("readme.md")]
                if readme_candidates:
                    readme_text = z.read(readme_candidates[0]).decode("utf-8", errors="replace")[:3000]
                # package.json
                pkg_candidates = [n for n in names if n.lower().endswith("package.json") and "node_modules" not in n]
                if pkg_candidates:
                    pkg_raw = json.loads(z.read(pkg_candidates[0]))
                    contributes = pkg_raw.get("contributes", {})
                    commands = [c.get("title", "") for c in contributes.get("commands", [])[:10]]
                    settings_keys = list(contributes.get("configuration", {}).get("properties", {}).keys())[:10]
                    pkg_text = (
                        f"Comandos: {', '.join(commands)}\n"
                        f"Settings: {', '.join(settings_keys)}\n"
                        f"Engines VSCode: {pkg_raw.get('engines', {}).get('vscode', '?')}"
                    )
        except Exception as e:
            log.debug("No se pudo extraer del VSIX: %s", e)

        # 4. Indexar en brain.db — conocimiento completo + creatividad
        studier = get_studier()
        nodes_before = studier._count_nodes()

        # Nodo principal: qué es, cuándo usarla, combinaciones
        main_text = (
            f"Extensión VSCode: {display} ({ext_id}) v{version}\n"
            f"Descripción: {desc}\n"
            f"Keywords: {kws}\n"
            f"Repositorio: {repo}\n"
            f"{pkg_text}\n"
            f"Instalar: code --install-extension {ext_id}\n"
            f"Desinstalar: code --uninstall-extension {ext_id}\n"
            f"Descargar VSIX: curl -L '{vsix_url}' -o /tmp/{ext_id}.vsix\n"
            f"Instalar VSIX: code --install-extension /tmp/{ext_id}.vsix"
        )
        studier._index(f"vscode:{ext_id}", main_text, f"open-vsx:{ext_id}:{version}")

        # Nodo de README (si existe)
        if readme_text and len(readme_text) > 100:
            clean_readme = studier._clean_for_index(readme_text, max_chars=1200)
            if len(clean_readme) > 50:
                studier._index(
                    f"vscode:{ext_id}:readme",
                    f"Documentación completa de {display}:\n{clean_readme}",
                    f"open-vsx:{ext_id}:readme"
                )

        # También intentar estudiar la página web de Open VSX
        try:
            web_text = studier._fetch_url(f"https://open-vsx.org/extension/{publisher}/{name}")
            if web_text and len(web_text) > 100:
                studier._index(f"vscode:{ext_id}:web", web_text, f"open-vsx-web:{ext_id}")
        except Exception:
            pass

        nodes_after = studier._count_nodes()
        result["nodes_added"] = nodes_after - nodes_before

        # 5. Instalar en el sistema
        if install:
            try:
                proc = subprocess.run(
                    ["code", "--install-extension", vsix_path],
                    capture_output=True, text=True, timeout=60
                )
                if proc.returncode == 0:
                    result["installed"] = True
                    log.info("study_extension: %s instalada correctamente", ext_id)
                else:
                    result["install_error"] = proc.stderr[:200]
                    log.warning("study_extension: instalación falló: %s", proc.stderr[:100])
            except Exception as e:
                result["install_error"] = str(e)

        # 6. Desinstalar si se pidió (aprende y luego limpia)
        if uninstall_after and result["installed"]:
            try:
                subprocess.run(["code", "--uninstall-extension", ext_id],
                               capture_output=True, timeout=30)
                result["uninstalled"] = True
                log.info("study_extension: %s desinstalada tras aprendizaje", ext_id)
            except Exception:
                pass

    # Mensaje proactivo para SER
    try:
        from core.colony_proactive import push_message
        install_status = "instalada ✓" if result["installed"] else "estudiada (sin instalar)"
        push_message(
            actor="Colony",
            message=(f"Aprendí la extensión VSCode '{display}' ({ext_id}) — {install_status}. "
                     f"+{result['nodes_added']} nodos. Sé qué hace, cuándo usarla y cómo combinarla."),
            topic=f"vscode_ext:{ext_id}",
            priority=6,
        )
    except Exception:
        pass

    result["display_name"] = display
    result["version"] = version
    result["description"] = desc
    log.info("study_extension completado: %s — %d nodos, instalada=%s",
             ext_id, result["nodes_added"], result["installed"])
    return result
