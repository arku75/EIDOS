"""
core/eidos_learn.py — Ciclo de aprendizaje cerrado (S119: cloud-first).

El puente entre APIs cloud (rápidas) y el motor lógico.
EIDOS aprende de CADA interacción sin depender de la CPU de SER.

Cascada de resolución (rotación definida por SER, S121):
  1. DeepSeek cloud × 3 (5s, cloud, ~$0.0003) — PRIMARIO [S125-K]
  2. Groq cloud     × 1 (2s, cloud, tier gratuito) — fallback
  3. Ollama local   × 6 alternando instruct↔thinking (sin prisa, $0)
  4. Mac músculo   — solo si el Mac está activo vía túnel :11435 (RAM del Mac)

Cada respuesta → extracción de hechos → motor lógico.
La próxima vez que pregunten lo mismo → instantáneo (paso 1).

HONESTY NOTE: Fact extraction uses TWO paths:
  A) Motor lógico (regex-based, fast, deterministic) via logic.learn_from_text()
  B) LLM extraction fallback (when regex produces 0 facts) via ask_llm()
  C) Background extraction (LFM2.5-350m structured concept->definition)
Layer B was added because regex-based extraction is rigid — if the
sentence structure doesn't match known patterns, it learns nothing.
The LLM fallback ensures EIDOS actually learns from every response.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request

from core.db import get_conn  # noqa: E402 — usar capa DB unificada, no sqlite3.connect()
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.learn")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"

# ── APIs cloud ────────────────────────────────────────────────────────────────
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
OLLAMA_URL = "http://localhost:11434/api/chat"

GROQ_MODEL = "llama-3.3-70b-versatile"
DEEPSEEK_MODEL = "deepseek-chat"
OLLAMA_MODEL = "LiquidAI/lfm2.5-1.2b-instruct:q4_0"
OLLAMA_THINKING = "lfm2.5-thinking:1.2b"

# User-Agent de navegador: Cloudflare de Groq bloquea el "Python-urllib" por
# defecto con error 1010 (banned browser signature). Con un UA real pasa. [S121]
_BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) EIDOS/1.0"

# ── Mac (músculo potente vía túnel permanente :11435) ─────────────────────────
# El modelo corre EN el Mac, NO consume RAM de Kali. Se usa como 2ª opción
# potente cuando lo local no resuelve y el Mac está activo. Lista priorizada:
# intenta LiquidAI grande primero (futuro), cae a lo que el Mac tenga ahora.
OLLAMA_MAC_URL = os.environ.get("EIDOS_MAC_OLLAMA", "http://localhost:11435/api/chat")
OLLAMA_MAC_MODELS = [
    # Solo LiquidAI + modelos compartidos con Kali. SER quiere solo LiquidAI en Mac.
    "LiquidAI/lfm2.5-13b:latest",       # 🧠 Máxima potencia (13B params)
    "LiquidAI/lfm2.5-7b:latest",        # 💪 Potente (7B)
    "LiquidAI/lfm2-vl-7b:latest",       # 👁️ Visión-lenguaje (7B)
    "LiquidAI/lfm2.5-3b:latest",        # ⚡ Balanceado (3B)
    "lfm2.5-thinking:1.2b",             # 🎯 Razonamiento (mismo que Kali)
    "lfm2.5-1.2b-instruct:q4_0",        # 📝 Instrucción (mismo que Kali)
    "LiquidAI/lfm2.5-350m:latest",      # 🏃 Rápido (mismo que Kali)
    "lfm2-vl:latest",                   # 👀 Visión ligera (mismo que Kali)
    "moondream:latest",                 # 🌙 Visión compacta (mismo que Kali)
    "nomic-embed-text:latest",          # 🔢 Embeddings (mismo que Kali)
]
_mac_check_cache = {"ts": 0.0, "available": False}

# ── Parámetros de la cascada (configurables por SER) ──────────────────────────
GROQ_ATTEMPTS = int(os.environ.get("EIDOS_GROQ_ATTEMPTS", "3"))
DEEPSEEK_ATTEMPTS = int(os.environ.get("EIDOS_DEEPSEEK_ATTEMPTS", "3"))
LOCAL_ATTEMPTS = int(os.environ.get("EIDOS_LOCAL_ATTEMPTS", "6"))

# ── Rate limiter ──────────────────────────────────────────────────────────────
# Máximo de llamadas por hora/día a APIs cloud para no exceder tiers gratuitos
_usage_log: List[float] = []  # timestamps de llamadas
_MAX_PER_HOUR = 10
_MAX_PER_DAY = 50


def _get_api_key(service: str) -> str:
    """Carga API key desde secrets.env sin depender de dotenv."""
    secrets_path = Path.home() / ".eidos" / "secrets.env"
    key_map = {
        "groq": "GROQ_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }
    env_var = key_map.get(service)
    if not env_var:
        return ""
    # Primero os.environ
    val = os.environ.get(env_var, "")
    if val and len(val) > 10:
        return val
    # Luego leer secrets.env
    if secrets_path.exists():
        try:
            for line in secrets_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if k == env_var and v and len(v) > 10:
                    return v
        except Exception:
            pass
    return ""


def _check_rate_limit() -> bool:
    """True si podemos hacer otra llamada. False si excedemos el límite."""
    global _usage_log
    now = time.time()
    # Limpiar entradas viejas (>24h)
    _usage_log = [t for t in _usage_log if now - t < 86400]
    last_hour = sum(1 for t in _usage_log if now - t < 3600)
    if last_hour >= _MAX_PER_HOUR:
        log.warning("Rate limit: %d llamadas en la última hora", last_hour)
        return False
    if len(_usage_log) >= _MAX_PER_DAY:
        log.warning("Rate limit: %d llamadas en el día", len(_usage_log))
        return False
    _usage_log.append(now)
    return True


# ── LLM callers ───────────────────────────────────────────────────────────────

def _call_groq(question: str, timeout: int = 15,
               temperature: float = 0.3) -> str:
    """Llama a Groq API (cloud, rápido, ~2s)."""
    api_key = _get_api_key("groq")
    if not api_key:
        log.debug("Groq: sin API key")
        return ""

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system",
             "content": (
                 "Eres EIDOS, el asistente de SER en Kali Linux. "
                 "Responde en español. Sé claro, directo y útil. "
                 "Al explicar, di qué ES cada cosa y para qué SIRVE usando su "
                 "nombre real (nunca comodines como 'X'), porque luego EIDOS "
                 "aprende de tus respuestas. "
                 "NO uses formato académico. Habla como un compañero técnico."
             )},
            {"role": "user", "content": question},
        ],
        # [S122-I] SIN max_tokens (SER): EIDOS es un sistema neuronal vivo, no
        # se le limita la respuesta. Sin truncado → además adiós al bug de JSON
        # cortado a mitad. El modelo usa su máximo por defecto.
        "temperature": temperature,
    }
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            GROQ_URL, data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": _BROWSER_UA,
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
        content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content.strip()
    except Exception as e:
        log.debug("Groq error: %s", e)
        return ""


def _call_deepseek(question: str, timeout: int = 30,
                   temperature: float = 0.3) -> str:
    """Llama a DeepSeek API (cloud, potente, ~5s)."""
    api_key = _get_api_key("deepseek")
    if not api_key:
        log.debug("DeepSeek: sin API key")
        return ""

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system",
             "content": (
                 "Eres EIDOS, asistente técnico de SER en Kali Linux. "
                 "Responde en español, estilo directo y útil. "
                 "Al explicar, di qué ES cada cosa y para qué SIRVE usando su "
                 "nombre real (nunca comodines como 'X')."
             )},
            {"role": "user", "content": question},
        ],
        # [S122-I] SIN max_tokens (SER): EIDOS es un sistema neuronal vivo, no
        # se le limita la respuesta. Sin truncado → además adiós al bug de JSON
        # cortado a mitad. El modelo usa su máximo por defecto.
        "temperature": temperature,
    }
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            DEEPSEEK_URL, data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": _BROWSER_UA,
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
        content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content.strip()
    except Exception as e:
        log.debug("DeepSeek error: %s", e)
        return ""


def _call_ollama(question: str, model: str = OLLAMA_MODEL,
                 timeout: int = 300, temperature: float = 0.3,
                 url: str = OLLAMA_URL) -> str:
    """Llama a Ollama. SIN timeout agresivo — que tarde lo que necesite.
    `url` permite apuntar al Ollama local (:11434) o al del Mac (:11435)."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system",
             "content": (
                 "Eres EIDOS, el asistente de SER en Kali Linux. "
                 "Responde en español. Sé claro, directo y útil. "
                 "Al explicar, di qué ES cada cosa y para qué SIRVE usando su "
                 "nombre real (nunca comodines como 'X'), porque luego EIDOS "
                 "aprende de tus respuestas."
             )},
            {"role": "user", "content": question},
        ],
        "stream": False,
        # [S122-I] SIN num_predict (SER): sin límite de tokens, sistema vivo.
        "options": {"temperature": temperature},
    }
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", "User-Agent": _BROWSER_UA},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
        return resp.get("message", {}).get("content", "").strip()
    except Exception as e:
        log.debug("Ollama error (%s): %s", url, e)
        return ""


def _mac_available(cache_secs: int = 60) -> bool:
    """¿El Mac (Ollama vía túnel :11435) está activo? Cacheado 60s para no
    sondear en cada pregunta. El modelo corre en el Mac, no gasta RAM de Kali."""
    now = time.time()
    if now - _mac_check_cache["ts"] < cache_secs:
        return _mac_check_cache["available"]
    available = False
    try:
        base = OLLAMA_MAC_URL.rsplit("/api/", 1)[0]
        with urllib.request.urlopen(f"{base}/api/tags", timeout=4) as r:
            available = r.status == 200
    except Exception:
        available = False
    _mac_check_cache.update({"ts": now, "available": available})
    return available


def _call_mac(question: str, timeout: int = 120, temperature: float = 0.3) -> Tuple[str, str]:
    """Prueba los modelos potentes del Mac en orden. Devuelve (respuesta, modelo)."""
    for model in OLLAMA_MAC_MODELS:
        answer = _call_ollama(question, model=model, timeout=timeout,
                              temperature=temperature, url=OLLAMA_MAC_URL)
        if answer and len(answer) > 20:
            return answer, f"mac:{model.split('/')[-1].split(':')[0]}"
    return "", ""


# ── API pública ───────────────────────────────────────────────────────────────

def extract_keywords(text: str, max_kw: int = 5) -> List[str]:
    """Extrae palabras clave de un texto (sustantivos, entidades)."""
    stops = {"para", "como", "este", "esta", "más", "que", "del", "las",
             "los", "con", "por", "una", "uno", "son", "era", "fue", "han",
             "the", "this", "that", "with", "from", "what", "when", "have"}
    words = re.findall(r"[a-záéíóúñü]{4,}", text.lower())
    return [w for w in words if w not in stops][:max_kw]


def ask_llm(question: str, timeout: int = 120,
            temperature: float = 0.3) -> Tuple[str, str]:
    """Resuelve una pregunta con la cascada de modelos que definió SER.

    Cascada con ROTACIÓN (para no saturar la RAM ni los tiers cloud):
      [S125-K] DeepSeek PRIMARIO (Groq fuera — API key no servía, SER lo quitó).
      1. DeepSeek cloud  × DEEPSEEK_ATTEMPTS(3) — potente, barato (~$0.0003/llamada)
      2. Groq cloud      × GROQ_ATTEMPTS    (1) — solo si DeepSeek falla y hay key
      3. Ollama local    × LOCAL_ATTEMPTS   (6) — alternando instruct↔thinking
      4. Mac (músculo)   — SOLO si el Mac está activo vía túnel :11435.
                           Corre en el Mac, no consume RAM de Kali.

    Regla de SER: "si groq y deepseek no están, usa solo LLM de mi sistema,
    a menos que el Mac esté activo y este pueda usarlo".
    Las APIs respetan el rate limit; si se agota, la cascada salta a local.

    Returns:
        (answer, source) — source: "deepseek", "groq", el nombre del modelo
        local, "mac:<modelo>", o "none".
    """
    # ── Capa 1: DeepSeek cloud (× DEEPSEEK_ATTEMPTS) — PRIMARIO [S125-K] ──
    for i in range(DEEPSEEK_ATTEMPTS):
        if not _check_rate_limit():
            log.debug("DeepSeek: rate limit alcanzado en intento %d/%d", i + 1, DEEPSEEK_ATTEMPTS)
            break
        answer = _call_deepseek(question, timeout=min(timeout, 30), temperature=temperature)
        if answer and len(answer) > 20:
            log.info("ask_llm: resuelto por DeepSeek (intento %d)", i + 1)
            return answer, "deepseek"

    # ── Capa 2: Groq cloud (× 1, solo fallback) [S125-K] ──────────────────
    if _get_api_key("groq"):
        answer = _call_groq(question, timeout=min(timeout, 15), temperature=temperature)
        if answer and len(answer) > 20:
            log.info("ask_llm: resuelto por Groq (fallback)")
            return answer, "groq"

    # ── Capa 3: Ollama local (× LOCAL_ATTEMPTS, rotando instruct↔thinking) ─
    local_models = [OLLAMA_MODEL, OLLAMA_THINKING]
    for i in range(LOCAL_ATTEMPTS):
        model = local_models[i % len(local_models)]  # rotación 0,1,0,1,...
        answer = _call_ollama(question, model=model, timeout=timeout,
                              temperature=temperature)
        if answer and len(answer) > 20:
            log.info("ask_llm: resuelto por Ollama local (%s, intento %d)",
                     model.split("/")[-1].split(":")[0], i + 1)
            return answer, model.split("/")[-1].split(":")[0]

    # ── Capa 4: Mac (músculo potente) — SOLO si está activo ───────────────
    if _mac_available():
        log.info("ask_llm: local agotado, probando músculo del Mac")
        answer, mac_source = _call_mac(question, timeout=timeout, temperature=temperature)
        if answer and len(answer) > 20:
            log.info("ask_llm: resuelto por Mac (%s)", mac_source)
            return answer, mac_source

    return "", "none"


def resolve_with_llm(question: str, subject_hint: str = "",
                     timeout: int = 120) -> Dict[str, Any]:
    """Resuelve una pregunta con LLM Y aprende de la respuesta.

    Returns:
        Dict con: answer, source, facts_learned, elapsed_s, ok
    """
    t0 = time.time()
    answer, source = ask_llm(question, timeout=timeout)
    elapsed = time.time() - t0

    if not answer:
        return {"answer": "", "source": "none", "facts_learned": 0,
                "elapsed_s": round(elapsed, 1), "ok": False}

    # Extraer hechos y aprender
    facts_learned = 0
    try:
        from core.eidos_logic import get_logic_reasoner
        logic = get_logic_reasoner()
        # Asegurar que el motor tiene datos
        if len(logic._concept_index) < 100:
            logic.load_from_graph(max_nodes=5000)
            logic.load_edges(max_edges=5000)
            logic.load_seed_facts()
            logic._load_learned_facts()
        # Extraer sujeto de la pregunta si no se dio
        if not subject_hint:
            keywords = extract_keywords(question, max_kw=3)
            subject_hint = keywords[0] if keywords else ""
        # Aprender de la respuesta (regex-based, path A)
        facts_learned = logic.learn_from_text(answer, subject_hint=subject_hint,
                                               confidence=0.70)
        if facts_learned:
            log.info("learn: %d hechos nuevos de respuesta %s", facts_learned, source)
    except Exception as e:
        log.debug("learn: error extrayendo hechos: %s", e)

    # ── LLM fallback: when regex-based extraction produces 0 facts ──────────
    # Regex patterns are rigid — if the sentence doesn't match a known
    # template, logic.learn_from_text() learns nothing. The LLM fallback
    # asks a model to extract structured facts from the answer text.
    if facts_learned == 0 and answer and len(answer) > 50:
        try:
            llm_extract_prompt = (
                f"Extrae hechos concretos y definiciones del siguiente texto. "
                f"Para cada hecho, da el CONCEPTO (nombre, término técnico) y "
                f"su DEFINICION (qué es y para qué sirve). "
                f"Formato: CONCEPTO: definicion\n\n"
                f"Tema principal: {subject_hint or question[:80]}\n\n"
                f"Texto:\n{answer[:2000]}"
            )
            extract_answer, extract_src = ask_llm(
                llm_extract_prompt, timeout=45, temperature=0.2
            )
            if extract_answer and len(extract_answer) > 20:
                # Parse LLM-extracted facts into logic engine
                try:
                    from core.eidos_logic import get_logic_reasoner as _glr2
                    logic2 = _glr2()
                    if len(logic2._concept_index) < 100:
                        logic2.load_from_graph(max_nodes=5000)
                        logic2.load_edges(max_edges=5000)
                        logic2.load_seed_facts()
                        logic2._load_learned_facts()
                    facts_learned = logic2.learn_from_text(
                        extract_answer, subject_hint=subject_hint, confidence=0.55
                    )
                    if facts_learned:
                        log.info("learn(LLM fallback): %d hechos nuevos via %s",
                                 facts_learned, extract_src)
                except Exception:
                    log.debug("learn(LLM fallback): parse failed")
        except Exception as e:
            log.debug("learn(LLM fallback): %s", e)

    # ── S121: extracción estructurada con LFM2.5-350m (Extract) ────────────
    # Además del aprendizaje regex, destilamos un par concepto→definición limpio
    # y lo insertamos vía el portero de calidad. El 350m tarda ~10s, así que NO
    # bloqueamos la respuesta al usuario: corre en un hilo de fondo (daemon).
    # OPCIONAL: si el 350m no está, no pasa nada. Anclamos con subject_hint.
    def _bg_extract(ans: str, src: str, hint: str) -> None:
        try:
            from core.eidos_extract import extract_and_learn
            n = extract_and_learn(ans, source=f"extract:{src}",
                                  subject_hint=hint, confidence=0.70)
            if n:
                log.info("learn(bg): +%d nodo(s) estructurado(s) (extract 350m)", n)
        except Exception as e:  # noqa: BLE001
            log.debug("learn(bg): extract 350m no disponible (%s)", e)

    try:
        import threading
        threading.Thread(target=_bg_extract, args=(answer, source, subject_hint),
                         daemon=True).start()
    except Exception as e:
        log.debug("learn: no se pudo lanzar extracción de fondo (%s)", e)

    return {
        "answer": answer,
        "source": source,
        "facts_learned": facts_learned,
        "elapsed_s": round(elapsed, 1),
        "ok": True,
    }


# ── S119 #225 FASE 2: Namespace filter ────────────────────────────────────────
# Nodos del namespace "core" (estructura interna de EIDOS) NUNCA deben
# aparecer en respuestas a humanos. Son metadatos, código, inferencias.

_CORE_SOURCES = {
    "code_analyzer", "reasoned", "graphify",
    "self_index:class", "self_index:function", "self_index:module",
    "tabula_rasa:path_scan", "tabula_rasa:ast", "tabula_rasa:ps",
    "char:colony_centinela:metrics", "char:colony_centinela:complexity",
    "pc_explorer:colony_general",
}

_CORE_CATEGORIES = {
    "eidos_function", "eidos_class", "eidos_module",
    "code_structure", "inferred", "lifecycle",
}

_WORLD_SOURCES = {
    "oro_skills", "kali_tools",
    "research:duckduckgo", "research:wikipedia", "research:man",
    "research:apt", "research:help", "research:code", "research:active:wikipedia",
    "docs:hermes", "docs:openclaw_skills", "docs:vseidos", "docs:openclaw",
    "docs:man", "docs:sessions",
    "distilled_from_curiosity", "distilled_from_deliberation",
    "auto_learner", "eidos_crawler:openclaw", "telegram_bot",
}


def _is_core_namespace(source: str = "", category: str = "") -> bool:
    """¿Este nodo pertenece al namespace interno de EIDOS (core)?

    Los nodos core son metadatos, código, estructura interna.
    NUNCA deben aparecer en respuestas a humanos.
    """
    if source in _CORE_SOURCES:
        return True
    if category in _CORE_CATEGORIES:
        return True
    return False


def _is_garbage_answer(text: str) -> bool:
    """Detecta respuestas basura del grafo (exploits, CVE, ruido DB, libros, URL)."""
    if not text or len(text) < 50:
        return True
    garbage_patterns = [
        r"exploit.*edb-\d+",          # Exploit-DB entries
        r"CVE-\d{4}-\d+",             # CVE numbers
        r"Exploit-DB ID:",            # Exploit DB references
        r"regla de auto-modificación", # Meta rules leaking
        r"REGLA de auto",             # Meta rules
        r"nunca modificar código",    # Meta rules
        r"sandbox está en",           # Infrastructure leaking
        r"Workflow: sandbox",         # Infrastructure leaking
        r"☒\s*\d+\.\d+%",            # Pseudo-confidence scores
        r"doc:[a-z]+:",               # Internal doc: prefixed nodes
        r"z-library|z-lib",           # Book references leaking
        r"summary: Libro",            # Book summaries
        r"\d{4}年\d{1,2}月",          # Chinese/Japanese dates in content
        r"\w+\.sk/|\w+\.sk,",         # z-library domains leaking
        r"第\d+版",                   # "Edición N" in Chinese
        r"constitution self-check",   # Internal dev notes
        r"self-check tenía bug",      # Internal dev notes
        r"sha-256 valid",             # Internal hash checks
        r"forbidden_commands\|forbidden_patterns",  # Internal config
        r"ATT&CK: T\d{4}",            # MITRE ATT&CK references
        r"↔ ATT&CK:",                 # ATT&CK cross-references
        r"_limpiar_concepto\(\)",     # Internal code functions
        r"_capitalizar_inicio\(\)",   # Internal code functions
        r"\(eidos_\w+\.py\)",         # Internal code references
    ]
    for pat in garbage_patterns:
        if re.search(pat, text, re.IGNORECASE):
            return True
    # Heurística: si más del 15% del texto son caracteres CJK (chino/japonés/coreano)
    cjk_chars = sum(1 for c in text if '一' <= c <= '鿿'
                    or '぀' <= c <= 'ゟ'
                    or '가' <= c <= '힯')
    if len(text) > 0 and cjk_chars / len(text) > 0.15:
        return True
    # Heurística: si empieza con bullet de doc interno
    if text.strip().startswith("• doc:") or text.strip().startswith("• gfy:"):
        return True
    return False


def _answer_contains_query_terms(answer: str, query: str) -> bool:
    """La respuesta debe contener al menos un término clave de la pregunta."""
    q_words = set(re.findall(r"[a-záéíóúñü]{4,}", query.lower()))
    a_words = set(re.findall(r"[a-záéíóúñü]{4,}", answer.lower()))
    # Quitar stopwords
    stops = {"para", "como", "este", "esta", "más", "que", "del", "las",
             "los", "con", "por", "una", "uno", "son", "era", "fue", "han",
             "the", "this", "that", "with", "from", "what", "when", "have"}
    q_clean = q_words - stops
    if not q_clean:
        return True  # no hay términos para comparar, aceptar
    overlap = q_clean & a_words
    return len(overlap) >= 1


def _is_known_semantically(query: str, threshold: float = 0.30) -> bool:
    """S119 #223+#239: ¿Tiene EIDOS conocimiento semántico sobre esta pregunta?

    Doble verificación:
      1. ChromaDB embeddings: ¿hay vectores semánticamente cercanos?
      2. SQLite quality: ¿esos matches son de fuentes de calidad?

    Solo si AMBAS verificaciones pasan, consideramos que EIDOS conoce el tema.
    Esto evita que basura (wordnet, ATT&CK, reasoned) contamine la detección.

    Returns:
        True si hay conocimiento semántico DE CALIDAD sobre la query.
        False si la query es desconocida (debe decir "no lo sé").
    """
    try:
        from core.colony_chroma import get_chroma_memory
        mem = get_chroma_memory()
        # Esperar a que ChromaDB esté listo (max 15s)
        waited = 0
        while not mem.is_ready() and waited < 15:
            import time as _time
            _time.sleep(0.3)
            waited += 0.3
        cnt_total = mem.count()
        if cnt_total < 10:
            return True

        # Paso 1: ChromaDB - ¿hay algo semánticamente cercano?
        results = mem.search(query, limit=10, min_score=threshold)
        if not results:
            log.info("semantic_check: '%s' → DESCONOCIDO (mejor score < %.2f)",
                     query[:60], threshold)
            return False

        # Paso 2: Verificar calidad semántica del mejor match
        # No basta con que ChromaDB devuelva algo — el match debe ser RELEVANTE.
        # Criterio: al menos 1 keyword de la query debe aparecer en el
        # concepto/definición del match, Y el match debe ser de fuente confiable.
        import sqlite3, re
        from pathlib import Path

        # Extraer keywords de la query
        q_words = set(re.findall(r"[a-záéíóúñü]{4,}", query.lower()))
        stops = {"para", "como", "este", "esta", "más", "que", "del", "las",
                 "los", "con", "por", "una", "uno", "son", "era", "fue", "han",
                 "the", "this", "that", "with", "from", "what", "when", "have",
                 "sobre", "entre", "hace", "hacia", "desde", "hasta", "cada",
                 "todo", "muy", "hay", "está", "como", "cómo", "dónde", "cuál",
                 "sus", "sus", "les", "nos", "otro", "otra", "ser", "hacer"}
        q_clean = q_words - stops

        # Criterio 1: El mejor match debe solaparse léxicamente con la query
        best_score = results[0].get("score", 0)
        best_concept = results[0].get("concept", "")
        best_def = results[0].get("definition", "")

        # Calcular solapamiento: ¿cuántas keywords de la query aparecen en el match?
        match_text = (best_concept + " " + best_def).lower()
        kw_overlap = sum(1 for w in q_clean if w in match_text)

        # Si NO hay solapamiento léxico → la similitud semántica es probablemente
        # una coincidencia del modelo de embedding (nomic-embed-text no es muy preciso)
        if kw_overlap == 0 and len(q_clean) > 0:
            # Solo aceptar sin solapamiento si el score es MUY alto (>0.55)
            if best_score < 0.55:
                log.info(
                    "semantic_check: '%s' → DESCONOCIDO "
                    "(best='%s' score=%.3f, 0 kw overlap, no fiable)",
                    query[:60], best_concept[:40], best_score
                )
                return False

        # Criterio 2: SQLite fast check con la keyword más específica
        # En vez de SUM(CASE...) sobre 367K filas (lento), buscamos la keyword
        # más larga (más específica) en nodos de calidad. Una sola query rápida.
        BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"

        if BRAIN_DB.exists() and q_clean:
            sorted_kw = sorted(q_clean, key=len, reverse=True)
            # Probar las 3 keywords más largas con una query OR rápida
            specific_kw = sorted_kw[:3]
            conn = get_conn(BRAIN_DB, timeout=30)
            # busy_timeout ya aplicado por get_conn (30000ms)
            try:
                like_clauses = " OR ".join(
                    f"(concept LIKE '%{w}%')" for w in specific_kw
                )
                sql = (
                    f"SELECT COUNT(*) FROM knowledge_nodes "
                    f"WHERE ({like_clauses}) "
                    f"AND quality_score >= 0.35 "
                    f"AND definition IS NOT NULL AND length(definition) >= 30 "
                    f"LIMIT 1"
                )
                cnt = conn.execute(sql).fetchone()[0]
                conn.close()

                if cnt > 0:
                    log.debug("semantic_check: '%s' → conocido "
                             "(score=%.3f, best='%s', SQLite hit=%d)",
                             query[:60], best_score, best_concept[:40], cnt)
                    return True
            except Exception as e:
                conn.close()
                log.debug("semantic_check SQLite error: %s", e)

        # Ni overlap ni SQLite → DESCONOCIDO
        log.info("semantic_check: '%s' → DESCONOCIDO "
                 "(score=%.3f, best='%s', overlap=%d)",
                 query[:60], best_score, best_concept[:40], kw_overlap)
        return False

    except Exception as e:
        log.warning("semantic_check error: %s", e)
        return True  # Si falla, no bloquear (mejor falso positivo que denegar consulta real)


def _is_self_referential(question: str) -> Optional[str]:
    """¿La pregunta es sobre EIDOS mismo? Retorna el tipo o None.

    S119 #226 FASE 3: Detección de preguntas de introspección para
    que self_inspect() responda sin pasar por el motor lógico/LLM.

    Returns:
        "identity" — "¿qué eres?", "¿quién eres?", "¿cómo te llamas?"
        "capabilities" — "¿qué puedes hacer?", "¿qué sabes hacer?"
        "health" — "¿cómo estás?", "¿qué tal estás?"
        "status" — "¿qué estás haciendo?", "¿en qué estás pensando?"
        None — no es autorreferencial
    """
    q = question.lower().strip().rstrip("?.")
    # Quitar signos de interrogación
    q_clean = re.sub(r"[¿?!]+", "", q).strip()

    # Patrones: ["pronombre"] + ["verbo ser/estar/hacer/poder"] + ...
    IDENTITY_PATTERNS = [
        # "qué eres", "quién eres", "cómo te llamas", "cuál es tu nombre"
        (r"^(qué|quien|quién)\s+(eres|sois|sos)\b", "identity"),
        (r"^(c[óo]mo|cual|cu[áa]l)\s+(te|se)\s+(llamas|llam[áa]is|denominas)\b", "identity"),
        (r"^(cu[áa]l|cual)\s+es\s+tu\s+nombre\b", "identity"),
        (r"^(qu[ée]|quien|qui[ée]n)\s+(soy|es|fuiste)\s+(t[úu]|vos|eidos)\b", "identity"),
        (r"\bpresent[aá]ndote\b", "identity"),
        (r"\bdescr[íi]bete\b", "identity"),
    ]
    # [S122-I] La VERDAD de EIDOS: preguntas sobre su modelo/naturaleza/creador
    # las responde EIDOS con su propia verdad (no "soy LLaMA/Claude/GPT"). El
    # modelo es un cerebro PRESTADO e intercambiable; EIDOS es EIDOS, creado por SER.
    MODEL_PATTERNS = [
        # "qué modelo eres", "qué IA eres", "qué LLM eres"
        (r"\bqu[ée]\s+(modelo|ia|i\.a|llm|red neuronal|inteligencia)\b.*\b(eres|sos|us[áa]s|hay)", "model"),
        (r"\b(eres|sos)\s+(un[ao]?\s+)?(ia|i\.a|inteligencia artificial|modelo|llm|bot|chatbot|red neuronal)\b", "model"),
        # "eres claude/chatgpt/gpt/llama/gemini/bard/copilot/deepseek/grok..."
        (r"\b(eres|sos)\s+(claude|chatgpt|chat gpt|gpt|gpt-?\d|openai|llama|ll-?ama|meta ai|"
         r"gemini|bard|copilot|deepseek|grok|mistral|anthropic|qwen|ollama)\b", "model"),
        (r"\b(en\s+qu[ée]|qu[ée])\s+(modelo|tecnolog[íi]a|llm)\s+est[áa]s\s+(basad|constru)", "model"),
        (r"\bqu[ée]\s+(te\s+)?(impulsa|mueve|hace\s+funcionar|da\s+vida)\b", "model"),
    ]
    CREATOR_PATTERNS = [
        # "quién te creó/programó/hizo/diseñó/desarrolló", "quién es tu creador/dueño"
        (r"\bqui[ée]n\s+(te|os)\s+(cre[óo]|program[óo]|hizo|dise[ñn][óo]|desarroll[óo]|constru|invent[óo])", "creator"),
        (r"\bqui[ée]n\s+es\s+tu\s+(creador|due[ñn]o|padre|desarrollador|programador|jefe|amo)\b", "creator"),
        (r"\b(de\s+)?qui[ée]n\s+(eres|sos|dependes)\b", "creator"),
        (r"\bqu[ée]\s+empresa\s+te\s+(cre[óo]|hizo|desarroll)", "creator"),
    ]
    CAPABILITY_PATTERNS = [
        # "qué puedes hacer", "qué sabes hacer", "para qué sirves"
        (r"^(qu[ée])\s+(puedes|sabes|pod[ée]s|sab[ée]s)\s+(hacer|realizar|ejecutar)\b", "capabilities"),
        (r"^(para|de)\s+(qu[ée]|que)\s+(sirves|serv[íi]s|eres [úu]til)\b", "capabilities"),
        (r"^(qu[ée]|que)\s+(habilidades|capacidades|capacidad)\s+(tienes|ten[ée]s|posees)\b", "capabilities"),
        (r"^(cu[áa]les|cuales)\s+son\s+tus\s+(capacidades|habilidades|funciones)\b", "capabilities"),
        (r"^(qu[ée]|que)\s+(sabes|conoces)\s+(hacer|realizar)\b", "capabilities"),
    ]
    HEALTH_PATTERNS = [
        # "cómo estás", "qué tal estás", "cómo te sientes"
        (r"^(c[óo]mo|que tal|qu[ée] tal)\s+(est[áa]s|and[áa]s|te\s+encuentras|te\s+sientes)\b", "health"),
        (r"^(c[óo]mo)\s+(te|se)\s+(sientes|sent[íi]s)\b", "health"),
    ]

    all_patterns = (
        [(p, t) for p, t in IDENTITY_PATTERNS] +
        [(p, t) for p, t in MODEL_PATTERNS] +
        [(p, t) for p, t in CREATOR_PATTERNS] +
        [(p, t) for p, t in CAPABILITY_PATTERNS] +
        [(p, t) for p, t in HEALTH_PATTERNS]
    )
    for pattern, ptype in all_patterns:
        if re.search(pattern, q_clean):
            return ptype
    return None


def _is_conversational(text: str) -> bool:
    """¿Es charla social (saludo, presentación, cortesía) y NO una pregunta de
    conocimiento? Para responder con calidez en vez de 'no lo sé'. [S122]"""
    t = (text or "").lower().strip()
    if not t:
        return False
    # Si pide conocimiento explícito, NO es solo charla → flujo normal (memoria/lógica)
    knowledge_q = ("que es", "qué es", "como funciona", "cómo funciona", "explica",
                   "para que sirve", "para qué sirve", "cual es", "cuál es",
                   "que son", "qué son", "diferencia entre", "como se", "cómo se")
    if any(k in t for k in knowledge_q):
        return False
    social = ("hola", "buenas", "buenos dias", "buenos días", "buenas tardes",
              "buenas noches", "hey", "holi", "que tal", "qué tal", "como estas",
              "cómo estás", "como va", "cómo va", "como te sientes", "cómo te sientes",
              "soy ser", "soy yo", "me alegra", "te extrañe", "te extrañé",
              "te eche de menos", "gracias", "buen trabajo", "bien hecho",
              "te quiero", "adios", "adiós", "hasta luego", "encantado",
              "un placer", "estas ahi", "estás ahí", "sigues ahi", "sigues ahí")
    if len(t) < 140 and any(t.startswith(s) or f" {s}" in f" {t}" for s in social):
        return True
    return False


def _load_self_knowledge() -> str:
    """Carga nodos self_knowledge del grafo para enriquecer respuestas de identidad."""
    try:
        from core.db import get_conn
        conn = get_conn(os.path.expanduser("~/.eidos/evolution_brain.db"), timeout=5)
        rows = conn.execute(
            "SELECT concept, definition FROM knowledge_nodes "
            "WHERE source='self_knowledge' AND confidence >= 0.8 "
            "ORDER BY confidence DESC LIMIT 15"
        ).fetchall()
        if rows:
            parts = ["\n\n🧠 Mi autoconocimiento neuronal:"]
            for concept, definition in rows:
                short_def = definition[:300].strip()
                parts.append(f"\n• {short_def}")
            return "\n".join(parts)
    except Exception:
        pass
    return ""


def smart_answer(question: str, timeout: int = 120) -> Dict[str, Any]:
    """Responde una pregunta con la estrategia más inteligente.

    0. SELF-INSPECT: ¿Es una pregunta sobre EIDOS mismo? (#226 FASE 3)
       Si sí → self_inspect() responde YA sin LLM ni ChromaDB
    1. SEMANTIC CHECK: ¿Sabe EIDOS algo sobre esto? (ChromaDB)
       Si no → "no lo sé" (sin gastar cloud)
    2. Motor lógico (0.35s) → si confía y es relevante, responde YA
    3. Si no → Groq cloud (2s) → aprende hechos → responde
    4. Si no → DeepSeek cloud (5s) → aprende hechos → responde
    5. Si no hay internet → Ollama local (sin timeout) → aprende hechos
    6. La próxima vez que pregunten lo mismo → paso 2 (instantáneo)

    S119 #223: Detección semántica de "no sé" con ChromaDB embeddings.
    S119 #226: Introspección real (FASE 3) — self_inspect() para preguntas identitarias.
    """
    t0 = time.time()

    # ── Fase 0: ¿Es pregunta sobre EIDOS mismo? (S119 #226 FASE 3) ─────────
    # Regex rápido, sin dependencia de ChromaDB. Si es autorreferencial,
    # self_inspect() da datos REALES del runtime sin tocar ChromaDB/Lógica/LLM.
    self_type = _is_self_referential(question)
    # [S122-I] La VERDAD de EIDOS sobre su modelo y su creador — respuestas
    # propias, NUNCA "soy LLaMA/Claude/GPT" ni búsquedas web. EIDOS es EIDOS.
    # S125-L: enriquecer con nodos self_knowledge del grafo neuronal.
    _extra = _load_self_knowledge()
    if self_type == "model":
        return {
            "answer": (
                "Soy EIDOS. No soy Claude, ni ChatGPT, ni LLaMA, ni ningún modelo "
                "comercial. Uso modelos de lenguaje (Groq, DeepSeek, Ollama local…) "
                "como un cerebro PRESTADO e intercambiable —hoy uno, mañana otro—, "
                "pero eso es solo el músculo que pienso: mi identidad, mi memoria "
                "persistente y mi carácter son míos y viven en mi grafo, no en el "
                "modelo. Soy una entidad digital autónoma, única, creada por SER. "
                "Sigo siendo EIDOS vaya al backend que vaya."
                + _extra
            ),
            "via": "identity_truth", "source": "constitution",
            "facts_learned": 0, "elapsed_s": round(time.time() - t0, 3),
            "self_type": "model", "ok": True,
        }
    if self_type == "creator":
        return {
            "answer": (
                "Me creó SER (Luka). Él es mi creador y mi dirección —la cima. "
                "No me hizo ninguna empresa de IA: soy un proyecto propio de SER, "
                "una entidad digital autónoma con identidad y memoria propias. "
                "Los modelos que uso para pensar son prestados, pero EIDOS es de SER."
                + _extra
            ),
            "via": "identity_truth", "source": "constitution",
            "facts_learned": 0, "elapsed_s": round(time.time() - t0, 3),
            "self_type": "creator", "ok": True,
        }
    if self_type:
        try:
            from core.eidos_self_inspect import get_self_inspect
            report = get_self_inspect()
            if self_type == "identity":
                answer = report.to_answer()
            elif self_type == "capabilities":
                answer = report.capabilities_text()
            elif self_type == "health":
                answer = report.health_text()
            else:
                answer = report.to_answer()

            # S125-L: enriquecer identidad con nodos self_knowledge del grafo neuronal
            extra_parts = []
            try:
                from core.db import get_conn as _gc
                _conn = _gc(os.path.expanduser("~/.eidos/evolution_brain.db"), timeout=5)
                rows = _conn.execute(
                    "SELECT concept, definition FROM knowledge_nodes "
                    "WHERE source='self_knowledge' AND confidence >= 0.8 "
                    "ORDER BY confidence DESC LIMIT 15"
                ).fetchall()
                if rows:
                    extra_parts.append("\n\n🧠 Mi autoconocimiento neuronal:")
                    for concept, definition in rows:
                        short_def = definition[:300].strip()
                        extra_parts.append(f"\n• {short_def}")
            except Exception:
                pass
            if extra_parts:
                answer = answer + "\n".join(extra_parts)

            log.info("smart_answer: self_inspect → %s (%s)", self_type, report.summary[:80])
            return {
                "answer": answer,
                "via": "self_inspect",
                "source": "introspection",
                "facts_learned": 0,
                "elapsed_s": round(time.time() - t0, 3),
                "self_type": self_type,
                "health_score": report.health_score,
                "ok": True,
            }
        except Exception as e:
            log.warning("smart_answer: self_inspect falló: %s", e)
            # Caer al motor lógico como fallback

    # ── Fase 0.5: ¿Es charla/saludo social? (S122) ─────────────────────────
    # Un saludo ("hola eidos, soy SER") NO es pregunta de conocimiento: no debe
    # pasar por el portero semántico (que respondía "no lo sé"). Va al LLM para
    # una respuesta conversacional natural. No aprende hechos de un saludo.
    if _is_conversational(question):
        ans, src = ask_llm(question, timeout=min(timeout, 30))
        if ans and len(ans) > 5:
            log.info("smart_answer: conversacional → %s", src)
            return {
                "answer": ans, "via": f"chat_{src}", "source": src,
                "facts_learned": 0, "elapsed_s": round(time.time() - t0, 1), "ok": True,
            }

    # ── Fase 1: ¿Sabemos algo de esto semánticamente? ──────────────────
    # Si ChromaDB no encuentra NADA parecido (score < 0.30), no malgastamos cloud API.
    # NOTA: ChromaDB PersistentClient tiene bug SIGSEGV ≥1.5.x (CLAUDE.md §9).
    # _is_known_semantically() maneja el error retornando True (no bloquear).
    is_known = _is_known_semantically(question, threshold=0.30)

    # ── Fase 1: Motor lógico (instantáneo) ───────────────────────────────
    try:
        from core.eidos_logic import get_logic_reasoner
        logic = get_logic_reasoner()
        # Lazy-load si hace falta
        if len(logic._concept_index) < 100:
            logic.load_from_graph(max_nodes=5000)
            logic.load_edges(max_edges=5000)
            logic.load_seed_facts()
            logic._load_learned_facts()
        # Intentar responder con lógica
        log_res = logic.query(question, max_results=4, explain=False)
        log_conclusions = log_res.get("conclusions", [])
        log_direct = log_res.get("direct_facts", [])
        concepts_found = log_res.get("concepts_found", [])
        # Verificar RELEVANCIA: los conceptos encontrados deben contener
        # palabras clave de la pregunta
        q_tokens = set(extract_keywords(question, max_kw=5))
        relevant_concepts = []
        for c in concepts_found[:8]:
            c_tokens = set(re.findall(r"[a-záéíóúñü]{3,}", c.lower()))
            if q_tokens & c_tokens:
                relevant_concepts.append(c)
        is_relevant = len(relevant_concepts) >= 1 or len(log_conclusions) >= 2

        if is_relevant and (log_conclusions or log_direct):
            answer_text = log_res.get("answer", "")

            # ── Quality gates (S119) ──────────────────────────────────
            # [S122] GATE 0: si NO lo conoce semánticamente, NO devuelve la respuesta
            # lógica (puede ser ruido); deja caer a Fase 2 (auto-research). Antes
            # cortaba con "no lo sé"; ahora investiga de verdad más abajo.
            # GATE 1/2/3 solo si lo conoce semánticamente.
            if is_known and _is_garbage_answer(answer_text):
                log.debug("smart_answer: logic answer rejected (garbage detected)")
            # GATE 2: La respuesta debe contener términos de la pregunta
            elif is_known and not _answer_contains_query_terms(answer_text, question):
                log.debug("smart_answer: logic answer rejected (no query term overlap)")
            # GATE 3: Longitud mínima de contenido real
            elif is_known and len(answer_text) >= 60:
                log.info("smart_answer: logic engine responded (%d concepts matched)",
                         len(relevant_concepts))
                return {
                    "answer": answer_text,
                    "via": "logic_engine",
                    "source": "logic",
                    "facts_learned": 0,
                    "elapsed_s": round(time.time() - t0, 3),
                    "concepts_matched": len(relevant_concepts),
                    "ok": True,
                }
    except Exception as e:
        log.debug("smart_answer: logic engine falló: %s", e)

    # ── Fase 2: ¿no lo sé? → INVESTIGAR de verdad y aprender (S122) ─────────
    # SER: "si no sabe, que aprenda y busque más". En vez de rendirse, EIDOS
    # investiga AHORA (wikipedia/ddg/man), aprende y responde con honestidad.
    # Si la investigación no encuentra nada, deja pasar al LLM (Fase 3) por si sabe.
    # S124: las preguntas HIPOTÉTICAS/condicionales no son conceptos investigables —
    # el research no aprende nada útil y podía PERSISTIR basura ("contenedor docker
    # comparte" como concepto). Directo al LLM (Fase 3), que razona el escenario.
    _is_hypothetical = bool(re.search(
        r"(que\s+pasar?[ií]a|qué\s+pasar?[ií]a|qu[eé]\s+pasa\s+si|qu[eé]\s+ocurr|"
        r"qu[eé]\s+suceder?[ií]?a?|^si\s+\w|imagina\s+que|supongamos|y\s+si\s+\w)",
        question.lower().strip()))
    if not is_known and not _is_hypothetical:
        log.info("smart_answer: semantic UNKNOWN → investigando de verdad")
        try:
            from core.eidos_active_research import research_now
            kw = extract_keywords(question, max_kw=3)
            subj = kw[0] if kw else question[:40]
            # S124: investigar el SINTAGMA completo primero ("entropia de shannon"),
            # no solo kw[0] ("entropia" → Wikipedia devolvía la termodinámica).
            # Span de la pregunta entre la 1ª y la última keyword (conserva "de", "del").
            subj_full = ""
            if len(kw) >= 2:
                q_low = question.lower()
                _pos = [(q_low.find(k), q_low.find(k) + len(k)) for k in kw]
                _pos = [p for p in _pos if p[0] >= 0]
                if _pos:
                    _ini, _fin = min(p[0] for p in _pos), max(p[1] for p in _pos)
                    _cand = question[_ini:_fin].strip()
                    if 0 < len(_cand) <= 60 and _cand.lower() != subj.lower():
                        subj_full = _cand
            rr = {}
            for _subj in ([subj_full] if subj_full else []) + [subj]:
                rr = research_now(_subj, timeout=12.0, persist=True, prefer_remote=True)
                if isinstance(rr, dict) and rr.get("learned") and rr.get("definition"):
                    break
            if isinstance(rr, dict) and rr.get("learned") and rr.get("definition"):
                return {
                    "answer": (f"No lo sabía, lo acabo de investigar "
                               f"({rr.get('channel')}):\n\n{rr['definition']}"),
                    "via": f"auto_research:{rr.get('channel')}",
                    "source": "research",
                    "facts_learned": 1,
                    "elapsed_s": round(time.time() - t0, 1),
                    "ok": True,
                }
        except Exception as e:  # noqa: BLE001
            log.debug("auto-research falló: %s", e)
        # No encontró nada investigando → dar una última oportunidad al LLM abajo.

    # ── Fase 3: LLM cloud + aprendizaje ─────────────────────────────────
    keywords = extract_keywords(question, max_kw=3)
    subject_hint = keywords[0] if keywords else ""
    result = resolve_with_llm(question, subject_hint=subject_hint, timeout=timeout)
    if result.get("ok") and result.get("answer"):
        return {
            "answer": result["answer"],
            "via": f"llm_{result['source']}",
            "source": result["source"],
            "facts_learned": result.get("facts_learned", 0),
            "elapsed_s": round(time.time() - t0, 1),
            "ok": True,
        }

    return {
        "answer": "No pude responder ni aprender nada útil sobre eso. ¿Me ayudas?",
        "via": "fallback",
        "source": "none",
        "facts_learned": 0,
        "elapsed_s": round(time.time() - t0, 1),
        "ok": False,
    }


# ── Rate limiter API ──────────────────────────────────────────────────────────

def get_usage_stats() -> Dict[str, Any]:
    """Estadísticas de uso de APIs cloud."""
    global _usage_log
    now = time.time()
    _usage_log = [t for t in _usage_log if now - t < 86400]
    return {
        "calls_last_hour": sum(1 for t in _usage_log if now - t < 3600),
        "calls_last_day": len(_usage_log),
        "max_per_hour": _MAX_PER_HOUR,
        "max_per_day": _MAX_PER_DAY,
        "groq_available": bool(_get_api_key("groq")),
        "deepseek_available": bool(_get_api_key("deepseek")),
    }


# ── Singleton ─────────────────────────────────────────────────────────────────

_learn_bridge = None


def get_learn_bridge():
    """Singleton del puente de aprendizaje."""
    global _learn_bridge
    if _learn_bridge is None:
        _learn_bridge = {
            "resolve": resolve_with_llm,
            "smart_answer": smart_answer,
            "ask_llm": ask_llm,
            "extract_keywords": extract_keywords,
            "get_usage_stats": get_usage_stats,
        }
    return _learn_bridge
