"""
EIDOS Tools — Ollama Native Tool Calling
Usa el API /api/chat con `tools` para que lfm2.5-thinking:1.2b ejecute
funciones reales de forma fiable (sin depender del parser EXEC:).

Uso:
    from core.tools import ToolRunner
    runner = ToolRunner()
    result = runner.run("instala nmap en el sistema")
"""
from __future__ import annotations
import json
import os
import subprocess
import base64
import time
import urllib.request
import urllib.error
from typing import Any

from core.paths import EIDOS_HOME, REPO_ROOT

# ── Claude Oracle (Playwright) ─────────────────────────────────────────────
try:
    from core.claude_web import ask_claude_sync as _claude_sync
    HAS_CLAUDE_WEB = True
except ImportError:
    HAS_CLAUDE_WEB = False

# ── GUI Observer (visión + DOM nativo + Playwright) ──────────────────────────
try:
    from core.gui_observer import get_screen_state, get_observer, ScreenState
    HAS_GUI_OBS = True
except ImportError:
    HAS_GUI_OBS = False

OLLAMA_URL   = "http://localhost:11434"
TOOL_MODEL   = "deepseek-r1:14b"   # soporta function calling
EIDOS_DIR    = str(REPO_ROOT)
SS_DIR       = str(EIDOS_HOME / "screenshots")
os.makedirs(SS_DIR, exist_ok=True)

# ── Definición de herramientas que EIDOS puede usar ─────────────────────
TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "exec_shell",
            "description": "Ejecuta cualquier comando bash/shell en el sistema de Ser. Úsalo para ls, cat, nmap, apt, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Comando bash completo a ejecutar. Ejemplo: 'ls -lh ~/Documents'"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout en segundos (por defecto 30)"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": "Toma una captura de pantalla del escritorio de Ser y la analiza con visión IA",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Pregunta sobre lo que hay en pantalla"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "record_terminal_action",
            "description": "Ejecuta un comando en la terminal de Kali y genera un video de 60fps de la salida para enviar a Ser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Comando a ejecutar y grabar."
                    },
                    "duration_s": {
                        "type": "integer",
                        "description": "Duración máxima del video en segundos."
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Lee el contenido de un archivo del sistema",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Ruta absoluta al archivo"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Escribe o sobreescribe el contenido de un archivo",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Ruta absoluta"},
                    "content": {"type": "string", "description": "Contenido a escribir"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "install_package",
            "description": "Instala un paquete usando apt-get, pip o npm",
            "parameters": {
                "type": "object",
                "properties": {
                    "package": {"type": "string", "description": "Nombre del paquete"},
                    "method": {"type": "string", "enum": ["apt", "pip", "npm"], "description": "Gestor de paquetes"}
                },
                "required": ["package"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "call_skill",
            "description": "Llama a un skill especializado de EIDOS",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string", "description": "Nombre del skill (sin .py). Ej: net_recon, osint_cortex"},
                    "action": {"type": "string", "description": "Acción a realizar dentro del skill"},
                    "kwargs": {"type": "string", "description": "Argumentos como JSON string. Ej: '{\"target\":\"192.168.1.1\"}'"}
                },
                "required": ["skill", "action"]
            }
        }
    }
    ,
    {
        "type": "function",
        "function": {
            "name": "generate_video",
            "description": "Genera un video automático con narración de voz y contenido visual (math art). Útil para crear faceless reels y contenido para redes sociales.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Título del video"
                    },
                    "narration": {
                        "type": "string",
                        "description": "Texto que se convertirá en narración de voz"
                    },
                    "duration_seconds": {
                        "type": "integer",
                        "description": "Duración del video en segundos (default: 30)",
                        "default": 30
                    },
                    "style": {
                        "type": "string",
                        "description": "Estilo visual: math_art, geometric, abstract (default: math_art)",
                        "enum": ["math_art", "geometric", "abstract"],
                        "default": "math_art"
                    }
                },
                "required": ["title", "narration"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_claude_oracle",
            "description": "Abre Claude.ai en el navegador visible y obtiene su respuesta. Úsalo cuando necesites razonamiento complejo que supere tus capacidades locales, o cuando SER lo pida explícitamente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "El prompt detallado para enviar a Claude"
                    }
                },
                "required": ["prompt"]
            }
        }
    },
    # ── GUI OBSERVER TOOLS (F27) ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "observe_screen",
            "description": "Ve y analiza la pantalla completa de SER: detecta contexto (browser/terminal/app), lista todos los elementos UI (botones, links, texto en negrita, headers, inputs), obtiene el DOM del browser si está abierto, y describe visualmente lo que hay. Úsalo para entender el estado actual de la pantalla antes de actuar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Pregunta específica sobre la pantalla (opcional). Si vacío, descripción completa."
                    },
                    "full_dom": {
                        "type": "boolean",
                        "description": "Si true, extrae el DOM completo con scroll total (solo browsers). Más lento pero más completo."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "click_gui_element",
            "description": "Hace click en un elemento de la GUI por su texto o coordenadas. Funciona en apps nativas y browsers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Texto del elemento a clickar (botón, link, etc.)"},
                    "x":    {"type": "integer", "description": "Coordenada X (si no hay texto)"},
                    "y":    {"type": "integer", "description": "Coordenada Y (si no hay texto)"},
                    "double": {"type": "boolean", "description": "Si true, doble click"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "scroll_gui",
            "description": "Hace scroll en la ventana activa o en el browser. Útil para ver contenido que está más abajo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down", "top", "bottom"], "description": "Dirección del scroll"},
                    "amount":    {
"type": "integer", "description": "Cantidad de líneas o píxeles (default: 5 líneas)"}
                },
                "required": ["direction"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "type_in_gui",
            "description": "Escribe texto en el elemento GUI actualmente enfocado (input, searchbar, editor, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "text":      {"type": "string", "description": "Texto a escribir"},
                    "press_enter": {"type": "boolean", "description": "Si true, pulsa Enter al terminar"}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_page_dom",
            "description": "Extrae el DOM COMPLETO de la página web activa en el browser, incluyendo scroll total. Devuelve todos los elementos: links, botones, headers, textos en negrita/cursiva, inputs. Perfecto para entender una web al 100%.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL a analizar (si vacío, usa la pestaña activa)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "navigate_browser",
            "description": "Navega el browser a una URL o pulsa una tecla especial (Back, Forward, Refresh).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url":    {"type": "string", "description": "URL completa a navegar (https://...)"},
                    "action": {"type": "string", "enum": ["goto", "back", "forward", "refresh"], "description": "Acción (default: goto)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "scroll_analyze_page",
            "description": "Analiza una página web COMPLETA haciendo scroll progresivo del 10% en 10%. Para cada sección captura screenshot y analiza con VLM. Esto permite a EIDOS 'leer' toda la página y comprenderla antes de actuar, igual que un humano. Usa esto cuando necesites entender el 100% del contenido de una página larga.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Pregunta específica para analizar en cada sección. Ej: '¿Hay algún botón de compra?'"},
                    "steps":    {"type": "integer", "description": "Número de secciones (default: 10 = cada 10%)"},
                    "use_vlm":  {"type": "boolean", "description": "Si true, usa VLM moondream para describir cada sección"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_resource_status",
            "description": "Devuelve el estado actual de los recursos del sistema: CPU%, RAM%, GPU% y modo de visión de EIDOS (REALTIME/NORMAL/LIGHT/SUSPENDED). Usa esto antes de hacer análisis visuales pesados para saber si puedes usar VLM o si debes ahorrar recursos.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    # ── Curiosidad Autónoma F-CURIOSITY (4 tools) ────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "wiki_search",
            "description": "Busca en Wikipedia para estudiar algo ANTES de tocarlo. Úsalo cuando encuentres un proceso, comando, OS, protocolo o concepto desconocido. Responde: qué es, cómo funciona, qué riesgos tiene. OBLIGATORIO antes de ejecutar comandos o programas desconocidos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":     {"type": "string", "description": "Qué buscar (nombre del proceso, comando, protocolo, OS, etc.)"},
                    "lang":      {"type": "string", "description": "Idioma: 'es' (español, default) o 'en' (inglés para términos técnicos)"},
                    "sentences": {"type": "integer", "description": "Número de frases del extracto (default: 5)"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "study_app",
            "description": "Estudia una aplicación o proceso del sistema ANTES de interactuar con él. Analiza: binario ejecutable, librerías cargadas, puertos de red abiertos, man page, y busca info en Wikipedia. OBLIGATORIO antes de tocar un proceso desconocido, especialmente en VMs o nuevos OS.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "Nombre del proceso o aplicación a estudiar (ej: 'nginx', 'python3', 'sshd')"}
                },
                "required": ["app_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_directory",
            "description": "Explora el árbol de un directorio con tamaños, tipos y resumen antes de modificar nada. OBLIGATORIO antes de modificar archivos en un directorio desconocido, nuevo OS o VM. Muestra estructura hasta profundidad N.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":        {"type": "string", "description": "Ruta absoluta o relativa del directorio a explorar"},
                    "depth":       {"type": "integer", "description": "Profundidad máxima del árbol (default: 2)"},
                    "show_hidden": {"type": "boolean", "description": "Si mostrar archivos ocultos (.dotfiles) (default: false)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Descarga una URL y extrae el texto limpio (sin HTML). Para leer documentación oficial, READMEs, wikis, APIs, páginas de manual online. Útil antes de usar una herramienta nueva o explorar un OS desconocido.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url":       {"type": "string", "description": "URL completa a descargar (https://...)"},
                    "max_chars": {"type": "integer", "description": "Máximo de caracteres a devolver (default: 3000)"}
                },
                "required": ["url"]
            }
        }
    },
    # ── Computer Use v2 (Fase 5) ─────────────────────────────────────────────

    {
        "type": "function",
        "function": {
            "name": "mouse_click",
            "description": "Hace clic con el ratón en coordenadas (x,y) de la pantalla. Si hay browser Playwright activo lo usa; si no, usa pyautogui (escritorio). Úsalo para hacer clic en botones, enlaces o cualquier elemento visual cuando conoces sus coordenadas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x":       {"type": "integer", "description": "Coordenada X en píxeles"},
                    "y":       {"type": "integer", "description": "Coordenada Y en píxeles"},
                    "button":  {"type": "string", "enum": ["left", "right", "middle"], "description": "Botón del ratón (default: left)"},
                    "clicks":  {"type": "integer", "description": "Número de clics (default: 1)"}
                },
                "required": ["x", "y"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "keyboard_type",
            "description": "Escribe texto en el elemento actualmente enfocado (campo de texto, barra de búsqueda, etc.). Funciona tanto en browser Playwright como en escritorio nativo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text":     {"type": "string", "description": "Texto a escribir"},
                    "interval": {"type": "number", "description": "Delay entre teclas en ms (default: 50)"}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "keyboard_press",
            "description": "Pulsa una tecla especial: Enter, Tab, Escape, F1-F12, Delete, BackSpace, ctrl+c, ctrl+v, etc. Funciona en browser y escritorio.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Tecla a pulsar. Ej: 'Enter', 'Tab', 'Escape', 'ctrl+c'"}
                },
                "required": ["key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "keyboard_hotkey",
            "description": "Ejecuta un atajo de teclado con múltiples teclas simultáneas. Ej: ctrl+alt+t para abrir terminal, ctrl+shift+i para DevTools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {"type": "array", "items": {"type": "string"}, "description": "Lista de teclas a pulsar juntas. Ej: ['ctrl', 'alt', 't']"}
                },
                "required": ["keys"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "dom_click",
            "description": "Hace clic en un elemento del DOM del browser por selector CSS o texto visible. Más preciso que mouse_click para elementos web. Úsalo cuando el browser Playwright está activo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "Selector CSS o texto visible del elemento. Ej: '#submit-btn', 'button:has-text(\"Enviar\")', '.nav-link'"}
                },
                "required": ["selector"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "dom_type",
            "description": "Escribe texto en un campo del DOM del browser por selector CSS. Úsalo para rellenar formularios web cuando el browser Playwright está activo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "Selector CSS del campo. Ej: 'input[name=email]', '#search-box'"},
                    "text":     {"type": "string", "description": "Texto a escribir"}
                },
                "required": ["selector", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "dom_get_text",
            "description": "Extrae el texto de un elemento del DOM del browser por selector CSS. Úsalo para leer contenido específico de páginas web.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "Selector CSS. Ej: 'body', 'main', '.article-text', 'h1'. Default: body"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_navigate",
            "description": "Navega el browser Playwright a una URL. A diferencia de navigate_browser (que usa xdotool), este usa Playwright directamente y funciona en sesiones headless.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL completa a navegar (https://...)"}
                },
                "required": ["url"]
            }
        }
    },
    # ── ADB TOOLS — Control de Android por USB (F30) ─────────────────────────
    {
        "type": "function",
        "function": {
            "name": "adb_screenshot",
            "description": "Captura la pantalla del móvil Android conectado por USB y la analiza con VLM.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Pregunta sobre la pantalla del móvil (opcional)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "adb_tap",
            "description": "Toca la pantalla del móvil Android en las coordenadas dadas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Coordenada X en píxeles"},
                    "y": {"type": "integer", "description": "Coordenada Y en píxeles"}
                },
                "required": ["x", "y"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "adb_type",
            "description": "Escribe texto en el campo activo del móvil Android.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Texto a escribir"}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "adb_swipe",
            "description": "Hace un swipe/scroll en la pantalla del móvil. Usa direction='up'/'down' para scroll, o coordenadas manuales.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"], "description": "Dirección del swipe"},
                    "x1": {"type": "integer"}, "y1": {"type": "integer"},
                    "x2": {"type": "integer"}, "y2": {"type": "integer"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "adb_install_apk",
            "description": "Instala un APK en el móvil Android conectado por USB.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Ruta local al archivo .apk"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "adb_get_info",
            "description": "Obtiene información del dispositivo Android: modelo, versión, batería, resolución, IP, apps en uso.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    # ── KALI SKILLS TOOLS (F28) ──────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "kali_tool_info",
            "description": "Obtiene información completa sobre una herramienta Kali Linux: versión, descripción, uso básico.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "description": "Nombre de la herramienta. Ej: nmap, sqlmap, aircrack-ng"}
                },
                "required": ["tool"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "kali_search_tools",
            "description": "Busca herramientas Kali Linux por categoría o descripción.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Búsqueda. Ej: 'wireless cracking', 'web scanner', 'password'"}
                },
                "required": ["query"]
            }
        }
    },
    # ── MATH ART (F32) ───────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "generate_math_art",
            "description": "Genera arte usando SOLO ecuaciones matemáticas paramétricas, como Hamid Naderi Yeganeh pero con IA que inventa las ecuaciones. SER puede pedir 'dibuja un pájaro', 'espiral dorada', 'forma de pez', etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt":  {"type": "string",  "description": "Qué dibujar. Ej: 'un pájaro', 'espiral dorada', 'ola del mar'"},
                    "style":   {"type": "string",  "enum": ["yeganeh", "lissajous", "polar", "bio", "fractal"], "description": "Estilo matemático"},
                    "format":  {"type": "string",  "enum": ["svg", "png"], "description": "Formato de salida (default: svg)"}
                },
                "required": ["prompt"]
            }
        }
    },
    # ── PURPLE TEAM ARSENAL ──────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "security_scan",
            "description": "Realiza un scan de seguridad de red usando nmap. Detecta puertos abiertos, servicios, y versiones.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "IP o dominio a escanear. Ej: '192.168.1.1', 'scanme.nmap.org'"},
                    "scan_type": {"type": "string", "enum": ["quick", "full", "purple"], "description": "Tipo de scan: quick (rápido), full (todos los puertos), purple (assessment completo)"}
                },
                "required": ["target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_vuln_scan",
            "description": "Escanea aplicación web en busca de vulnerabilidades: directorios ocultos (gobuster), inyección SQL (sqlmap).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL completa a escanear. Ej: 'http://example.com'"},
                    "wordlist": {"type": "string", "description": "Path a wordlist para gobuster (opcional)"},
                    "test_sql": {"type": "boolean", "description": "Si true, también ejecuta test de SQLMap"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "system_audit",
            "description": "Auditoría de hardening y seguridad del sistema usando Lynis. Genera recomendaciones para mejorar la seguridad.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "malware_scan",
            "description": "Escanea archivos en busca de malware usando ClamAV.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directorio o archivo a escanear. Ej: '/tmp', '~/Downloads'"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "purple_team_assessment",
            "description": "Evaluación completa de seguridad Purple Team: combina tests ofensivos (Red Team) y defensivos (Blue Team).",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Target principal (IP, dominio, o 'localhost' para auto-evaluación)"},
                    "offensive": {"type": "boolean", "description": "Incluir tests ofensivos (Red Team)"},
                    "defensive": {"type": "boolean", "description": "Incluir auditoría defensiva (Blue Team)"}
                },
                "required": ["target"]
            }
        }
    }
]


# ── Implementaciones reales de las herramientas ──────────────────────────

import shlex

def _exec_shell(command: str, timeout: int = 30) -> str:
    print(f"\033[91m[🛡️ RUST]\033[0m $ {command}")
    
    rust_binary = os.path.join(EIDOS_DIR, "safe-executor", "target", "release", "safe-executor")
    if not os.path.exists(rust_binary):
        return "[ERROR] Rust executor binary no encontrado. Ejecuta 'cargo build --release'."
        
    try:
        # Se divide el comando sin usar shell=True
        args = shlex.split(command)
        if not args:
            return "(sin output)"
            
        # Env sanitizado: elimina API keys, tokens, secretos (Shield F40)
        try:
            from core.eidos_shield import EnvSanitizer
            clean_env = EnvSanitizer.sanitize(keep={"PATH", "HOME", "USER", "DISPLAY", "TERM", "LANG", "SHELL"})
        except ImportError:
            clean_env = dict(os.environ)
        clean_env["PYTHONPATH"] = EIDOS_DIR

        r = subprocess.run(
            [rust_binary] + args,
            capture_output=True, text=True, timeout=timeout+2,
            env=clean_env
        )
        
        try:
            res = json.loads(r.stdout.strip())
            stdout_str = res.get("stdout", "")
            stderr_str = res.get("stderr", "")
            err = res.get("error")
            
            if err:
                return f"[RUST-ERROR] {err}"
                
            out = (stdout_str + "\n" + stderr_str).strip()
            if not out:
                out = "(sin_output)"
            print(f"\033[90m{out[:600]}\033[0m")  # pyre-ignore[arg-type]
            return out[:3000]  # pyre-ignore[arg-type]
        except json.JSONDecodeError:
            return f"[ERROR] Salida no JSON del sandbox: {r.stdout[:200]} {r.stderr[:200]}"  # pyre-ignore[arg-type]
            
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT {timeout}s en Python Wrapper]"
    except Exception as e:
        return f"[ERROR RUST-WRAPPER] {e}"


def _take_screenshot(question: str = "¿Qué hay en pantalla? Describe brevemente.") -> str:
    print(f"\033[94m[👁️]\033[0m {question}")
    ts   = int(time.time())
    path = f"{SS_DIR}/screen_{ts}.png"
    for cmd in [f"scrot -z '{path}'", f"gnome-screenshot -f '{path}'"]:
        r = subprocess.run(cmd, shell=True, capture_output=True)
        if r.returncode == 0 and os.path.exists(path):
            break
    if not os.path.exists(path):
        return "[VISION] No hay pantalla activa (instala scrot: sudo apt install scrot)"
    with open(path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    payload = json.dumps({
        "model": "moondream2",
        "prompt": question,
        "images": [img_b64],
        "stream": False,
        "options": {"num_predict": 200}
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate", data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
            result = data.get("response", "?")
            print(f"\033[90m📸 {path}\n{result[:300]}\033[0m")  # pyre-ignore[arg-type]
            return result
    except Exception as e:
        return f"[VISION ERROR] {e}"


def _read_file(path: str) -> str:
    try:
        with open(os.path.expanduser(path)) as f:
            return f.read()[:4000]  # pyre-ignore[arg-type]
    except Exception as e:
        return f"[READ ERROR] {e}"


def _write_file(path: str, content: str) -> str:
    try:
        exp = os.path.expanduser(path)
        os.makedirs(os.path.dirname(exp), exist_ok=True)
        with open(exp, "w") as f:
            f.write(content)
        return f"✅ Escrito: {path} ({len(content)} chars)"
    except Exception as e:
        return f"[WRITE ERROR] {e}"


def _install_package(package: str, method: str = "apt") -> str:
    cmds = {
        "apt": f"sudo apt-get install -y {package}",
        "pip": f"{EIDOS_DIR}/venv_eidos/bin/pip install {package} -q",
        "npm": f"npm install -g {package}",
    }
    return _exec_shell(cmds.get(method, cmds["apt"]), timeout=120)


def _call_skill(skill: str, action: str, kwargs: str = "{}") -> str:
    try:
        kw = json.loads(kwargs) if kwargs else {}
    except json.JSONDecodeError:
        kw = {}
    cmd = (
        f"python3 -c \""
        f"import sys; sys.path.insert(0,'{EIDOS_DIR}'); "
        f"from skills.plugins.{skill} import run; "
        f"print(run('{action}', **{kw}))\""
    )
    return _exec_shell(cmd, timeout=45)


def _generate_video(title: str, narration: str, duration_seconds: int = 30, style: str = "math_art") -> str:
    """Genera un video con narración usando el VideoPipeline."""
    try:
        from core.video_pipeline import get_video_pipeline, VideoSpec
        pipeline = get_video_pipeline()
        spec = VideoSpec(
            title=title,
            narration=narration,
            duration_seconds=duration_seconds,
            style=style
        )
        result = pipeline.generate_video(spec)
        if result:
            return f"✅ Video generado: {result.final_path} ({result.file_size_mb:.1f} MB)"
        return "❌ Error generando video"
    except Exception as e:
        return f"[VIDEO ERROR] {e}"


def _ask_claude_oracle(prompt: str) -> str:
    """Abre Claude.ai via Playwright y devuelve su respuesta."""
    if not HAS_CLAUDE_WEB:
        return "[ORACLE] Playwright no disponible. Ejecuta: pip install playwright && playwright install chromium"
    print(f"\033[95m[🧠 ORACLE]\033[0m Consultando a Claude: {prompt[:60]}...")  # pyre-ignore[arg-type]
    return _claude_sync(prompt)


# ── GUI Observer Implementations (F27) ──────────────────────────────────────

def _observe_screen(question: str = "", full_dom: bool = True) -> str:
    """Analiza la pantalla completa: AT-SPI + Playwright DOM + VLM moondream."""
    if not HAS_GUI_OBS:
        # Fallback a la screenshot básica
        return _take_screenshot(question or "¿Qué hay en pantalla?")
    print(f"\033[94m[👁️  OBSERVER]\033[0m Analizando pantalla completa...")
    try:
        state = get_screen_state(use_vlm=True, use_dom=full_dom, full_page=full_dom)
        result = state.to_agent_context()
        if question and state.screenshot_b64:
            from core.gui_observer import vlm_analyze
            answer = vlm_analyze(state.screenshot_b64, question)
            result = f"[RESPUESTA] {answer}\n\n" + result
        print(f"\033[90m{result[:200]}...\033[0m")  # pyre-ignore[arg-type]
        return result
    except Exception as e:
        return f"[OBSERVER ERROR] {e}"


def _click_gui(text: str = "", x: int = 0, y: int = 0, double: bool = False) -> str:
    """Click en elemento GUI por texto (xdotool search) o coordenadas."""
    try:
        if text:
            # Buscar el elemento por texto visible usando xdotool
            r = subprocess.run(
                ["xdotool", "search", "--name", text],
                capture_output=True, text=True, timeout=3
            )
            if r.returncode == 0 and r.stdout.strip():
                wid = r.stdout.strip().split("\n")[0]  # pyre-ignore[arg-type]
                subprocess.run(["xdotool", "windowfocus", wid], timeout=2)
                subprocess.run(["xdotool", "key", "--window", wid, "Return"], timeout=2)
                return f"✅ Foco en ventana con texto: {text}"
            # Buscar con AT-SPI si está disponible
            if HAS_GUI_OBS:
                state = get_screen_state(use_vlm=False, use_dom=True)
                for el in state.elements:
                    if text.lower() in el.text.lower() and el.x > 0 and el.y > 0:
                        x, y = el.x + el.w // 2, el.y + el.h // 2
                        break
            if not (x or y):
                return f"[CLICK] No se encontró elemento con texto: {text}"

        if x or y:
            click_type = "click" if not double else "doubleclick"
            subprocess.run(
                ["xdotool", "mousemove", "--sync", str(x), str(y)],
                timeout=2
            )
            import time as _time; _time.sleep(0.1)
            subprocess.run(
                ["xdotool", click_type, str(x), str(y)],
                timeout=2
            )
            return f"✅ {'Doble click' if double else 'Click'} en ({x},{y})"
        return "[CLICK] No se especificaron coordenadas ni texto"
    except Exception as e:
        return f"[CLICK ERROR] {e}"


def _scroll_gui(direction: str = "down", amount: int = 5) -> str:
    """Scroll en la ventana activa."""
    try:
        key_map = {
            "down":   ["Down"] * min(amount, 20),
            "up":     ["Up"]   * min(amount, 20),
            "bottom": ["End"],
            "top":    ["Home"],
        }
        keys = key_map.get(direction, ["Down"] * 5)
        for key in keys:
            subprocess.run(["xdotool", "key", key], timeout=1)
        return f"✅ Scroll {direction} ({len(keys)} pasos)"
    except Exception as e:
        # Fallback: scroll con rueda del ratón
        try:
            btn = "5" if direction == "down" else "4"
            for _ in range(min(amount, 10)):
                subprocess.run(["xdotool", "click", btn], timeout=1)
            return f"✅ Scroll {direction} (rueda ratón)"
        except Exception as e2:
            return f"[SCROLL ERROR] {e} / {e2}"


def _type_gui(text: str, press_enter: bool = False) -> str:
    """Escribe texto en el elemento enfocado."""
    try:
        import time as _time
        _time.sleep(0.1)
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--delay", "30", text],
            timeout=30
        )
        if press_enter:
            _time.sleep(0.1)
            subprocess.run(["xdotool", "key", "Return"], timeout=2)
        return f"✅ Escrito: '{text[:40]}'" + (" + Enter" if press_enter else "")  # pyre-ignore[arg-type]
    except Exception as e:
        return f"[TYPE ERROR] {e}"


def _get_page_dom(url: str = "") -> str:
    """Extrae el DOM completo del browser con scroll total."""
    if not HAS_GUI_OBS:
        return "[DOM] gui_observer no disponible"
    try:
        from core.gui_observer import get_browser_dom_complete, get_screen_state, detect_context
        print(f"\033[94m[🌐 DOM]\033[0m Extrayendo DOM completo{' de ' + url if url else ''}...")
        # Si no hay URL, detectar la activa
        if not url:
            ctx, _, active_url = detect_context()
            url = active_url
        els, links, scroll_y, total_h = get_browser_dom_complete(url)
        if not els:
            # Fallback: observe_screen con DOM
            state = get_screen_state(use_vlm=False, use_dom=True)
            els = state.elements
            links = state.links
            total_h = state.total_height

        lines = [
            f"[DOM] {len(els)} elementos | {len(links)} links | {total_h}px total",
            "",
        ]
        # Headers primero
        headers = [e for e in els if e.tag == "header" and e.text]
        if headers:
            lines.append("=== ESTRUCTURA (Headers) ===")
            for h in headers[:20]:  # pyre-ignore[arg-type]
                lines.append(f"{'  '*h.level}H{h.level}: {h.text}")
            lines.append("")
        # Links
        if links:
            lines.append(f"=== LINKS ({len(links)}) ===")
            for lnk in links[:30]:  # pyre-ignore[arg-type]
                lines.append(f"  [{lnk['text'][:50]}] → {lnk['href'][:60]}")  # pyre-ignore[arg-type]
            if len(links) > 30:
                lines.append(f"  ... y {len(links)-30} más")
            lines.append("")
        # Botones e inputs
        interactive = [e for e in els if e.tag in ("button", "input", "select") and e.text]
        if interactive:
            lines.append(f"=== INTERACTIVOS ({len(interactive)}) ===")
            for el in interactive[:15]:  # pyre-ignore[arg-type]
                lines.append(f"  [{el.tag.upper()}] {el.text[:50]} {'(disabled)' if not el.enabled else ''}")  # pyre-ignore[arg-type]
            lines.append("")
        # Textos destacados (bold, H1-H3)
        bold_texts = [e for e in els if (e.bold or e.level in (1,2,3)) and e.text]
        if bold_texts:
            lines.append("=== CONTENIDO DESTACADO (negrita / H1-H3) ===")
            for e in bold_texts[:20]:  # pyre-ignore[arg-type]
                prefix = f"H{e.level}" if e.level else "BOLD"
                lines.append(f"  [{prefix}] {e.text[:80]}")  # pyre-ignore[arg-type]

        return "\n".join(lines)[:6000]  # pyre-ignore[arg-type]
    except Exception as e:
        return f"[DOM ERROR] {e}"


def _navigate_browser(url: str = "", action: str = "goto") -> str:
    """Navega el browser a una URL o ejecuta back/forward/refresh."""
    try:
        if action == "back":
            subprocess.run(["xdotool", "key", "alt+Left"], timeout=2)
            return "✅ Browser: Back"
        elif action == "forward":
            subprocess.run(["xdotool", "key", "alt+Right"], timeout=2)
            return "✅ Browser: Forward"
        elif action == "refresh":
            subprocess.run(["xdotool", "key", "ctrl+r"], timeout=2)
            return "✅ Browser: Refresh"
        else:  # goto
            if not url:
                return "[NAVIGATE] URL no especificada"
            # Ctrl+L para abrir barra de dirección, limpiar y escribir URL
            subprocess.run(["xdotool", "key", "ctrl+l"], timeout=2)
            import time as _time; _time.sleep(0.2)
            subprocess.run(["xdotool", "key", "ctrl+a"], timeout=1)
            _time.sleep(0.1)
            subprocess.run(["xdotool", "type", "--clearmodifiers", url], timeout=10)
            _time.sleep(0.1)
            subprocess.run(["xdotool", "key", "Return"], timeout=2)
            return f"✅ Navegando a: {url}"
    except Exception as e:
        return f"[NAVIGATE ERROR] {e}"


def _scroll_analyze_page(question: str = "", steps: int = 10, use_vlm: bool = True) -> str:
    """Analiza la página completa en tramos del 10% con VLM en cada sección."""
    if not HAS_GUI_OBS:
        return "[SCROLL ANALYZE] gui_observer no disponible"
    print(f"\033[94m[📜 SCROLL]\033[0m Analizando página en {steps} secciones ({100//steps}% cada una)...")
    try:
        from core.gui_observer import scroll_analyze_full, format_page_analysis, get_resources
        # Verificar recursos antes de usar VLM
        res = get_resources()
        res.update()
        use_vlm_now = use_vlm and res.can_use_vlm()
        if not use_vlm_now:
            print(f"\033[93m[⚠️  RECURSOS]\033[0m {res.summary()} — usando modo texto")

        sections = scroll_analyze_full(
            steps=steps,
            use_vlm=use_vlm_now,
            question=question,
            resources=res,
        )
        result = format_page_analysis(sections)
        print(f"\033[90m{result[:200]}...\033[0m")  # pyre-ignore[arg-type]
        return result
    except Exception as e:
        return f"[SCROLL ANALYZE ERROR] {e}"


def _get_resource_status() -> str:
    """Lee CPU, RAM y GPU y devuelve el modo de visión de EIDOS."""
    if not HAS_GUI_OBS:
        # Fallback sin gui_observer: leer /proc directamente
        try:
            with open("/proc/stat") as f:
                cpu_line = f.readline()
            with open("/proc/meminfo") as f:
                mem_lines = f.readlines()
            cpu_fields = [float(x) for x in cpu_line.split()[1:]]  # pyre-ignore[arg-type]
            total = sum(cpu_fields)
            idle  = cpu_fields[3] if len(cpu_fields) > 3 else total * 0.5  # pyre-ignore[arg-type]
            cpu_pct = 100.0 * (1.0 - idle / total) if total > 0 else 0
            mem = {l.split()[0].rstrip(":"): int(l.split()[1])  # pyre-ignore[arg-type]
                   for l in mem_lines if len(l.split()) >= 2 and l.split()[1].isdigit()}  # pyre-ignore[arg-type]
            ram_pct = 100.0 * (mem.get("MemTotal", 1) - mem.get("MemAvailable", 0)) / mem.get("MemTotal", 1)
            return f"CPU:{cpu_pct:.0f}% RAM:{ram_pct:.0f}% | gui_observer no disponible"
        except Exception as e:
            return f"[RESOURCE ERROR] {e}"
    try:
        from core.gui_observer import get_resources
        res = get_resources()
        res.update()
        return res.summary()
    except Exception as e:
        return f"[RESOURCE ERROR] {e}"


# ══════════════════════════════════════════════════════════════════════════════
# MOTOR DE CURIOSIDAD AUTÓNOMA (F-CURIOSITY)
# EIDOS SIEMPRE estudia antes de tocar: lee, analiza, aprende, luego actúa.
# ══════════════════════════════════════════════════════════════════════════════

def _wiki_search(query: str, lang: str = "es", sentences: int = 5) -> str:
    """
    Busca en Wikipedia usando la REST API oficial (sin clave, sin rate limit duro).
    Retorna el extracto + URL + secciones del artículo más relevante.
    """
    import urllib.parse
    query_enc = urllib.parse.quote(query)
    # 1. Buscar artículos más relevantes
    search_url = f"https://{lang}.wikipedia.org/w/api.php?action=opensearch&search={query_enc}&limit=3&format=json"
    try:
        req = urllib.request.Request(search_url, headers={"User-Agent": "EIDOS/2.0 (autonomous agent)"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        titles = data[1] if len(data) > 1 else []
        if not titles:
            return f"[WIKI] Sin resultados para '{query}' en {lang}.wikipedia.org"
        # 2. Obtener extracto del primer resultado
        title_enc = urllib.parse.quote(titles[0])
        extract_url = (
            f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title_enc}"
        )
        req2 = urllib.request.Request(extract_url, headers={"User-Agent": "EIDOS/2.0"})
        with urllib.request.urlopen(req2, timeout=10) as r2:
            article = json.loads(r2.read())
        title    = article.get("title", titles[0])
        extract  = article.get("extract", "(sin extracto)")
        page_url = article.get("content_urls", {}).get("desktop", {}).get("page", "")
        # Limitar a N frases
        frases = [s.strip() for s in extract.split(".") if s.strip()]
        resumen = ". ".join(frases[:sentences]) + "."
        # 3. Otras opciones de artículos
        otras = ", ".join(titles[1:3]) if len(titles) > 1 else "ninguna"
        return (
            f"[WIKI] {title}\n"
            f"URL: {page_url}\n"
            f"Extracto ({sentences} frases): {resumen}\n"
            f"También relevante: {otras}"
        )
    except Exception as e:
        return f"[WIKI ERROR] {e}"


def _study_app(app_name: str) -> str:
    """
    EIDOS estudia una aplicación/proceso ANTES de interactuar con ella.
    Analiza: qué es, qué hace, qué archivos tiene, qué permisos usa,
    qué puertos abre, y busca su descripción en Wikipedia + man pages.
    Core del principio: 'observar → aprender → actuar'.
    """
    lines: list[str] = [f"[STUDY] Analizando '{app_name}'...\n"]
    # 1. Proceso en el sistema
    r = subprocess.run(["pgrep", "-la", app_name], capture_output=True, text=True, timeout=3)
    if r.stdout.strip():
        lines.append(f"PROCESOS ACTIVOS:\n{r.stdout.strip()[:400]}")
        # PIDs
        pids = [l.split()[0] for l in r.stdout.strip().splitlines() if l.split()]  # pyre-ignore[arg-type]
        for pid in pids[:2]:  # pyre-ignore[arg-type]
            try:
                exe = subprocess.run(["readlink", f"/proc/{pid}/exe"], capture_output=True, text=True, timeout=2)
                if exe.stdout.strip():
                    lines.append(f"  Binario: {exe.stdout.strip()}")
                maps = subprocess.run(["cat", f"/proc/{pid}/maps"], capture_output=True, text=True, timeout=2)
                libs = set(
                    l.split()[-1] for l in maps.stdout.splitlines()
                    if l.split() and l.split()[-1].startswith("/") and ".so" in l
                )
                if libs:
                    lines.append(f"  Librerías: {', '.join(sorted(libs)[:5])}")
            except Exception:
                pass  # error no crítico, continuar
    else:
        lines.append(f"PROCESO: No encontrado activo (quizás no está corriendo)")
    # 2. Paquete instalado
    for cmd in [["dpkg", "-S", app_name], ["which", app_name]]:
        r2 = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        if r2.stdout.strip():
            lines.append(f"PAQUETE/RUTA: {r2.stdout.strip()[:200]}")  # pyre-ignore[arg-type]
            break
    # 3. Puertos de red abiertos
    r3 = subprocess.run(["ss", "-tulpn"], capture_output=True, text=True, timeout=3)
    net_lines = [l for l in r3.stdout.splitlines() if app_name.lower() in l.lower()]
    if net_lines:
        lines.append(f"PUERTOS:\n" + "\n".join(net_lines[:5]))  # pyre-ignore[arg-type]
    # 4. Man page (descripción oficial)
    r4 = subprocess.run(["man", "-f", app_name], capture_output=True, text=True, timeout=3)
    if r4.stdout.strip():
        lines.append(f"MAN: {r4.stdout.strip()[:200]}")
    # 5. Wikipedia
    wiki = _wiki_search(app_name, lang="en", sentences=3)
    lines.append(f"\n{wiki}")
    return "\n".join(lines)


def _inspect_directory(path: str, depth: int = 2, show_hidden: bool = False) -> str:
    """
    EIDOS explora un directorio completo antes de modificar nada.
    Muestra árbol de archivos con tamaños, tipos, y da un resumen
    inteligente de qué contiene el directorio.
    """
    import pathlib
    try:
        p = pathlib.Path(path).expanduser().resolve()
        if not p.exists():
            return f"[INSPECT] '{path}' no existe"
        lines: list[str] = [f"[INSPECT] {p} (profundidad {depth})"]
        file_count = 0
        dir_count  = 0
        total_bytes = 0
        ext_counts: dict[str, int] = {}
        def _tree(current: pathlib.Path, indent: str, current_depth: int) -> None:
            nonlocal file_count, dir_count, total_bytes
            if current_depth > depth:
                return
            try:
                entries = sorted(current.iterdir(), key=lambda e: (e.is_file(), e.name))
            except PermissionError:
                lines.append(f"{indent}[Sin permiso]")
                return
            for entry in entries[:40]:  # máx 40 por directorio  # pyre-ignore[arg-type]
                if not show_hidden and entry.name.startswith("."):
                    continue
                if entry.is_symlink():
                    lines.append(f"{indent}↗ {entry.name} → {os.readlink(entry)}")
                elif entry.is_dir():
                    dir_count += 1
                    lines.append(f"{indent}📁 {entry.name}/")
                    _tree(entry, indent + "   ", current_depth + 1)
                else:
                    size = entry.stat().st_size if entry.exists() else 0
                    total_bytes += size
                    file_count += 1
                    ext = entry.suffix.lower() or "(sin ext)"
                    ext_counts[ext] = ext_counts.get(ext, 0) + 1
                    size_str = f"{size/1024:.1f}KB" if size > 1024 else f"{size}B"
                    lines.append(f"{indent}📄 {entry.name} ({size_str})")
        _tree(p, "", 0)
        total_kb = total_bytes / 1024
        lines.insert(1, (
            f"Resumen: {file_count} archivos, {dir_count} carpetas, "
            f"{total_kb:.1f}KB total | "
            f"Extensiones: {dict(sorted(ext_counts.items(), key=lambda x: -x[1])[:5])}"
        ))
        return "\n".join(lines[:150])  # pyre-ignore[arg-type]
    except Exception as e:
        return f"[INSPECT ERROR] {e}"


def _web_fetch(url: str, max_chars: int = 3000) -> str:
    """
    Descarga una URL y la convierte en texto limpio (sin HTML).
    Ideal para que EIDOS lea documentación, wikis, READMEs, APIs.
    Elimina tags, scripts, style y deja solo el texto visible.
    """
    import html
    import re
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,text/plain",
            }
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read(1024 * 512).decode("utf-8", errors="replace")  # 512KB máx
        # Limpiar HTML
        raw = re.sub(r'<script[^>]*>.*?</script>', '', raw, flags=re.DOTALL | re.I)
        raw = re.sub(r'<style[^>]*>.*?</style>',  '', raw, flags=re.DOTALL | re.I)
        raw = re.sub(r'<[^>]+>',  ' ', raw)
        raw = html.unescape(raw)
        raw = re.sub(r'\s+', ' ', raw).strip()
        return f"[WEB] {url}\n{raw[:max_chars]}"
    except Exception as e:
        return f"[WEB ERROR] {url}: {e}"


def _deep_crawl_url(url: str, max_pages: int = 20, visual: bool = False) -> str:
    """
    Crawl profundo: lee url + todos sus sublinks del mismo dominio.
    visual=True → abre el browser visible primero (cuando el usuario lo pide).
    visual=False → modo headless eficiente (modo libre autónomo).
    Sintetiza con Ollama y guarda en ChromaDB.
    """
    import sys
    sys.path.insert(0, str(REPO_ROOT))

    # Modo visual: abrir browser visible para la primera página
    if visual:
        try:
            _navigate_browser(url)
            import time as _time; _time.sleep(2)
            _scroll_analyze_page(question=f"Analiza el contenido y estructura de {url}", steps=5)
        except Exception as e:
            print(f"[DEEP CRAWL] browser visual: {e} — continuando con urllib")

    try:
        from core.deep_crawler import get_deep_crawler

        def _progress(visited, queued, current_url):
            short = current_url[:60] + "…" if len(current_url) > 60 else current_url
            print(f"\r  🕷️  [{visited}/{max_pages}] {short}      ", end="", flush=True)

        crawler = get_deep_crawler()
        print(f"\n🕷️  Deep crawl: {url} (max {max_pages} páginas)")
        report = crawler.crawl(url, max_pages=max_pages, progress_cb=_progress)
        print()

        lines = [
            f"✅ Crawl completado — {report.pages_visited} páginas | {report.duration_s}s",
            f"ChromaDB: {report.chroma_stored} nodos guardados",
            "",
        ]
        if report.key_topics:
            lines.append(f"Temas: {', '.join(report.key_topics)}\n")
        lines.append("SÍNTESIS:")
        lines.append(report.synthesis)
        return "\n".join(lines)

    except Exception as e:
        return f"[DEEP CRAWL ERROR] {e}"


def _record_terminal_action(args: dict) -> str:
    """Ejecuta un comando y genera un video de 60fps con el resultado."""
    from core.vision_60fps import generate_video_60fps
    command = args["command"]
    duration = int(args.get("duration_s", 5))
    
    start_time = time.time()
    try:
        output = _exec_shell(command, timeout=duration + 10)
    except Exception as e:
        output = f"Error ejecutando comando: {e}"
    end_time = time.time()
    
    actual_duration = int(end_time - start_time)
    prompt = f"Terminal output of command: {command}. Result: {output[:200]}..."
    video_path = generate_video_60fps(prompt, duration_s=max(2, actual_duration))
    
    return f"Acción grabada con éxito. {video_path}\n\nSalida terminal:\n{output}"


# Mapa de herramientas — core tools
TOOL_IMPL: dict[str, Any] = {
    "exec_shell":        lambda args: _exec_shell(args["command"], int(args.get("timeout", 30))),
    "record_terminal_action": lambda args: _record_terminal_action(args),
    "take_screenshot":   lambda args: _take_screenshot(args.get("question", "¿Qué hay en pantalla?")),
    "read_file":         lambda args: _read_file(args["path"]),
    "write_file":        lambda args: _write_file(args["path"], args["content"]),
    "install_package":   lambda args: _install_package(args["package"], args.get("method", "apt")),
    "call_skill":        lambda args: _call_skill(args["skill"], args["action"], args.get("kwargs", "{}")),
    "generate_video":    lambda args: _generate_video(args["title"], args["narration"], args.get("duration_seconds", 30), args.get("style", "math_art")),
    "ask_claude_oracle": lambda args: _ask_claude_oracle(args["prompt"]),
    # ── GUI Observer Tools (F27) ─────────────────────────────────────────────
    "observe_screen":    lambda args: _observe_screen(args.get("question", ""), args.get("full_dom", True)),
    "click_gui_element": lambda args: _click_gui(args.get("text", ""), args.get("x", 0), args.get("y", 0), args.get("double", False)),
    "scroll_gui":        lambda args: _scroll_gui(args.get("direction", "down"), args.get("amount", 5)),
    "type_in_gui":       lambda args: _type_gui(args["text"], args.get("press_enter", False)),
    "get_page_dom":      lambda args: _get_page_dom(args.get("url", "")),
    "navigate_browser":  lambda args: _navigate_browser(args.get("url", ""), args.get("action", "goto")),
    "scroll_analyze_page": lambda args: _scroll_analyze_page(args.get("question", ""), args.get("steps", 10), args.get("use_vlm", True)),
    "get_resource_status":  lambda args: _get_resource_status(),
    # ── RAM Guardian (F-RAM) — monitoreo y limpieza de RAM ──────────────────
    "ram_check":   lambda args: _ram_check(kill=args.get("kill", False)),
    "gpu_status":  lambda args: _gpu_status(),
    # ── Motor de Curiosidad Autónoma (F-CURIOSITY) ───────────────────────────
    "wiki_search":       lambda args: _wiki_search(args["query"], args.get("lang", "es"), args.get("sentences", 5)),
    "study_app":         lambda args: _study_app(args["app_name"]),
    "inspect_directory": lambda args: _inspect_directory(args["path"], args.get("depth", 2), args.get("show_hidden", False)),
    "web_fetch":         lambda args: _web_fetch(args["url"], args.get("max_chars", 3000)),
    # ── Deep Crawler (sesión 27) — crawl recursivo URL + sublinks ────────────
    "deep_crawl_url":    lambda args: _deep_crawl_url(args["url"], args.get("max_pages", 20), args.get("visual", False)),
}


def _ram_check(kill: bool = False) -> str:
    """Llama al RAM Guardian para escanear y opcionalmente limpiar procesos hog."""
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "ram_guardian",
            str(REPO_ROOT / "_SCRIPTS" / "ram_guardian.py")
        )
        _mod = importlib.util.module_from_spec(_spec)   # pyre-ignore[arg-type]
        _spec.loader.exec_module(_mod)  # pyre-ignore[arg-type]
        result = _mod.scan(kill=kill, verbose=False)
        lines = [
            f"[RAM] {result['ram_available_mb']}MB libre de {result['ram_total_mb']}MB "
            f"({100 - result['ram_pct_used']:.1f}% libre) — {'🟢 OK' if result['ok'] else '🔴 CRÍTICO'}",
        ]
        if result["hogs"]:
            lines.append(f"  Hogs: {len(result['hogs'])} procesos > 600MB")
            for h in result["hogs"][:3]:  # pyre-ignore[arg-type]
                lines.append(f"  • {h['name']} PID={h['pid']} {h['ram_mb']}MB")
        if result.get("killed"):
            freed = sum(k["ram_mb"] for k in result["killed"])
            lines.append(f"  💀 Matados: {len(result['killed'])} procesos (~{freed}MB liberado)")
        if result.get("cache_msg"):
            lines.append(f"  {result['cache_msg']}")
        return "\n".join(lines)
    except Exception as e:
        return f"[RAM] Error en ram_guardian: {e}"


def _gpu_status() -> str:
    """Retorna estado de la GPU AMD Radeon 610M: VRAM, driver, Vulkan."""
    import subprocess as _sp
    lines = ["[GPU] AMD Radeon 610M (Mendocino)"]
    # VRAM via sysfs
    try:
        vram_total = int(open("/sys/class/drm/card0/device/mem_info_vram_total").read().strip())
        vram_used  = int(open("/sys/class/drm/card0/device/mem_info_vram_used", "r").read().strip())
        gtt_used   = int(open("/sys/class/drm/card0/device/mem_info_gtt_used", "r").read().strip())
        lines.append(f"  VRAM: {vram_used//1048576}MB / {vram_total//1048576}MB usado")
        lines.append(f"  GTT:  {gtt_used//1048576}MB usado")
    except Exception:
        lines.append("  VRAM: no disponible via sysfs")
    # Driver via lsmod
    try:
        lsmod = _sp.check_output(["lsmod"], text=True, timeout=5)
        if "amdgpu" in lsmod:
            lines.append("  Driver: amdgpu ✅")
        elif "radeon" in lsmod:
            lines.append("  Driver: radeon (legacy)")
        else:
            lines.append("  Driver: desconocido ⚠️")
    except Exception:
        pass  # error no crítico, continuar
    # Vulkan via vulkaninfo quick
    try:
        vk = _sp.check_output(
            ["vulkaninfo", "--summary"], text=True, timeout=8, stderr=_sp.DEVNULL
        )
        for ln in vk.split("\n"):
            if "deviceName" in ln or "driverName" in ln:
                lines.append(f"  Vulkan: {ln.strip()}")
                break
        lines.append("  Vulkan: RADV ✅")
    except Exception:
        lines.append("  Vulkan: vulkaninfo no disponible")
    # zswap status
    try:
        zswap_en   = open("/sys/module/zswap/parameters/enabled").read().strip()
        zswap_comp = open("/sys/module/zswap/parameters/compressor").read().strip()
        lines.append(f"  zswap: {'✅ activo' if zswap_en=='Y' else '❌ inactivo'} ({zswap_comp})")
    except Exception:
        pass  # error no crítico, continuar
    return "\n".join(lines)


# Inyectar Computer Use tools (Fase 5) — lazy import para evitar circulares
try:
    from core.computer_use_v2 import get_computer_use_tools as _get_cu_tools
    TOOL_IMPL.update(_get_cu_tools())
except Exception:
    pass  # computer_use_v2 opcional si no hay pantalla

# ── ADB Tools (F30) — Android por USB ────────────────────────────────────────
try:
    from core.adb_bridge import get_adb as _get_adb, ADBBridge as _ADBBridge
    import base64 as _b64

    def _adb_screenshot(args: dict) -> str:
        adb = _get_adb(auto_connect=True, watchdog=False)
        if not adb._connected:
            return "[ADB] No hay dispositivo Android conectado. Conecta el móvil por USB y activa depuración USB."
        path = adb.take_screenshot()
        if not path:
            return "[ADB] No se pudo capturar pantalla del móvil."
        question = args.get("question", "¿Qué muestra la pantalla del móvil?")
        # Analizar con VLM si está disponible
        try:
            import urllib.request, json as _json
            with open(path, "rb") as f:
                img_b64 = _b64.b64encode(f.read()).decode()
            payload = _json.dumps({
                "model": "moondream:latest", "stream": False,
                "prompt": question, "images": [img_b64]
            }).encode()
            req = urllib.request.Request("http://127.0.0.1:11434/api/generate",
                                          data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = _json.load(resp)
            vlm_desc = result.get("response", "")
            return f"📱 Screenshot guardado: {path}\n🔍 VLM: {vlm_desc}"
        except Exception as e:
            return f"📱 Screenshot guardado: {path}\n⚠️ VLM no disponible: {e}"

    def _adb_swipe_smart(args: dict) -> str:
        adb = _get_adb(auto_connect=True, watchdog=False)
        direction = args.get("direction", "")
        if direction:
            if direction == "down":
                ok = adb.scroll_down()
            elif direction == "up":
                ok = adb.scroll_up()
            else:
                x1 = args.get("x1", 540); y1 = args.get("y1", 960)
                x2 = x1 + (200 if direction == "right" else -200)
                ok = adb.swipe(x1, y1, x2, y1)
        else:
            ok = adb.swipe(args.get("x1", 540), args.get("y1", 700),
                           args.get("x2", 540), args.get("y2", 300))
        return "✅ Swipe OK" if ok else "❌ Swipe falló"

    TOOL_IMPL.update({
        "adb_screenshot":  _adb_screenshot,
        "adb_tap":         lambda args: "✅ Tap OK" if _get_adb(True, False).tap(args["x"], args["y"]) else "❌ Tap falló",
        "adb_type":        lambda args: "✅ Texto escrito" if _get_adb(True, False).type_text(args["text"]) else "❌ Error al escribir",
        "adb_swipe":       _adb_swipe_smart,
        "adb_install_apk": lambda args: "✅ APK instalado" if _get_adb(True, False).install_apk(args["path"]) else "❌ Error instalando APK",
        "adb_get_info":    lambda args: str(_get_adb(True, False).get_device_info()),
    })
    print("\033[92m[ADB] Tools ADB disponibles (F30)\033[0m")
except Exception as _adb_e:
    print(f"\033[93m[ADB] adb_bridge no disponible: {_adb_e}\033[0m")

# ── Kali Skills Tools (F28) ──────────────────────────────────────────────────
try:
    from kali_skills_auto import eidos_get_tool_info as _kali_info, eidos_search_tools as _kali_search
    TOOL_IMPL.update({
        "kali_tool_info":    lambda args: _kali_info(args["tool"]),
        "kali_search_tools": lambda args: _kali_search(args["query"]),
    })
    print("\033[92m[KALI] Kali Skills Tools disponibles (F28)\033[0m")
except Exception:
    # Si kali_skills_auto no importa, implementación mínima via subprocess
    def _kali_info_fallback(args: dict) -> str:
        import subprocess
        tool = args.get("tool", "")
        try:
            res = subprocess.run(["which", tool], capture_output=True, text=True, timeout=5)
            if res.returncode != 0:
                return f"[KALI] '{tool}' no encontrado en PATH. Instala con: sudo apt install {tool}"
            ver = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=5)
            hlp = subprocess.run([tool, "--help"],   capture_output=True, text=True, timeout=5)
            return f"✅ {tool}: {res.stdout.strip()}\n{ver.stdout[:200]}\n{hlp.stdout[:400]}"  # pyre-ignore[arg-type]
        except Exception as e:
            return f"[KALI INFO ERROR] {e}"

    def _kali_search_fallback(args: dict) -> str:
        import subprocess
        query = args.get("query", "")
        try:
            res = subprocess.run(["apt-cache", "search", query], capture_output=True, text=True, timeout=10)
            lines = res.stdout.strip().splitlines()[:20]  # pyre-ignore[arg-type]
            return f"🔍 Herramientas Kali para '{query}':\n" + "\n".join(lines) if lines else f"Sin resultados para '{query}'"
        except Exception as e:
            return f"[KALI SEARCH ERROR] {e}"

    TOOL_IMPL.update({
        "kali_tool_info":    _kali_info_fallback,
        "kali_search_tools": _kali_search_fallback,
    })

# ── Math Art (F32) ──────────────────────────────────────────────────────────
try:
    from core.math_art import generate_math_art as _math_art_fn
    def _generate_math_art(args: dict) -> str:
        prompt = args.get("prompt", "arte geométrico")
        style  = args.get("style", "Yeganeh-style")
        return _math_art_fn(prompt, style=style)
    TOOL_IMPL["generate_math_art"] = _generate_math_art
    print("\033[92m[ART] Math Art Engine disponible (F32)\033[0m")
except Exception as _art_e:
    TOOL_IMPL["generate_math_art"] = lambda args: f"[MATH ART] No disponible: {_art_e}"

# ── Fase 35 — Memory Tools: record_exchange, memory_recall, notify_ser, schedule_task ─────
try:
    from core.memory_tools import MEMORY_TOOLS, MEMORY_TOOL_IMPL
    TOOLS.extend(MEMORY_TOOLS)
    TOOL_IMPL.update(MEMORY_TOOL_IMPL)
    print("\033[95m[F35] Memory Tools activas: record_exchange | memory_recall | notify_ser | schedule_task\033[0m")
except Exception as _mem_e:
    print(f"\033[91m[F35] Memory Tools no disponibles: {_mem_e}\033[0m")

# ── Schemas para ram_check y gpu_status (ya en TOOL_IMPL, faltaba el schema JSON) ────────
TOOLS.extend([
    {
        "type": "function",
        "function": {
            "name": "ram_check",
            "description": (
                "Devuelve el estado actual de RAM del sistema: total, usado, libre y porcentaje. "
                "Úsalo antes de lanzar modelos Ollama pesados o tareas que consuman mucha memoria."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "gpu_status",
            "description": (
                "Devuelve información sobre la GPU disponible: nombre, VRAM, utilización y temperatura. "
                "Úsalo para saber si puedes usar aceleración GPU (ROCm/AMD) antes de tareas de visión o ML."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
])


# ── ToolRunner — el orquestador ──────────────────────────────────────────

class ToolRunner:
    """
    Ejecuta un ciclo ReAct completo con lfm2.5-thinking:1.2b + Ollama tool calling.
    El modelo decide qué herramienta usar; ToolRunner la ejecuta y vuelve al modelo.
    """

    def __init__(self, soul: str = "") -> None:
        self.soul = soul or "Eres EIDOS, agente soberano de SER. Usa las herramientas para actuar."

    def _call_ollama(self, messages: list[dict],
                     use_tools: bool = True) -> dict:
        payload: dict[str, Any] = {
            "model":    TOOL_MODEL,
            "messages": messages,
            "stream":   False,
            "options":  {"num_ctx": 4096, "num_predict": 512},
        }
        if use_tools:
            payload["tools"] = TOOLS
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat", data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.load(resp)

    def run(self, user_input: str, history: list[dict] | None = None,
            max_iterations: int = 5) -> str:
        """
        Loop ReAct completo:
          1. Envía mensaje al modelo con tools disponibles
          2. Si el modelo llama una tool → ejecuta → vuelve al modelo
          3. Repite hasta que el modelo dé respuesta final (sin tool calls)
        """
        messages: list[dict] = [{"role": "system", "content": self.soul}]
        if history:
            messages.extend(history[-10:])
        messages.append({"role": "user", "content": user_input})

        for iteration in range(max_iterations):
            try:
                response = self._call_ollama(messages, use_tools=True)
            except Exception as e:
                return f"[TOOL ERR] {e}"

            msg = response.get("message", {})
            tool_calls: list[dict] = msg.get("tool_calls", [])

            # ── Respuesta final (sin tool calls) ──────────────────────────
            if not tool_calls:
                return msg.get("content", "(sin respuesta)")

            # ── Ejecutar tool calls ────────────────────────────────────────
            messages.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": tool_calls})

            for tc in tool_calls:
                fn_name = tc.get("function", {}).get("name", "")
                fn_args = tc.get("function", {}).get("arguments", {})
                if isinstance(fn_args, str):
                    try:
                        fn_args = json.loads(fn_args)
                    except Exception:
                        fn_args = {}

                if fn_name in TOOL_IMPL:
                    try:
                        tool_result = str(TOOL_IMPL[fn_name](fn_args))
                    except Exception as e:
                        tool_result = f"[TOOL_ERR] {fn_name}: {e}"
                else:
                    tool_result = f"[UNKNOWN TOOL] {fn_name}"

                messages.append({
                    "role": "tool",
                    "name": fn_name,
                    "content": tool_result[:2000],  # pyre-ignore[arg-type]
                })

        return "[EIDOS] Máximo de iteraciones alcanzado."

# ── Alta Prioridad: diff_files + process_control ──────────────────────────────

# Schemas
TOOLS.extend([
    {
        "type": "function",
        "function": {
            "name": "diff_files",
            "description": (
                "Muestra las diferencias entre dos archivos (similar a 'diff -u'). "
                "ÚSALO antes de sobreescribir cualquier archivo para ver qué cambia exactamente."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path_a": {"type": "string", "description": "Ruta del primer archivo (original)"},
                    "path_b": {"type": "string", "description": "Ruta del segundo archivo (nuevo/modificado)"},
                },
                "required": ["path_a", "path_b"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "process_control",
            "description": (
                "Controla servicios systemd de forma segura: start, stop, restart, status, enable, disable. "
                "Más seguro que exec_shell para gestionar servicios del sistema."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "Nombre del servicio (ej: 'ollama', 'eidos-ram-guardian')"},
                    "action":  {"type": "string", "description": "Acción: start|stop|restart|status|enable|disable"},
                    "user":    {"type": "boolean", "description": "Si True, usa --user (servicio de usuario, no root)"},
                },
                "required": ["service", "action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ocr_image",
            "description": (
                "Extrae texto de una imagen usando Tesseract OCR. "
                "Útil para leer capturas de apps que no tienen accesibilidad AT-SPI2."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Ruta a la imagen (PNG, JPG, WEBP)"},
                    "lang": {"type": "string", "description": "Idioma Tesseract (ej: 'spa', 'eng', 'spa+eng'). Por defecto: 'spa+eng'"},
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "compress_extract",
            "description": (
                "Comprime o extrae archivos ZIP/TAR/GZ/BZ2/XZ sin usar exec_shell manualmente. "
                "Acciones: 'compress' (crea archivo), 'extract' (descomprime)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "compress|extract"},
                    "path":   {"type": "string", "description": "Archivo o carpeta a comprimir, o archivo a extraer"},
                    "dest":   {"type": "string", "description": "Destino: carpeta de salida (extract) o nombre del archivo resultante (compress)"},
                    "format": {"type": "string", "description": "Formato: zip|tar.gz|tar.bz2|tar.xz (solo para compress)"},
                },
                "required": ["action", "path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "translate",
            "description": (
                "Traduce texto a cualquier idioma usando LibreTranslate (local/gratuito) o fallback a modelo Ollama. "
                "EIDOS puede entender y responder en cualquier idioma usando esto."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text":        {"type": "string", "description": "Texto a traducir"},
                    "target_lang": {"type": "string", "description": "Idioma destino (ISO 639-1): es, en, fr, de, it, pt, ru, zh, ar, ja..."},
                    "source_lang": {"type": "string", "description": "Idioma origen (auto si no se especifica)"},
                },
                "required": ["text", "target_lang"]
            }
        }
    },
])

# Implementaciones
import difflib as _difflib

def _diff_files(args: dict) -> str:
    path_a = args.get("path_a", "")
    path_b = args.get("path_b", "")
    try:
        with open(path_a) as fa:
            lines_a = fa.readlines()
        with open(path_b) as fb:
            lines_b = fb.readlines()
        diff = list(_difflib.unified_diff(lines_a, lines_b, fromfile=path_a, tofile=path_b, lineterm=""))
        if not diff:
            return f"✅ Los archivos son idénticos: {path_a} == {path_b}"
        result = "".join(diff[:200])  # Limitar a 200 líneas de diff
        lines_shown = min(len(diff), 200)
        suffix = f"\n... ({len(diff) - 200} líneas más)" if len(diff) > 200 else ""
        return f"📄 Diff ({lines_shown}/{len(diff)} líneas):\n{result}{suffix}"
    except FileNotFoundError as e:
        return f"[DIFF] Archivo no encontrado: {e}"
    except Exception as e:
        return f"[DIFF ERROR] {e}"

def _process_control(args: dict) -> str:
    service = args.get("service", "")
    action  = args.get("action", "status")
    is_user = args.get("user", True)
    allowed = {"start", "stop", "restart", "status", "enable", "disable"}
    if action not in allowed:
        return f"[PROCESS_CTRL] Acción no permitida: '{action}'. Usa: {allowed}"
    if not service:
        return "[PROCESS_CTRL] Se requiere nombre de servicio."
    cmd = ["systemctl"] + (["--user"] if is_user else []) + [action, service]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        out = (r.stdout + r.stderr).strip()
        return f"{'✅' if r.returncode == 0 else '❌'} systemctl {action} {service}:\n{out[:1000]}"
    except Exception as e:
        return f"[PROCESS_CTRL ERROR] {e}"

def _ocr_image(args: dict) -> str:
    path = args.get("path", "")
    lang = args.get("lang", "spa+eng")
    if not path:
        return "[OCR] Se requiere ruta a la imagen."

    # -- Smart Cache: Check cache first --
    try:
        from core.smart_cache import smart_cache
        import hashlib

        # Cache key basado en path + lang + file hash
        with open(path, 'rb') as f:
            file_hash = hashlib.md5(f.read()).hexdigest()
        cache_key = f"ocr_{file_hash}_{lang}"

        cached = smart_cache.get(cache_key, cache_type='ocr')
        if cached is not None:
            print(f"💾 [Cache HIT] OCR cached para {path}")
            return cached
    except Exception:
        pass  # Sin cache, continuar normal

    try:
        r = subprocess.run(
            ["tesseract", path, "stdout", "-l", lang, "--psm", "3"],
            capture_output=True, text=True, timeout=30
        )
        text = r.stdout.strip()
        if not text:
            result = f"[OCR] Sin texto detectado en: {path}"
        else:
            result = f"📝 OCR ({path}):\n{text[:3000]}"

        # -- Smart Cache: Save result --
        try:
            smart_cache.set(cache_key, result, cache_type='ocr')
        except Exception:
            pass  # error no crítico, continuar
        return result
    except FileNotFoundError:
        return "[OCR] Tesseract no instalado. Instalar con: sudo apt install tesseract-ocr tesseract-ocr-spa"
    except Exception as e:
        return f"[OCR ERROR] {e}"

def _compress_extract(args: dict) -> str:
    import tarfile, zipfile as _zip
    action = args.get("action", "")
    path   = args.get("path", "")
    dest   = args.get("dest", "")
    fmt    = args.get("format", "tar.gz")
    if not path:
        return "[COMPRESS] Se requiere 'path'."
    try:
        if action == "extract":
            out_dir = dest or os.path.dirname(path) or "."
            os.makedirs(out_dir, exist_ok=True)
            if path.endswith(".zip"):
                with _zip.ZipFile(path) as zf:
                    zf.extractall(out_dir)
            else:
                with tarfile.open(path) as tf:
                    tf.extractall(out_dir)
            return f"✅ Extraído: {path} → {out_dir}"
        elif action == "compress":
            out = dest or (path.rstrip("/") + "." + fmt)
            if fmt == "zip":
                with _zip.ZipFile(out, "w", _zip.ZIP_DEFLATED) as zf:
                    if os.path.isdir(path):
                        for root, _, files in os.walk(path):
                            for f in files:
                                fp = os.path.join(root, f)
                                zf.write(fp, os.path.relpath(fp, path))
                    else:
                        zf.write(path)
            else:
                mode = "w:gz" if "gz" in fmt else ("w:bz2" if "bz2" in fmt else "w:xz")
                with tarfile.open(out, mode) as tf:
                    tf.add(path, arcname=os.path.basename(path))
            return f"✅ Comprimido: {path} → {out}"
        else:
            return f"[COMPRESS] Acción no reconocida: '{action}'. Usa: extract|compress"
    except Exception as e:
        return f"[COMPRESS ERROR] {e}"

def _translate(args: dict) -> str:
    text   = args.get("text", "")
    target = args.get("target_lang", "en")
    source = args.get("source_lang", "auto")
    if not text:
        return "[TRANSLATE] Se requiere texto."
    # Intento 1: LibreTranslate local
    try:
        payload = json.dumps({"q": text, "source": source, "target": target, "format": "text"}).encode()
        req = urllib.request.Request(
            "http://localhost:5000/translate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.load(resp)
            return f"🌐 [{source}→{target}]: {data.get('translatedText', '')}"
    except Exception:
        pass  # error no crítico, continuar
    # Fallback: Ollama
    try:
        payload = json.dumps({
            "model": "deepseek-r1:14b",
            "messages": [{"role": "user", "content": f"Translate to {target}, output ONLY the translation:\n{text[:1000]}"}],
            "stream": False,
            "options": {"num_predict": 256},
        }).encode()
        req = urllib.request.Request(
            f"http://localhost:11434/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
            return f"�� [{source}→{target}] (Ollama): {data.get('message', {}).get('content', '')}"
    except Exception as e:
        return f"[TRANSLATE ERROR] {e}"

TOOL_IMPL.update({
    "diff_files":       _diff_files,
    "process_control":  _process_control,
    "ocr_image":        _ocr_image,
    "compress_extract": _compress_extract,
    "translate":        _translate,
})
print("\033[95m[ALTA+MEDIA] diff_files | process_control | ocr_image | compress_extract | translate — ACTIVAS\033[0m")

# ── Auto-Parcheador de BugBot (Alta Urgencia) ─────────────────────────────────

TOOLS.append({
    "type": "function",
    "function": {
        "name": "auto_patch",
        "description": (
            "Analiza y aplica correcciones automáticas en disco a un archivo Python "
            "usando el revisor de código BugBot (AST + LLM profundo). Hace backup (.bak) antes de parchear."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Ruta al archivo .py a revisar y parchear"},
                "dry_run":  {"type": "boolean", "description": "Si True, solo simula el parche; si False, lo aplica al disco y hace backup."}
            },
            "required": ["filepath"]
        }
    }
})

def _auto_patch(args: dict) -> str:
    path = args.get("filepath", "")
    dry  = args.get("dry_run", False)
    if not path:
        return "[auto_patch] Error: Falta 'filepath'"
    try:
        from core.bugbot import bugfix
        return bugfix(path, dry_run=dry)
    except Exception as e:
        return f"[auto_patch] Error inesperado: {e}"

TOOL_IMPL["auto_patch"] = _auto_patch
print("\033[95m[BUGBOT] auto_patch (Auto-Curación) — ACTIVA\033[0m")

# ── Skills Loader (Las 6660 tools de Kali) ────────────────────────────────────

TOOLS.extend([
    {
        "type": "function",
        "function": {
            "name": "kali_skill_search",
            "description": "Busca entre más de 6660 herramientas de Kali Linux instaladas. Devuelve un resumen corto de las coincidencias. Úsalo para descubrir herramientas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Término de búsqueda (ej: 'sql', 'wifi', 'nmap')"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "kali_skill_get",
            "description": "Obtiene la información detallada y cómo usar desde EIDOS una herramienta de Kali específica (por su nombre exacto).",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "Nombre exacto de la herramienta (ej: 'nmap')"}
                },
                "required": ["tool_name"]
            }
        }
    }
])

def _kali_skill_search(args: dict) -> str:
    query = args.get("query", "")
    try:
        from kali_skills_auto import eidos_search_tools
        return eidos_search_tools(query)
    except Exception as e:
        return f"[KALI SKILLS] Error: {e}"

def _kali_skill_get(args: dict) -> str:
    tool_name = args.get("tool_name", "")
    try:
        from kali_skills_auto import eidos_get_tool_info
        return eidos_get_tool_info(tool_name)
    except Exception as e:
        return f"[KALI SKILLS] Error: {e}"

TOOL_IMPL["kali_skill_search"] = _kali_skill_search
TOOL_IMPL["kali_skill_get"] = _kali_skill_get
print("\033[95m[KALI SKILLS] 6660+ Skills listos en RAM — ACTIVAS\033[0m")

# ── Computer Use (Visión, Teclado, Ratón) ─────────────────────────────────────

TOOLS.extend([
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": "Toma una captura de la pantalla actual y devuelve la ruta. Úsalo para ver qué hay en la pantalla antes de hacer clic o escribir.",
            "parameters": {
                "type": "object",
                "properties": {
                    "save_name": {"type": "string", "description": "Nombre de archivo opcional (ej: 'pantalla1.png')"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "click_mouse",
            "description": "Hace clic con el ratón. Puede ser en una coordenada específica (X, Y) o en la posición actual si no se pasan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Coordenada X (pantalla)"},
                    "y": {"type": "integer", "description": "Coordenada Y (pantalla)"},
                    "button": {"type": "string", "description": "Botón: 'left', 'right', 'middle' (por defecto 'left')"},
                    "clicks": {"type": "integer", "description": "Número de clics (por defecto 1)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Escribe texto usando el teclado en la ventana que tenga el foco actualmente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Texto a escribir"},
                    "press_enter": {"type": "boolean", "description": "Si es true, pulsa Enter al terminar de escribir"}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": "Pulsa una tecla especial (ej: 'enter', 'esc', 'ctrl', 'alt', 'tab', 'win', 'up', 'down').",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Nombre de la tecla"}
                },
                "required": ["key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "hotkey",
            "description": "Ejecuta un atajo de teclado combinando varias teclas (ej: ['ctrl', 'c'] o ['alt', 'tab']).",
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Lista de teclas a pulsar en orden"
                    }
                },
                "required": ["keys"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_screen",
            "description": "Envía una imagen (captura de pantalla) al motor visual VLM (Moondream) para que la interprete. Útil para que EIDOS sepa qué hay en la pantalla.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {"type": "string", "description": "Ruta de la captura (generada por take_screenshot)"},
                    "prompt": {"type": "string", "description": "Pregunta opcional (ej: '¿Dónde está el icono del navegador?')"}
                },
                "required": ["image_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "parse_ui",
            "description": "Detecta TODOS los elementos de UI en la pantalla (botones, inputs, links, iconos, checkboxes, dropdowns, tabs, menús). Devuelve una lista con tipo, texto, coordenadas y referencia (u1, u2...) para cada elemento. Usa OpenCV + OCR, mucho más rápido que VLM. Úsalo ANTES de hacer clic para saber qué hay en pantalla.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {"type": "string", "description": "Ruta a imagen PNG/JPG (opcional, si no se pasa captura pantalla actual)"},
                    "use_vlm": {"type": "boolean", "description": "Si true, usa Moondream para clasificar elementos ambiguos (más lento)"},
                    "min_confidence": {"type": "number", "description": "Confianza mínima 0.0-1.0 (default 0.4)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "click_ui_element",
            "description": "Hace clic en un elemento UI detectado por parse_ui, usando su referencia (u1, u2, etc.). Primero usa parse_ui para detectar elementos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "Referencia del elemento (ej: 'u3', 'u12')"}
                },
                "required": ["ref"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_ui_element",
            "description": "Busca un elemento UI por su texto visible. Devuelve la referencia y coordenadas. Requiere haber ejecutado parse_ui antes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Texto a buscar en los elementos (case-insensitive, match parcial)"},
                    "element_type": {"type": "string", "description": "Filtrar por tipo: button, input, link, icon, checkbox, dropdown, tab, menu_item"}
                },
                "required": ["text"]
            }
        }
    }
])


def _analyze_screen(args: dict) -> str:
    from core.gui_observer import analyze_screen
    try:
        path = args.get("image_path", "")
        prompt = args.get("prompt", "¿Qué ves en la pantalla?")
        return analyze_screen(path, prompt=prompt)
    except Exception as e:
        return f"[VLM ERROR] {e}"

def _take_screenshot(args: dict) -> str:
    try:
        from core.vision_tools import take_screenshot
        return take_screenshot(args.get("save_name", "current_screen.png"))
    except Exception as e:
        return f"[VISION ERROR] {e}"

def _click_mouse(args: dict) -> str:
    try:
        from core.vision_tools import click_mouse
        return click_mouse(x=args.get("x"), y=args.get("y"), button=args.get("button", "left"), clicks=args.get("clicks", 1))
    except Exception as e:
        return f"[MOUSE ERROR] {e}"

def _type_text(args: dict) -> str:
    try:
        from core.vision_tools import type_text
        return type_text(args.get("text", ""), press_enter=args.get("press_enter", False))
    except Exception as e:
        return f"[KEYBOARD ERROR] {e}"

def _press_key(args: dict) -> str:
    try:
        from core.vision_tools import press_key
        return press_key(args.get("key", ""))
    except Exception as e:
        return f"[KEYBOARD ERROR] {e}"

def _hotkey(args: dict) -> str:
    try:
        from core.vision_tools import hotkey
        keys = args.get("keys", [])
        return hotkey(*keys)
    except Exception as e:
        return f"[KEYBOARD ERROR] {e}"

TOOL_IMPL.update({
    "take_screenshot": _take_screenshot,
    "analyze_screen": _analyze_screen,
    "click_mouse": _click_mouse,
    "type_text": _type_text,
    "press_key": _press_key,
    "hotkey": _hotkey
})
print("\033[95m[COMPUTER USE] Visión, Ratón y Teclado — ACTIVAS\033[0m")

# ═══════════════════════════════════════════════════════════════════════════
# UI PARSER — OmniParser-inspired UI Element Detection
# ═══════════════════════════════════════════════════════════════════════════

try:
    from core.ui_parser import get_ui_parser

    def _parse_ui(args: dict) -> str:
        """Detecta elementos UI en pantalla o imagen."""
        try:
            use_vlm = args.get("use_vlm", False)
            min_conf = args.get("min_confidence", 0.4)
            parser = get_ui_parser(use_vlm=use_vlm)

            image_path = args.get("image_path")
            if image_path:
                elements = parser.parse_image(image_path, min_confidence=min_conf)
            else:
                elements = parser.parse_screen(min_confidence=min_conf)

            if not elements:
                return "[UI PARSER] No se detectaron elementos UI"

            header = f"Detectados {len(elements)} elementos ({len(parser.get_interactive())} interactivos):\n"
            return header + parser.to_text(elements)
        except Exception as e:
            return f"[UI PARSER ERROR] {e}"

    def _click_ui_element(args: dict) -> str:
        """Click en elemento UI por referencia (u1, u2, etc.)."""
        try:
            ref = args.get("ref", "")
            parser = get_ui_parser()
            el = parser.find_by_ref(ref)
            if not el:
                return f"[UI PARSER] Elemento '{ref}' no encontrado. Usa parse_ui primero."
            from core.vision_tools import click_mouse
            result = click_mouse(el.cx, el.cy)
            return f"Click en {el.type} '{el.text}' ({ref}) at ({el.cx},{el.cy}) — {result}"
        except Exception as e:
            return f"[UI PARSER ERROR] {e}"

    def _find_ui_element(args: dict) -> str:
        """Busca elemento UI por texto."""
        try:
            text = args.get("text", "")
            el_type = args.get("element_type")
            parser = get_ui_parser()
            el = parser.find_element(text, element_type=el_type)
            if not el:
                return f"[UI PARSER] No se encontró elemento con texto '{text}'"
            return f"[{el.ref}] {el.type} '{el.text}' at ({el.cx},{el.cy}) {el.w}x{el.h} conf={el.confidence:.2f}"
        except Exception as e:
            return f"[UI PARSER ERROR] {e}"

    TOOL_IMPL.update({
        "parse_ui": _parse_ui,
        "click_ui_element": _click_ui_element,
        "find_ui_element": _find_ui_element,
    })
    print("\033[95m[UI PARSER] OmniParser-style UI detection — ACTIVO\033[0m")

except ImportError:
    print("\033[93m[UI PARSER] No disponible (faltan dependencias cv2/pytesseract)\033[0m")

# ═══════════════════════════════════════════════════════════════════════════
# PURPLE TEAM ARSENAL — Red + Blue + Purple Team Security Tools
# ═══════════════════════════════════════════════════════════════════════════

try:
    from core.purple_team_arsenal import purple_team, security_scan

    def _purple_scan(args: dict) -> str:
        """Scan de seguridad (nmap, etc.)"""
        try:
            target = args.get("target", "127.0.0.1")
            scan_type = args.get("scan_type", "quick")
            return security_scan(target, scan_type)
        except Exception as e:
            return f"[PURPLE TEAM ERROR] {e}"

    def _purple_web_scan(args: dict) -> str:
        """Scan de aplicación web (gobuster, sqlmap)"""
        try:
            url = args.get("url")
            if not url:
                return "[ERROR] URL requerida"

            results = []

            # Gobuster
            wordlist = args.get("wordlist", "/usr/share/wordlists/dirb/common.txt")
            if os.path.exists(wordlist):
                result = purple_team.gobuster_dir(url, wordlist=wordlist)
                results.append(f"Gobuster: {len(result.findings)} paths encontrados")

            # SQLMap (solo si se solicita explícitamente)
            if args.get("test_sql", False):
                result = purple_team.sqlmap_test(url)
                results.append(f"SQLMap: {'Vulnerable' if result.findings else 'No vulnerable'}")

            return "\n".join(results)
        except Exception as e:
            return f"[WEB SCAN ERROR] {e}"

    def _purple_audit(args: dict) -> str:
        """Auditoría de hardening del sistema (Lynis)"""
        try:
            result = purple_team.lynis_audit()
            return f"Lynis Audit: {len(result.findings)} recomendaciones de hardening"
        except Exception as e:
            return f"[AUDIT ERROR] {e}"

    def _purple_malware_scan(args: dict) -> str:
        """Scan de malware (ClamAV)"""
        try:
            path = args.get("path", "/tmp")
            result = purple_team.clamav_scan(path)
            return f"ClamAV: {len(result.findings)} amenazas detectadas en {path}"
        except Exception as e:
            return f"[MALWARE SCAN ERROR] {e}"

    def _purple_full_assessment(args: dict) -> str:
        """Evaluación Purple Team completa"""
        try:
            target = args.get("target", "localhost")
            offensive = args.get("offensive", False)
            defensive = args.get("defensive", True)

            results = purple_team.full_purple_team_assessment(
                target,
                include_offensive=offensive,
                include_defensive=defensive
            )

            summary = results["summary"]
            return (
                f"Purple Team Assessment completado:\n"
                f"  Total findings: {summary['total_findings']}\n"
                f"  Critical: {summary['critical_findings']}\n"
                f"  Tools used: {summary['tools_used']}"
            )
        except Exception as e:
            return f"[PURPLE ASSESSMENT ERROR] {e}"

    TOOL_IMPL.update({
        "security_scan": _purple_scan,
        "web_vuln_scan": _purple_web_scan,
        "system_audit": _purple_audit,
        "malware_scan": _purple_malware_scan,
        "purple_team_assessment": _purple_full_assessment
    })

    print("\033[95m[PURPLE TEAM] Arsenal de Seguridad — ACTIVO\033[0m")
    print(f"   Tools disponibles: {len(purple_team.available_tools)}")

except ImportError as e:
    print(f"\033[90m[PURPLE TEAM] No disponible ({e})\033[0m")

# Alias for backward compatibility

# Alias for backward compatibility
def get_tools():
    return TOOLS
