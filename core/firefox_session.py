"""
core/firefox_session.py — EIDOS usa la sesión REAL de Firefox ESR de SER.

SOLUCIÓN PERMANENTE al problema del navegador:
  - Lee las cookies del perfil Firefox ESR real de SER (SQLite)
  - Usa curl_cffi con impersonate="firefox" → TLS fingerprint idéntico al Firefox real
  - Cloudflare ve exactamente lo mismo que cuando SER navega manualmente
  - NO abre ningún navegador nuevo — usa la sesión activa de SER

Si SER tiene el Firefox abierto con sesión activa → EIDOS hereda esa sesión.
Si Firefox no está abierto → EIDOS usa las cookies guardadas del último login.

Uso:
  from core.firefox_session import get_session, search_zlibrary, get_page
  session = get_session()
  books = search_zlibrary("python security 2023")
  html = get_page("https://z-lib.fm/book/...")
"""
import sqlite3, shutil, tempfile, logging, time
from pathlib import Path
from typing import Dict, Any, List, Optional
from core.db import get_conn

log = logging.getLogger("firefox_session")

FIREFOX_BIN     = "/usr/bin/firefox-esr"   # Firefox ESR real del sistema de SER
FIREFOX_PROFILES = [
    Path.home() / ".mozilla/firefox/yii3m1ky.default-esr",
    Path.home() / ".mozilla/firefox",
]
BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"


# ── Leer cookies reales del perfil Firefox de SER ─────────────────────────────

def _find_firefox_profile() -> Optional[Path]:
    """Encuentra el perfil Firefox ESR de SER."""
    direct = Path.home() / ".mozilla/firefox/yii3m1ky.default-esr"
    if direct.exists() and (direct / "cookies.sqlite").exists():
        return direct

    # Buscar en profiles.ini
    profiles_ini = Path.home() / ".mozilla/firefox/profiles.ini"
    if profiles_ini.exists():
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(str(profiles_ini))
        for section in cfg.sections():
            if cfg.has_option(section, "Path"):
                p = Path.home() / ".mozilla/firefox" / cfg.get(section, "Path")
                if p.exists() and (p / "cookies.sqlite").exists():
                    return p
    return None


def get_cookies(domain_filter: str = None) -> Dict[str, str]:
    """
    Extrae cookies del perfil Firefox ESR de SER.
    Retorna dict {nombre: valor} listo para requests/curl_cffi.
    """
    profile = _find_firefox_profile()
    if not profile:
        log.error("No se encontró perfil Firefox ESR")
        return {}

    cookies_db = profile / "cookies.sqlite"

    # Copiar para no interferir con Firefox si está abierto
    tmp = Path(tempfile.mktemp(suffix=".sqlite"))
    try:
        shutil.copy2(str(cookies_db), str(tmp))
        conn = get_conn(tmp)

        if domain_filter:
            rows = conn.execute(
                "SELECT name, value FROM moz_cookies WHERE host LIKE ?",
                (f"%{domain_filter}%",)
            ).fetchall()
        else:
            rows = conn.execute("SELECT name, value FROM moz_cookies").fetchall()

        pass  # S109: get_conn no necesita close()
        return {name: value for name, value in rows}
    except Exception as e:
        log.error(f"Error leyendo cookies: {e}")
        return {}
    finally:
        if tmp.exists():
            tmp.unlink()


def get_all_cookies_jar(domain: str) -> List[Dict]:
    """Retorna lista de cookies en formato completo para curl_cffi."""
    profile = _find_firefox_profile()
    if not profile:
        return []

    tmp = Path(tempfile.mktemp(suffix=".sqlite"))
    try:
        shutil.copy2(str(profile / "cookies.sqlite"), str(tmp))
        conn = get_conn(tmp)
        rows = conn.execute(
            "SELECT host, name, value, path, isSecure, expiry FROM moz_cookies WHERE host LIKE ?",
            (f"%{domain}%",)
        ).fetchall()
        pass  # S109: get_conn no necesita close()
        return [{"host": h, "name": n, "value": v, "path": p, "secure": bool(s), "expires": e}
                for h, n, v, p, s, e in rows]
    except Exception as e:
        log.error(f"get_all_cookies_jar: {e}")
        return []
    finally:
        if tmp.exists():
            tmp.unlink()


# ── Sesión con TLS fingerprint real de Firefox ────────────────────────────────

def get_session():
    """
    Crea una sesión curl_cffi que imita exactamente el TLS de Firefox ESR.
    Cloudflare ve el mismo fingerprint que cuando SER navega manualmente.
    """
    try:
        from curl_cffi import requests as cffi_requests
        session = cffi_requests.Session(impersonate="firefox")

        # Cargar cookies de z-library de Firefox de SER
        cookies = get_cookies("z-lib")
        if not cookies:
            log.warning("No hay cookies de z-library — SER debe haber iniciado sesión primero")
        else:
            log.info(f"Sesión de SER cargada: {len(cookies)} cookies de z-library")
            for name, value in cookies.items():
                session.cookies.set(name, value, domain=".z-lib.fm")

        # Headers exactos de Firefox ESR 140
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.8,en-US;q=0.5,en;q=0.3",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        })

        return session

    except ImportError:
        log.error("curl_cffi no instalado — pip install curl_cffi --break-system-packages")
        return None


# ── Funciones de alto nivel ───────────────────────────────────────────────────

def get_page(url: str, referer: str = "https://z-lib.fm/") -> Optional[str]:
    """Obtiene el HTML de cualquier página usando la sesión real de SER."""
    session = get_session()
    if not session:
        return None
    try:
        session.headers["Referer"] = referer
        r = session.get(url, timeout=20, allow_redirects=True)
        log.info(f"GET {url} → HTTP {r.status_code}")
        return r.text if r.status_code == 200 else None
    except Exception as e:
        log.error(f"get_page({url}): {e}")
        return None


def search_zlibrary(query: str, limit: int = 8) -> List[Dict]:
    """
    Busca libros en z-library usando la sesión real de SER.
    Retorna lista de {title, author, year, url, format}.
    """
    import re
    session = get_session()
    if not session:
        return []

    url = f"https://z-lib.fm/s/{query.replace(' ', '+')}"
    try:
        session.headers["Referer"] = "https://z-lib.fm/"
        r = session.get(url, timeout=20)
        log.info(f"Búsqueda '{query}' → HTTP {r.status_code}")

        if r.status_code != 200:
            log.warning(f"HTTP {r.status_code} para búsqueda '{query}'")
            return []

        html = r.text
        books = []

        # Extraer libros del HTML de z-library
        book_links = re.findall(r'href="(/book/[A-Za-z0-9]+/[^"]{5,100})"', html)
        titles     = re.findall(r'<(?:h3|h2|b)[^>]*class="[^"]*title[^"]*"[^>]*>\s*<a[^>]*>([^<]{5,80})</a>', html)
        authors    = re.findall(r'class="[^"]*author[^"]*"[^>]*>([^<]{3,60})<', html)
        years      = re.findall(r'class="[^"]*year[^"]*"[^>]*>([^<]{4,10})<', html)

        # Si no hay estructura clara, intentar con links + texto cercano
        if not book_links:
            book_links = re.findall(r'href="(/book/[^"]+)"', html)

        seen = set()
        for i, href in enumerate(book_links[:limit]):
            if href in seen:
                continue
            seen.add(href)
            books.append({
                "title":  titles[i]  if i < len(titles)  else f"Libro {i+1}",
                "author": authors[i] if i < len(authors) else "?",
                "year":   years[i]   if i < len(years)   else "?",
                "url":    f"https://z-lib.fm{href}",
                "format": "pdf"
            })

        return books

    except Exception as e:
        log.error(f"search_zlibrary: {e}")
        return []


def get_download_url(book_url: str) -> Optional[str]:
    """
    Navega a la página del libro y extrae la URL de descarga /dl/XXXX.
    Usa la sesión real de SER.
    """
    import re
    html = get_page(book_url)
    if not html:
        return None

    # Buscar enlace /dl/
    match = re.search(r'href="(/dl/[A-Za-z0-9]+)"', html)
    if match:
        return f"https://z-lib.fm{match.group(1)}"

    return None


def search_openlibrary(query: str, min_year: int = 2020, limit: int = 5) -> List[Dict]:
    """
    Busca libros en Open Library (sin autenticación, siempre funciona).
    Fallback cuando z-library no responde.
    """
    import requests as std_requests, re
    try:
        r = std_requests.get(
            "https://openlibrary.org/search.json",
            params={"q": query, "sort": "new", "limit": limit,
                    "fields": "title,author_name,first_publish_year,key,ia"},
            headers={"User-Agent": "EIDOS/1.0"},
            timeout=10
        )
        books = []
        for doc in r.json().get("docs", []):
            year = doc.get("first_publish_year", 0)
            if year >= min_year:
                ia = doc.get("ia", [""])[0]
                books.append({
                    "title":  doc.get("title", "?")[:80],
                    "author": ", ".join(doc.get("author_name", ["?"])[:2])[:50],
                    "year":   year,
                    "url":    f"https://openlibrary.org{doc.get('key','')}",
                    "free":   f"https://archive.org/details/{ia}" if ia else None
                })
        return books
    except Exception as e:
        log.error(f"search_openlibrary: {e}")
        return []


def find_best_books(topic: str, min_year: int = 2020) -> List[Dict]:
    """
    EIDOS busca los mejores libros sobre un tema.
    Intenta z-library primero (sesión real de SER), luego Open Library.
    Ordena por año descendente.
    """
    log.info(f"Buscando libros sobre: {topic}")

    # Intentar z-library
    books = search_zlibrary(topic)
    source = "z-library"

    # Si falla o hay pocos resultados, usar Open Library
    if len(books) < 3:
        ol_books = search_openlibrary(topic, min_year)
        books.extend(ol_books)
        source = "open-library" if not books else "mixto"

    # Ordenar por año descendente
    books.sort(key=lambda b: -int(str(b.get("year","0")).strip() or 0)
               if str(b.get("year","0")).strip().isdigit() else 0)

    log.info(f"Encontrados {len(books)} libros sobre '{topic}' (fuente: {source})")

    # Guardar en brain
    import sqlite3, uuid, time
    if books:
        try:
            conn = get_conn(BRAIN_DB)
            for b in books[:5]:
                concept = f"book:found:{topic.replace(' ','_')[:20]}:{b.get('title','').replace(' ','_')[:20]}"
                definition = f"Libro sobre '{topic}': '{b['title']}' de {b.get('author','?')} ({b.get('year','?')}). URL: {b.get('url','')}"
                conn.execute("INSERT OR REPLACE INTO knowledge_nodes (id,concept,definition,category,confidence,source,last_used,agent_id,character) VALUES (?,?,?,?,?,'firefox_session',?,  'eidos','EIDOS')",
                    (str(uuid.uuid4()), concept, definition, f"books:{topic[:20]}", 0.8, time.time()))
            conn.commit(); conn.close()
        except Exception as e:
            log.debug(f"brain save: {e}")

    return books


def get_playwright_with_system_firefox():
    """
    Lanza Playwright usando el Firefox ESR del SISTEMA de SER.
    compatibility.ini ya está actualizado → Firefox ESR 140 acepta el perfil.
    Esta es la función PERMANENTE para usar el Firefox real de SER.
    """
    profile = _find_firefox_profile()
    if not profile:
        return None, None
    return str(profile), FIREFOX_BIN
