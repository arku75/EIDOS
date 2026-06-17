"""
core/eidos_english.py — COMPRENSIÓN DE INGLÉS para EIDOS. S125-K.

SER: "le estamos enseñando a loguearse en labex e iniciar su primer laboratorio
pero es todo en ingles y no sabemos si eidos sabe o no ese idioma y si no lo sabe
habra que añadirselo todo el diccionario y demas como cuando das clases en la escuela
sujeto predicado y asi todo lo demas mas pulido avanzado para que entienda que significa
que hacer donde hacer clik en la pantalla y todo lo demas el solito razonar"

Este módulo enseña a EIDOS INGLÉS DE VERDAD:
  1. GRAMÁTICA: sujeto, predicado (verbo), objeto, complementos.
  2. VOCABULARIO UI/ACCIÓN: verbos de acción (click, type, press, select, navigate...),
     sustantivos de interfaz (button, link, field, terminal, window, tab...),
     adjetivos posicionales (left, right, top, bottom, green, blue...).
  3. PARSEO DE INSTRUCCIONES: dado un texto en inglés de una página web (labex, docs,
     tutorial...), extrae QUÉ hay que HACER (acción), SOBRE QUÉ (objetivo), y CÓMO.
  4. DICCIONARIO PERSISTENTE: cada palabra/expresión nueva → nodo en el grafo con su
     significado en español, categoría gramatical, y ejemplos de uso.
  5. RAZONAMIENTO: usa DeepSeek para comprender instrucciones complejas, pero primero
     intenta resolver con gramática local (rápido, sin API).

Arquitectura:
  - GrammarEngine: análisis sintáctico local (regex + diccionarios)
  - ActionLexicon: vocabulario de acciones UI mapeadas a funciones EIDOS
  - InstructionParser: extrae pasos accionables de texto web
  - EnglishDictionary: persistencia en grafo del vocabulario aprendido

Ligero: no depende de NLTK/spaCy. Usa regex + diccionarios + DeepSeek como fallback.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.english")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"

# ═══════════════════════════════════════════════════════════════════════════════
# GRAMMAR ENGINE — análisis sintáctico local
# ═══════════════════════════════════════════════════════════════════════════════

# Partes de la oración
POS = {
    # Pronombres personales (sujeto)
    "PRONOUN_SUBJECT": [
        "i", "you", "he", "she", "it", "we", "they",
    ],
    # Pronombres objeto
    "PRONOUN_OBJECT": [
        "me", "him", "her", "us", "them", "it",
    ],
    # Artículos
    "ARTICLE": ["a", "an", "the"],
    # Preposiciones comunes
    "PREPOSITION": [
        "in", "on", "at", "to", "from", "with", "without", "by", "for",
        "about", "into", "onto", "upon", "within", "through", "over", "under",
        "above", "below", "between", "among", "after", "before", "during",
    ],
    # Conjunciones
    "CONJUNCTION": ["and", "or", "but", "if", "then", "when", "while", "because", "so"],
    # Adjetivos posicionales/visuales
    "ADJECTIVE_POSITIONAL": [
        "left", "right", "top", "bottom", "upper", "lower", "center", "middle",
        "first", "last", "next", "previous", "above", "below",
    ],
    "ADJECTIVE_VISUAL": [
        "green", "blue", "red", "yellow", "white", "black", "gray", "grey",
        "orange", "purple", "dark", "light", "bright",
    ],
}

# VERBOS DE ACCIÓN — el corazón del entendimiento UI de EIDOS
# Cada verbo mapea a una acción concreta que EIDOS puede ejecutar
ACTION_VERBS: Dict[str, Dict[str, Any]] = {
    # ── Navegación ──
    "click": {
        "action": "click", "target": "element", "spanish": "hacer clic",
        "example": "Click the Start Learning button",
        "params": ["what_to_click"],
    },
    "press": {
        "action": "press_key", "target": "key", "spanish": "pulsar tecla",
        "example": "Press Enter to continue",
        "params": ["key"],
    },
    "type": {
        "action": "type_text", "target": "field", "spanish": "escribir texto",
        "example": "Type nmap -sS 192.168.1.1 in the terminal",
        "params": ["text", "where"],
    },
    "enter": {
        "action": "type_text+enter", "target": "terminal", "spanish": "escribir y ejecutar",
        "example": "Enter the command and press Enter",
        "params": ["command"],
    },
    "navigate": {
        "action": "goto_url", "target": "url", "spanish": "navegar a",
        "example": "Navigate to https://example.com",
        "params": ["url"],
    },
    "open": {
        "action": "open", "target": "app_or_url", "spanish": "abrir",
        "example": "Open the terminal",
        "params": ["what"],
    },
    "select": {
        "action": "select", "target": "option", "spanish": "seleccionar",
        "example": "Select the Kali Linux option from the dropdown",
        "params": ["option", "from_where"],
    },
    "scroll": {
        "action": "scroll", "target": "page", "spanish": "desplazar",
        "example": "Scroll down to see the results",
        "params": ["direction", "amount"],
    },
    "switch": {
        "action": "switch_to", "target": "tab_or_window", "spanish": "cambiar a",
        "example": "Switch to the lab tab",
        "params": ["what"],
    },
    # ── Terminal / comandos ──
    "run": {
        "action": "run_command", "target": "command", "spanish": "ejecutar comando",
        "example": "Run nmap -sV target.com",
        "params": ["command"],
    },
    "execute": {
        "action": "run_command", "target": "command", "spanish": "ejecutar",
        "example": "Execute the following command",
        "params": ["command"],
    },
    # ── Observación ──
    "check": {
        "action": "verify", "target": "condition", "spanish": "verificar/comprobar",
        "example": "Check that the output shows open ports",
        "params": ["what"],
    },
    "verify": {
        "action": "verify", "target": "condition", "spanish": "verificar",
        "example": "Verify the Kali version",
        "params": ["what"],
    },
    "look": {
        "action": "observe", "target": "element", "spanish": "mirar/observar",
        "example": "Look at the output",
        "params": ["what"],
    },
    "find": {
        "action": "find_element", "target": "element", "spanish": "encontrar/buscar",
        "example": "Find the terminal window",
        "params": ["what"],
    },
    "read": {
        "action": "read_text", "target": "content", "spanish": "leer",
        "example": "Read the instructions carefully",
        "params": ["what"],
    },
    # ── Acciones de sistema ──
    "create": {
        "action": "create", "target": "file_or_dir", "spanish": "crear",
        "example": "Create a directory called tools",
        "params": ["what", "name"],
    },
    "copy": {
        "action": "copy", "target": "file_or_text", "spanish": "copiar",
        "example": "Copy the file to /tmp",
        "params": ["what", "where"],
    },
    "move": {
        "action": "move", "target": "file", "spanish": "mover",
        "example": "Move the script to /usr/local/bin",
        "params": ["what", "where"],
    },
    "delete": {
        "action": "delete", "target": "file", "spanish": "eliminar",
        "example": "Delete the temporary file",
        "params": ["what"],
    },
    "install": {
        "action": "install", "target": "package", "spanish": "instalar",
        "example": "Install nmap using apt",
        "params": ["what", "how"],
    },
    "download": {
        "action": "download", "target": "file", "spanish": "descargar",
        "example": "Download the script from the URL",
        "params": ["what", "from_where"],
    },
    # ── Espera ──
    "wait": {
        "action": "wait", "target": "time_or_condition", "spanish": "esperar",
        "example": "Wait for the VM to start",
        "params": ["what", "seconds"],
    },
    "start": {
        "action": "start", "target": "service_or_process", "spanish": "iniciar/arrancar",
        "example": "Start the lab environment",
        "params": ["what"],
    },
    "stop": {
        "action": "stop", "target": "service_or_process", "spanish": "parar/detener",
        "example": "Stop the running service",
        "params": ["what"],
    },
}

# SUSTANTIVOS DE INTERFAZ — qué cosas hay en una pantalla
UI_NOUNS: Dict[str, Dict[str, str]] = {
    "button": {"spanish": "botón", "category": "elemento_clicable"},
    "link": {"spanish": "enlace", "category": "navegación"},
    "field": {"spanish": "campo de texto", "category": "entrada"},
    "input": {"spanish": "entrada/campo", "category": "entrada"},
    "textbox": {"spanish": "caja de texto", "category": "entrada"},
    "terminal": {"spanish": "terminal", "category": "entrada_comandos"},
    "console": {"spanish": "consola", "category": "entrada_comandos"},
    "window": {"spanish": "ventana", "category": "contenedor"},
    "tab": {"spanish": "pestaña", "category": "navegación"},
    "menu": {"spanish": "menú", "category": "navegación"},
    "dropdown": {"spanish": "desplegable", "category": "selección"},
    "checkbox": {"spanish": "casilla", "category": "selección"},
    "icon": {"spanish": "icono", "category": "elemento_clicable"},
    "dialog": {"spanish": "diálogo/ventana emergente", "category": "contenedor"},
    "popup": {"spanish": "ventana emergente", "category": "contenedor"},
    "toolbar": {"spanish": "barra de herramientas", "category": "navegación"},
    "sidebar": {"spanish": "barra lateral", "category": "navegación"},
    "scrollbar": {"spanish": "barra de desplazamiento", "category": "navegación"},
    "label": {"spanish": "etiqueta", "category": "texto"},
    "heading": {"spanish": "título/encabezado", "category": "texto"},
    "paragraph": {"spanish": "párrafo", "category": "texto"},
    "code": {"spanish": "código", "category": "texto"},
    "command": {"spanish": "comando", "category": "terminal"},
    "output": {"spanish": "salida/resultado", "category": "texto"},
    "error": {"spanish": "error", "category": "texto"},
    "warning": {"spanish": "advertencia", "category": "texto"},
    "notification": {"spanish": "notificación", "category": "contenedor"},
    "indicator": {"spanish": "indicador", "category": "visual"},
    "cursor": {"spanish": "cursor", "category": "visual"},
    "prompt": {"spanish": "símbolo de espera ($, #, >)", "category": "terminal"},
    "login": {"spanish": "inicio de sesión", "category": "autenticación"},
    "password": {"spanish": "contraseña", "category": "autenticación"},
    "email": {"spanish": "correo electrónico", "category": "autenticación"},
    "account": {"spanish": "cuenta", "category": "autenticación"},
    "dashboard": {"spanish": "panel principal", "category": "navegación"},
    "lab": {"spanish": "laboratorio/práctica", "category": "aprendizaje"},
    "course": {"spanish": "curso", "category": "aprendizaje"},
    "lesson": {"spanish": "lección", "category": "aprendizaje"},
    "challenge": {"spanish": "desafío/reto", "category": "aprendizaje"},
    "vm": {"spanish": "máquina virtual", "category": "sistema"},
    "container": {"spanish": "contenedor", "category": "sistema"},
    "docker": {"spanish": "Docker (contenedor)", "category": "sistema"},
    "ip": {"spanish": "dirección IP", "category": "red"},
    "port": {"spanish": "puerto de red", "category": "red"},
    "network": {"spanish": "red", "category": "red"},
    "scan": {"spanish": "escaneo/análisis", "category": "seguridad"},
    "vulnerability": {"spanish": "vulnerabilidad", "category": "seguridad"},
}

# FRASES IMPERATIVAS COMUNES (patrones de instrucción)
IMPERATIVE_PATTERNS = [
    # "Click the X button"
    (r"(?i)click\s+(?:the\s+)?(.+?)\s+(?:button|link|tab|icon|menu|option)", "click"),
    # "Press X key" / "Press Enter"
    (r"(?i)press\s+(?:the\s+)?(.+?)(?:\s+key)?$", "press_key"),
    # "Type X in/into the Y"
    (r"(?i)type\s+(?:the\s+)?(.+?)\s+(?:in|into|on)\s+(?:the\s+)?(.+)$", "type_text"),
    # "Run/Execute X command"
    (r"(?i)(?:run|execute)\s+(?:the\s+)?(?:following\s+)?(?:command\s+)?[:]?\s*(.+)", "run_command"),
    # "Navigate to X"
    (r"(?i)navigate\s+to\s+(.+)", "navigate"),
    # "Open X"
    (r"(?i)open\s+(?:the\s+)?(.+)", "open"),
    # "Select X from Y"
    (r"(?i)select\s+(?:the\s+)?(.+?)\s+from\s+(.+)", "select"),
    # "Switch to X"
    (r"(?i)switch\s+to\s+(?:the\s+)?(.+)", "switch_to"),
    # "Wait for X" / "Wait X seconds"
    (r"(?i)wait\s+(?:for\s+)?(.+)", "wait"),
    # "Check X" / "Verify X"
    (r"(?i)(?:check|verify)\s+(?:that\s+)?(?:the\s+)?(.+)", "verify"),
    # "Find the X"
    (r"(?i)find\s+(?:the\s+)?(.+)", "find_element"),
    # "Scroll up/down/to X"
    (r"(?i)scroll\s+(up|down|left|right|to\s+.+)", "scroll"),
    # "Enter X" (command)
    (r"(?i)enter\s+(?:the\s+)?(?:following\s+)?(?:command\s+)?(.+)", "type_text+enter"),
    # "Create a X called/named Y"
    (r"(?i)create\s+(?:a\s+)?(.+?)\s+(?:called|named)\s+(.+)", "create"),
    # "Start the X"
    (r"(?i)start\s+(?:the\s+)?(.+)", "start"),
]

# ═══════════════════════════════════════════════════════════════════════════════
# FUNCIONES DE GRAMÁTICA LOCAL
# ═══════════════════════════════════════════════════════════════════════════════


def tokenize(text: str) -> List[str]:
    """Separa un texto en tokens (palabras), limpiando puntuación."""
    return [t.strip(".,;:!?\"'()[]{}") for t in text.lower().split() if t.strip(".,;:!?\"'()[]{}")]


def identify_pos(word: str) -> List[str]:
    """Identifica la categoría gramatical de una palabra. Devuelve lista (puede ser varias)."""
    w = word.lower()
    tags = []
    for pos_tag, words in POS.items():
        if w in words:
            tags.append(pos_tag)
    if w in ACTION_VERBS:
        tags.append("VERB_ACTION")
    if w in UI_NOUNS:
        tags.append("NOUN_UI")
    if not tags:
        tags.append("UNKNOWN")
    return tags


def parse_sentence_grammar(sentence: str) -> Dict[str, Any]:
    """
    Analiza una oración en inglés y extrae:
      - subject: de quién/que se habla
      - predicate: verbo principal + complements
      - verb: el verbo principal
      - object: sobre qué recae la acción
      - modifiers: adjetivos, adverbios, frases preposicionales
      - is_imperative: si es una orden/instrucción
      - ui_actions: lista de acciones UI detectadas

    Devuelve un dict parseado listo para que EIDOS razone.
    """
    s = sentence.strip()
    if not s:
        return {"error": "empty"}

    tokens = tokenize(s)
    if not tokens:
        return {"error": "no tokens"}

    result: Dict[str, Any] = {
        "original": s,
        "tokens": tokens,
        "subject": None,
        "predicate": None,
        "verb": None,
        "verb_spanish": None,
        "object": None,
        "modifiers": [],
        "prepositional_phrases": [],
        "is_imperative": False,
        "ui_actions": [],
    }

    # ── Detectar si es imperativo (orden/instrucción) ──
    # Patrón 1: empieza por verbo en imperativo (sin sujeto)
    first_word = tokens[0].lower()
    if first_word in ACTION_VERBS:
        result["is_imperative"] = True
        result["verb"] = first_word
        result["verb_spanish"] = ACTION_VERBS[first_word]["spanish"]
        result["subject"] = "you (implied)"

    # Patrón 2: "You should/must/can X" → imperativo suavizado
    modal_imperative = re.match(r"(?i)you\s+(should|must|need to|have to|can|will)\s+(.+)", s)
    if modal_imperative:
        result["is_imperative"] = True
        result["subject"] = "you"
        rest = modal_imperative.group(2)
        verb_match = re.match(r"(\w+)", rest)
        if verb_match and verb_match.group(1).lower() in ACTION_VERBS:
            result["verb"] = verb_match.group(1).lower()
            result["verb_spanish"] = ACTION_VERBS[result["verb"]]["spanish"]

    # Patrón 3: "Let's X" / "Let us X"
    if re.match(r"(?i)let'?s?\s+", s):
        result["is_imperative"] = True
        result["subject"] = "we"

    # ── Buscar el verbo principal si no se encontró ──
    if not result["verb"]:
        for i, tok in enumerate(tokens):
            if tok in ACTION_VERBS:
                result["verb"] = tok
                result["verb_spanish"] = ACTION_VERBS[tok]["spanish"]
                if i > 0:
                    result["subject"] = " ".join(tokens[:i])
                break
        # Si no es verbo de acción, buscar verbos comunes (to be, to have, etc.)
        if not result["verb"]:
            common_verbs = ["is", "are", "was", "were", "be", "have", "has", "had",
                          "do", "does", "did", "will", "would", "can", "could",
                          "show", "display", "contain", "include", "provide", "allow",
                          "use", "make", "get", "set", "go", "see"]
            for tok in tokens:
                if tok in common_verbs:
                    result["verb"] = tok
                    break

    # ── Extraer objeto (lo que sigue al verbo) ──
    if result["verb"]:
        try:
            v_idx = tokens.index(result["verb"])
            after_verb = tokens[v_idx + 1:]
            if after_verb:
                # Separar objeto de preposiciones
                obj_parts = []
                for tok in after_verb:
                    if tok in POS.get("PREPOSITION", []):
                        break
                    if tok not in POS.get("ARTICLE", []):
                        obj_parts.append(tok)
                if obj_parts:
                    result["object"] = " ".join(obj_parts)
                # Frases preposicionales
                for i, tok in enumerate(after_verb):
                    if tok in POS.get("PREPOSITION", []) and i + 1 < len(after_verb):
                        pp_end = i + 1
                        while pp_end < len(after_verb) and after_verb[pp_end] not in POS.get("PREPOSITION", []):
                            pp_end += 1
                        result["prepositional_phrases"].append(
                            f"{tok} {' '.join(after_verb[i+1:pp_end])}")
        except ValueError:
            pass

    # ── Clasificar tokens por categoría ──
    for tok in tokens:
        tags = identify_pos(tok)
        if "ADJECTIVE_POSITIONAL" in tags or "ADJECTIVE_VISUAL" in tags:
            result["modifiers"].append({"word": tok, "type": "adjective", "subtype": tags[0]})

    # ── Detectar acciones UI usando patrones regex ──
    for pattern, action_type in IMPERATIVE_PATTERNS:
        m = re.search(pattern, s)
        if m:
            groups = list(m.groups())
            result["ui_actions"].append({
                "type": action_type,
                "params": groups,
                "verb_info": ACTION_VERBS.get(result["verb"] or "", {}),
            })

    # ── Si no se detectó acción por patrón pero hay verbo de acción ──
    if not result["ui_actions"] and result["verb"] in ACTION_VERBS:
        result["ui_actions"].append({
            "type": ACTION_VERBS[result["verb"]]["action"],
            "params": [result["object"]] if result["object"] else [],
            "verb_info": ACTION_VERBS[result["verb"]],
        })

    return result


def parse_instructions_page(text: str) -> List[Dict[str, Any]]:
    """
    Toma el texto COMPLETO de una página (labex, tutorial, etc.) y extrae
    TODAS las instrucciones accionables que EIDOS debe ejecutar, en orden.

    Cada instrucción = un paso concreto: qué hacer, sobre qué, cómo.
    """
    steps: List[Dict[str, Any]] = []

    # 1. Extraer bloques de código (comandos a ejecutar)
    code_patterns = [
        r'`([^`]+)`',                          # inline code
        r'<code>([^<]+)</code>',               # HTML code
        r'<pre>([^<]+)</pre>',                 # HTML pre
        r'\$\s*(.+?)(?:\n|$)',                 # $ command prompt
        r'#\s*(.+?)(?:\n|$)',                  # # command prompt (root)
    ]
    for pat in code_patterns:
        for m in re.finditer(pat, text, re.MULTILINE):
            cmd = m.group(1).strip()
            if 3 <= len(cmd) <= 200 and not cmd.startswith(("//", "<!--")):
                steps.append({
                    "type": "command",
                    "command": cmd,
                    "raw_match": m.group(0)[:100],
                })

    # 2. Extraer pasos numerados (Step 1, 1., Task 1, etc.)
    step_patterns = [
        r'(?i)(?:step|task|exercise)\s*(\d+)[:.]?\s*(.+?)(?=(?:step|task|exercise)\s*\d+|$)',
        r'(?i)^(\d+)[.)]\s*(.+?)(?=^\d+[.)]|\Z)',
    ]
    for pat in step_patterns:
        for m in re.finditer(pat, text, re.MULTILINE | re.DOTALL):
            step_num = m.group(1)
            step_text = m.group(2).strip()[:500]
            parsed = parse_sentence_grammar(step_text)
            steps.append({
                "type": "step",
                "number": int(step_num),
                "text": step_text,
                "parsed": parsed,
            })

    # 3. Detectar instrucciones sueltas (líneas imperativas)
    for line in text.split("\n"):
        line = line.strip()
        if len(line) < 10 or len(line) > 500:
            continue
        parsed = parse_sentence_grammar(line)
        if parsed.get("is_imperative") and parsed.get("ui_actions"):
            # Solo añadir si no está ya capturada
            if not any(s.get("text") == line for s in steps):
                steps.append({
                    "type": "instruction",
                    "text": line,
                    "parsed": parsed,
                })

    return steps


# ═══════════════════════════════════════════════════════════════════════════════
# DICCIONARIO PERSISTENTE (grafo)
# ═══════════════════════════════════════════════════════════════════════════════


def _conn():
    from core.db import get_conn
    c = get_conn(BRAIN_DB, timeout=20)
    c.execute("""CREATE TABLE IF NOT EXISTS english_vocabulary(
        word TEXT PRIMARY KEY, spanish TEXT, pos TEXT, category TEXT,
        examples TEXT, confidence REAL, source TEXT, learned_at TEXT)""")
    return c


def learn_word(word: str, spanish: str, pos: str = "unknown",
               category: str = "general", example: str = "",
               confidence: float = 0.85, source: str = "eidos_english") -> bool:
    """
    EIDOS aprende una palabra inglesa NUEVA y la persiste en su diccionario.
    Si ya la sabe, refuerza la confianza.
    """
    try:
        c = _conn()
        w = word.lower().strip()
        existing = c.execute(
            "SELECT confidence, examples FROM english_vocabulary WHERE word=?", (w,)).fetchone()
        if existing:
            new_conf = min(0.99, float(existing[0] or 0.5) + 0.02)
            new_examples = existing[1] or ""
            if example and example not in new_examples:
                new_examples = f"{new_examples}; {example}" if new_examples else example
            c.execute(
                "UPDATE english_vocabulary SET confidence=?, examples=?, learned_at=? WHERE word=?",
                (new_conf, new_examples[:2000],
                 time.strftime("%Y-%m-%d %H:%M:%S"), w))
        else:
            c.execute(
                "INSERT INTO english_vocabulary "
                "(word, spanish, pos, category, examples, confidence, source, learned_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (w, spanish, pos, category, example[:500] if example else "",
                 confidence, source, time.strftime("%Y-%m-%d %H:%M:%S")))
        c.commit()
        return True
    except Exception as e:
        log.debug("learn_word: %s", e)
        return False


def translate_word(word: str) -> Optional[Dict[str, Any]]:
    """Busca una palabra en el diccionario de EIDOS. None si no la conoce."""
    try:
        c = _conn()
        row = c.execute(
            "SELECT word, spanish, pos, category, examples, confidence "
            "FROM english_vocabulary WHERE word=?", (word.lower().strip(),)).fetchone()
        if row:
            return {
                "word": row[0], "spanish": row[1], "pos": row[2],
                "category": row[3], "examples": row[4], "confidence": row[5],
            }
        return None
    except Exception as e:
        log.debug("translate_word: %s", e)
        return None


def translate_text(text: str) -> str:
    """
    Traduce palabra por palabra usando el diccionario de EIDOS.
    Las palabras que no conoce las deja en inglés con [?].
    Para traducción real de frases, usar comprehend_with_deepseek().
    """
    words = tokenize(text)
    result = []
    for w in words:
        t = translate_word(w)
        if t:
            result.append(t["spanish"])
        elif w.lower() in UI_NOUNS:
            result.append(UI_NOUNS[w.lower()]["spanish"])
        elif w.lower() in ACTION_VERBS:
            result.append(ACTION_VERBS[w.lower()]["spanish"])
        else:
            result.append(f"{w}[?]")
    return " ".join(result)


def vocabulary_stats() -> Dict[str, int]:
    """Estadísticas del diccionario de EIDOS."""
    try:
        c = _conn()
        total = c.execute("SELECT COUNT(*) FROM english_vocabulary").fetchone()[0]
        by_cat = {}
        for row in c.execute(
            "SELECT category, COUNT(*) FROM english_vocabulary GROUP BY category").fetchall():
            by_cat[row[0]] = row[1]
        return {"total_words": total, "by_category": by_cat}
    except Exception:
        return {"total_words": 0, "by_category": {}}


# ═══════════════════════════════════════════════════════════════════════════════
# SEMBRAR VOCABULARIO BÁSICO (primera vez)
# ═══════════════════════════════════════════════════════════════════════════════

def seed_basic_vocabulary() -> int:
    """
    Siembra el vocabulario inglés FUNDAMENTAL que EIDOS necesita para entender
    páginas web técnicas y labs. Solo si el diccionario está vacío.
    Devuelve el número de palabras sembradas.
    """
    stats = vocabulary_stats()
    if stats["total_words"] > 20:
        return 0  # ya tiene vocabulario

    # Vocabulario esencial: palabra → (español, categoría gramatical, categoría)
    essential = [
        # ── Verbos de acción (UI) ──
        ("click", "hacer clic", "verb", "ui_action"),
        ("press", "pulsar/oprimir", "verb", "ui_action"),
        ("type", "escribir/teclear", "verb", "ui_action"),
        ("enter", "ingresar/introducir", "verb", "ui_action"),
        ("select", "seleccionar/elegir", "verb", "ui_action"),
        ("choose", "elegir/escoger", "verb", "ui_action"),
        ("navigate", "navegar/ir a", "verb", "ui_navigation"),
        ("open", "abrir", "verb", "ui_action"),
        ("close", "cerrar", "verb", "ui_action"),
        ("start", "iniciar/comenzar", "verb", "ui_action"),
        ("stop", "detener/parar", "verb", "ui_action"),
        ("run", "ejecutar/correr", "verb", "terminal"),
        ("execute", "ejecutar", "verb", "terminal"),
        ("wait", "esperar/aguardar", "verb", "ui_action"),
        ("check", "verificar/comprobar", "verb", "ui_action"),
        ("verify", "verificar/confirmar", "verb", "ui_action"),
        ("find", "encontrar/buscar", "verb", "ui_action"),
        ("search", "buscar", "verb", "ui_action"),
        ("read", "leer", "verb", "ui_action"),
        ("scroll", "desplazar", "verb", "ui_action"),
        ("switch", "cambiar/alternar", "verb", "ui_navigation"),
        ("toggle", "alternar/cambiar", "verb", "ui_action"),
        ("create", "crear", "verb", "system"),
        ("delete", "eliminar/borrar", "verb", "system"),
        ("copy", "copiar", "verb", "system"),
        ("move", "mover", "verb", "system"),
        ("download", "descargar", "verb", "system"),
        ("install", "instalar", "verb", "system"),
        ("configure", "configurar", "verb", "system"),
        ("enable", "activar/habilitar", "verb", "system"),
        ("disable", "desactivar/deshabilitar", "verb", "system"),
        ("restart", "reiniciar", "verb", "system"),
        # ── Sustantivos de UI ──
        ("button", "botón", "noun", "ui_element"),
        ("link", "enlace/vínculo", "noun", "ui_element"),
        ("field", "campo", "noun", "ui_element"),
        ("input", "entrada/campo de texto", "noun", "ui_element"),
        ("textbox", "caja de texto", "noun", "ui_element"),
        ("terminal", "terminal/consola", "noun", "ui_element"),
        ("console", "consola", "noun", "ui_element"),
        ("window", "ventana", "noun", "ui_container"),
        ("tab", "pestaña", "noun", "ui_navigation"),
        ("menu", "menú", "noun", "ui_navigation"),
        ("dropdown", "desplegable/lista", "noun", "ui_element"),
        ("checkbox", "casilla de verificación", "noun", "ui_element"),
        ("icon", "icono", "noun", "ui_element"),
        ("dialog", "cuadro de diálogo", "noun", "ui_container"),
        ("popup", "ventana emergente", "noun", "ui_container"),
        ("sidebar", "barra lateral", "noun", "ui_navigation"),
        ("toolbar", "barra de herramientas", "noun", "ui_navigation"),
        ("scrollbar", "barra de desplazamiento", "noun", "ui_navigation"),
        ("label", "etiqueta/rótulo", "noun", "ui_text"),
        ("heading", "encabezado/título", "noun", "ui_text"),
        ("paragraph", "párrafo", "noun", "ui_text"),
        ("code", "código", "noun", "ui_text"),
        ("command", "comando/orden", "noun", "terminal"),
        ("output", "salida/resultado", "noun", "ui_text"),
        ("error", "error", "noun", "ui_text"),
        ("warning", "advertencia/aviso", "noun", "ui_text"),
        ("notification", "notificación/aviso", "noun", "ui_text"),
        # ── Computación / Redes ──
        ("login", "inicio de sesión", "noun", "auth"),
        ("password", "contraseña", "noun", "auth"),
        ("email", "correo electrónico", "noun", "auth"),
        ("account", "cuenta", "noun", "auth"),
        ("dashboard", "panel/tablero", "noun", "ui_navigation"),
        ("lab", "laboratorio/práctica", "noun", "learning"),
        ("course", "curso", "noun", "learning"),
        ("lesson", "lección", "noun", "learning"),
        ("tutorial", "tutorial/guía", "noun", "learning"),
        ("challenge", "desafío/reto", "noun", "learning"),
        ("vm", "máquina virtual", "noun", "system"),
        ("container", "contenedor", "noun", "system"),
        ("docker", "Docker", "noun", "system"),
        ("ip", "dirección IP", "noun", "networking"),
        ("port", "puerto", "noun", "networking"),
        ("network", "red", "noun", "networking"),
        ("protocol", "protocolo", "noun", "networking"),
        ("scan", "escaneo/análisis", "noun", "security"),
        ("firewall", "cortafuegos", "noun", "security"),
        ("vulnerability", "vulnerabilidad", "noun", "security"),
        ("exploit", "explotar/aprovechar (vulnerabilidad)", "noun", "security"),
        ("payload", "carga útil", "noun", "security"),
        ("root", "superusuario/raíz", "noun", "system"),
        ("shell", "intérprete de comandos", "noun", "terminal"),
        ("script", "script/programa", "noun", "system"),
        ("file", "archivo", "noun", "system"),
        ("directory", "directorio/carpeta", "noun", "system"),
        ("folder", "carpeta", "noun", "system"),
        ("path", "ruta/trayectoria", "noun", "system"),
        ("permission", "permiso", "noun", "system"),
        ("owner", "propietario/dueño", "noun", "system"),
        ("process", "proceso", "noun", "system"),
        ("service", "servicio", "noun", "system"),
        ("daemon", "demonio (servicio de fondo)", "noun", "system"),
        ("package", "paquete", "noun", "system"),
        ("repository", "repositorio", "noun", "system"),
        ("kernel", "núcleo del sistema", "noun", "system"),
        # ── Adjetivos / Adverbios ──
        ("left", "izquierda", "adjective", "position"),
        ("right", "derecha", "adjective", "position"),
        ("top", "arriba/superior", "adjective", "position"),
        ("bottom", "abajo/inferior", "adjective", "position"),
        ("first", "primero", "adjective", "ordinal"),
        ("last", "último", "adjective", "ordinal"),
        ("next", "siguiente/próximo", "adjective", "ordinal"),
        ("previous", "anterior/previo", "adjective", "ordinal"),
        ("green", "verde", "adjective", "color"),
        ("red", "rojo", "adjective", "color"),
        ("blue", "azul", "adjective", "color"),
        ("currently", "actualmente", "adverb", "time"),
        ("always", "siempre", "adverb", "frequency"),
        ("never", "nunca", "adverb", "frequency"),
        # ── Frases clave de labs/instrucciones ──
        ("start learning", "comenzar a aprender/iniciar laboratorio", "phrase", "ui_action"),
        ("continue with google", "continuar con Google", "phrase", "auth"),
        ("sign in", "iniciar sesión", "phrase", "auth"),
        ("log in", "iniciar sesión", "phrase", "auth"),
        ("sign out", "cerrar sesión", "phrase", "auth"),
        ("log out", "cerrar sesión", "phrase", "auth"),
        ("sign up", "registrarse", "phrase", "auth"),
        ("get started", "comenzar/empezar", "phrase", "ui_action"),
        ("try again", "intentar de nuevo", "phrase", "ui_action"),
        ("go back", "volver/retroceder", "phrase", "ui_navigation"),
        ("set up", "configurar/instalar", "phrase", "system"),
    ]

    count = 0
    for word, spanish, pos, category in essential:
        if learn_word(word, spanish, pos, category, source="seed_basic"):
            count += 1

    log.info("📚 vocabulario inglés sembrado: %d palabras", count)
    return count


# ═══════════════════════════════════════════════════════════════════════════════
# COMPRENSIÓN PROFUNDA CON DeepSeek (para frases complejas)
# ═══════════════════════════════════════════════════════════════════════════════

def comprehend_with_deepseek(text: str, context: str = "") -> Dict[str, Any]:
    """
    Usa DeepSeek para COMPRENDER de verdad un texto en inglés.
    No solo traduce — razona: qué significa, qué hay que hacer, dónde clicar.

    Devuelve un dict con:
      - spanish_summary: resumen en español
      - what_it_means: qué significa realmente
      - what_to_do: acciones concretas a ejecutar
      - where_to_click: elementos de UI mencionados
      - commands: comandos a ejecutar
      - key_vocabulary: palabras clave que EIDOS debería aprender
    """
    prompt = (
        "Eres EIDOS aprendiendo inglés. Analiza este texto de una página web/lab "
        "y responde en español con este formato JSON exacto:\n"
        "{\n"
        '  "spanish_summary": "resumen de qué trata en 1-2 frases",\n'
        '  "what_it_means": "explicación clara de qué significa esto",\n'
        '  "what_to_do": ["acción 1", "acción 2", ...],\n'
        '  "where_to_click": ["elemento UI 1", ...],\n'
        '  "commands_to_run": ["comando1", ...],\n'
        '  "key_vocabulary": [{"word": "english", "spanish": "español", "pos": "verb/noun/adj"}, ...]\n'
        "}\n\n"
        f"Contexto (dónde está EIDOS): {context}\n\n"
        f"Texto a comprender:\n{text[:2000]}"
    )

    try:
        from core.eidos_learn import _call_deepseek
        result = _call_deepseek(prompt, timeout=45, temperature=0.2)
        if not result:
            return {"error": "DeepSeek no respondió", "raw_text": text[:500]}

        # Intentar parsear JSON de la respuesta
        # Primero, extraer el bloque JSON
        json_match = re.search(r'\{[\s\S]*\}', result)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        # Si no hay JSON, devolver la respuesta como texto
        return {
            "spanish_summary": result[:500],
            "what_it_means": result[:500],
            "what_to_do": [],
            "where_to_click": [],
            "commands_to_run": [],
            "key_vocabulary": [],
            "raw_response": result[:1000],
        }
    except Exception as e:
        log.debug("comprehend_with_deepseek: %s", e)
        return {"error": str(e), "raw_text": text[:500]}


def learn_from_page(title: str, english_text: str, url: str = "") -> Dict[str, Any]:
    """
    EIDOS LEE una página en inglés y APRENDE de ella:
      1. Parseo gramatical local (rápido) → acciones detectadas
      2. Comprensión profunda con DeepSeek → razonamiento
      3. Extrae vocabulario nuevo y lo aprende
      4. Devuelve todo lo que entendió
    """
    result = {
        "title": title,
        "url": url,
        "grammar_parse": parse_sentence_grammar(english_text[:500]),
        "instructions_found": parse_instructions_page(english_text),
        "deep_comprehension": None,
        "new_words_learned": 0,
    }

    # Comprensión profunda con DeepSeek
    comprehension = comprehend_with_deepseek(english_text, f"Página: {title} | URL: {url}")
    result["deep_comprehension"] = comprehension

    # Aprender vocabulario nuevo detectado por DeepSeek
    if "key_vocabulary" in comprehension:
        for item in comprehension["key_vocabulary"]:
            if isinstance(item, dict) and "word" in item:
                if learn_word(
                    item["word"],
                    item.get("spanish", ""),
                    item.get("pos", "unknown"),
                    category="from_deepseek",
                    example=english_text[:200],
                ):
                    result["new_words_learned"] += 1

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-TEST
# ═══════════════════════════════════════════════════════════════════════════════

def _test():
    """Pruebas rápidas de gramática y comprensión."""
    print("=== TEST eidos_english ===\n")

    # Test 1: tokenize
    assert tokenize("Click the Start button") == ["click", "the", "start", "button"]
    print("✓ tokenize")

    # Test 2: identify_pos
    tags = identify_pos("click")
    assert "VERB_ACTION" in tags, f"click debería ser VERB_ACTION: {tags}"
    print("✓ identify_pos (click=VERB_ACTION)")

    # Test 3: parse_sentence_grammar - imperativo
    r = parse_sentence_grammar("Click the Start Learning button")
    assert r["is_imperative"], f"Debería ser imperativo: {r}"
    assert r["verb"] == "click", f"Verbo debería ser click: {r}"
    print(f"✓ parse imperativo: verb={r['verb']}, object={r['object']}")

    # Test 4: parse_sentence_grammar - comando terminal
    r = parse_sentence_grammar("Run nmap -sS 172.17.0.1")
    assert r["verb"] == "run", f"Verbo: {r}"
    print(f"✓ parse comando: verb={r['verb']}, object={r['object']}")

    # Test 5: parse_instructions_page
    sample = """Step 1: Click the terminal window.
    Run the following command: nmap -sV target.com
    Check the output for open ports.
    Step 2: Type `whoami` and press Enter."""
    steps = parse_instructions_page(sample)
    assert len(steps) >= 2, f"Debería encontrar >=2 pasos: {len(steps)}"
    print(f"✓ parse_instructions_page: {len(steps)} pasos encontrados")

    # Test 6: translate con diccionario
    seed_basic_vocabulary()
    t = translate_word("click")
    assert t and t["spanish"] == "hacer clic", f"click → {t}"
    print(f"✓ translate_word: click → {t['spanish']}")

    # Test 7: UI nouns
    assert "terminal" in UI_NOUNS
    assert UI_NOUNS["terminal"]["spanish"] == "terminal/consola"
    print("✓ UI_NOUNS cargado")

    # Test 8: ACTION_VERBS
    assert "click" in ACTION_VERBS
    assert ACTION_VERBS["click"]["action"] == "click"
    print(f"✓ ACTION_VERBS: {len(ACTION_VERBS)} verbos de acción")

    print(f"\n✅ Todos los tests pasaron. Vocabulario: {vocabulary_stats()}")


if __name__ == "__main__":
    _test()
