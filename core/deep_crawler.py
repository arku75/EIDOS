"""
EIDOS core/deep_crawler.py — Deep Web Crawler
Lee una URL principal + TODOS sus sublinks del mismo dominio.
Sintetiza con Ollama y guarda en ChromaDB.

Uso:
    from core.deep_crawler import get_deep_crawler
    crawler = get_deep_crawler()
    report = crawler.crawl("https://docs.openclaw.dev", max_pages=30)
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

log = logging.getLogger("eidos.deep_crawler")

# ── Configuración ──────────────────────────────────────────────────────────────
MAX_PAGES_DEFAULT = 30
REQUEST_DELAY     = 0.8     # segundos entre requests (cortesía)
REQUEST_TIMEOUT   = 15
MAX_CONTENT_CHARS = 8000    # chars de contenido por página para síntesis
OLLAMA_URL        = "http://localhost:11434"
SYNTH_MODEL       = "lfm2.5-thinking:1.2b"


@dataclass
class PageResult:
    url: str
    title: str
    text: str
    links: List[str]
    status: int
    error: Optional[str] = None
    summary: str = ""


@dataclass
class CrawlReport:
    root_url: str
    pages_visited: int
    pages_failed: int
    total_chars: int
    synthesis: str
    key_topics: List[str]
    all_urls: List[str]
    duration_s: float
    chroma_stored: int = 0


# ── Utilidades HTTP ────────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 EIDOS/1.0",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "es,en;q=0.7",
}


def _fetch_html(url: str, timeout: int = REQUEST_TIMEOUT) -> tuple[str, int]:
    """Descarga HTML de una URL. Devuelve (html, status_code)."""
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = "utf-8"
            ct = resp.headers.get("Content-Type", "")
            if "charset=" in ct:
                charset = ct.split("charset=")[-1].strip().split(";")[0]
            return resp.read().decode(charset, errors="replace"), resp.status
    except urllib.error.HTTPError as e:
        return "", e.code
    except Exception as e:
        return "", 0


def _html_to_text(html: str) -> str:
    """Extrae texto limpio de HTML."""
    # Quitar scripts y styles
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>",  " ", html, flags=re.DOTALL | re.IGNORECASE)
    # Quitar tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Limpiar espacios
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""


def _extract_links(html: str, base_url: str) -> List[str]:
    """Extrae todos los <a href> y los normaliza respecto a base_url."""
    base = urllib.parse.urlparse(base_url)
    links = []
    for href in re.findall(r'href=["\']([^"\'#?][^"\']*)["\']', html, re.IGNORECASE):
        try:
            abs_url = urllib.parse.urljoin(base_url, href).split("#")[0].split("?")[0]
            parsed = urllib.parse.urlparse(abs_url)
            if parsed.scheme in ("http", "https") and parsed.netloc == base.netloc:
                links.append(abs_url)
        except Exception:
            pass
    return list(dict.fromkeys(links))  # deduplicar manteniendo orden


# ── Ollama ─────────────────────────────────────────────────────────────────────

def _ollama_summarize(text: str, url: str, model: str = SYNTH_MODEL) -> str:
    """Genera un resumen conciso de una página con Ollama."""
    snippet = text[:MAX_CONTENT_CHARS]
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content":
                "Eres un investigador. Responde en español. Sé conciso y factual."},
            {"role": "user", "content":
                f"Resume en 3-5 frases los puntos más importantes de esta página:\n\nURL: {url}\n\n{snippet}"},
        ],
        "stream": False,
        "options": {"num_predict": 300, "temperature": 0.3},
    }).encode()
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat",
            data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read()).get("message", {}).get("content", "").strip()
    except Exception as e:
        return f"[sin resumen: {e}]"


def _ollama_synthesize(summaries: List[str], root_url: str, model: str = SYNTH_MODEL) -> tuple[str, List[str]]:
    """Síntesis final de todas las páginas."""
    combined = "\n\n---\n\n".join(summaries[:25])[:12000]
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content":
                "Eres un investigador experto. Responde en español. Sé analítico y exhaustivo."},
            {"role": "user", "content":
                f"He investigado {len(summaries)} páginas del sitio {root_url}.\n\n"
                f"Aquí están los resúmenes de cada página:\n\n{combined}\n\n"
                "Por favor:\n"
                "1. Síntesis global del sitio (qué es, para qué sirve, arquitectura principal)\n"
                "2. Funcionalidades y características clave encontradas\n"
                "3. Patrones o conceptos recurrentes\n"
                "4. Lista los 10 temas principales como: TEMAS: tema1, tema2, ..."},
        ],
        "stream": False,
        "options": {"num_predict": 1500, "temperature": 0.4},
    }).encode()
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat",
            data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            synthesis = json.loads(resp.read()).get("message", {}).get("content", "").strip()
    except Exception as e:
        synthesis = f"[síntesis no disponible: {e}]"

    # Extraer temas
    topics = []
    m = re.search(r"TEMAS:\s*(.+)", synthesis, re.IGNORECASE)
    if m:
        topics = [t.strip() for t in m.group(1).split(",") if t.strip()][:10]

    return synthesis, topics


# ── ChromaDB ───────────────────────────────────────────────────────────────────

def _store_in_chroma(pages: List[PageResult], root_url: str) -> int:
    """Guarda los resultados en ChromaDB para búsqueda futura."""
    try:
        import chromadb
        import os
        client = chromadb.PersistentClient(path=os.path.expanduser("~/.eidos/chroma"))
        col = client.get_or_create_collection("eidos_knowledge")
        stored = 0
        for p in pages:
            if not p.text or p.error:
                continue
            doc_id = "crawl_" + hashlib.md5(p.url.encode()).hexdigest()[:16]
            doc = f"[{p.title}] {p.url}\n\n{p.summary or p.text[:1000]}"
            col.upsert(
                documents=[doc],
                ids=[doc_id],
                metadatas=[{"url": p.url, "title": p.title,
                            "root": root_url, "source": "deep_crawler",
                            "ts": str(int(time.time()))}]
            )
            stored += 1
        return stored
    except Exception as e:
        log.warning("ChromaDB: %s", e)
        return 0


# ── Clase principal ────────────────────────────────────────────────────────────

class DeepCrawler:
    """
    Crawleador recursivo que lee una URL y todos sus sublinks del mismo dominio.
    Sintetiza con Ollama y guarda en ChromaDB.
    """

    def __init__(self, max_pages: int = MAX_PAGES_DEFAULT, delay: float = REQUEST_DELAY):
        self.max_pages = max_pages
        self.delay = delay

    def crawl(self, url: str, max_pages: int = None, summarize: bool = True,
              store_chroma: bool = True, progress_cb=None) -> CrawlReport:
        """
        Crawl completo de url + sublinks.

        Args:
            url: URL raíz a explorar
            max_pages: máximo de páginas (None = usa default del crawler)
            summarize: si True, resume cada página con Ollama
            store_chroma: si True, guarda en ChromaDB
            progress_cb: callback(visited, total_queued, current_url) para progreso
        """
        limit = max_pages or self.max_pages
        t0 = time.time()

        visited: Set[str] = set()
        queue: List[str] = [url.rstrip("/")]
        pages: List[PageResult] = []
        failed = 0

        log.info("DeepCrawler iniciando: %s (max=%d páginas)", url, limit)

        while queue and len(visited) < limit:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            if progress_cb:
                progress_cb(len(visited), len(queue), current)

            log.info("[%d/%d] %s", len(visited), limit, current)

            html, status = _fetch_html(current)
            if not html or status not in (200, 0):
                pages.append(PageResult(url=current, title="", text="",
                                        links=[], status=status,
                                        error=f"HTTP {status}"))
                failed += 1
                continue

            title = _extract_title(html)
            text  = _html_to_text(html)
            links = _extract_links(html, current)

            # Añadir links nuevos a la cola
            for lnk in links:
                if lnk not in visited and lnk not in queue:
                    queue.append(lnk)

            # Resumir con Ollama si se pide
            summary = ""
            if summarize and text.strip():
                summary = _ollama_summarize(text, current)

            pages.append(PageResult(
                url=current, title=title,
                text=text[:MAX_CONTENT_CHARS],
                links=links, status=status, summary=summary
            ))

            if self.delay > 0:
                time.sleep(self.delay)

        # Síntesis global
        summaries = [p.summary or p.text[:500] for p in pages if not p.error]
        synthesis, topics = _ollama_synthesize(summaries, url)

        # Guardar en ChromaDB
        stored = _store_in_chroma(pages, url) if store_chroma else 0

        duration = round(time.time() - t0, 1)
        total_chars = sum(len(p.text) for p in pages)

        log.info("Crawl completo: %d páginas, %d fallos, %.1fs", len(pages), failed, duration)

        return CrawlReport(
            root_url=url,
            pages_visited=len(pages) - failed,
            pages_failed=failed,
            total_chars=total_chars,
            synthesis=synthesis,
            key_topics=topics,
            all_urls=[p.url for p in pages],
            duration_s=duration,
            chroma_stored=stored,
        )

    def crawl_and_print(self, url: str, max_pages: int = None) -> CrawlReport:
        """Crawl con output en tiempo real."""
        def progress(visited, queued, current_url):
            short = current_url[:70] + "…" if len(current_url) > 70 else current_url
            print(f"\r  🕷️  [{visited}] {short}      ", end="", flush=True)

        print(f"\n🔍 DeepCrawler → {url}")
        print(f"   Máx páginas: {max_pages or self.max_pages} | delay: {self.delay}s\n")

        report = self.crawl(url, max_pages=max_pages, progress_cb=progress)
        print()  # newline tras el progreso

        print(f"\n{'─'*60}")
        print(f"✅ Crawl completado en {report.duration_s}s")
        print(f"   Páginas: {report.pages_visited} OK / {report.pages_failed} fallidas")
        print(f"   Caracteres totales: {report.total_chars:,}")
        print(f"   ChromaDB: {report.chroma_stored} nodos guardados")
        if report.key_topics:
            print(f"   Temas: {', '.join(report.key_topics)}")
        print(f"\n{'─'*60}")
        print("📋 SÍNTESIS:\n")
        print(report.synthesis)
        print(f"{'─'*60}\n")

        return report


_instance: Optional[DeepCrawler] = None


def get_deep_crawler(max_pages: int = MAX_PAGES_DEFAULT) -> DeepCrawler:
    global _instance
    if _instance is None:
        _instance = DeepCrawler(max_pages=max_pages)
    return _instance
