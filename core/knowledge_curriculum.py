"""
core/knowledge_curriculum.py — Curriculums estructurados para EIDOS.

EIDOS aprende estos temas de forma sistemática, en orden, con pruebas prácticas.
No es conocimiento teórico: cada concepto viene con un ejercicio ejecutable.

Temas incluidos:
  - SQL Injection (completo: Kali + devtools)
  - Browser Flags / Experimental Features
  - Lenguajes de programación (Python, Bash, Rust, Go, JS, SQL)
  - Redes y pentesting
"""
import subprocess, os, logging, json, time, uuid, sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional

from core.paths import EIDOS_HOME as DEFAULT_EIDOS_HOME, SANDBOX_ROOT

log = logging.getLogger("curriculum")

BRAIN_DB   = DEFAULT_EIDOS_HOME / "evolution_brain.db"
EIDOS_HOME = DEFAULT_EIDOS_HOME
SANDBOX    = SANDBOX_ROOT / "experiments"


# ── Utilidades brain ──────────────────────────────────────────────────────────

def _brain_save(concept: str, definition: str, category: str, confidence: float = 0.85):
    try:
        conn = get_conn(BRAIN_DB)
        conn.execute("""
            INSERT OR REPLACE INTO knowledge_nodes
            (id, concept, definition, category, confidence, source, last_used, agent_id, character)
            VALUES (?, ?, ?, ?, ?, 'curriculum', ?, 'eidos', 'EIDOS')
        """, (str(uuid.uuid4()), concept, definition, category, confidence, time.time()))
        conn.commit()
        pass  # S109: get_conn no necesita close()
    except Exception as e:
        log.error(f"brain_save error: {e}")


def _brain_check(concept_prefix: str) -> bool:
    """¿Ya aprendió este concepto?"""
    try:
        conn = get_conn(BRAIN_DB)
        row = conn.execute(
            "SELECT id FROM knowledge_nodes WHERE concept LIKE ? LIMIT 1",
            (f"{concept_prefix}%",)
        ).fetchone()
        pass  # S109: get_conn no necesita close()
        return row is not None
    except Exception:
        return False


# ── SQLi Curriculum ──────────────────────────────────────────────────────────

SQLI_CURRICULUM = [
    {
        "id": "sqli:01:fundamentos",
        "title": "SQL Injection — ¿Qué es y por qué funciona?",
        "theory": """
SQL Injection ocurre cuando datos del usuario se insertan directamente en una query SQL sin sanear.
Ejemplo vulnerable: SELECT * FROM users WHERE user='$input'
Si input = "' OR '1'='1" → la query devuelve todos los usuarios.
Funciona porque el motor SQL no distingue entre código y datos.
Tipos principales:
  - Classic/In-band: el resultado viene en la respuesta HTTP
  - Blind: no hay output visible, se infiere por comportamiento (True/False, tiempo)
  - Out-of-band: datos exfiltrados por canal distinto (DNS, HTTP)
""",
        "tools_kali": ["sqlmap", "sqlninja", "havij", "bbqsql"],
        "exercise": "sqlmap --version && echo 'SQLi tools ready'",
        "category": "security:sqli"
    },
    {
        "id": "sqli:02:manual_testing",
        "title": "SQLi Manual — Cómo detectar con devtools",
        "theory": """
Detección manual con browser devtools:
1. Abrir DevTools (F12) → Network tab
2. Buscar requests con parámetros (GET/POST)
3. Probar payloads básicos en cada parámetro:
   '  →  error de sintaxis SQL (vulnerabilidad clásica)
   1 AND 1=1  →  mismo resultado (verdadero)
   1 AND 1=2  →  resultado vacío (falso)
4. En Application tab → ver cookies, storage para tokens
5. En Console: fetch('/api/users?id=1 OR 1=1') para probar directamente
Herramienta: Burp Suite Community Edition (man-in-the-middle proxy)
""",
        "tools_kali": ["burpsuite", "owasp-zap", "nikto"],
        "exercise": "which burpsuite || apt list --installed 2>/dev/null | grep -i burp",
        "category": "security:sqli"
    },
    {
        "id": "sqli:03:sqlmap_basico",
        "title": "SQLmap — Automatización de SQLi",
        "theory": """
sqlmap es la herramienta estándar para detectar y explotar SQLi automáticamente.
Uso básico:
  sqlmap -u "http://target.com/page?id=1" --dbs
  sqlmap -u "URL" -D database_name --tables
  sqlmap -u "URL" -D db -T tabla --dump
  sqlmap -r request.txt  (desde archivo Burp)
  sqlmap --forms -u URL  (detecta forms automáticamente)
Flags importantes:
  --level 1-5      → profundidad del análisis
  --risk 1-3       → agresividad (3 = destructivo, cuidado)
  --tamper=...     → bypass de WAF
  --batch          → no preguntar, usar defaults
  --tor            → usar Tor para anonimato
""",
        "tools_kali": ["sqlmap"],
        "exercise": "sqlmap --help | head -40",
        "category": "security:sqli"
    },
    {
        "id": "sqli:04:blind_sqli",
        "title": "Blind SQLi — Inferir datos sin output visible",
        "theory": """
Cuando la app no muestra errores ni resultados, usamos Blind SQLi:

Boolean-based Blind:
  ' AND (SELECT SUBSTRING(password,1,1) FROM users WHERE username='admin')='a' --
  → Probar cada carácter hasta encontrar el correcto

Time-based Blind:
  ' AND SLEEP(5) --         (MySQL)
  ' AND pg_sleep(5) --      (PostgreSQL)
  '; WAITFOR DELAY '0:0:5'  (MSSQL)
  → Si tarda 5s, hay vulnerabilidad

Herramientas: sqlmap con --technique=B (boolean) o --technique=T (time)
Con devtools: medir tiempo de respuesta en Network tab
""",
        "tools_kali": ["sqlmap", "python3"],
        "exercise": "python3 -c \"import time; print('Time-based: medir con time.time()')\"",
        "category": "security:sqli"
    },
    {
        "id": "sqli:05:payloads_avanzados",
        "title": "SQLi Payloads — Bypass WAF y filtros",
        "theory": """
Cuando hay filtros/WAF, usar ofuscación:

Mayúsculas: SeLeCt * FrOm users
Comentarios: SEL/**/ECT * FR/**/OM users
URL encoding: %27 = ' , %20 = espacio
Double encoding: %2527 = %27 = '
NULL bytes: %00 (a veces ignora el resto)
Comentarios en línea:
  ' UNION SELECT 1,2,3--
  ' UNION SELECT 1,2,3#
  ' UNION SELECT 1,2,3/*

sqlmap tamper scripts:
  --tamper=space2comment   (espacios → comentarios)
  --tamper=charencode      (encode carácteres)
  --tamper=randomcase      (mayúsculas random)
  --tamper=between         (NOT BETWEEN en vez de =)
""",
        "tools_kali": ["sqlmap"],
        "exercise": "ls /usr/share/sqlmap/tamper/ 2>/dev/null | head -20 || find / -name 'tamper' -type d 2>/dev/null | head -3",
        "category": "security:sqli"
    },
    {
        "id": "sqli:06:lab_dvwa",
        "title": "SQLi Lab — DVWA en localhost para practicar",
        "theory": """
DVWA (Damn Vulnerable Web App) es el estándar para practicar SQLi de forma legal.
Instalación rápida con Docker:
  docker run -d -p 80:80 vulnerables/web-dvwa

Niveles de dificultad:
  Low    → sin filtros, vulnerabilidad obvia
  Medium → algún escape, bypass simple
  High   → filtros más serios
  Impossible → código seguro (para comparar)

Acceso: http://localhost/dvwa (admin/password)
Login bypass clásico: admin' -- (en campo usuario)

Otras opciones:
  - WebGoat (OWASP): docker run -p 8080:8080 webgoat/goat-and-wolf
  - HackTheBox / TryHackMe (online, gratis)
  - bWAPP: docker run -d -p 8888:80 raesene/bwapp
""",
        "tools_kali": ["docker"],
        "exercise": "docker ps -a 2>/dev/null | grep -E '(dvwa|webgoat|bwapp)' || echo 'Sin lab activo — usar: docker run -d -p 8080:80 vulnerables/web-dvwa'",
        "category": "security:sqli"
    },
]


# ── Browser Flags Curriculum ──────────────────────────────────────────────────

BROWSER_FLAGS_CURRICULUM = [
    {
        "id": "flags:01:que_son",
        "title": "Browser Flags — ¿Qué son y cómo acceder?",
        "theory": """
Los flags/funciones experimentales son features en desarrollo que el usuario puede activar manualmente.
Acceso según browser:
  Chrome/Chromium: chrome://flags
  Firefox:         about:config  (más granular que flags)
                   about:flags   (no existe en Firefox, es about:config)
  Edge:            edge://flags
  Brave:           brave://flags
  Tor:             about:config  (muy limitado por privacidad)
  Opera:           opera://flags
  Vivaldi:         vivaldi://experiments
  LibreWolf:       about:config

Importante: los flags NO son permanentes por defecto.
En Firefox Nightly, sí existen flags explícitos.
En Firefox ESR/Release, todo se configura via about:config.
""",
        "category": "browser:flags"
    },
    {
        "id": "flags:02:firefox_aboutconfig",
        "title": "Firefox about:config — Las 50 claves más importantes",
        "theory": """
Prefs más útiles en about:config de Firefox:

PRIVACIDAD:
  privacy.resistFingerprinting = true     → antihuella
  privacy.trackingprotection.enabled = true
  network.cookie.cookieBehavior = 1       → bloquear third-party
  dom.battery.enabled = false             → no exponer batería
  media.peerconnection.enabled = false    → bloquear WebRTC leak

RENDIMIENTO:
  gfx.webrender.all = true               → GPU rendering
  layers.acceleration.force-enabled = true
  browser.sessionstore.interval = 60000  → menos escritura disco
  content.notify.interval = 100000       → menos repaints

DEVTOOLS:
  devtools.chrome.enabled = true          → Browser Console
  devtools.debugger.remote-enabled = true → debug remoto
  browser.urlbar.trimURLs = false         → ver URL completa
  layout.css.devPixelsPerPx = -1.0        → DPI real

SEGURIDAD:
  security.mixed_content.block_all_mixed_content = true
  dom.security.https_only_mode = true
  network.stricttransportsecurity.preloadlist = true

EXPERIMENTAL (solo Nightly/Beta):
  dom.streams.enabled = true
  javascript.options.wasm = true
  network.http.http3.enabled = true       → HTTP/3 (QUIC)
""",
        "category": "browser:flags"
    },
    {
        "id": "flags:03:chrome_flags",
        "title": "Chrome/Chromium Flags — Los más potentes",
        "theory": """
chrome://flags flags más importantes:

RENDIMIENTO:
  #enable-gpu-rasterization          → GPU para rasterización
  #enable-zero-copy                  → cero copia memoria GPU
  #smooth-scrolling                  → scroll suave
  #enable-parallel-downloading       → descargas paralelas

PRIVACIDAD:
  #privacy-sandbox-ads-apis          → bloquearlo = más privacidad
  #fingerprinting-client-hints-header = Disabled

EXPERIMENTAL:
  #enable-webgpu                     → WebGPU (futuro de gráficos web)
  #enable-webrtc-hide-local-ips      → ocultar IPs locales en WebRTC
  #enable-quic                       → protocolo QUIC (HTTP/3)
  #enable-javascript-harmony         → features JS nuevas

DEVTOOLS:
  #enable-devtools-experiments       → ¡activar esto primero!
    → desbloquea: Network conditions, Performance monitor, Coverage
  #show-performance-metrics-hud      → overlay de FPS/CPU

SEGURIDAD:
  #block-insecure-private-network-requests
  #enable-tls13-early-data

Chromium CLI flags (no via UI):
  chromium --disable-web-security --user-data-dir=/tmp/chrome_dev URL
  chromium --remote-debugging-port=9222  → CDP para automatización
""",
        "category": "browser:flags"
    },
    {
        "id": "flags:04:playwright_flags",
        "title": "Browser Flags en Playwright/Automatización",
        "theory": """
Pasar flags al browser desde Playwright (Python):

from playwright.sync_api import sync_playwright
from core.db import get_conn

with sync_playwright() as p:
    browser = p.chromium.launch(
        args=[
            "--disable-blink-features=AutomationControlled",  # ocultar bot
            "--disable-web-security",                          # CORS off (dev)
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--enable-gpu",
            "--remote-debugging-port=9222"
        ]
    )

Para Firefox con about:config via Playwright:
    browser = p.firefox.launch(
        firefox_user_prefs={
            "privacy.resistFingerprinting": True,
            "network.http.http3.enabled": True,
            "dom.webdriver.enabled": False,  # ocultar webdriver
        }
    )

Detectar si el sitio sabe que somos un bot:
  Verificar: navigator.webdriver == false
  Tool: https://bot.sannysoft.com (test antibot)
""",
        "category": "browser:flags"
    },
    {
        "id": "flags:05:devtools_avanzado",
        "title": "DevTools — Features avanzadas que EIDOS debe dominar",
        "theory": """
DevTools va mucho más allá de ver HTML:

NETWORK:
  - Throttling: simular 3G/4G
  - Copy as cURL/fetch: reproducir cualquier request
  - Override initiator: ver exactamente qué llamó a qué
  - HAR export: grabar toda la sesión de red

PERFORMANCE:
  - Performance tab: grabar, ver flame graph
  - Memory: heap snapshots, detectar memory leaks
  - Coverage: qué JS/CSS se usa realmente
  - Layers: ver layers de composición GPU

SOURCES:
  - Local overrides: modificar código del sitio persistentemente
  - Snippets: scripts reutilizables (como macros)
  - Workspaces: editar archivos locales desde devtools

CONSOLE:
  $0       → elemento seleccionado en inspector
  $$('css') → querySelectorAll
  copy(object) → copiar al portapapeles
  monitor(fn)  → interceptar llamadas a función
  queryObjects(Constructor) → ver todos los objetos del tipo

APPLICATION:
  - Service Workers: ver/debuggear PWAs
  - IndexedDB / localStorage / cookies: leer y modificar
  - Web SQL (legacy): ver bases de datos

SECURITY:
  - Ver certificados TLS
  - Mixed content warnings
  - Certificate transparency logs
""",
        "category": "browser:flags"
    },
]


# ── Lenguajes de Programación Curriculum ─────────────────────────────────────

LANGUAGES_CURRICULUM = [
    {
        "id": "lang:python:advanced",
        "title": "Python Avanzado — Lo que EIDOS ya usa pero debe dominar",
        "theory": """
Python es el lenguaje principal de EIDOS. Áreas a profundizar:

ASYNCIO:
  import asyncio
  async def main(): await asyncio.gather(task1(), task2())
  asyncio.run(main())
  → Útil para: requests paralelas, Playwright, websockets

GENERADORES:
  def stream(): yield data  → memoria infinita sin cargar todo
  (x for x in range(1M))  → lazy evaluation

DECORADORES:
  @functools.wraps(fn)    → preservar metadata
  @contextlib.contextmanager → crear context managers custom
  @dataclass               → clases con __init__ auto

TYPE HINTS + RUNTIME:
  from typing import Protocol, TypeVar, Generic
  from pydantic import BaseModel  → validación automática

INTROSPECCIÓN:
  inspect.getsource(fn)   → ver código de cualquier función
  dis.dis(fn)             → bytecode
  ast.parse(code)         → AST del código

SUBPROCESS SEGURO:
  subprocess.run(["cmd", "arg"], capture_output=True, text=True)
  # NUNCA: subprocess.run(f"cmd {user_input}", shell=True) → injection
""",
        "exercise": "python3 -c \"import asyncio,inspect,dis; print('Python async+inspect OK')\"",
        "category": "languages:python"
    },
    {
        "id": "lang:bash:advanced",
        "title": "Bash Avanzado — Scripting real para automatización",
        "theory": """
Bash es la lingua franca del sistema. EIDOS debe dominarlo:

ARRAYS:
  declare -a arr=("a" "b" "c")
  arr+=("d")
  for i in "${arr[@]}"; do echo $i; done

ASOCIATIVOS (dict):
  declare -A map=([key]="value")
  map[new]="data"

PROCESS SUBSTITUTION:
  diff <(ls dir1) <(ls dir2)     → comparar outputs sin archivos temp

HEREDOC:
  cat << 'EOF' > file.sh
  #!/bin/bash
  echo "sin expansión de variables"
  EOF

TRAPS (manejo de errores/señales):
  set -euo pipefail              → fallo inmediato en errores
  trap 'echo ERROR en línea $LINENO' ERR
  trap 'cleanup' EXIT

JOBS Y PARALELO:
  cmd1 & cmd2 & wait            → paralelo básico
  parallel -j4 process {} ::: *.txt  → GNU parallel

REGEX EN BASH:
  if [[ "$str" =~ ^[0-9]+$ ]]; then echo "solo números"; fi
  ${var//pattern/replacement}   → sustitución

IPC:
  mkfifo /tmp/pipe
  producer > /tmp/pipe &
  consumer < /tmp/pipe
""",
        "exercise": "bash -c 'declare -A m=([a]=1); echo ${m[a]}; echo Bash OK'",
        "category": "languages:bash"
    },
    {
        "id": "lang:rust:basics",
        "title": "Rust — Sistemas seguros y WebAssembly",
        "theory": """
Rust es el lenguaje del futuro del sistema. Útil para:
  - Herramientas CLI rápidas
  - WebAssembly (wasm) para browser
  - Código de sistema seguro sin GC

CONCEPTOS ÚNICOS DE RUST:
  Ownership: cada valor tiene un único owner
    let s = String::from("hello");  // s owns it
    let t = s;                      // s moved to t, s inválido

  Borrowing: préstamo de referencia
    let r = &s;   // immutable borrow
    let r = &mut s;  // mutable borrow (solo uno a la vez)

  Lifetimes: garantía de que referencias son válidas

BASICS:
  fn main() {
      let mut x: i32 = 5;
      let s = format!("valor: {x}");
      println!("{s}");
      let v: Vec<i32> = vec![1, 2, 3];
      for i in &v { println!("{i}"); }
  }

COMPILAR Y EJECUTAR:
  rustc main.rs && ./main
  cargo new proyecto && cd proyecto && cargo run
  cargo build --target wasm32-unknown-unknown  → WebAssembly

TOOLS:
  cargo clippy   → linter
  cargo fmt      → formatter
  rustup target add wasm32-unknown-unknown
""",
        "exercise": "rustc --version 2>/dev/null || echo 'Rust no instalado — curl https://sh.rustup.rs | sh'",
        "category": "languages:rust"
    },
    {
        "id": "lang:javascript:devtools",
        "title": "JavaScript Moderno — Para browser automation y devtools",
        "theory": """
JS en el contexto de EIDOS = automatización de browser y devtools.

FETCH API (reemplaza XMLHttpRequest):
  const resp = await fetch('/api/data', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({key: 'value'})
  });
  const data = await resp.json();

INTERCEPTAR REQUESTS (devtools/Playwright):
  // En devtools console:
  const origFetch = window.fetch;
  window.fetch = async (...args) => {
    console.log('Fetch:', args[0]);
    return origFetch(...args);
  };

MANIPULAR DOM:
  document.querySelectorAll('a[href]').forEach(a => console.log(a.href))
  document.evaluate('//input[@type="password"]', document, null, XPathResult.ANY_TYPE, null)

STORAGE:
  localStorage.setItem('key', JSON.stringify(data))
  JSON.parse(localStorage.getItem('key'))
  document.cookie  // leer cookies

WEBWORKERS (paralelismo en browser):
  const w = new Worker('worker.js');
  w.postMessage({data: 'task'});
  w.onmessage = e => console.log(e.data);

WEBSOCKETS:
  const ws = new WebSocket('ws://localhost:8080');
  ws.onmessage = e => console.log(e.data);
  ws.send(JSON.stringify({action: 'ping'}));
""",
        "exercise": "node --version 2>/dev/null || echo 'Node.js: sudo apt install nodejs'",
        "category": "languages:javascript"
    },
    {
        "id": "lang:sql:mastery",
        "title": "SQL Completo — Desde básico hasta análisis forense",
        "theory": """
SQL que EIDOS necesita dominar (más allá de SELECT básico):

WINDOW FUNCTIONS:
  SELECT name, salary,
    RANK() OVER (PARTITION BY dept ORDER BY salary DESC) as rank,
    LAG(salary) OVER (ORDER BY hire_date) as prev_salary
  FROM employees;

CTEs (Common Table Expressions):
  WITH ranked AS (
    SELECT *, ROW_NUMBER() OVER (ORDER BY created_at) as n FROM logs
  )
  SELECT * FROM ranked WHERE n BETWEEN 100 AND 200;

JSON EN SQL (SQLite/PostgreSQL):
  SELECT json_extract(data, '$.user.name') FROM events;
  SELECT * FROM logs WHERE json_data->>'type' = 'error';

ANÁLISIS FORENSE (útil para EIDOS leyendo sus propias DBs):
  SELECT concept, COUNT(*) as freq
  FROM knowledge_nodes
  GROUP BY category
  ORDER BY freq DESC;

  SELECT * FROM knowledge_nodes
  WHERE created_at > datetime('now', '-1 day')
  ORDER BY confidence DESC;

SQLITE ESPECÍFICO:
  .schema tabla     → ver estructura
  .export csv file  → exportar
  PRAGMA table_info(tabla);
  PRAGMA foreign_keys = ON;
  VACUUM;           → compactar DB
  ANALYZE;          → estadísticas para optimizer
""",
        "exercise": "sqlite3 ~/.eidos/evolution_brain.db \"SELECT category, COUNT(*) FROM knowledge_nodes GROUP BY category ORDER BY COUNT(*) DESC LIMIT 10;\"",
        "category": "languages:sql"
    },
    {
        "id": "lang:go:basics",
        "title": "Go — Concurrencia y herramientas de red",
        "theory": """
Go es ideal para: servidores HTTP, herramientas de red, CLIs rápidas.
Muchas herramientas de seguridad están en Go (nmap, gobuster, etc.)

BASICS:
  package main
  import ("fmt"; "net/http")

  func main() {
      go func() { fmt.Println("goroutine!") }()  // goroutine
      ch := make(chan int, 1)
      ch <- 42
      fmt.Println(<-ch)
  }

HTTP SERVER (10 líneas):
  http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
      fmt.Fprintf(w, "Hello from Go!")
  })
  http.ListenAndServe(":8080", nil)

CONCURRENCIA:
  var wg sync.WaitGroup
  for i := 0; i < 10; i++ {
      wg.Add(1)
      go func(n int) { defer wg.Done(); process(n) }(i)
  }
  wg.Wait()

INSTALAR:
  sudo apt install golang-go
  go version

HERRAMIENTAS DE SEGURIDAD EN GO:
  gobuster, nuclei, subfinder, amass, ffuf → todas en Go
  go install github.com/OJ/gobuster/v3@latest
""",
        "exercise": "go version 2>/dev/null || echo 'Go: sudo apt install golang-go'",
        "category": "languages:go"
    },
]


# ── Función principal: cargar curriculum en brain ─────────────────────────────

def load_curriculum_to_brain(curriculum: List[Dict], force: bool = False) -> Dict[str, Any]:
    """
    Carga un curriculum completo en el brain de EIDOS.
    Si force=False, salta los conceptos ya conocidos.
    """
    saved = 0
    skipped = 0

    for item in curriculum:
        cid = item["id"]
        cat = item.get("category", "curriculum")

        if not force and _brain_check(cid):
            skipped += 1
            continue

        theory = item.get("theory", "").strip()
        title  = item.get("title", cid)

        _brain_save(cid, f"[{title}]\n{theory}", cat, 0.9)
        saved += 1

        # Si hay tools_kali, guardar también
        for tool in item.get("tools_kali", []):
            tool_key = f"tool:{tool}:kali"
            if not _brain_check(tool_key):
                _brain_save(tool_key, f"Herramienta Kali: {tool}. Usado en: {title}", cat, 0.7)

        # Si hay ejercicio, ejecutarlo y guardar resultado
        exercise = item.get("exercise")
        if exercise:
            try:
                r = subprocess.run(
                    exercise, shell=True,
                    capture_output=True, text=True, timeout=10
                )
                output = r.stdout.strip() or r.stderr.strip()
                if output:
                    _brain_save(
                        f"{cid}:exercise_result",
                        f"Resultado del ejercicio ({cid}):\n{output[:500]}",
                        cat, 0.8
                    )
            except Exception as ex:
                log.debug(f"Exercise error {cid}: {ex}")

        time.sleep(0.1)

    log.info(f"Curriculum cargado: {saved} nuevos, {skipped} ya conocidos")
    return {"saved": saved, "skipped": skipped, "total": len(curriculum)}


def load_all_curriculums() -> Dict[str, Any]:
    """Carga todos los curriculums en el brain."""
    results = {}
    results["sqli"]     = load_curriculum_to_brain(SQLI_CURRICULUM)
    results["flags"]    = load_curriculum_to_brain(BROWSER_FLAGS_CURRICULUM)
    results["langs"]    = load_curriculum_to_brain(LANGUAGES_CURRICULUM)

    total_saved = sum(r["saved"] for r in results.values())
    log.info(f"Total curriculums cargados: {total_saved} nuevos conceptos en brain")
    return {"ok": True, "results": results, "total_saved": total_saved}


def get_next_lesson(category_prefix: str = None) -> Optional[Dict]:
    """
    Devuelve la próxima lección que EIDOS no ha aprendido.
    Útil para que Colony muestre el progreso.
    """
    all_items = SQLI_CURRICULUM + BROWSER_FLAGS_CURRICULUM + LANGUAGES_CURRICULUM

    if category_prefix:
        all_items = [i for i in all_items if i["id"].startswith(category_prefix)]

    for item in all_items:
        if not _brain_check(item["id"]):
            return item
    return None  # Todo aprendido


def get_curriculum_progress() -> Dict[str, Any]:
    """Resumen del progreso en cada curriculum."""
    curricula = {
        "sqli":  SQLI_CURRICULUM,
        "flags": BROWSER_FLAGS_CURRICULUM,
        "langs": LANGUAGES_CURRICULUM,
    }
    progress = {}
    for name, items in curricula.items():
        known = sum(1 for i in items if _brain_check(i["id"]))
        progress[name] = {"known": known, "total": len(items), "pct": int(known/len(items)*100)}
    return progress
