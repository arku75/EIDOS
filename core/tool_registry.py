"""
core/tool_registry.py — Catálogo de herramientas de EIDOS (estilo Anthropic).

Cada herramienta es una función Python anotada con @tool.
El motor ReAct las llama por nombre con argumentos JSON.

Herramientas incluidas:
  bash          — ejecuta comandos shell (con sandbox configurable)
  read_file     — lee archivos del sistema
  write_file    — escribe/crea archivos
  grep          — busca patrones en archivos
  glob          — lista archivos por patrón
  web_search    — búsqueda DuckDuckGo sin API key
  web_fetch     — descarga URL y devuelve texto
  search_brain  — busca en knowledge_nodes (brain de EIDOS)
  ask_colony    — consulta a Colony /api/query
  finish        — herramienta especial: termina el loop

Añadir una herramienta nueva:
    @registry.register
    def mi_herramienta(arg1: str, arg2: int = 5) -> str:
        '''Descripción para el LLM.'''
        return resultado
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import sqlite3
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable
from core.db import get_conn

log = logging.getLogger("eidos.tools")

EIDOS_DIR  = Path(os.environ.get("EIDOS_DIR", str(Path.home() / "EIDOS")))
BRAIN_DB   = Path.home() / ".eidos" / "evolution_brain.db"
COLONY_URL = os.environ.get("EIDOS_COLONY_URL", "http://localhost:7777")
BASH_TIMEOUT = int(os.environ.get("EIDOS_BASH_TIMEOUT", "30"))


class ToolRegistry:
    """Registro centralizado de herramientas ejecutables por el ReAct engine."""

    def __init__(self):
        self._tools: dict[str, dict] = {}
        self._register_defaults()

    # ── API pública ──────────────────────────────────────────────────────────

    def register(self, func: Callable) -> Callable:
        """Decorador para registrar una función como herramienta."""
        name = func.__name__
        doc  = (func.__doc__ or "").strip()
        self._tools[name] = {"fn": func, "doc": doc}
        return func

    def call(self, name: str, args: dict) -> str:
        """Ejecuta la herramienta name con los args dados."""
        if name == "finish":
            return "[finish]"
        entry = self._tools.get(name)
        if not entry:
            return f"[error] Herramienta '{name}' no encontrada. Disponibles: {list(self._tools.keys())}"
        try:
            result = entry["fn"](**args)
            return str(result)[:2000]
        except TypeError as e:
            return f"[error] Argumentos incorrectos para '{name}': {e}"
        except Exception as e:
            log.warning("tool %s error: %s", name, e)
            return f"[error] {name}: {e}"

    def describe(self) -> str:
        """Devuelve un bloque de texto describiendo todas las herramientas."""
        lines = []
        for name, entry in self._tools.items():
            lines.append(f"- {name}: {entry['doc'][:120]}")
        lines.append("- finish: Termina el loop y devuelve la respuesta final. args: {answer: str}")
        return "\n".join(lines)

    def list_names(self) -> list[str]:
        return list(self._tools.keys()) + ["finish"]

    # ── Registro de herramientas por defecto ─────────────────────────────────

    def _register_defaults(self) -> None:

        @self.register
        def bash(command: str, timeout: int = BASH_TIMEOUT) -> str:
            """Ejecuta un comando bash. Útil para operaciones del sistema, git, red, etc.
            args: {command: str, timeout: int=30}"""
            try:
                r = subprocess.run(
                    command, shell=True, capture_output=True, text=True,
                    timeout=timeout, cwd=str(EIDOS_DIR)
                )
                out = (r.stdout + r.stderr).strip()
                return out[:2000] if out else f"[exit {r.returncode}]"
            except subprocess.TimeoutExpired:
                return f"[timeout tras {timeout}s]"
            except Exception as e:
                return f"[error bash] {e}"

        @self.register
        def read_file(path: str, max_lines: int = 100) -> str:
            """Lee un archivo del sistema. args: {path: str, max_lines: int=100}"""
            try:
                p = Path(path).expanduser()
                if not p.exists():
                    return f"[error] No existe: {path}"
                lines = p.read_text(errors="replace").splitlines()
                truncated = len(lines) > max_lines
                result = "\n".join(lines[:max_lines])
                if truncated:
                    result += f"\n... ({len(lines) - max_lines} líneas más)"
                return result
            except Exception as e:
                return f"[error read_file] {e}"

        @self.register
        def write_file(path: str, content: str) -> str:
            """Escribe contenido a un archivo (lo crea si no existe).
            args: {path: str, content: str}"""
            try:
                p = Path(path).expanduser()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)
                return f"✅ Escrito {len(content)} chars en {path}"
            except Exception as e:
                return f"[error write_file] {e}"

        @self.register
        def grep(pattern: str, path: str, max_results: int = 20) -> str:
            """Busca un patrón regex en archivos. args: {pattern: str, path: str, max_results: int=20}"""
            try:
                r = subprocess.run(
                    ["grep", "-rn", "--include=*.py", "--include=*.sh",
                     "--include=*.txt", "--include=*.md",
                     pattern, path],
                    capture_output=True, text=True, timeout=15
                )
                lines = r.stdout.splitlines()[:max_results]
                return "\n".join(lines) if lines else f"[0 resultados para '{pattern}' en {path}]"
            except Exception as e:
                return f"[error grep] {e}"

        @self.register
        def glob(pattern: str, max_results: int = 30) -> str:
            """Lista archivos que coincidan con un patrón glob.
            args: {pattern: str, max_results: int=30}"""
            import glob as _glob
            try:
                matches = _glob.glob(pattern, recursive=True)[:max_results]
                return "\n".join(matches) if matches else f"[0 archivos para '{pattern}']"
            except Exception as e:
                return f"[error glob] {e}"

        @self.register
        def web_search(query: str, max_results: int = 5) -> str:
            """Busca en la web de forma indetectable.
            Usa curl_cffi con fingerprint TLS de Chrome real — imposible de distinguir de un navegador.
            args: {query: str, max_results: int=5}"""
            import re as _re, random as _rnd, time as _time

            q = urllib.parse.quote_plus(query)

            # Headers reales de Chrome 124 en Linux — idénticos a un usuario normal
            _CHROME_HEADERS = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                          "image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Linux"',
                "DNT": "1",
            }

            def _parse_ddg_html(html: str) -> list:
                snippets = _re.findall(
                    r'class="result__snippet"[^>]*>(.*?)</(?:a|span)>',
                    html, _re.DOTALL
                )
                titles = _re.findall(
                    r'class="result__a"[^>]*>(.*?)</a>',
                    html, _re.DOTALL
                )
                results = []
                for t, s in zip(titles[:max_results], snippets[:max_results]):
                    tc = _re.sub(r"<[^>]+>", "", t).strip()
                    sc = _re.sub(r"<[^>]+>", "", s).strip()
                    if tc and sc:
                        results.append(f"• {tc}\n  {sc[:160]}")
                return results

            # 1. curl_cffi — imita TLS fingerprint de Chrome124, indetectable
            try:
                from curl_cffi import requests as _cf
                _time.sleep(_rnd.uniform(0.3, 1.0))  # delay humano
                resp = _cf.get(
                    f"https://html.duckduckgo.com/html/?q={q}",
                    headers=_CHROME_HEADERS,
                    impersonate="chrome124",
                    timeout=15,
                )
                if resp.status_code == 200:
                    results = _parse_ddg_html(resp.text)
                    if results:
                        return "\n\n".join(results)
            except Exception:
                pass

            # 2. Bing HTML con curl_cffi (si DDG falla)
            try:
                from curl_cffi import requests as _cf
                _time.sleep(_rnd.uniform(0.5, 1.5))
                resp = _cf.get(
                    f"https://www.bing.com/search?q={q}&count={max_results}",
                    headers={**_CHROME_HEADERS, "Referer": "https://www.bing.com/"},
                    impersonate="chrome124",
                    timeout=15,
                )
                if resp.status_code == 200:
                    html = resp.text
                    # Extraer resultados Bing
                    titles = _re.findall(r'<h2[^>]*><a[^>]*>(.*?)</a>', html, _re.DOTALL)
                    snips  = _re.findall(r'<p class="b_algoSlug[^"]*">(.*?)</p>', html, _re.DOTALL)
                    results = []
                    for t, s in zip(titles[:max_results], snips[:max_results]):
                        tc = _re.sub(r"<[^>]+>", "", t).strip()
                        sc = _re.sub(r"<[^>]+>", "", s).strip()
                        if tc and len(tc) > 5:
                            results.append(f"• {tc}\n  {sc[:160]}")
                    if results:
                        return "\n\n".join(results)
            except Exception:
                pass

            # 3. urllib fallback con headers Chrome (menos fiable pero funciona para DuckDuckGo básico)
            try:
                req = urllib.request.Request(
                    f"https://html.duckduckgo.com/html/?q={q}",
                    headers=_CHROME_HEADERS
                )
                with urllib.request.urlopen(req, timeout=12) as r:
                    html = r.read(80000).decode("utf-8", errors="replace")
                results = _parse_ddg_html(html)
                if results:
                    return "\n\n".join(results)
            except Exception:
                pass

            return (f"[sin resultados web para '{query}']\n"
                    f"Alternativa: /research {query} (usa LLM + brain interno)")

        @self.register
        def web_fetch(url: str, max_chars: int = 3000) -> str:
            """Descarga una URL y extrae el texto (indetectable como bot).
            args: {url: str, max_chars: int=3000}"""
            import re as _re, random as _rnd, time as _time
            _CHROME_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
            _HEADERS = {
                "User-Agent": _CHROME_UA,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "DNT": "1",
            }
            # 1. curl_cffi con impersonación Chrome124
            try:
                from curl_cffi import requests as _cf
                _time.sleep(_rnd.uniform(0.2, 0.8))
                resp = _cf.get(url, headers=_HEADERS, impersonate="chrome124", timeout=20)
                raw = resp.text
                text = _re.sub(r"<[^>]+>", " ", raw)
                text = _re.sub(r"\s{2,}", " ", text).strip()
                return text[:max_chars]
            except Exception:
                pass
            # 2. urllib con headers Chrome (fallback)
            try:
                req = urllib.request.Request(url, headers=_HEADERS)
                with urllib.request.urlopen(req, timeout=15) as r:
                    raw = r.read(80000).decode("utf-8", errors="replace")
                text = _re.sub(r"<[^>]+>", " ", raw)
                text = _re.sub(r"\s{2,}", " ", text).strip()
                return text[:max_chars]
            except Exception as e:
                return f"[error web_fetch] {e}"

        @self.register
        def search_brain(query: str, limit: int = 5) -> str:
            """Busca en el brain (knowledge_nodes) de EIDOS.
            args: {query: str, limit: int=5}"""
            try:
                conn = get_conn(BRAIN_DB, timeout=5)
                # Intentar ChromaDB semántico primero
                try:
                    sys_path_added = False
                    import sys
                    if str(EIDOS_DIR) not in sys.path:
                        sys.path.insert(0, str(EIDOS_DIR))
                        sys_path_added = True
                    from core.colony_chroma import get_chroma_memory
                    chroma = get_chroma_memory()
                    if chroma.is_ready():
                        results = chroma.search(query, limit=limit, min_score=0.3)
                        if results:
                            rows = [(r["concept"], r["definition"]) for r in results]
                            return "\n\n".join(f"**{c}**\n{d[:300]}" for c, d in rows)
                except Exception:
                    pass
                # Fallback LIKE
                words = [w for w in query.split() if len(w) >= 3][:4]
                all_results, seen = [], set()
                for word in words:
                    for row in conn.execute(
                        "SELECT concept, definition FROM knowledge_nodes "
                        "WHERE concept LIKE ? ORDER BY confidence DESC LIMIT 4",
                        (f"%{word}%",)
                    ).fetchall():
                        if row[0] not in seen:
                            seen.add(row[0]); all_results.append(row)
                pass  # S109: get_conn no necesita close()
                if not all_results:
                    return f"[0 nodos en brain para '{query}']"
                return "\n\n".join(f"**{c}**\n{d[:300]}"
                                   for c, d in all_results[:limit])
            except Exception as e:
                return f"[error search_brain] {e}"

        @self.register
        def ask_colony(question: str) -> str:
            """Pregunta a Colony EIDOS y devuelve la respuesta del agente.
            args: {question: str}"""
            try:
                payload = json.dumps({"text": question}).encode()
                req = urllib.request.Request(
                    f"{COLONY_URL}/api/query",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.loads(r.read())
                return data.get("response") or data.get("error", "[sin respuesta]")
            except Exception as e:
                return f"[error ask_colony] {e}"

        @self.register
        def list_files(path: str, max_items: int = 30) -> str:
            """Lista archivos en un directorio. args: {path: str, max_items: int=30}"""
            try:
                p = Path(path).expanduser()
                if not p.exists():
                    return f"[error] No existe: {path}"
                entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))[:max_items]
                lines = []
                for e in entries:
                    size = f" ({e.stat().st_size // 1024}KB)" if e.is_file() else "/"
                    lines.append(f"{'📁' if e.is_dir() else '📄'} {e.name}{size}")
                return "\n".join(lines)
            except Exception as e:
                return f"[error list_files] {e}"

        # ── Control de sistema: mouse, teclado, pantalla, terminal ────────────

        @self.register
        def screenshot(filename: str = "/tmp/eidos_screen.png") -> str:
            """Captura la pantalla actual y guarda como PNG.
            args: {filename: str='/tmp/eidos_screen.png'}"""
            try:
                # -z = no countdown/cursor, --silent — NO muestra "+" al usuario
                r = subprocess.run(["scrot", "-z", filename],
                                   capture_output=True, timeout=15)
                if r.returncode == 0:
                    return f"✅ Captura guardada en {filename}"
                # Fallback: import con -silent (no cursor grab)
                r2 = subprocess.run(
                    ["import", "-silent", "-window", "root", filename],
                    capture_output=True, timeout=15
                )
                if r2.returncode == 0:
                    return f"✅ Captura guardada (import) en {filename}"
                return f"[error screenshot] rc={r.returncode}: {r.stderr.decode()}"
            except Exception as e:
                return f"[error screenshot] {e}"

        @self.register
        def mouse_click(x: int, y: int, button: str = "left") -> str:
            """Hace clic del mouse en posición (x, y) de la pantalla.
            args: {x: int, y: int, button: str='left'}"""
            try:
                btn_map = {"left": "1", "middle": "2", "right": "3"}
                btn = btn_map.get(button, "1")
                r = subprocess.run(["xdotool", "mousemove", str(x), str(y),
                                    "click", btn],
                                   capture_output=True, timeout=10)
                return f"✅ Click {button} en ({x},{y})" if r.returncode == 0 \
                    else f"[error] {r.stderr.decode()}"
            except Exception as e:
                return f"[error mouse_click] {e}"

        @self.register
        def mouse_move(x: int, y: int) -> str:
            """Mueve el cursor del mouse a posición (x, y).
            args: {x: int, y: int}"""
            try:
                r = subprocess.run(["xdotool", "mousemove", str(x), str(y)],
                                   capture_output=True, timeout=10)
                return f"✅ Mouse movido a ({x},{y})" if r.returncode == 0 \
                    else f"[error] {r.stderr.decode()}"
            except Exception as e:
                return f"[error mouse_move] {e}"

        @self.register
        def keyboard_type(text: str, delay_ms: int = 12) -> str:
            """Escribe texto en la ventana activa (como si fuera el teclado).
            args: {text: str, delay_ms: int=12}"""
            try:
                # Usar xdotool type con --clearmodifiers para fiabilidad
                r = subprocess.run(
                    ["xdotool", "type", "--clearmodifiers", f"--delay={delay_ms}", "--", text],
                    capture_output=True, timeout=30
                )
                return f"✅ Texto escrito ({len(text)} chars)" if r.returncode == 0 \
                    else f"[error] {r.stderr.decode()}"
            except Exception as e:
                return f"[error keyboard_type] {e}"

        @self.register
        def keyboard_key(key: str) -> str:
            """Pulsa una tecla especial: Return, ctrl+c, alt+F4, super, etc.
            args: {key: str}"""
            try:
                r = subprocess.run(["xdotool", "key", "--clearmodifiers", key],
                                   capture_output=True, timeout=10)
                return f"✅ Tecla '{key}' pulsada" if r.returncode == 0 \
                    else f"[error] {r.stderr.decode()}"
            except Exception as e:
                return f"[error keyboard_key] {e}"

        @self.register
        def open_url(url: str) -> str:
            """Abre una URL en Firefox-ESR (sin interferir con el teclado).
            args: {url: str}"""
            try:
                subprocess.Popen(["firefox-esr", "--new-tab", url],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return f"✅ URL abierta en Firefox: {url}"
            except Exception as e:
                return f"[error open_url] {e}"

        @self.register
        def get_screen_text(region: str = "full") -> str:
            """Captura la pantalla y extrae el texto visible con OCR (tesseract).
            args: {region: str='full'}  region puede ser 'full' o 'x,y,w,h'"""
            import tempfile
            try:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                    tmp_png = tf.name
                subprocess.run(["scrot", "-z", tmp_png], capture_output=True, timeout=15)
                r = subprocess.run(["tesseract", tmp_png, "stdout", "-l", "eng+spa"],
                                   capture_output=True, text=True, timeout=30)
                os.unlink(tmp_png)
                text = r.stdout.strip()
                return text[:3000] if text else "[OCR: sin texto detectado]"
            except FileNotFoundError as e:
                return f"[error: tesseract o scrot no instalado] {e}"
            except Exception as e:
                return f"[error get_screen_text] {e}"

        @self.register
        def terminal_run(command: str, wait: bool = True) -> str:
            """Abre una terminal con el comando dado (visible en pantalla).
            Para comandos que necesitan interacción visual.
            args: {command: str, wait: bool=True}"""
            try:
                env = os.environ.copy()
                env["DISPLAY"] = env.get("DISPLAY", ":0")
                if wait:
                    r = subprocess.run(
                        ["xterm", "-e", command],
                        capture_output=True, timeout=120, env=env
                    )
                    return f"✅ Terminal completada rc={r.returncode}"
                else:
                    subprocess.Popen(["xterm", "-e", command], env=env)
                    return f"✅ Terminal abierta con: {command[:60]}"
            except Exception as e:
                return f"[error terminal_run] {e}"

        @self.register
        def window_list() -> str:
            """Lista ventanas abiertas en el escritorio.
            args: {}"""
            try:
                r = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=10)
                return r.stdout.strip()[:1500] if r.stdout else "[sin ventanas]"
            except Exception as e:
                return f"[error window_list] {e}"

        @self.register
        def window_focus(title_pattern: str) -> str:
            """Trae al frente una ventana que contenga el texto dado en su título.
            args: {title_pattern: str}"""
            try:
                r = subprocess.run(["wmctrl", "-a", title_pattern],
                                   capture_output=True, timeout=10)
                return f"✅ Ventana '{title_pattern}' en foco" if r.returncode == 0 \
                    else f"[error] ventana no encontrada: {title_pattern}"
            except Exception as e:
                return f"[error window_focus] {e}"

        @self.register
        def clipboard_get() -> str:
            """Obtiene el contenido del portapapeles del sistema.
            args: {}"""
            try:
                r = subprocess.run(["xclip", "-selection", "clipboard", "-o"],
                                   capture_output=True, text=True, timeout=5)
                return r.stdout[:2000] if r.stdout else "[portapapeles vacío]"
            except Exception as e:
                try:
                    r2 = subprocess.run(["xsel", "--clipboard", "--output"],
                                        capture_output=True, text=True, timeout=5)
                    return r2.stdout[:2000]
                except Exception:
                    return f"[error clipboard_get] {e}"

        @self.register
        def clipboard_set(text: str) -> str:
            """Establece el contenido del portapapeles.
            args: {text: str}"""
            try:
                r = subprocess.run(["xclip", "-selection", "clipboard"],
                                   input=text.encode(), timeout=5)
                return f"✅ Portapapeles establecido ({len(text)} chars)"
            except Exception as e:
                try:
                    subprocess.run(["xsel", "--clipboard", "--input"],
                                   input=text.encode(), timeout=5)
                    return f"✅ Portapapeles establecido (xsel)"
                except Exception:
                    return f"[error clipboard_set] {e}"

        # ── Control de ventanas específicas SIN mover el ratón del usuario ───
        # Todas estas herramientas operan en una ventana por ID (wmctrl -l / xdotool)
        # sin afectar el cursor ni el foco visual del usuario.

        @self.register
        def window_capture(window_id: str, output: str = "/tmp/eidos_win.png") -> str:
            """Captura una ventana específica por ID sin mover el ratón.
            Funciona aunque la ventana esté minimizada (usa import -window).
            args: {window_id: str, output: str='/tmp/eidos_win.png'}"""
            try:
                # Convertir a decimal si viene en hex
                wid = str(int(window_id, 16)) if window_id.startswith("0x") else window_id
                # Activar ventana (trae contenido a memoria sin levantar foco visual)
                subprocess.run(["xdotool", "windowactivate", wid],
                               capture_output=True, timeout=5)
                # -silent evita que import cambie el cursor del usuario
                r = subprocess.run(
                    ["import", "-silent", "-window", wid, output],
                    capture_output=True, timeout=15
                )
                if r.returncode == 0:
                    return f"✅ Ventana {window_id} capturada en {output}"
                # Fallback: scrot -z (silencioso, sin crosshair "+")
                r2 = subprocess.run(["scrot", "-z", "-u", output], capture_output=True, timeout=10)
                return f"✅ Captura (fallback scrot) en {output}" if r2.returncode == 0 \
                    else f"[error] {r.stderr.decode()}"
            except Exception as e:
                return f"[error window_capture] {e}"

        @self.register
        def window_type_silent(window_id: str, text: str) -> str:
            """Escribe texto en una ventana específica SIN mover el ratón del usuario.
            Usa xdotool type --window para no afectar lo que el usuario está haciendo.
            args: {window_id: str, text: str}"""
            try:
                wid = str(int(window_id, 16)) if window_id.startswith("0x") else window_id
                r = subprocess.run(
                    ["xdotool", "type", "--window", wid,
                     "--clearmodifiers", "--delay=30", "--", text],
                    capture_output=True, timeout=30
                )
                return f"✅ Texto escrito en ventana {window_id}" if r.returncode == 0 \
                    else f"[error] {r.stderr.decode()}"
            except Exception as e:
                return f"[error window_type_silent] {e}"

        @self.register
        def window_click_silent(window_id: str, x: int, y: int,
                                button: str = "left") -> str:
            """Click en coordenadas LOCALES de una ventana sin mover el cursor real.
            args: {window_id: str, x: int, y: int, button: str='left'}"""
            try:
                wid  = str(int(window_id, 16)) if window_id.startswith("0x") else window_id
                btn  = {"left": "1", "middle": "2", "right": "3"}.get(button, "1")
                # Mover el ratón DENTRO de la ventana (coordenadas relativas a ella)
                r = subprocess.run(
                    ["xdotool", "mousemove", "--window", wid, str(x), str(y),
                     "click", "--window", wid, btn],
                    capture_output=True, timeout=10
                )
                return f"✅ Click {button} en ({x},{y}) ventana {window_id}" \
                    if r.returncode == 0 else f"[error] {r.stderr.decode()}"
            except Exception as e:
                return f"[error window_click_silent] {e}"

        @self.register
        def window_key_silent(window_id: str, key: str) -> str:
            """Envía una tecla a una ventana específica sin afectar el foco del usuario.
            args: {window_id: str, key: str}  ej: key='Return', 'ctrl+a', 'Escape'"""
            try:
                wid = str(int(window_id, 16)) if window_id.startswith("0x") else window_id
                r = subprocess.run(
                    ["xdotool", "key", "--window", wid, "--clearmodifiers", key],
                    capture_output=True, timeout=10
                )
                return f"✅ Tecla '{key}' enviada a {window_id}" if r.returncode == 0 \
                    else f"[error] {r.stderr.decode()}"
            except Exception as e:
                return f"[error window_key_silent] {e}"

        @self.register
        def window_read_text(window_id: str) -> str:
            """Lee el texto de una ventana específica con OCR (sin levantar al usuario).
            args: {window_id: str}"""
            import tempfile
            try:
                wid = str(int(window_id, 16)) if window_id.startswith("0x") else window_id
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                    tmp = tf.name
                subprocess.run(["import", "-silent", "-window", wid, tmp],
                               capture_output=True, timeout=15)
                r = subprocess.run(
                    ["tesseract", tmp, "stdout", "-l", "eng+spa"],
                    capture_output=True, text=True, timeout=30
                )
                os.unlink(tmp)
                return r.stdout.strip()[:3000] if r.stdout else "[sin texto]"
            except Exception as e:
                return f"[error window_read_text] {e}"

        @self.register
        def find_window(title_fragment: str) -> str:
            """Busca ventanas cuyo título contenga el texto dado.
            Devuelve ID y título de las ventanas encontradas.
            args: {title_fragment: str}"""
            try:
                r = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=10)
                matches = [line for line in r.stdout.splitlines()
                           if title_fragment.lower() in line.lower()]
                if not matches:
                    return f"[0 ventanas con '{title_fragment}']"
                return "\n".join(matches)
            except Exception as e:
                return f"[error find_window] {e}"

        @self.register
        def send_telegram_message(to_username: str, message: str,
                                  account: str = "Ema") -> str:
            """Envía un mensaje de Telegram usando Telegram Desktop en el Kali.
            Usa control de ventana silencioso para no molestar al usuario.
            args: {to_username: str, message: str, account: str='Ema'}"""
            import time as _time
            try:
                # Buscar ventana de Telegram Desktop
                r = subprocess.run(["wmctrl", "-l"], capture_output=True,
                                   text=True, timeout=10)
                tg_lines = [l for l in r.stdout.splitlines()
                            if "telegram" in l.lower()]
                if not tg_lines:
                    return "[error] Telegram Desktop no encontrado en ventanas activas"

                wid = tg_lines[0].split()[0]  # ID en hex
                wid_dec = str(int(wid, 16))

                # 1. Activar sin levantar foco real (no afecta cursor)
                subprocess.run(["xdotool", "windowactivate", wid_dec],
                               capture_output=True, timeout=5)
                _time.sleep(0.5)

                # 2. Ctrl+K (buscar chat)
                subprocess.run(
                    ["xdotool", "key", "--window", wid_dec, "ctrl+k"],
                    capture_output=True, timeout=5
                )
                _time.sleep(0.8)

                # 3. Escribir el username
                subprocess.run(
                    ["xdotool", "type", "--window", wid_dec,
                     "--clearmodifiers", "--delay=50", "--",
                     to_username.lstrip("@")],
                    capture_output=True, timeout=10
                )
                _time.sleep(1.0)

                # 4. Enter para abrir chat
                subprocess.run(
                    ["xdotool", "key", "--window", wid_dec, "Return"],
                    capture_output=True, timeout=5
                )
                _time.sleep(0.8)

                # 5. Escribir el mensaje
                subprocess.run(
                    ["xdotool", "type", "--window", wid_dec,
                     "--clearmodifiers", "--delay=30", "--", message],
                    capture_output=True, timeout=20
                )
                _time.sleep(0.5)

                # 6. Enter para enviar
                subprocess.run(
                    ["xdotool", "key", "--window", wid_dec, "Return"],
                    capture_output=True, timeout=5
                )
                return (f"✅ Mensaje enviado a @{to_username.lstrip('@')} "
                        f"via Telegram Desktop ({account})")
            except Exception as e:
                return f"[error send_telegram_message] {e}"
