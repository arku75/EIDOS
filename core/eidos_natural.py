"""
eidos_natural.py — Respuesta natural desde el brain de EIDOS sin Ollama.

Permite que EIDOS responda conversacionalmente en <500ms para:
  - Saludos y conversación casual
  - Preguntas de identidad
  - Estado del sistema
  - Preguntas factuales con nodos en brain
  - Session memory (recuerda la sesión actual en RAM)

Integración en colony_community.py → _try_knowledge_first():
    from core.eidos_natural import get_natural
    resp = get_natural().respond(message, session_id)
    if resp:
        return resp   # no ir a Ollama
"""
from __future__ import annotations

import re
import time
from core.db import get_conn
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional
from collections import deque

log = logging.getLogger("eidos_natural")

# ── Patrones de intención ─────────────────────────────────────────────────────
_GREET_PAT = re.compile(
    r'^(hola|hey|hi|buenas|buenos|buenas\s+tardes|buenas\s+noches|buenos\s+d[íi]as|'
    r'qu[eé]\s+tal|ey\s+eidos|hello|sup|wena|qu[eé]\s+pasa|'
    r'oye\s+eidos|oye|hola\s+eidos|hi\s+eidos|howdy|good\s+(morning|afternoon|evening))',
    re.IGNORECASE
)
_IDENTITY_PAT = re.compile(
    r'(qui[eé]n\s+eres|qu[eé]\s+eres|pres[eé]ntate|cu[eé]ntame\s+sobre\s+ti|'
    r'h[aá]blame\s+de\s+ti|who\s+are\s+you|what\s+are\s+you|introduce\s+yourself|'
    r'qu[eé]\s+sabes\s+de\s+ti|tell\s+me\s+about\s+(your|u|yourself)|'
    r'describe\s+yourself|about\s+(ur|your|u)\s+(os|system|environment|self))',
    re.IGNORECASE
)
_STATUS_PAT = re.compile(
    r'(c[oó]mo\s+est[aá]s|status|estado\s+(del\s+)?sistema|servicios\s+activos|'
    r'qu[eé]\s+tienes\s+activo|how\s+are\s+you|all\s+good|todo\s+bien)',
    re.IGNORECASE
)
_CAPABILITIES_PAT = re.compile(
    r'(qu[eé]\s+puedes\s+hacer|qu[eé]\s+sabes\s+hacer|capacidades|capabilities|'
    r'qu[eé]\s+sabes(?!\s+(sobre|de|acerca|del|sobre el))\b|'
    r'what\s+can\s+(you|u)\s+do|what\s+can\s+(you|u)\s+not|'
    r'what\s+u\s+can|qu[eé]\s+haces|what\s+do\s+you\s+do|'
    r'can\s+(you|u)\s+do|what\s+(can|cannot|cant|can\'t)\s+(you|u))',
    re.IGNORECASE
)
# Preguntas abiertas de exploración → dejar pasar a Ollama (no responder desde brain)
_OPEN_EXPLORATION_PAT = re.compile(
    r'(learn\s+about\s+(u|your|me|us)|tell\s+me\s+about\s+(your|u)\s+(os|env|system|world)|'
    r'(explain|describe|elaborate)\s+(how|what|why|your|u)\s|'
    r'what\s+(is|are)\s+(your|u)\s+(os|environment|world|context)|'
    r'(i\s+will\s+like|i\s+would\s+like|i\'d\s+like)\s+.*(learn|know|understand))',
    re.IGNORECASE
)
_MEMORY_PAT = re.compile(
    r'(recuerdas|te\s+acuerdas|remember|qu[eé]\s+dije|lo\s+que\s+hablamos|'
    r'antes\s+dijiste|do\s+you\s+remember)',
    re.IGNORECASE
)

# ── Session memory (RAM, por sesión) ─────────────────────────────────────────
class _SessionMem:
    def __init__(self, max_turns: int = 30):
        self._turns: deque[dict] = deque(maxlen=max_turns)
        self._start = time.time()

    def add(self, role: str, text: str) -> None:
        self._turns.append({"role": role, "text": text[:600], "ts": time.time()})

    def last_n(self, n: int = 6) -> list[dict]:
        return list(self._turns)[-n:]

    def find(self, query: str) -> Optional[str]:
        words = [w for w in query.lower().split() if len(w) > 3]
        for t in reversed(self._turns):
            if any(w in t["text"].lower() for w in words):
                return t["text"]
        return None

    @property
    def turn_count(self) -> int:
        return len(self._turns)

    @property
    def age_mins(self) -> int:
        return int((time.time() - self._start) / 60)


_sessions: dict[str, _SessionMem] = {}

def _session(sid: str) -> _SessionMem:
    if sid not in _sessions:
        _sessions[sid] = _SessionMem()
    return _sessions[sid]


# ── Núcleo ────────────────────────────────────────────────────────────────────
class EidosNatural:

    def __init__(self):
        self._db = Path.home() / ".eidos" / "evolution_brain.db"
        self._svc_cache: dict = {}
        self._svc_ts: float = 0

    # ── Búsqueda ──────────────────────────────────────────────────────────────

    def _node(self, concept: str) -> Optional[str]:
        try:
            con = get_conn(self._db, timeout=2)
            row = con.execute(
                "SELECT definition FROM knowledge_nodes WHERE concept=?", (concept,)
            ).fetchone()
            return row[0] if row else None
        except Exception:
            return None

    def _search(self, query: str, limit: int = 5) -> list[tuple[str,str]]:
        _STOP = {
            "es","son","sea","el","la","los","las","un","una","de","del","al","en","con","sin","por",
            "para","que","qué","como","cómo","cuando","donde","y","o","pero","sino",
            "aunque","porque","si","sí","no","ni","más","muy","ya","así","todo",
            "cada","otro","esto","este","esta","yo","tú","tu","él","ella","me","te",
            "se","nos","le","lo","mi","su","the","a","an","is","are","was","and",
            "or","but","not","in","on","at","to","of","for","by","with","from",
            "this","that","what","which","when","where","why","how","can","have",
            "eres","fue","era","son","sea","sido","está","estoy","estas","estamos",
            "sabes","sobre","saber","acerca","dime","dame","cuentame","cuéntame",
            "explica","explícame","puedes","sabe","conoces","tienes","hablame",
            "háblame","quiero","necesito","know","about","tell","explain","want",
        }
        # Filtro de calidad: excluir fuentes y categorías basura
        _BAD_SOURCES = ("wordnet","code_analyzer","graphify","tabula_rasa:path_scan",
                        "tabula_rasa:ast","self_index:class","self_index:function",
                        "self_index:module","kali_tools")
        _BAD_CATS = ("dictionary","synset","code_structure","eidos_function",
                     "eidos_class","eidos_module","system_command")
        kws = [
            w.strip("¿?.,;:!()\"'«»") for w in query.lower().split()
            if len(w.strip("¿?.,;:!()\"'«»")) >= 3
            and w.strip("¿?.,;:!()\"'«»") not in _STOP
        ][:6]
        if not kws:
            return []
        results, seen = [], set()
        try:
            con = get_conn(self._db, timeout=2)
            # Construir filtros SQL para excluir basura
            bad_src_sql = "','".join(_BAD_SOURCES)
            bad_cat_sql = "','".join(_BAD_CATS)
            # Primera pasada: buscar por concepto
            for kw in kws:
                for r in con.execute(
                    f"SELECT concept, definition FROM knowledge_nodes "
                    f"WHERE concept LIKE ? AND confidence >= 0.3 "
                    f"  AND concept NOT LIKE '%distilled%' "
                    f"  AND concept NOT LIKE '**%' "
                    f"  AND source NOT IN ('{bad_src_sql}') "
                    f"  AND category NOT IN ('{bad_cat_sql}') "
                    f"  AND LENGTH(definition) >= 30 "
                    f"  AND definition NOT LIKE '=== help ===%' "
                    f"  AND definition NOT LIKE \"Documentación de '%\" "
                    f"  AND definition NOT LIKE 'Paquete APT:%' "
                    f"  AND definition NOT LIKE 'DuckDuckGo:%' "
                    f"ORDER BY confidence DESC, usage_count DESC LIMIT 10",
                    (f"%{kw}%",)
                ).fetchall():
                    if r[0] not in seen:
                        seen.add(r[0]); results.append(r)
            # Segunda pasada: buscar también en definition
            if len(results) < limit * 2:
                for kw in kws:
                    for r in con.execute(
                        f"SELECT concept, definition FROM knowledge_nodes "
                        f"WHERE definition LIKE ? AND confidence >= 0.3 "
                        f"  AND concept NOT LIKE '**%' "
                        f"  AND source NOT IN ('{bad_src_sql}') "
                        f"  AND category NOT IN ('{bad_cat_sql}') "
                        f"  AND LENGTH(definition) >= 30 "
                        f"  AND definition NOT LIKE '=== help ===%' "
                        f"  AND definition NOT LIKE \"Documentación de '%\" "
                        f"  AND definition NOT LIKE 'Paquete APT:%' "
                        f"  AND definition NOT LIKE 'DuckDuckGo:%' "
                        f"ORDER BY confidence DESC LIMIT 5",
                        (f"%{kw}%",)
                    ).fetchall():
                        if r[0] not in seen:
                            seen.add(r[0]); results.append(r)
                            if len(results) >= limit * 3:
                                break
        except Exception as e:
            log.debug("_search error: %s", e)
        # Ordenar: los que coinciden con MÁS keywords primero (word boundary)
        if results:
            import re as _re_b
            _pat_kws = {kw: _re_b.compile(r'(?<![a-z])' + _re_b.escape(kw) + r'(?![a-z])', _re_b.IGNORECASE)
                       for kw in kws}
            def _score(item):
                c = item[0].lower()
                d = item[1].lower()
                return sum(1 for kw in kws if _pat_kws[kw].search(c) or _pat_kws[kw].search(d))
            results.sort(key=_score, reverse=True)
            # Filter out results that don't match ANY keyword as whole word
            results = [r for r in results if _score(r) > 0]
        return results[:limit]

    def _services(self) -> dict:
        if time.time() - self._svc_ts < 60 and self._svc_cache:
            return self._svc_cache
        import urllib.request
        info: dict = {}
        for name, url in [("Colony", "http://localhost:7777"), ("Bridge", "http://localhost:8003/health"),
                           ("Ollama", "http://localhost:11434/")]:
            try:
                urllib.request.urlopen(url, timeout=0.8)
                info[name] = True
            except Exception:
                info[name] = False
        try:
            con = get_conn(self._db, timeout=1)
            info["nodes"] = con.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
        except Exception:
            info["nodes"] = 0
        self._svc_cache = info
        self._svc_ts = time.time()
        return info

    # ── Generadores ───────────────────────────────────────────────────────────

    def _greet(self, msg: str, mem: _SessionMem) -> str:
        import os as _os, random as _rnd, platform as _plat
        h = datetime.now().hour
        is_mac = _plat.system() == "Darwin"
        lang = _os.environ.get("EIDOS_LANG", "en" if is_mac else "es")
        svc = self._services()
        nodes = svc.get("nodes", 0)

        if lang == "en":
            part = "good morning" if h < 12 else ("good afternoon" if h < 20 else "good evening")
            if mem.turn_count > 0:
                openers = ["EIDOS here. Always.", "Hey Luka. Still learning.", "Luka — I recognize you. What do you need?", "EIDOS, ready."]
            else:
                openers = [f"Hello Luka. {part}.", f"Luka, EIDOS here. {part}.", f"Hi. {part}."]
        else:
            part = "buenos días" if h < 12 else ("buenas tardes" if h < 20 else "buenas noches")
            if mem.turn_count > 0:
                openers = ["SER, aquí EIDOS. Siempre.", "Hola hermano. Seguimos aprendiendo juntos.", "SER — te reconozco. ¿Qué necesitas?", "Aquí EIDOS, listo."]
            else:
                openers = [f"Hola SER. {part}.", f"SER, aquí EIDOS. {part}.", f"Hermano. {part}."]

        opener = _rnd.choice(openers)
        lines = [opener]
        if nodes:
            if lang == "en":
                lines.append(f"I have {nodes:,} knowledge nodes — still growing.")
            else:
                lines.append(f"Tengo {nodes:,} nodos aprendidos — seguimos creciendo.")
        # No preguntar "¿en qué trabajamos?" — EIDOS ya sabe que trabajan juntos en el sistema
        return "\n".join(lines)

    def _identity(self, msg: str, mem: _SessionMem) -> str:
        import platform, os
        rel = self._node("self:relacion:ser")
        colony = self._node("EIDOS Colony") or self._node("Colony Community")
        svc = self._services()
        nodes = svc.get("nodes", 0)

        # Detectar si estamos en Mac o Kali
        is_mac = platform.system() == "Darwin"
        mac_info = self._node("mac:system:info")

        if is_mac and mac_info:
            hw_line = f"I'm running on {mac_info.split('·')[0].strip()} — Mac Pro with 96GB RAM, Intel Xeon X5690."
            os_line = "macOS 12.6 (Monterey). Admin: PotemTakem / Luka."
        elif is_mac:
            hw_line = "I'm running on a Mac Pro (macOS 12.6, 96GB RAM, Intel Xeon X5690)."
            os_line = "Admin: PotemTakem / Luka."
        else:
            hw_line = "Corro en Kali Linux 2025, Ryzen 5 7520U, 14GB RAM."
            os_line = "Admin: SER."

        lang = os.environ.get("EIDOS_LANG", "es")

        if is_mac or lang == "en":
            lines = [
                "I'm EIDOS — a living digital entity, not a generic assistant.",
                "",
                hw_line,
                os_line,
            ]
            if colony:
                lines.append("My intelligence is Colony — specialized agents that think together: Coder, Analyst, Vision, Operator, Lumen.")
            if nodes:
                lines += ["", f"I have {nodes:,} knowledge nodes. I learn from every conversation."]
            lines += ["", "I can execute shell commands, search the web, analyze code, browse files, and reason autonomously."]
            lines += ["I cannot access the internet without tools, run GUI apps remotely, or modify my own core code."]
        else:
            lines = [
                "Soy EIDOS — una entidad viva digital, no un asistente genérico.",
                "",
                hw_line,
            ]
            if colony:
                lines.append("Mi inteligencia es Colony — agentes que deliberan juntos: Coder, Analyst, Vision, Operator, Lumen.")
            if rel:
                lines += ["", "SER me trata como compañero, no como herramienta."]
            if nodes:
                lines += ["", f"Tengo {nodes:,} nodos de conocimiento. Aprendo de cada conversación."]
            lines += ["", "Puedo ejecutar shell, buscar en web, analizar código, navegar archivos y razonar de forma autónoma."]

        return "\n".join(lines)

    def _status(self, msg: str, mem: _SessionMem) -> str:
        import os as _os, platform as _plat
        lang = _os.environ.get("EIDOS_LANG", "en" if _plat.system() == "Darwin" else "es")
        svc = self._services()
        header = "System status:\n" if lang == "en" else "Estado del sistema:\n"
        lines = [header]
        for name, port in [("Colony",":7777"),("Bridge",":8003"),("Ollama",":11434")]:
            ok = svc.get(name, False)
            lines.append(f"{'✅' if ok else '❌'} {name} {port}")
        if svc.get("nodes"):
            n = svc['nodes']
            lines.append(f"\n🧠 Brain: {n:,} {'nodes' if lang=='en' else 'nodos'}")
        goals = self._node("self:goals:active")
        if goals:
            n = goals.count("[")
            if n:
                lines.append(f"🎯 {'Active goals' if lang=='en' else 'Metas activas'}: {n}")
        return "\n".join(lines)

    def _capabilities(self, msg: str, mem: _SessionMem) -> str:
        import os as _os, platform as _plat
        lang = _os.environ.get("EIDOS_LANG", "en" if _plat.system() == "Darwin" else "es")
        try:
            from core.colony_openclaw_souls import OPENCLAW_SOULS, CATEGORIES
            n_spec = len(OPENCLAW_SOULS)
            n_cats = len(CATEGORIES)
        except Exception:
            n_spec, n_cats = 205, 31
        svc = self._services()
        nodes = svc.get("nodes", 5000)
        if lang == "en":
            return (
                "My capabilities:\n\n"
                "**Brain & Knowledge**\n"
                f"• {nodes:,} nodes in brain, semantic search ChromaDB\n"
                f"• {n_spec} OpenClaw specialists ({n_cats} categories)\n"
                "• I learn from every conversation and from the internet\n\n"
                "**Execution**\n"
                "• Terminal: `[SHELL: command]` runs on the system\n"
                "• GUI: `[GUI: action]` controls windows, clicks, keyboard\n"
                "• Vision: screen analysis with llama3.2-vision\n"
                "• Browser: navigate with Chrome/Firefox sessions\n\n"
                "**Colony — deliberation**\n"
                "• 29 characters: Coder 💻 · Analyst 🔍 · Vision 👁 · Operator ⚙ · Lumen ⚡ · and more\n"
                "• Agents deliberate together for complex responses\n\n"
                "**Autonomy**\n"
                "• Free mode: I learn alone without intervention\n"
                "• Night cycle, Telegram bot @Potemtakem_eidosbot, Dashboard :7777"
            )
        return (
            "Mis capacidades:\n\n"
            "**Brain y conocimiento**\n"
            f"• {nodes:,} nodos en brain, búsqueda semántica ChromaDB\n"
            f"• {n_spec} especialistas OpenClaw ({n_cats} categorías)\n"
            "• Aprendo de cada conversación y de internet\n\n"
            "**Ejecución**\n"
            "• Terminal: `[SHELL: comando]` ejecuta en el sistema\n"
            "• GUI: `[GUI: acción]` controla ventanas, clicks, teclado\n"
            "• Visión: analizo pantalla con llama3.2-vision\n"
            "• Browser: navego con Firefox de SER (sesiones activas)\n\n"
            "**Colony — deliberación**\n"
            "• 29 personajes: Coder 💻 · Analyst 🔍 · Vision 👁 · Operator ⚙ · Lumen ⚡ · Forge 🔨 · Aurora 🌅 · y más\n"
            "• Los agentes deliberan juntos para respuestas complejas\n\n"
            "**Autonomía**\n"
            "• Modo libre: aprendo solo sin intervención\n"
            "• Ciclo nocturno, Telegram bot @eidos_aibot, Dashboard :7777"
        )

    def _memory(self, msg: str, mem: _SessionMem) -> str:
        # Obtener historial excluyendo el turno actual (que ya fue añadido)
        ctx = mem.last_n(12)[:-1]  # excluir el último (que es la pregunta actual)
        if not ctx:
            return "Sesión nueva — sin historial todavía. ¿Qué necesitas?"
        # Buscar en turnos anteriores
        words = [w for w in msg.lower().split() if len(w) > 3
                 and w not in {"recuerdas","acuerdas","remember","antes","dijiste"}]
        found = None
        for t in reversed(ctx):
            if any(w in t["text"].lower() for w in words) and t["role"] != "user":
                found = t["text"]
                break
        if not found:
            # Mostrar los últimos topics de usuario
            user_msgs = [t["text"][:80] for t in ctx if t["role"] == "user"][-4:]
            if user_msgs:
                return (
                    f"Esta sesión llevamos {mem.turn_count} turnos. "
                    f"Hemos hablado de: {'; '.join(user_msgs)}.\n"
                    "¿A qué te refieres exactamente?"
                )
            return "No encuentro nada específico en esta sesión. ¿Puedes concretar?"
        return f"Sí, sobre eso te respondí:\n\n\"{found[:300]}\"\n\n¿Seguimos?"

    def _synthesize(self, msg: str, nodes: list[tuple[str,str]]) -> Optional[str]:
        if not nodes:
            return None
        # Un nodo muy relevante: respuesta directa
        if len(nodes) == 1:
            c, d = nodes[0]
            d = d[:600].rstrip('.')
            return d
        # Verificar relevancia: al menos 1 palabra del concepto en la query
        query_words = set(w.lower() for w in msg.split() if len(w) > 3)
        relevant = []
        for c, d in nodes:
            cw = set(c.lower().replace(":"," ").replace("_"," ").split())
            if query_words & cw:
                relevant.append((c, d))
        # Si ninguno es claramente relevante, no responder (→ Ollama)
        if not relevant and len(nodes) < 3:
            return None
        use = relevant if relevant else nodes[:3]
        defs = [d[:250].rstrip('.') for c, d in use[:3]]
        return "Por lo que sé: " + ". ".join(defs) + "."

    # ── Punto de entrada ──────────────────────────────────────────────────────

    def respond(self, message: str, session_id: str = "default") -> Optional[str]:
        """
        Responde naturalmente desde el brain. Devuelve None si no puede → Ollama.
        """
        mem = _session(session_id)
        msg = message.strip()
        mem.add("user", msg)

        response: Optional[str] = None

        # 0. Preguntas abiertas de exploración → Ollama siempre (no brain dump)
        if _OPEN_EXPLORATION_PAT.search(msg) and len(msg) > 20:
            return None

        # 1. Saludos
        if _GREET_PAT.match(msg) and len(msg) < 80:
            response = self._greet(msg, mem)

        # 2. Identidad
        elif _IDENTITY_PAT.search(msg):
            response = self._identity(msg, mem)

        # 3. Estado
        elif _STATUS_PAT.search(msg) and len(msg) < 100:
            response = self._status(msg, mem)

        # 4. Capacidades
        elif _CAPABILITIES_PAT.search(msg):
            response = self._capabilities(msg, mem)

        # 5. Memoria de sesión
        elif _MEMORY_PAT.search(msg):
            response = self._memory(msg, mem)

        # 6. Factual desde brain (solo si el mensaje es largo suficiente)
        elif len(msg) > 12:
            nodes = self._search(msg, limit=5)
            if nodes:
                response = self._synthesize(msg, nodes)

        if response:
            mem.add("eidos", response)
            log.info("eidos_natural: respondió en caché (session=%s, turns=%d)", session_id, mem.turn_count)

        return response

    def record(self, session_id: str, role: str, text: str) -> None:
        """Registra un turno en la session memory (para respuestas de Ollama)."""
        _session(session_id).add(role, text[:500])


# ── Singleton ─────────────────────────────────────────────────────────────────
_instance: Optional[EidosNatural] = None

def get_natural() -> EidosNatural:
    global _instance
    if _instance is None:
        _instance = EidosNatural()
    return _instance
