"""
core/eidos_knowledge_bootstrap.py — Base de conocimiento de EIDOS

Indexa en brain.db + ChromaDB:
  - Man pages de Kali Linux (todas las herramientas del sistema)
  - Python stdlib completo (todos los módulos)
  - Librerías clave (requests, numpy, pandas, etc.)
  - APIs gratuitas sin key: Wikipedia, PyPI, DevDocs, npm
  - Lenguajes de programación: Python, Bash, JS, Go, Rust, SQL, C

APIs gratuitas indexadas (sin API key):
  - Wikipedia REST API       — sin key, sin límite razonable
  - DevDocs.io               — documentación de todos los lenguajes
  - PyPI JSON API            — info de cualquier paquete Python
  - npm registry API         — info de cualquier paquete JS
  - pkg.go.dev               — documentación de Go
  - MDN Web Docs             — JavaScript, CSS, HTML
  - OpenLibrary              — libros técnicos gratuitos

Uso:
    python3 core/eidos_knowledge_bootstrap.py
    # o desde daemon:
    from core.eidos_knowledge_bootstrap import run_bootstrap
    run_bootstrap()
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import subprocess
import time
import urllib.request
import urllib.parse
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from core.db import get_conn

log = logging.getLogger("eidos.bootstrap")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
HEADERS  = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Accept": "application/json",
}


# ── Indexado básico ──────────────────────────────────────────────────────────

def _index(concept: str, definition: str, source: str,
           confidence: float = 0.90, category: str = "general") -> bool:
    """Guarda un nodo de conocimiento en brain.db."""
    if not definition or len(definition) < 20:
        return False
    try:
        node_id = hashlib.md5(f"boot:{source}".encode()).hexdigest()[:16]
        now     = time.time()
        conn    = get_conn(BRAIN_DB, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_nodes "
            "(id,concept,definition,category,source,confidence,created_at,last_used,usage_count) "
            "VALUES (?,?,?,?,?,?,?,?,1)",
            (node_id, concept[:120], definition[:2000], category,
             f"bootstrap:{source[:120]}", confidence, now, now),
        )
        changed = conn.total_changes
        conn.commit()
        pass  # S109: get_conn no necesita close()
        return changed > 0
    except Exception as e:
        log.debug("_index: %s", e)
        return False


def _fetch(url: str, timeout: int = 20) -> Optional[str]:
    """HTTP GET con User-Agent real."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(524288).decode("utf-8", errors="ignore")
    except Exception as e:
        log.debug("_fetch %s: %s", url[:60], e)
        return None


def _fetch_json(url: str, timeout: int = 20) -> Optional[dict]:
    """HTTP GET → JSON."""
    raw = _fetch(url, timeout)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


# ── 1. Man pages del sistema ─────────────────────────────────────────────────

def bootstrap_man_pages(max_tools: int = 300) -> int:
    """Indexa man pages de las herramientas instaladas en el sistema."""
    log.info("Indexando man pages del sistema (máx %d)...", max_tools)
    indexed = 0

    try:
        # Obtener lista de todos los man pages disponibles
        r = subprocess.run(
            ["bash", "-c",
             "man -k . 2>/dev/null | awk '{print $1}' | sort -u"],
            capture_output=True, text=True, timeout=30,
        )
        all_cmds = [line.strip() for line in r.stdout.splitlines()
                    if line.strip() and len(line.strip()) >= 2]
    except Exception:
        all_cmds = []

    # Priorizar herramientas de Kali y más usadas
    priority = [
        "nmap", "metasploit", "burpsuite", "sqlmap", "hydra", "john",
        "aircrack-ng", "wireshark", "tcpdump", "netcat", "nc", "socat",
        "curl", "wget", "git", "docker", "ssh", "scp", "rsync", "tar",
        "grep", "awk", "sed", "find", "xargs", "sort", "uniq", "cut",
        "python3", "python", "pip", "pip3", "node", "npm", "bash", "zsh",
        "systemctl", "journalctl", "ps", "top", "htop", "lsof", "netstat",
        "ss", "ip", "ifconfig", "iptables", "ufw", "dig", "nslookup",
        "openssl", "gpg", "hashcat", "hashid", "crunch", "wordlists",
        "msfconsole", "msfvenom", "gobuster", "dirb", "nikto", "wfuzz",
        "ffuf", "sublist3r", "theharvester", "maltego", "recon-ng",
        "enum4linux", "smbclient", "rpcclient", "ldapsearch", "crackmapexec",
        "evil-winrm", "impacket", "responder", "bettercap", "ettercap",
        "mitmproxy", "proxychains", "tor", "ngrok", "chisel", "ligolo",
        "vim", "nano", "tmux", "screen", "strace", "ltrace", "gdb", "radare2",
        "binwalk", "strings", "file", "hexdump", "xxd", "objdump", "nm",
        "volatility", "autopsy", "foremost", "photorec", "testdisk",
        "p7zip", "zip", "unzip", "gzip", "bzip2", "xz", "pv", "dd",
    ]

    # Primero los prioritarios, luego el resto
    ordered = priority + [c for c in all_cmds if c not in priority]

    for cmd in ordered[:max_tools]:
        if indexed >= max_tools:
            break
        try:
            r = subprocess.run(
                ["man", "-P", "cat", cmd],
                capture_output=True, text=True, timeout=8,
                env={"PATH": "/usr/bin:/bin", "MANPAGER": "cat",
                     "MANWIDTH": "100", "HOME": str(Path.home())},
            )
            if r.returncode != 0 or len(r.stdout) < 100:
                # Intentar --help como fallback
                rh = subprocess.run(
                    [cmd, "--help"],
                    capture_output=True, text=True, timeout=3,
                )
                text = (rh.stdout + rh.stderr).strip()
                if len(text) < 80:
                    continue
                text = text[:2000]
            else:
                text = r.stdout[:2000]

            # Limpiar texto
            text = re.sub(r'\x1b\[[0-9;]*m', '', text)  # escape codes
            text = re.sub(r'\s+', ' ', text).strip()

            if _index(f"comando:{cmd}", text, f"man:{cmd}"):
                indexed += 1
                if indexed % 50 == 0:
                    log.info("Man pages: %d/%d indexadas", indexed, max_tools)
        except Exception:
            continue

    log.info("Man pages: %d indexadas", indexed)
    return indexed


# ── 2. Python stdlib ─────────────────────────────────────────────────────────

# Todos los módulos de la biblioteca estándar de Python
_PYTHON_STDLIB = [
    "os", "sys", "pathlib", "io", "re", "json", "csv", "xml", "html",
    "http", "urllib", "socket", "ssl", "email", "smtplib", "ftplib",
    "datetime", "time", "calendar", "collections", "itertools", "functools",
    "operator", "string", "textwrap", "struct", "codecs", "unicodedata",
    "hashlib", "hmac", "secrets", "base64", "binascii", "uuid",
    "math", "cmath", "decimal", "fractions", "random", "statistics",
    "threading", "multiprocessing", "concurrent", "asyncio", "queue",
    "subprocess", "shutil", "tempfile", "glob", "fnmatch", "fileinput",
    "sqlite3", "pickle", "shelve", "dbm", "zipfile", "tarfile", "gzip",
    "logging", "warnings", "traceback", "inspect", "dis", "ast", "token",
    "argparse", "optparse", "getopt", "configparser", "toml",
    "unittest", "doctest", "pdb", "profile", "timeit", "cProfile",
    "typing", "dataclasses", "enum", "abc", "contextlib", "copy",
    "pprint", "reprlib", "numbers", "array", "bisect", "heapq",
    "weakref", "gc", "ctypes", "platform", "signal", "errno",
]

def bootstrap_python_stdlib(modules: Optional[List[str]] = None) -> int:
    """Indexa la documentación de la biblioteca estándar de Python."""
    log.info("Indexando Python stdlib...")
    if modules is None:
        modules = _PYTHON_STDLIB

    indexed = 0
    for mod in modules:
        try:
            r = subprocess.run(
                ["python3", "-c", f"import {mod}; help({mod})"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0 or len(r.stdout) < 50:
                continue
            text = r.stdout[:3000]
            text = re.sub(r'\s+', ' ', text).strip()
            if _index(f"python:{mod}", text, f"python_help:{mod}"):
                indexed += 1
        except Exception:
            continue

    log.info("Python stdlib: %d módulos indexados", indexed)
    return indexed


# ── 3. PyPI — librerías clave ────────────────────────────────────────────────

_KEY_LIBRARIES = [
    # Web / HTTP
    "requests", "httpx", "aiohttp", "flask", "fastapi", "django",
    "uvicorn", "starlette", "pydantic", "marshmallow",
    # Datos / ML
    "numpy", "pandas", "scipy", "matplotlib", "seaborn", "sklearn",
    "scikit-learn", "tensorflow", "torch", "transformers", "pillow",
    # Bases de datos
    "sqlalchemy", "pymongo", "redis", "psycopg2", "motor", "tortoise-orm",
    # Seguridad / Crypto
    "cryptography", "paramiko", "scapy", "impacket", "pycparser",
    "pyopenssl", "pyjwt", "bcrypt", "passlib",
    # Automatización
    "selenium", "playwright", "pyautogui", "keyboard", "mouse",
    "schedule", "apscheduler", "celery", "dramatiq",
    # CLI / Terminal
    "click", "typer", "rich", "colorama", "tqdm", "prompt-toolkit",
    # NLP / LLM
    "openai", "anthropic", "langchain", "sentence-transformers",
    "chromadb", "faiss-cpu", "tiktoken", "nltk", "spacy",
    # Otros comunes
    "pyyaml", "toml", "dotenv", "python-dotenv", "loguru",
    "pytest", "mypy", "black", "ruff", "poetry",
    "beautifulsoup4", "lxml", "html5lib", "feedparser",
    "parameterized", "mock", "faker", "hypothesis",
]

def bootstrap_pypi_libraries(packages: Optional[List[str]] = None) -> int:
    """Indexa descripción y metadatos de librerías PyPI clave."""
    log.info("Indexando librerías PyPI...")
    if packages is None:
        packages = _KEY_LIBRARIES

    indexed = 0
    for pkg in packages:
        try:
            data = _fetch_json(f"https://pypi.org/pypi/{pkg}/json")
            if not data:
                continue
            info = data.get("info", {})
            name    = info.get("name", pkg)
            summary = info.get("summary", "")
            desc    = info.get("description", "")[:1500]
            version = info.get("version", "")
            home    = info.get("home_page", "")

            text = (f"Librería Python: {name} v{version}\n"
                    f"Resumen: {summary}\n"
                    f"Web: {home}\n"
                    f"Descripción: {desc}")
            text = re.sub(r'\s+', ' ', text).strip()

            if _index(f"pypi:{name}", text, f"pypi:{name}:{version}"):
                indexed += 1
        except Exception:
            continue
        time.sleep(0.1)  # respetar rate limit

    log.info("PyPI: %d librerías indexadas", indexed)
    return indexed


# ── 4. Wikipedia — lenguajes de programación y conceptos ────────────────────

_PROGRAMMING_TOPICS = [
    # Lenguajes
    "Python (programming language)",
    "JavaScript", "TypeScript",
    "Bash (Unix shell)", "Shell script",
    "Go (programming language)",
    "Rust (programming language)",
    "C (programming language)", "C++",
    "Java (programming language)", "Kotlin (programming language)",
    "SQL", "PostgreSQL", "SQLite",
    "HTML", "CSS", "WebAssembly",
    "Assembly language", "x86 assembly language",
    "Ruby (programming language)", "PHP",
    # Conceptos fundamentales
    "Algorithm", "Data structure", "Big O notation",
    "Object-oriented programming", "Functional programming",
    "Design pattern (computer science)", "SOLID",
    "REST API", "GraphQL", "WebSocket",
    "Cryptography", "Public-key cryptography", "Hash function",
    "Computer network", "OSI model", "TCP/IP",
    "Linux kernel", "Unix philosophy",
    "Git", "Version control",
    "Docker (software)", "Kubernetes",
    "Machine learning", "Neural network", "Large language model",
    # Seguridad
    "Penetration testing", "OWASP Top 10",
    "SQL injection", "Cross-site scripting", "Buffer overflow",
    "Metasploit", "Nmap", "Wireshark",
    "Kali Linux", "Parrot OS",
]

def bootstrap_wikipedia(topics: Optional[List[str]] = None) -> int:
    """Indexa artículos de Wikipedia sobre programación y seguridad.
    Fix 15: retry 3 veces con delay 2s si falla por rate limiting.
    """
    log.info("Indexando Wikipedia...")
    if topics is None:
        topics = _PROGRAMMING_TOPICS

    indexed = 0
    failed: List[str] = []
    base = "https://en.wikipedia.org/w/api.php"

    def _fetch_topic(topic: str) -> bool:
        params = urllib.parse.urlencode({
            "action": "query",
            "titles": topic,
            "prop": "extracts",
            "exintro": "1",
            "explaintext": "1",
            "format": "json",
            "exsentences": "10",
        })
        data = _fetch_json(f"{base}?{params}")
        if not data:
            return False
        pages = data.get("query", {}).get("pages", {})
        for pid, page in pages.items():
            if pid == "-1":
                return False
            extract = page.get("extract", "").strip()
            if len(extract) < 50:
                return False
            title = page.get("title", topic)
            text  = re.sub(r'\s+', ' ', extract).strip()
            return bool(_index(f"wikipedia:{title}", text[:2000],
                               f"wikipedia:{urllib.parse.quote(title)}"))
        return False

    for topic in topics:
        ok = False
        for attempt in range(3):  # Fix 15: hasta 3 intentos
            try:
                ok = _fetch_topic(topic)
                if ok:
                    indexed += 1
                    break
            except Exception:
                pass
            if attempt < 2:
                time.sleep(2)  # Fix 15: delay entre reintentos
        if not ok:
            failed.append(topic)
        time.sleep(0.3)

    if failed:
        log.info("Wikipedia: %d fallaron (rate limit?): %s", len(failed), failed[:5])
    log.info("Wikipedia: %d/%d artículos indexados", indexed, len(topics))
    return indexed


# ── 5. npm — librerías JavaScript clave ─────────────────────────────────────

_KEY_NPM = [
    "express", "fastify", "koa", "hapi",
    "react", "vue", "angular", "svelte", "next", "nuxt",
    "axios", "node-fetch", "got",
    "lodash", "ramda", "underscore",
    "moment", "dayjs", "date-fns",
    "socket.io", "ws", "mqtt",
    "webpack", "vite", "rollup", "esbuild", "parcel",
    "typescript", "ts-node", "tsx",
    "jest", "mocha", "chai", "vitest",
    "eslint", "prettier", "biome",
    "prisma", "sequelize", "typeorm", "mongoose",
    "jsonwebtoken", "passport", "bcrypt",
    "dotenv", "winston", "pino",
    "n8n", "node-red", "bull", "bullmq",
    "puppeteer", "playwright",
    "cheerio", "jsdom", "htmlparser2",
    "inquirer", "commander", "yargs",
    "zod", "joi", "yup",
]

def bootstrap_npm_libraries(packages: Optional[List[str]] = None) -> int:
    """Indexa descripción y metadatos de paquetes npm clave."""
    log.info("Indexando paquetes npm...")
    if packages is None:
        packages = _KEY_NPM

    indexed = 0
    for pkg in packages:
        try:
            data = _fetch_json(f"https://registry.npmjs.org/{pkg}/latest")
            if not data:
                continue
            name    = data.get("name", pkg)
            version = data.get("version", "")
            desc    = data.get("description", "")
            readme  = data.get("readme", "")[:1000]
            home    = data.get("homepage", "")
            kw      = ", ".join(data.get("keywords", [])[:10])

            text = (f"Paquete npm: {name} v{version}\n"
                    f"Descripción: {desc}\n"
                    f"Keywords: {kw}\n"
                    f"Web: {home}\n"
                    f"README: {readme}")
            text = re.sub(r'\s+', ' ', text).strip()

            if _index(f"npm:{name}", text, f"npm:{name}:{version}"):
                indexed += 1
        except Exception:
            continue
        time.sleep(0.15)

    log.info("npm: %d paquetes indexados", indexed)
    return indexed


# ── 6. DevDocs — documentación oficial de lenguajes ─────────────────────────

_DEVDOCS_SLUGS = [
    "bash", "python~3.12", "javascript", "typescript",
    "go", "rust", "c", "cpp",
    "css", "html", "dom",
    "node", "deno",
    "docker", "git", "nginx",
    "postgresql", "mysql", "sqlite",
    "http", "jinja~2.11",
]

def bootstrap_devdocs(slugs: Optional[List[str]] = None) -> int:
    """Indexa el índice de documentación de DevDocs.io para cada lenguaje."""
    log.info("Indexando DevDocs.io...")
    if slugs is None:
        slugs = _DEVDOCS_SLUGS

    indexed = 0
    for slug in slugs:
        try:
            # El índice de entradas de cada doc
            data = _fetch_json(f"https://devdocs.io/docs/{slug}/index.json")
            if not data:
                continue
            entries = data.get("entries", [])[:100]  # primeras 100 entradas
            lang    = slug.split("~")[0]

            for entry in entries:
                name   = entry.get("name", "")
                etype  = entry.get("type", "")
                if not name:
                    continue
                concept = f"{lang}:{name}"
                defn    = (f"[{lang.upper()} — {etype}] {name}\n"
                           f"Documentación en devdocs.io/{slug}/{entry.get('path','')}")
                if _index(concept, defn, f"devdocs:{slug}:{name}",
                          confidence=0.80):
                    indexed += 1
        except Exception:
            continue
        time.sleep(0.3)

    log.info("DevDocs: %d entradas indexadas", indexed)
    return indexed


# ── 7. Kali tools — lista completa + versiones ───────────────────────────────

def bootstrap_kali_tools() -> int:
    """Indexa herramientas instaladas en Kali Linux con su versión y ayuda."""
    log.info("Inventariando herramientas de Kali...")
    indexed = 0

    kali_tools = [
        # Reconocimiento
        "nmap", "masscan", "zmap", "rustscan", "arp-scan",
        "theharvester", "recon-ng", "maltego", "shodan",
        "sublist3r", "amass", "dnsx", "massdns",
        # Web
        "nikto", "wfuzz", "ffuf", "gobuster", "dirb", "dirbuster",
        "sqlmap", "xsstrike", "commix", "wafw00f", "whatweb",
        "burpsuite", "zaproxy",
        # Explotación
        "metasploit-framework", "msfconsole", "msfvenom",
        "exploitdb", "searchsploit",
        # Post-explotación
        "empire", "covenant", "crackmapexec", "impacket",
        "evil-winrm", "pwncat", "chisel", "ligolo-ng",
        # Contraseñas
        "hashcat", "john", "hydra", "medusa", "ncrack",
        "crunch", "cewl", "cupp",
        # Wireless
        "aircrack-ng", "airbase-ng", "airodump-ng", "aireplay-ng",
        "wifite", "kismet", "bettercap",
        # Forense
        "volatility3", "autopsy", "sleuthkit", "binwalk",
        "foremost", "testdisk", "photorec", "bulk-extractor",
        # Sniffing
        "wireshark", "tcpdump", "tshark", "ettercap",
        "mitmproxy", "responder", "p0f",
        # Misc
        "proxychains4", "tor", "onion", "openssl", "gpg",
        "python3", "ruby", "perl", "go", "rustc",
    ]

    for tool in kali_tools:
        # Buscar en sistema
        found_where = []
        for cmd in [f"which {tool} 2>/dev/null",
                    f"dpkg -l {tool} 2>/dev/null | grep '^ii' | head -1",
                    f"{tool} --version 2>&1 | head -1"]:
            try:
                r = subprocess.run(["bash", "-c", cmd],
                                   capture_output=True, text=True, timeout=5)
                out = (r.stdout + r.stderr).strip()
                if out and "not found" not in out.lower() and len(out) > 2:
                    found_where.append(out[:100])
            except Exception:
                continue
        if not found_where:
            continue

        text = (f"Herramienta Kali: {tool}\n"
                f"Estado en sistema: {'; '.join(found_where)}")
        if _index(f"kali:{tool}", text, f"kali_inventory:{tool}"):
            indexed += 1

    log.info("Kali tools: %d herramientas inventariadas", indexed)
    return indexed


# ── 8. APIs gratuitas para EIDOS (configuración) ─────────────────────────────

_FREE_APIS_INFO = """
APIs GRATUITAS disponibles para EIDOS (sin key o key gratuita):

1. Wikipedia REST API — https://en.wikipedia.org/w/api.php
   Sin key. Artículos de cualquier tema en cualquier idioma.

2. DevDocs.io — https://devdocs.io/docs/{lang}/index.json
   Sin key. Documentación oficial de todos los lenguajes.

3. PyPI JSON API — https://pypi.org/pypi/{package}/json
   Sin key. Info de cualquier paquete Python.

4. npm Registry — https://registry.npmjs.org/{package}
   Sin key. Info de cualquier paquete JavaScript.

5. GitHub API — https://api.github.com
   Sin key: 60 req/h. Con token gratis: 5000 req/h.
   Obtener en: github.com/settings/tokens

6. Stack Exchange — https://api.stackexchange.com/2.3/search
   Sin key: 300 req/day. Con key gratis: 10000 req/day.
   Obtener en: stackapps.com

7. OpenLibrary — https://openlibrary.org/api/books?bibkeys=ISBN:...
   Sin key. Metadatos de millones de libros técnicos.

8. DuckDuckGo — https://html.duckduckgo.com/html/?q=...
   Sin key. Motor de búsqueda (ya en uso).

9. pkg.go.dev — https://pkg.go.dev/{package}
   Sin key. Documentación de todos los paquetes Go.

10. crates.io — https://crates.io/api/v1/crates/{name}
    Sin key. Paquetes Rust.

11. Open VSX Registry — https://open-vsx.org/api/{publisher}/{name}
    Sin key. Alternativa libre al marketplace de VSCode.
    Info de cualquier extensión: descripción, versión, readme, repositorio.
    Ejemplo: https://open-vsx.org/api/ms-python/python
    Listar extensiones instaladas: [SHELL: code --list-extensions --show-versions]
    Instalar extensión: [SHELL: code --install-extension {publisher.name}]
    Desinstalar extensión: [SHELL: code --uninstall-extension {publisher.name}]

12. VS Marketplace (público) — https://marketplace.visualstudio.com/items?itemName={publisher.name}
    Sin key. Página pública de cada extensión. EIDOS puede estudiarla con /estudio.
    Ejemplo: /estudio marketplace.visualstudio.com/items?itemName=ms-python.python
    También puede buscar extensiones: /estudio marketplace.visualstudio.com/search?term={query}

13. Kali Linux Tools — https://www.kali.org/tools/{toolname}/
    Sin key. Documentación oficial en inglés de cada herramienta de Kali.
    Ejemplo: /estudio kali.org/tools/nmap — aprende nmap en inglés completo.

Para añadir APIs con key: ~/.eidos/api_keys.json
"""

def bootstrap_system_inventory() -> int:
    """Fix 6: Inventario completo del sistema de SER.
    EIDOS aprende qué programas, repos, servicios y herramientas tiene SER.
    """
    log.info("Inventariando el sistema de SER...")
    indexed = 0
    home = Path.home()

    def _run(cmd: str, timeout: int = 15) -> str:
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True,
                               text=True, timeout=timeout)
            return (r.stdout or "").strip()[:3000]
        except Exception:
            return ""

    # 1. Paquetes instalados con dpkg (Debian/Kali)
    pkgs_raw = _run("dpkg -l | grep '^ii' | awk '{print $2, $3}' | head -200")
    if pkgs_raw:
        if _index("sistema:paquetes_instalados",
                  f"Paquetes Debian/Kali instalados:\n{pkgs_raw}",
                  "system:dpkg", category="system", confidence=0.9):
            indexed += 1

    # 2. Repos git en HOME
    repos_raw = _run(
        "find ~ -maxdepth 3 -name '.git' -type d 2>/dev/null "
        "| head -20 | sed 's|/.git||' "
        "| while read d; do echo \"$d: $(git -C '$d' log --oneline -1 2>/dev/null)\"; done"
    )
    if repos_raw:
        if _index("sistema:repos_git",
                  f"Repositorios git del usuario:\n{repos_raw}",
                  "system:git_repos", category="system", confidence=0.9):
            indexed += 1

    # 3. Servicios systemd del usuario
    svcs_raw = _run("systemctl --user list-units --state=active --no-pager 2>/dev/null | head -30")
    if svcs_raw:
        if _index("sistema:servicios_activos",
                  f"Servicios systemd activos del usuario:\n{svcs_raw}",
                  "system:systemd", category="system", confidence=0.9):
            indexed += 1

    # 4. Binarios en PATH del usuario
    path_bins = _run(
        "echo $PATH | tr ':' '\\n' | while read d; do "
        "[ -d \"$d\" ] && ls \"$d\" 2>/dev/null; done | sort -u | head -150"
    )
    if path_bins:
        if _index("sistema:binarios_path",
                  f"Binarios disponibles en PATH:\n{path_bins}",
                  "system:path_bins", category="system", confidence=0.85):
            indexed += 1

    # 5. Librerías Python instaladas
    pip_list = _run("pip3 list --format=columns 2>/dev/null | head -100")
    if pip_list:
        if _index("sistema:python_packages",
                  f"Paquetes Python instalados:\n{pip_list}",
                  "system:pip3", category="system", confidence=0.9):
            indexed += 1

    # 6. npm global
    npm_list = _run("npm list -g --depth=0 2>/dev/null | head -50")
    if npm_list:
        if _index("sistema:npm_global",
                  f"Paquetes npm globales:\n{npm_list}",
                  "system:npm_global", category="system", confidence=0.9):
            indexed += 1

    # 7. Docker images
    docker_imgs = _run("docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}' 2>/dev/null | head -30")
    if docker_imgs and "REPOSITORY" in docker_imgs:
        if _index("sistema:docker_images",
                  f"Docker images disponibles:\n{docker_imgs}",
                  "system:docker", category="system", confidence=0.9):
            indexed += 1

    # 8. Archivos importantes del HOME
    home_files = _run(f"ls -la ~/ 2>/dev/null | head -40")
    if home_files:
        if _index("sistema:home_directorio",
                  f"Archivos en directorio home:\n{home_files}",
                  "system:home_ls", category="system", confidence=0.8):
            indexed += 1

    # 9. Hardware
    hw = _run("lscpu | head -20; echo '---'; free -h; echo '---'; df -h --total 2>/dev/null | tail -5")
    if hw:
        if _index("sistema:hardware",
                  f"Hardware del sistema:\n{hw}",
                  "system:hardware", category="system", confidence=0.95):
            indexed += 1

    # 10. Modelos Ollama disponibles
    ollama_models = _run("ollama list 2>/dev/null")
    if ollama_models:
        if _index("sistema:ollama_modelos",
                  f"Modelos Ollama disponibles:\n{ollama_models}",
                  "system:ollama", category="system", confidence=0.95):
            indexed += 1

    log.info("System inventory: %d nodos indexados", indexed)
    return indexed


def bootstrap_free_apis_info() -> int:
    """Indexa información sobre APIs gratuitas disponibles."""
    if _index("apis:gratuitas", _FREE_APIS_INFO.strip(),
              "eidos_bootstrap:free_apis", confidence=0.95):
        log.info("APIs gratuitas indexadas")
        return 1
    return 0


def bootstrap_vscode_extensions() -> int:
    """Indexa las extensiones VSCode instaladas y aprende su propósito.
    Usa Open VSX API (gratuita, sin key) para obtener descripción de cada extensión.
    EIDOS puede luego instalar/desinstalar extensiones por sí solo con [SHELL: code ...].
    """
    import subprocess, re

    def _run(cmd):
        try:
            return subprocess.run(cmd, shell=True, capture_output=True,
                                  text=True, timeout=10).stdout.strip()
        except Exception:
            return ""

    # Obtener lista de extensiones instaladas
    ext_raw = _run("code --list-extensions --show-versions 2>/dev/null")
    if not ext_raw:
        log.warning("VSCode no disponible o sin extensiones")
        return 0

    extensions = []
    for line in ext_raw.splitlines():
        line = line.strip()
        if "@" in line:
            parts = line.rsplit("@", 1)
            ext_id = parts[0].strip()
            version = parts[1].strip() if len(parts) > 1 else ""
            extensions.append((ext_id, version))
        elif line:
            extensions.append((line, ""))

    # Indexar lista completa primero
    ext_list_text = (
        "Extensiones VSCode instaladas en el sistema de SER:\n" +
        "\n".join(f"  {e[0]} v{e[1]}" if e[1] else f"  {e[0]}" for e in extensions) +
        "\n\nPara instalar: [SHELL: code --install-extension publisher.name]\n"
        "Para desinstalar: [SHELL: code --uninstall-extension publisher.name]\n"
        "Para actualizar: [SHELL: code --install-extension publisher.name --force]\n"
        "API info de extensión: https://open-vsx.org/api/{publisher}/{name}"
    )
    indexed = 0
    if _index("vscode:extensiones_instaladas", ext_list_text,
              "system:vscode_extensions", category="tool", confidence=0.95):
        indexed += 1
        log.info("VSCode: lista de %d extensiones indexada", len(extensions))

    # Obtener info detallada de cada extensión via Open VSX API
    for ext_id, version in extensions[:20]:  # max 20 para no saturar
        try:
            if "." not in ext_id:
                continue
            publisher, name = ext_id.split(".", 1)
            url = f"https://open-vsx.org/api/{publisher}/{name}"
            data = _fetch_json(url)
            if not data or "error" in data:
                continue
            display = data.get("displayName", ext_id)
            desc    = data.get("description", "")
            readme  = (data.get("files", {}).get("readme", "") or "")[:200]
            repo    = data.get("repository", {})
            if isinstance(repo, dict):
                repo = repo.get("url", "")
            kws     = ", ".join(data.get("keywords", [])[:8])
            ver     = data.get("version", version)

            text = (
                f"VSCode Extension: {display} ({ext_id}) v{ver}\n"
                f"Descripción: {desc}\n"
                f"Keywords: {kws}\n"
                f"Repo: {repo}\n"
                f"Instalar: code --install-extension {ext_id}\n"
                f"Desinstalar: code --uninstall-extension {ext_id}"
            )
            if readme:
                text += f"\n{readme}"
            if _index(f"vscode:{ext_id}", text.strip(),
                      f"open-vsx:{ext_id}:{ver}", category="tool", confidence=0.88):
                indexed += 1
            time.sleep(0.3)
        except Exception:
            continue

    log.info("VSCode extensions: %d nodos indexados (%d extensiones)", indexed, len(extensions))
    return indexed


# ── Ejecución completa ────────────────────────────────────────────────────────

def run_bootstrap(
    man_pages: bool = True,
    python_stdlib: bool = True,
    pypi: bool = True,
    npm: bool = True,
    wikipedia: bool = True,
    devdocs: bool = True,
    kali: bool = True,
    system_inventory: bool = True,
    vscode: bool = True,
    max_man: int = 300,
) -> Dict[str, int]:
    """Ejecuta el bootstrap completo. Devuelve conteo de nodos indexados."""
    results: Dict[str, int] = {}

    log.info("=== EIDOS Knowledge Bootstrap INICIADO ===")
    t0 = time.time()

    # Fix 6: inventario del sistema siempre primero (rápido, sin red)
    if system_inventory:
        results["system_inventory"] = bootstrap_system_inventory()

    # VSCode extensiones instaladas + Open VSX API (rápido, ~30s)
    if vscode:
        results["vscode_extensions"] = bootstrap_vscode_extensions()

    if man_pages:
        results["man_pages"]     = bootstrap_man_pages(max_man)

    if python_stdlib:
        results["python_stdlib"] = bootstrap_python_stdlib()

    if kali:
        results["kali_tools"]    = bootstrap_kali_tools()

    if wikipedia:
        results["wikipedia"]     = bootstrap_wikipedia()

    if pypi:
        results["pypi"]          = bootstrap_pypi_libraries()

    if npm:  # Fix 16: npm ahora sí se ejecuta
        results["npm"]           = bootstrap_npm_libraries()

    if devdocs:
        results["devdocs"]       = bootstrap_devdocs()

    results["free_apis"] = bootstrap_free_apis_info()

    elapsed = time.time() - t0
    total   = sum(results.values())
    log.info("=== Bootstrap completado en %.1fs — %d nodos nuevos ===",
             elapsed, total)
    log.info("Detalle: %s", results)
    return results


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    print("=== EIDOS Knowledge Bootstrap ===")
    print("Indexando: man pages, Python stdlib, PyPI, npm, Wikipedia, DevDocs, Kali tools")
    print("Esto tarda ~5-15 minutos. Puedes usar EIDOS mientras tanto.\n")

    results = run_bootstrap(max_man=300)

    print("\n=== Completado ===")
    total = sum(results.values())
    for k, v in results.items():
        print(f"  {k:20s}: {v:4d} nodos")
    print(f"  {'TOTAL':20s}: {total:4d} nodos nuevos")
