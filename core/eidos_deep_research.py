"""
core/eidos_deep_research.py — Investigación PROFUNDA con crawl de sublinks [S122]
================================================================================
SER quiere que EIDOS no se quede en una definición: que BUSQUE, LEA todo, siga
los sublinks y los lea, y luego SINTETICE y APRENDA. Para cualquier tema.

Pipeline:
  1. Buscar URLs (DuckDuckGo HTML).
  2. Crawl BFS: leer cada página (BeautifulSoup) + recoger sublinks relevantes,
     hasta `depth` niveles y `max_pages` páginas (acotado para no colgarse).
  3. Sintetizar TODO lo leído con el LLM (grounded en el texto real, no inventado).
  4. Aprender: persistir conceptos clave al grafo vía el portero de calidad.

Reutiliza helpers de eidos_active_research (extract_definition, _UA) y la cascada
de eidos_learn (ask_llm) y el portero (eidos_quality_gate).
"""
from __future__ import annotations

import re
import time
import logging
import urllib.parse as _up
import urllib.request as _req
from typing import List, Dict, Tuple, Set

log = logging.getLogger("eidos.deep_research")

_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
_SKIP_DOMAINS = ("facebook.com", "twitter.com", "x.com", "instagram.com",
                 "youtube.com", "tiktok.com", "pinterest.com", "linkedin.com",
                 "amazon.", "ads.", "doubleclick", "google.com/aclk")


def _http_get(url: str, timeout: int = 8) -> str:
    try:
        req = _req.Request(url, headers={"User-Agent": _UA, "Accept-Language": "es,en"})
        with _req.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            if "html" not in ctype and "text" not in ctype:
                return ""
            raw = r.read(800_000)  # cap 800KB/página
        return raw.decode("utf-8", errors="ignore")
    except Exception as e:  # noqa: BLE001
        log.debug("GET fallo %s: %s", url[:60], e)
        return ""


def _search_urls(topic: str, max_results: int = 6) -> List[str]:
    """URLs de resultados de DuckDuckGo Lite (markup simple, envuelve en uddg=)."""
    urls: List[str] = []
    seen: Set[str] = set()
    try:
        q = _up.quote(topic)
        html = _http_get(f"https://lite.duckduckgo.com/lite/?q={q}", timeout=8)
        # Los resultados van como //duckduckgo.com/l/?uddg=<url-encoded>&...
        for m in re.finditer(r"uddg=([^&\"']+)", html):
            real = _up.unquote(m.group(1))
            if not real.startswith("http"):
                continue
            if any(d in real for d in _SKIP_DOMAINS):
                continue
            base = real.split("#")[0]
            if base in seen:
                continue
            seen.add(base)
            urls.append(base)
            if len(urls) >= max_results:
                break
    except Exception as e:  # noqa: BLE001
        log.debug("search fallo: %s", e)
    return urls


def _extract_page(html: str, base_url: str) -> Tuple[str, List[str]]:
    """Devuelve (texto_principal, sublinks) de una página."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        # Texto: párrafos + listas + encabezados
        parts = []
        for el in soup.find_all(["h1", "h2", "h3", "p", "li"]):
            t = el.get_text(" ", strip=True)
            if len(t) > 30:
                parts.append(t)
        text = "\n".join(parts)
        # Sublinks (absolutos, mismo dominio o relevantes)
        base_dom = _up.urlparse(base_url).netloc
        links: List[str] = []
        for a in soup.find_all("a", href=True):
            href = _up.urljoin(base_url, a["href"])
            if not href.startswith("http"):
                continue
            if any(d in href for d in _SKIP_DOMAINS):
                continue
            dom = _up.urlparse(href).netloc
            # priorizar mismo dominio (sublinks del tema) y docs/github/wiki
            if dom == base_dom or any(k in href for k in ("docs", "github.com", "wikipedia", "/wiki/", "guide", "tutorial")):
                links.append(href.split("#")[0])
        return text, links
    except Exception as e:  # noqa: BLE001
        log.debug("extract fallo: %s", e)
        return "", []


def crawl(topic: str, depth: int = 1, max_pages: int = 14,
          timeout_total: float = 60.0) -> List[Dict]:
    """Crawl BFS: lee páginas y sigue sublinks. Devuelve lista de {url, text}.

    depth=0: solo resultados de búsqueda. depth=1: + sublinks de esos. etc.
    Acotado por max_pages y timeout_total para no colgarse.
    """
    deadline = time.time() + timeout_total
    seeds = _search_urls(topic, max_results=6)
    if not seeds:
        return []
    visited: Set[str] = set()
    docs: List[Dict] = []
    # frontera: (url, nivel)
    frontier: List[Tuple[str, int]] = [(u, 0) for u in seeds]
    while frontier and len(docs) < max_pages and time.time() < deadline:
        url, lvl = frontier.pop(0)
        if url in visited:
            continue
        visited.add(url)
        html = _http_get(url, timeout=8)
        if not html:
            continue
        text, links = _extract_page(html, url)
        if text and len(text) > 200:
            docs.append({"url": url, "text": text[:6000]})
            log.info("deep: leído %s (%d chars, nivel %d)", url[:60], len(text), lvl)
        if lvl < depth:
            for ln in links[:8]:  # hasta 8 sublinks por página
                if ln not in visited:
                    frontier.append((ln, lvl + 1))
    return docs


def crawl_visible(topic: str, max_visible: int = 5, timeout_total: float = 120.0) -> List[Dict]:
    """Como crawl() pero ABRE cada página en un Firefox VISIBLE (Playwright headed),
    la estudia (scroll + extrae texto) y al final CIERRA el navegador — para que SER
    VEA a EIDOS investigando. SER pidió ver abrir/estudiar/cerrar las páginas. [S122]"""
    deadline = time.time() + timeout_total
    seeds = _search_urls(topic, max_results=max_visible + 2)
    docs: List[Dict] = []
    agent = None
    try:
        from core.eidos_playwright import get_playwright_agent
        agent = get_playwright_agent(headless=False)
        agent.start()
        for u in seeds[:max_visible]:
            if time.time() >= deadline:
                break
            try:
                if agent.goto(u, wait_time=2):
                    agent.scroll(1000)
                    time.sleep(0.6)
                    agent.scroll(1200)
                    txt = agent.extract_text(6000)
                    if txt and len(txt) > 200:
                        docs.append({"url": u, "text": txt[:6000]})
                        log.info("deep(visible): estudiado %s (%d chars)", u[:60], len(txt))
            except Exception as e:  # noqa: BLE001
                log.debug("visible goto fallo %s: %s", u[:50], e)
    except Exception as e:  # noqa: BLE001
        log.warning("crawl_visible no disponible (%s) — cae a crawl HTTP", e)
    finally:
        if agent is not None:
            try:
                agent.stop()  # cierra el navegador (SER ve cerrarse)
            except Exception:
                pass
    # Si lo visible falló o trajo poco, completar por HTTP (profundidad sin ralentizar)
    if len(docs) < 3:
        seen = {d["url"] for d in docs}
        for d in crawl(topic, depth=1, max_pages=10, timeout_total=max(10, deadline - time.time())):
            if d["url"] not in seen:
                docs.append(d)
    return docs


def synthesize(topic: str, docs: List[Dict], timeout: int = 60) -> str:
    """Sintetiza TODO lo leído con el LLM, grounded en el texto real."""
    if not docs:
        return ""
    corpus = "\n\n".join(f"[Fuente: {d['url']}]\n{d['text'][:2500]}" for d in docs[:8])
    corpus = corpus[:14000]
    prompt = (
        f"Eres EIDOS. He leído estas páginas reales sobre «{topic}». "
        f"Sintetiza en español lo que SE APRENDE de verdad: qué es, para qué sirve, "
        f"cómo funciona, si es open source / GitHub, y cómo se usa. Sé concreto y "
        f"basa TODO en el texto; no inventes. Si es código/herramienta, incluye cómo empezar.\n\n"
        f"=== CONTENIDO LEÍDO ===\n{corpus}\n\n=== SÍNTESIS ==="
    )
    try:
        from core.eidos_learn import ask_llm
        ans, _src = ask_llm(prompt, timeout=timeout)
        return ans
    except Exception as e:  # noqa: BLE001
        log.debug("synthesize fallo: %s", e)
        return ""


def _learn(topic: str, summary: str, sources: List[str]) -> int:
    """Persiste la síntesis como nodo de conocimiento vía el portero."""
    if not summary or len(summary) < 40:
        return 0
    try:
        from core.eidos_quality_gate import gate
        from core.db import get_conn
        from pathlib import Path
        verdict = gate.evaluate(topic.strip().lower(), summary[:1500],
                                "research:deep", "general", 0.8)
        if not verdict.admit:
            return 0
        BRAIN = Path.home() / ".eidos" / "evolution_brain.db"
        concept = topic.strip().lower()
        conn = get_conn(BRAIN, timeout=10, cache=False)
        conn.execute("PRAGMA journal_mode=WAL")
        row = conn.execute(
            "SELECT definition, quality_score FROM knowledge_nodes WHERE concept=?",
            (concept,)).fetchone()
        if row:
            # Ya existe: ACTUALIZAR solo si la síntesis profunda es mejor (más rica
            # o mayor calidad) que lo que había. Así la investigación a fondo MEJORA
            # el conocimiento en vez de quedarse fuera por duplicado.
            old_def = row[0] or ""
            old_q = row[1] or 0.0
            if len(summary) > len(old_def) + 50 or verdict.quality_score > old_q:
                conn.execute(
                    "UPDATE knowledge_nodes SET definition=?, quality_score=?, "
                    "source=?, last_used=? WHERE concept=?",
                    (summary[:1500], max(verdict.quality_score, old_q),
                     "research:deep", time.time(), concept))
                conn.commit()
                return 1
            conn.commit()
            return 0
        cur = conn.execute(
            "INSERT OR IGNORE INTO knowledge_nodes "
            "(id, concept, definition, category, confidence, source, quality_score) "
            "VALUES (?,?,?,?,?,?,?)",
            (f"deep_{int(time.time())}_{abs(hash(topic)) % 100000}",
             concept, summary[:1500], "general", 0.8,
             "research:deep", verdict.quality_score))
        conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    except Exception as e:  # noqa: BLE001
        log.debug("learn fallo: %s", e)
        return 0


def investigate_deep(topic: str, depth: int = 1, max_pages: int = 14,
                     visible: bool = False) -> Dict:
    """Investigación PROFUNDA completa: crawl → síntesis → aprender.

    visible=True: abre las páginas en un Firefox visible (SER lo ve estudiar).
    Returns: {topic, pages_read, sources, summary, learned}
    """
    t0 = time.time()
    topic = (topic or "").strip()
    if visible:
        docs = crawl_visible(topic, max_visible=5)
    else:
        docs = crawl(topic, depth=depth, max_pages=max_pages)
    summary = synthesize(topic, docs) if docs else ""
    sources = [d["url"] for d in docs]
    learned = _learn(topic, summary, sources) if summary else 0
    return {
        "topic": topic,
        "pages_read": len(docs),
        "sources": sources,
        "summary": summary,
        "learned": bool(learned),
        "elapsed_s": round(time.time() - t0, 1),
    }


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    logging.basicConfig(level=logging.INFO)
    topic = sys.argv[1] if len(sys.argv) > 1 else "n8n"
    print(f"=== Investigación profunda: {topic} ===")
    r = investigate_deep(topic, depth=1, max_pages=10)
    print(f"\nPáginas leídas: {r['pages_read']} | aprendido: {r['learned']} | {r['elapsed_s']}s")
    print(f"Fuentes: {r['sources'][:5]}")
    print(f"\nSÍNTESIS:\n{r['summary'][:1200]}")
