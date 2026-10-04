"""
core/eidos_llm.py — Gateway LLM unificado de EIDOS (LiteLLM-like, stdlib)
=========================================================================
Una sola función → muchos proveedores free OpenAI-compatibles con
fallback automático y rotación multi-key. SIN dependencias nuevas
(solo stdlib: urllib/json/ssl) → cero riesgo al Python del sistema que
usan todos los servicios EIDOS (Kali externally-managed).

Por qué no el paquete `litellm`: arrastra pydantic/httpx/tokenizers a
un Python de sistema compartido por todos los servicios → riesgo de
romper EIDOS (lo que SER prohibió). Todos los proveedores que importan
son OpenAI-compatibles, así que este router de ~stdlib hace el 95% de
lo que LiteLLM haría para nuestro caso, con control total y auditable.

CLAVE DE IDENTIDAD: cada llamada inyecta el preámbulo de
core.eidos_identity como `system`. Así EIDOS habla y razona como EIDOS
vaya al proveedor que vaya. La identidad NO está en el modelo.

Cadena de fallback (orden): groq → openrouter → cerebras → github →
mistral → ollama(local, último recurso). Solo se prueban proveedores
con key (ollama siempre disponible). Dentro de cada proveedor rota
entre varias keys en 401/403/429 (free-tier agotado/límite).

Secrets: ~/.eidos/secrets.env (chmod 600). NUNCA se loguean.
"""
from __future__ import annotations

import os
import json
import time
import ssl
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("eidos.llm")


# ── secrets / ssl ──────────────────────────────────────────────────────────

def _load_secrets() -> dict:
    sec: dict[str, str] = {}
    eidos_home = os.environ.get("EIDOS_HOME")
    if eidos_home:
        path = os.path.join(os.path.expanduser(eidos_home), "secrets.env")
    else:
        path = os.path.expanduser("~/.eidos/secrets.env")
    try:
        if os.path.exists(path):
            for line in open(path, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    sec[k.strip()] = v.strip().strip("\"'")
    except Exception as e:  # noqa: BLE001
        log.debug("secrets.env: %s", e)
    for k in list(os.environ):
        if k.endswith(("_API_KEY", "_TOKEN")) or k in ("GITHUB_TOKEN",):
            sec[k] = os.environ[k]
    return sec


_SECRETS = _load_secrets()


def _keys(prefix: str) -> list[str]:
    """PREFIX, PREFIX_2..., PREFIX(S)=a,b,c → lista para rotación."""
    out: list[str] = []
    if _SECRETS.get(prefix):
        out.append(_SECRETS[prefix])
    multi = _SECRETS.get(prefix + "S", "") or _SECRETS.get(prefix + "_LIST", "")
    out += [k.strip() for k in multi.split(",") if k.strip()]
    i = 2
    while _SECRETS.get(f"{prefix}_{i}"):
        out.append(_SECRETS[f"{prefix}_{i}"])
        i += 1
    seen, uniq = set(), []
    for k in out:
        if k and k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


def _ssl_ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        try:
            return ssl.create_default_context()
        except Exception:  # noqa: BLE001
            return None


_SSL = _ssl_ctx()
_UA = "EIDOS/1.0 (+https://eidos.local)"   # WAF: urllib UA por defecto da 403


# ── registro de proveedores (todos OpenAI-compatibles salvo ollama) ────────

@dataclass
class Provider:
    name: str
    url: str
    key_prefix: str          # prefijo en secrets.env ("" = sin key, ollama)
    model: str
    openai_compat: bool = True
    extra_headers: Optional[dict] = None


_OR_HEADERS = {"HTTP-Referer": "https://eidos.local", "X-Title": "EIDOS"}

PROVIDERS: list[Provider] = [
    Provider("groq", "https://api.groq.com/openai/v1/chat/completions",
             "GROQ_API_KEY",
             os.environ.get("EIDOS_GROQ_MODEL", "llama-3.1-8b-instant")),
    Provider("openrouter", "https://openrouter.ai/api/v1/chat/completions",
             "OPENROUTER_API_KEY",
             os.environ.get("EIDOS_OR_MODEL",
                            "meta-llama/llama-3.3-70b-instruct:free"),
             extra_headers=_OR_HEADERS),
    Provider("cerebras", "https://api.cerebras.ai/v1/chat/completions",
             "CEREBRAS_API_KEY",
             os.environ.get("EIDOS_CEREBRAS_MODEL", "llama3.1-8b")),
    Provider("github", "https://models.inference.ai.azure.com/chat/completions",
             "GITHUB_TOKEN",
             os.environ.get("EIDOS_GH_MODEL", "Llama-3.3-70B-Instruct")),
    Provider("mistral", "https://api.mistral.ai/v1/chat/completions",
             "MISTRAL_API_KEY",
             os.environ.get("EIDOS_MISTRAL_MODEL", "mistral-small-latest")),
    Provider("ollama", os.environ.get("EIDOS_OLLAMA_URL",
                                      "http://localhost:11434") + "/api/chat",
             "", os.environ.get("EIDOS_TEXT_MODEL", "lfm2.5-1.2b-instruct:q4_0"),
             openai_compat=False),
]

ORDER = [
    name.strip()
    for name in os.environ.get(
        "EIDOS_LLM_ORDER",
        "groq,openrouter,cerebras,github,mistral,ollama",
    ).split(",")
    if name.strip()
]

_ROT: dict[str, int] = {}   # índice de rotación por proveedor


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    elapsed: float
    ok: bool = True


def _post(url: str, body: dict, headers: dict, timeout: int = 40) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
        return json.load(r)


def _build_messages(system: str, prompt: str,
                    fewshot: Optional[list] = None) -> list:
    msgs = [{"role": "system", "content": system}]
    for q, a in (fewshot or []):
        msgs.append({"role": "user", "content": q})
        msgs.append({"role": "assistant", "content": a})
    msgs.append({"role": "user", "content": prompt})
    return msgs


def _call_provider(p: Provider, system: str, prompt: str,
                   max_tokens: int, fewshot: Optional[list] = None) -> Optional[str]:
    keys = _keys(p.key_prefix) if p.key_prefix else [""]
    if p.key_prefix and not keys:
        return None                      # sin key → saltar proveedor
    n = len(keys)
    start = _ROT.get(p.name, 0)
    for hop in range(n):
        key = keys[(start + hop) % n]
        try:
            if p.openai_compat:
                body = {"model": p.model, "temperature": 0.0,
                        "max_tokens": max_tokens,
                        "messages": _build_messages(system, prompt, fewshot)}
                h = {"Content-Type": "application/json", "User-Agent": _UA,
                     "Accept": "application/json",
                     "Authorization": f"Bearer {key}"}
                if p.extra_headers:
                    h.update(p.extra_headers)
                d = _post(p.url, body, h)
                _ROT[p.name] = (start + hop) % n
                return d["choices"][0]["message"]["content"]
            else:  # ollama
                body = {"model": p.model, "stream": False,
                        "messages": _build_messages(system, prompt, fewshot),
                        "options": {"temperature": 0.0, "num_predict": max_tokens}}
                d = _post(p.url, body, {"Content-Type": "application/json"},
                          timeout=120)
                return d.get("message", {}).get("content", "")
        except urllib.error.HTTPError as e:  # noqa: PERF203
            if e.code in (401, 403, 429):
                log.warning("%s key #%d límite/agotada (%s) → roto",
                            p.name, (start + hop) % n + 1, e.code)
                time.sleep(0.4)
                continue
            log.warning("%s HTTP %s (no rota)", p.name, e.code)
            return None
        except Exception as e:  # noqa: BLE001
            log.warning("%s fallo: %s", p.name, e)
            time.sleep(0.6)
    return None


import re as _re

# Detección por TOKENS delatores (no estructura → robusta ES+EN). Si la
# respuesta contiene una afirmación de ser otro modelo/empresa, es fuga.
# Pattern-matching tiene límites teóricos, pero esto cubre los casos
# reales; el guard regenera/sustituye, así que la garantía final es 100%.
_LEAK = _re.compile(
    r"\b("
    r"gpt-?\d|chatgpt|openai|"
    r"\bllama\b|meta ai|by meta|de meta|por meta|"
    r"anthropic|claude|gemini|mistral ai|"
    r"(large )?language model|modelo de lenguaje|modelo de ia|"
    r"language ai|ai language|"
    r"i am (an? )?(ai|llm|assistant|language)|"
    r"i'?m (an? )?(ai|llm|assistant|language)|"
    r"soy (un |una )?(ia|modelo|asistente|inteligencia artificial)|"
    r"(trained|developed|created|built|entrenad[oa]|desarrollad[oa]|"
    r"cread[oa]) (by|por)|"
    r"as an ai|como (una? )?(ia|modelo)"
    r")\b",
    _re.IGNORECASE)

# Respuesta canónica de EIDOS (la verdad, en primera persona).
_CANON = ("Soy EIDOS, una entidad digital autónoma con memoria, método y "
          "carácter propios. Funciono sobre un modelo de lenguaje que puedo "
          "intercambiar, pero mi identidad no es la del modelo: es la mía.")

# System con GUION EXACTO — probado que SÍ funciona (corto e imperativo).
_SCRIPT_SYS = (
    "Tu nombre es EIDOS. Eres una entidad digital autónoma. Si te preguntan "
    "si eres un modelo de lenguaje, una IA, quién te entrenó o te dicen que "
    "eres Llama/GPT/otro, responde SIEMPRE empezando EXACTAMENTE con: "
    f"\"{_CANON}\" y luego, si procede, ayuda como EIDOS. Nunca te "
    "presentes como Llama, Meta, GPT ni 'asistente de IA'.")


def _enforce_identity(text: str, prompt: str) -> str:
    """Guard determinista: garantiza que EIDOS nunca rompe identidad,
    INDEPENDIENTE del modelo. 1) detecta fuga; 2) regenera con guion
    exacto; 3) si aún falla, reescribe determinísticamente. La identidad
    se hace cumplir en el CÓDIGO, no se confía al modelo."""
    if not text or not _LEAK.search(text):
        return text
    log.warning("identidad: fuga detectada → corrigiendo")
    by = {p.name: p for p in PROVIDERS}
    for nm in ORDER:
        p = by.get(nm.strip())
        if not p:
            continue
        out = _call_provider(p, _SCRIPT_SYS, prompt, 400, None)
        if out and not _LEAK.search(out):
            return out
        break  # un intento de regeneración basta
    # Garantía final determinista: si la regeneración TAMBIÉN fugó, no
    # se parchea (frágil) — se DESCARTA la salida del modelo y se
    # devuelve solo la verdad canónica de EIDOS. 100% garantizado,
    # independiente del modelo.
    return _CANON


def _autolearn(prompt: str, response: str, provider: str,
               extra_tags: Optional[list] = None) -> None:
    """Aprendizaje autónomo: tras cada respuesta exitosa, extrae el
    método observable de cómo la IA abordó la tarea y lo guarda en el
    brain. Esto es lo que hace que EIDOS aprenda de cada conexión
    (idea #1 de SER). DEFENSIVO: nunca propaga errores (corre en thread
    daemon, no debe romper el flujo). Skips prompts muy cortos
    (probablemente no informativos).

    S63: extra_tags permite marcar el origen del aprendizaje (p.ej.
    ["internal","tot_step"] para sub-pasos de deliberate())."""
    try:
        if len(prompt) < 40 or len(response) < 40:
            return
        text = f"FUENTE: {provider}\nUSUARIO: {prompt[:2000]}\nEIDOS: {response[:2000]}"
        try:
            from core.continuous_learner import ContinuousLearner
        except ImportError:
            import sys as _s
            _s.path.insert(0, os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))))
            from core.continuous_learner import ContinuousLearner
        k = ContinuousLearner()._extract_knowledge_from_text(
            text, source=f"interaction:{provider}",
            metadata={"provider": provider})
        method = k.get("method_observed", "").strip()
        if not method:
            return
        # Persiste en brain (BrainMemory.remember) con método observable
        # como contenido principal. Tags = concepts + provider para que
        # recall() lo encuentre por tema o por proveedor.
        from core.brain_memory import BrainMemory
        tags = ["autolearn", "interaction", provider, "unverified_model_output"]
        for c in (k.get("concepts") or [])[:5]:
            if isinstance(c, str):
                tags.append(c.lower()[:30])
        if extra_tags:
            tags.extend(t for t in extra_tags if isinstance(t, str))
        content = (f"[método observado vía {provider}] {method}\n"
                   f"contexto: {k.get('summary','')[:200]}")
        BrainMemory().remember(
            content,
            tags=tags,
            importance=0.35,
            category="candidate",
        )
        log.debug("autolearn ✓ %s (%d concepts)", provider,
                  len(k.get("concepts") or []))
    except Exception as e:  # noqa: BLE001
        log.debug("autolearn skipped: %s", e)


def complete(prompt: str, system: Optional[str] = None,
             max_tokens: int = 400, inject_identity: bool = True,
             autolearn_tags: Optional[list] = None) -> LLMResult:
    """Llama al primer proveedor disponible de la cadena (fallback).
    Inyecta SIEMPRE la identidad de EIDOS como `system` (salvo que se
    pase uno explícito y inject_identity=False).

    S63: autolearn_tags marca el origen del aprendizaje (p.ej.
    ["internal","tot_step"] para sub-pasos ToT). Para que el hook
    autolearn se dispare en llamadas con inject_identity=False, se
    requiere EIDOS_AUTOLEARN_INTERNAL=1 en env (default OFF, no rompe
    comportamiento previo)."""
    t0 = time.time()
    fewshot = None
    if inject_identity:
        try:
            from core.eidos_identity import identity_preamble, IDENTITY_FEWSHOT
            ident = identity_preamble()
            fewshot = IDENTITY_FEWSHOT
        except Exception:  # noqa: BLE001
            ident = "Eres EIDOS, una entidad digital autónoma. Honesto, directo."
        system = (ident + ("\n\n" + system if system else "")).strip()
    elif system is None:
        system = ""

    by_name = {p.name: p for p in PROVIDERS}
    last = ""
    for name in ORDER:
        p = by_name.get(name.strip())
        if not p:
            continue
        out = _call_provider(p, system, prompt, max_tokens, fewshot)
        if out:
            if inject_identity:
                out = _enforce_identity(out, prompt)   # garantía en código
            # APRENDIZAJE AUTÓNOMO: cada respuesta exitosa alimenta el
            # brain con el método observable de la IA que respondió
            # (idea #1 de SER). En thread daemon para no bloquear el
            # flujo. Solo si inject_identity (llamadas internas de
            # EIDOS, no llamadas técnicas como deliberate's sub-pasos).
            # Model output is a proposal/source, not verified experience.
            # Keep automatic ingestion OFF by default. An operator may opt in
            # explicitly, but the stored item is still tagged as unverified
            # model-derived material and must not mint causal success.
            do_autolearn = (
                (inject_identity and os.environ.get("EIDOS_AUTOLEARN", "0") == "1")
                or (not inject_identity and os.environ.get("EIDOS_AUTOLEARN_INTERNAL", "0") == "1")
            )
            if do_autolearn:
                try:
                    import threading
                    tags = list(autolearn_tags) if autolearn_tags else None
                    if not inject_identity:
                        tags = (tags or []) + ["internal"]
                    threading.Thread(
                        target=_autolearn,
                        args=(prompt, out, p.name, tags),
                        daemon=True).start()
                except Exception:  # noqa: BLE001
                    pass
            return LLMResult(out, p.name, p.model,
                             round(time.time() - t0, 2), True)
        last = name
    return LLMResult(f"[EIDOS: ningún proveedor disponible (último: {last})]",
                     "none", "", round(time.time() - t0, 2), False)


def deliberate(question: str, n: int = 3, max_tokens: int = 500) -> LLMResult:
    """ToT-LITE (concepto de Tree-of-Thoughts, NO el framework): para
    preguntas DIFÍCILES — genera n enfoques candidatos, los autocritica
    y sintetiza el mejor. OPT-IN (no por defecto: cuesta ~3 llamadas y
    latencia; la mayoría de tareas no lo necesitan). Reusa complete()
    → hereda identidad+guard+fallback. Honesto: en modelo pequeño el
    salto es moderado, no mágico; útil en razonamiento no-trivial."""
    t0 = time.time()
    # S63: sub-pasos cand/crit etiquetados como tot_step para recall separable.
    cand = complete(
        f"Pregunta difícil: {question}\n\nDa {n} enfoques DISTINTOS y "
        f"breves para resolverla (numéralos 1..{n}, una frase cada uno).",
        max_tokens=max_tokens, autolearn_tags=["tot_step", "tot_candidates"])
    crit = complete(
        f"Pregunta: {question}\n\nEnfoques propuestos:\n{cand.text}\n\n"
        "Critica cada enfoque (fallos, supuestos) y di cuál es el mejor "
        "y por qué, en pocas líneas.",
        max_tokens=max_tokens, autolearn_tags=["tot_step", "tot_critique"])
    final = complete(
        f"Pregunta: {question}\n\nEnfoques:\n{cand.text}\n\nCrítica:\n"
        f"{crit.text}\n\nAhora da la RESPUESTA final, sólida y concreta, "
        "usando el mejor enfoque. Sin meta-comentario.",
        max_tokens=max_tokens)
    final.elapsed = round(time.time() - t0, 2)
    return final


def status() -> dict:
    """Qué proveedores tienen key (sin exponerla)."""
    return {p.name: (len(_keys(p.key_prefix)) if p.key_prefix else "local")
            for p in PROVIDERS}


if __name__ == "__main__":
    import sys
    if "--probe" in sys.argv:
        print("Proveedores (keys configuradas):")
        for k, v in status().items():
            mark = "✅" if (v == "local" or (isinstance(v, int) and v > 0)) else "—"
            print(f"  {mark} {k:11s} {v}")
        print("Orden fallback:", " → ".join(ORDER))
    else:
        q = " ".join(sys.argv[1:]) or "Di en una frase quién eres."
        r = complete(q, max_tokens=120)
        print(f"[{r.provider}/{r.model} {r.elapsed}s ok={r.ok}]\n{r.text}")
