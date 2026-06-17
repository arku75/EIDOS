"""
core/library_reader.py — EIDOS descarga y aprende libros de Z-Library.

Usa Firefox con la sesión de SER ya iniciada.
Descarga el libro, lo lee (PDF/EPUB), extrae conceptos y los guarda en el brain.
También descubre libros relacionados por su cuenta.
"""
import subprocess, os, logging, time, json, re
from pathlib import Path
from typing import Dict, Any, List, Optional

log = logging.getLogger("library_reader")

EIDOS_HOME   = Path.home() / ".eidos"
BOOKS_DIR    = EIDOS_HOME / "books"
BRAIN_DB     = EIDOS_HOME / "evolution_brain.db"
FIREFOX_PROF = Path.home() / ".mozilla" / "firefox" / "yii3m1ky.default-esr"

# Libros prioritarios que SER indicó
PRIORITY_BOOKS = [
    {
        "url": "https://z-lib.fm/book/pyMQ88dKyw/linux-basics-for-hackers-getting-started-with-networking-scripting-and-security-in-kali.html",
        "title": "Linux Basics for Hackers",
        "category": "security",
        "priority": 10
    },
    {
        "url": "https://z-lib.fm/book/JLZx8oKKy8/clawdbot-openclaw-the-local-first-ai-agent-handbook-build-host-and-secure-your-own-private-ai-ass.html",
        "title": "ClawdBot OpenClaw AI Handbook",
        "category": "ai",
        "priority": 10
    },
    {
        "url": "https://z-lib.fm/book/Wyrp4YYMLJ/the-ultimate-docker-container-book-build-ship-deploy-and-scale-containerized-applications-with-d.html",
        "title": "The Ultimate Docker Container Book",
        "category": "devops",
        "priority": 9
    },
    {
        "url": "https://z-lib.fm/book/PXolmM07LV/docker-das-praxisbuch-f%C3%BCr-entwickler-und-devopsteams-grundlagen-einstieg-konzepte-f%C3%BCr-windows.html",
        "title": "Docker Das Praxisbuch",
        "category": "devops",
        "priority": 8
    }
]

# Temas para buscar más libros automáticamente
DISCOVER_TOPICS = [
    "python programming", "sql injection", "penetration testing",
    "machine learning", "linux administration", "network security",
    "rust programming", "kubernetes", "web scraping", "artificial intelligence"
]


def _save_to_brain(concept: str, definition: str, category: str = "book_knowledge", confidence: float = 0.8):
    """Guarda conocimiento extraído del libro en evolution_brain.db."""
    import sqlite3
    try:
        import uuid, time as _t
        conn = get_conn(BRAIN_DB)
        conn.execute("""
            INSERT OR REPLACE INTO knowledge_nodes
            (id, concept, definition, category, confidence, source, last_used, agent_id, character)
            VALUES (?, ?, ?, ?, ?, 'library_reader', ?, 'eidos', 'EIDOS')
        """, (str(uuid.uuid4()), concept, definition, category, confidence, _t.time()))
        conn.commit()
        pass  # S109: get_conn no necesita close()
    except Exception as e:
        log.error(f"Brain save error: {e}")


def _extract_text_from_pdf(pdf_path: Path) -> str:
    """Extrae texto de PDF con pdftotext o pdfminer."""
    # Intentar pdftotext (poppler)
    r = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True, text=True, timeout=30
    )
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout[:50000]  # máx 50k chars

    # Fallback: pdfminer.six
    try:
        from pdfminer.high_level import extract_text
        return extract_text(str(pdf_path))[:50000]
    except ImportError:
        pass

    return ""


def _extract_text_from_epub(epub_path: Path) -> str:
    """Extrae texto de EPUB."""
    try:
        import zipfile
        text_parts = []
        with zipfile.ZipFile(str(epub_path)) as z:
            for name in z.namelist():
                if name.endswith((".html", ".xhtml", ".htm")):
                    with z.open(name) as f:
                        html = f.read().decode("utf-8", errors="ignore")
                        # Limpiar tags HTML básico
                        text = re.sub(r"<[^>]+>", " ", html)
                        text = re.sub(r"\s+", " ", text).strip()
                        text_parts.append(text)
        return " ".join(text_parts)[:50000]
    except Exception as e:
        log.error(f"EPUB extract error: {e}")
        return ""


def _analyze_and_learn(text: str, book_title: str, category: str):
    """
    Analiza el texto del libro con Ollama y guarda conceptos en el brain.
    Divide el libro en chunks de 2000 chars para procesar.
    """
    if not text.strip():
        log.warning(f"Texto vacío para: {book_title}")
        return 0

    chunks = [text[i:i+2000] for i in range(0, min(len(text), 20000), 2000)]
    concepts_saved = 0

    for i, chunk in enumerate(chunks[:10]):  # máx 10 chunks por sesión
        try:
            prompt = f"""Eres EIDOS, un agente de IA. Estás leyendo el libro "{book_title}".

Analiza este fragmento y extrae los 3-5 conceptos más importantes:

{chunk}

Responde en este formato JSON exacto:
[
  {{"concept": "nombre_del_concepto", "definition": "explicación clara en español de 1-2 frases", "importance": 0.9}},
  ...
]

Solo JSON, sin texto extra."""

            r = subprocess.run(
                ["ollama", "run", "lfm2.5-thinking:1.2b", prompt],
                capture_output=True, text=True, timeout=60,
                env={**os.environ, "OLLAMA_NUM_PARALLEL": "1"}
            )

            if r.returncode == 0:
                # Extraer JSON de la respuesta
                output = r.stdout.strip()
                json_match = re.search(r'\[.*?\]', output, re.DOTALL)
                if json_match:
                    concepts = json.loads(json_match.group())
                    for c in concepts:
                        concept_key = f"book:{book_title.lower().replace(' ', '_')}:{c.get('concept', '').replace(' ', '_')}"
                        _save_to_brain(
                            concept_key,
                            c.get("definition", ""),
                            category,
                            float(c.get("importance", 0.7))
                        )
                        concepts_saved += 1

        except Exception as e:
            log.error(f"Chunk {i} analysis error: {e}")
            continue

        time.sleep(1)  # No saturar Ollama

    log.info(f"Libro '{book_title}': {concepts_saved} conceptos guardados en brain")
    return concepts_saved


def _get_download_link_native(book_url: str) -> Optional[str]:
    """
    Usa el Firefox REAL de SER (xdotool) para navegar a la página del libro
    y extraer la URL /dl/... via JavaScript bookmarklet en la barra de direcciones.
    Si Firefox no está abierto, lo lanza con el perfil de SER.
    """
    try:
        from core.browser_native import open_firefox_if_needed, navigate_to, get_firefox_wid
        wid = open_firefox_if_needed()
        if not wid:
            return None

        navigate_to(book_url, wid)
        time.sleep(4)  # esperar que cargue la página

        # JavaScript que extrae los /dl/ links y los imprime en la barra de títulos
        # Lo ejecutamos via la consola del developer (F12 → Console) no es posible via xdotool
        # Alternativa: leer el HTML via curl_cffi con cookies frescas del perfil
        from core.firefox_session import get_cookies
        cookies = get_cookies("z-lib")
        if not cookies:
            log.warning("No hay cookies z-library — SER debe estar loggeado en Firefox ESR")
            return None

        try:
            from curl_cffi import requests as cffi_req
            session = cffi_req.Session(impersonate="firefox")
            for name, val in cookies.items():
                session.cookies.set(name, val, domain=".z-lib.fm")
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
                "Referer": "https://z-lib.fm/",
            })
            r = session.get(book_url, timeout=20)
            if r.status_code == 200:
                import re
                # Buscar /dl/ link en el HTML
                for m in re.finditer(r'href="(/dl/[A-Za-z0-9]+[^"]*)"', r.text):
                    return f"https://z-lib.fm{m.group(1)}"
        except Exception as e:
            log.debug(f"curl_cffi fallback: {e}")
    except Exception as e:
        log.error(f"_get_download_link_native: {e}")
    return None


def _get_download_link(book_url: str) -> Optional[str]:
    """
    Navega a la página del libro y extrae URL /dl/... directa.
    Método 1: Firefox REAL de SER (browser_native + curl_cffi con cookies frescas).
    Método 2: Playwright Firefox (caché en ~/.cache/ms-playwright) como fallback.
    """
    # Método 1 — Firefox ESR real de SER (preferido)
    link = _get_download_link_native(book_url)
    if link:
        return link

    # Método 2 — Playwright con el Firefox que SÍ funciona (150, en caché)
    script = f"""
import asyncio, json, sys
from playwright.async_api import async_playwright
from pathlib import Path

async def find_link():
    async with async_playwright() as p:
        browser = await p.firefox.launch_persistent_context(
            user_data_dir="{FIREFOX_PROF}",
            headless=True
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()
        await page.goto("{book_url}", timeout=30000)
        await page.wait_for_timeout(3000)

        links = await page.evaluate('''() => {{
            const results = [];
            document.querySelectorAll("a").forEach(a => {{
                const href = a.href || "";
                const text = a.textContent.trim().toLowerCase();
                if (href.includes("/dl/") || href.includes("/download/") ||
                    text.includes("pdf") || text.includes("epub") ||
                    text.includes("download") || text.includes("descarg")) {{
                    results.push({{href: href, text: a.textContent.trim().slice(0,60)}});
                }}
            }});
            return results;
        }}''')
        await browser.close()
        print(json.dumps(links))

asyncio.run(find_link())
"""
    tmp = Path("/tmp/zlib_find_link.py")
    tmp.write_text(script)
    r = subprocess.run(["python3", str(tmp)], capture_output=True, text=True, timeout=45)
    try:
        links = json.loads(r.stdout.strip())
        for link in links:
            href = link.get("href", "")
            if "/dl/" in href and ("pdf" in link.get("text","").lower() or "pdf" in href.lower()):
                return href
        for link in links:
            if "/dl/" in link.get("href",""):
                return link["href"]
    except Exception:
        pass
    return None


def download_book_native(url: str, output_dir: Path, title: str = "") -> Optional[Path]:
    """
    MÉTODO PRINCIPAL: descarga usando el Firefox REAL de SER (xdotool/browser_native).
    EIDOS toma control del Firefox que SER ya tiene abierto y descarga el libro.
    Si Firefox no está abierto, lo lanza con el perfil real de SER.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        from core.browser_native import download_from_zlibrary_native
        result = download_from_zlibrary_native(url, title or "libro")
        if result and result.exists():
            # Mover a output_dir si no está ya allí
            if result.parent != output_dir:
                dest = output_dir / result.name
                result.rename(dest)
                return dest
            return result
    except Exception as e:
        log.warning(f"download_book_native falló: {e}")
    return None


def download_book_playwright(url: str, output_dir: Path) -> Optional[Path]:
    """
    FALLBACK: Playwright Firefox (usa la versión 150 del caché ms-playwright).
    Estrategia verificada y funcional:
      1. Extraer URL /dl/XXXX de la página del libro
      2. Navegar directamente a esa URL → Firefox dispara la descarga
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Paso 1: obtener URL de descarga directa
    dl_url = _get_download_link(url)
    if not dl_url:
        log.error(f"No se encontró enlace de descarga en: {url}")
        return None

    log.info(f"URL descarga directa: {dl_url}")

    # Paso 2: navegar directo a /dl/XXXX → Firefox descarga
    script = f"""
import asyncio, sys
from playwright.async_api import async_playwright
from pathlib import Path

async def do_download():
    async with async_playwright() as p:
        browser = await p.firefox.launch_persistent_context(
            user_data_dir="{FIREFOX_PROF}",
            headless=False,
            downloads_path="{output_dir}",
            accept_downloads=True
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()
        try:
            async with page.expect_download(timeout=90000) as dl_info:
                await page.goto("{dl_url}", wait_until="commit", timeout=30000)
            dl = await dl_info.value
            save_path = "{output_dir}/" + dl.suggested_filename
            await dl.save_as(save_path)
            print(save_path)
        except Exception as e:
            print("FAILED: " + str(e), file=sys.stderr)
            print("FAILED")
        finally:
            await browser.close()

asyncio.run(do_download())
"""
    tmp = Path("/tmp/zlibrary_dl.py")
    tmp.write_text(script)

    r = subprocess.run(["python3", str(tmp)], capture_output=True, text=True, timeout=120)
    output = r.stdout.strip()

    if output and output != "FAILED" and not output.startswith("FAILED") and Path(output).exists():
        return Path(output)

    # Buscar en carpeta por fecha (descarga pudo ocurrir igualmente)
    files = sorted(output_dir.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
    if files and (time.time() - files[0].stat().st_mtime) < 120:
        return files[0]

    log.error(f"Descarga fallida. stderr: {r.stderr[:300]}")
    return None


def read_book(url: str, title: str, category: str = "general") -> Dict[str, Any]:
    """
    Proceso completo: descargar → extraer texto → aprender → guardar en brain.
    Usa browser_native si Firefox ya está abierto (sin ser detectado como bot).
    """
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"Descargando libro: {title}")

    # Marcar en brain que está leyendo
    _save_to_brain(
        f"book:reading:{title.lower().replace(' ', '_')}",
        f"EIDOS está leyendo: {title}. URL: {url}",
        "reading_progress",
        0.5
    )

    # Método 1: Firefox REAL de SER (xdotool — sin ser detectado como bot)
    book_file = download_book_native(url, BOOKS_DIR, title)
    # Método 2: Playwright Firefox 150 (fallback verificado)
    if not book_file:
        log.info(f"Nativo falló, intentando con Playwright Firefox...")
        book_file = download_book_playwright(url, BOOKS_DIR)
    if not book_file:
        log.error(f"No se pudo descargar: {title}")
        return {"ok": False, "error": "Descarga fallida", "title": title}

    log.info(f"Descargado: {book_file}")

    # Extraer texto según formato
    suffix = book_file.suffix.lower()
    if suffix == ".pdf":
        text = _extract_text_from_pdf(book_file)
    elif suffix in (".epub", ".epub3"):
        text = _extract_text_from_epub(book_file)
    else:
        # Intentar leer como texto plano
        try:
            text = book_file.read_text(errors="ignore")[:50000]
        except Exception:
            text = ""

    if not text:
        return {"ok": False, "error": "No se pudo extraer texto", "file": str(book_file)}

    # Aprender del libro
    concepts = _analyze_and_learn(text, title, category)

    # Marcar como completado
    _save_to_brain(
        f"book:completed:{title.lower().replace(' ', '_')}",
        f"EIDOS completó la lectura de '{title}'. Conceptos aprendidos: {concepts}. Archivo: {book_file}",
        "books_read",
        1.0
    )

    return {
        "ok": True,
        "title": title,
        "file": str(book_file),
        "text_length": len(text),
        "concepts_saved": concepts
    }


def read_priority_books() -> List[Dict]:
    """Lee todos los libros prioritarios que indicó SER."""
    results = []
    for book in PRIORITY_BOOKS:
        log.info(f"Procesando: {book['title']}")
        result = read_book(book["url"], book["title"], book["category"])
        results.append(result)
        time.sleep(2)  # Pausa entre libros
    return results


def discover_books(topic: str = None) -> List[Dict]:
    """
    Busca libros relacionados con un tema en z-library.
    Guarda los encontrados en brain para leerlos después.
    """
    if not topic:
        import random
        topic = random.choice(DISCOVER_TOPICS)

    search_url = f"https://z-lib.fm/s/{topic.replace(' ', '+')}"

    script = f"""
import asyncio
from playwright.async_api import async_playwright
import json
from core.db import get_conn

async def search():
    async with async_playwright() as p:
        browser = await p.firefox.launch_persistent_context(
            user_data_dir="{FIREFOX_PROF}",
            headless=True
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()
        await page.goto("{search_url}", timeout=20000)
        await page.wait_for_timeout(2000)

        books = []
        items = await page.query_selector_all(".bookRow, .book-item, .z-book-item")
        for item in items[:8]:
            try:
                title_el = await item.query_selector("h3 a, .title a, a[class*='title']")
                title = await title_el.inner_text() if title_el else ""
                href  = await title_el.get_attribute("href") if title_el else ""
                if title and href:
                    books.append({{"title": title.strip(), "url": "https://z-lib.fm" + href}})
            except:
                pass

        await browser.close()
        print(json.dumps(books))

asyncio.run(search())
"""
    tmp = Path("/tmp/zlib_search.py")
    tmp.write_text(script)
    r = subprocess.run(["python3", str(tmp)], capture_output=True, text=True, timeout=30)

    books = []
    try:
        books = json.loads(r.stdout.strip())
    except Exception:
        pass

    # Guardar descubrimientos en brain
    for book in books:
        _save_to_brain(
            f"book:discovered:{book['title'].lower().replace(' ', '_')[:40]}",
            f"Libro encontrado en z-library sobre '{topic}': {book['title']}. URL: {book['url']}",
            "books_discovered",
            0.6
        )

    return books
