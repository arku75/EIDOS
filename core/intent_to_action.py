"""
EIDOS core/intent_to_action.py — Intent-to-Action Mapper
=========================================================
Convierte el lenguaje de SER directamente en cadenas de tools concretas,
igual que Antigravity hace internamente — pero con memoria persistente.

EIDOS aprende de cada conversación exitosa y guarda el patrón en
EIDOS_Knowledge/patterns.json para reconocerlo siempre.

Uso:
    from core.intent_to_action import IntentMapper
    mapper = IntentMapper()
    result = mapper.match("escanea la red local con nmap")
    # -> {"intent": "kali_recon", "tool_chain": ["kali_tool_info", "exec_shell"], "confidence": 0.9}
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

EIDOS_DIR      = os.path.expanduser("~/EIDOS")
PATTERNS_FILE  = os.path.join(EIDOS_DIR, "EIDOS_Knowledge", "patterns.json")
LEARNED_FILE   = os.path.expanduser("~/.eidos/learned_patterns.json")
OLLAMA_URL     = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

os.makedirs(os.path.dirname(LEARNED_FILE), exist_ok=True)


@dataclass
class MatchResult:
    intent:        str            # ID del patrón (ip001..ip020 o "learned_xxx" o "unknown")
    category:      str            # Categoría semántica
    tool_chain:    list[str]      # Tools a ejecutar en orden
    confidence:    float          # 0.0 – 1.0
    extracted:     dict[str, str] # Variables extraídas del mensaje
    description:   str            # Descripción del patrón
    command:       str = ""       # Comando pre-construido si lo hay
    is_learned:    bool = False   # Si viene de un patrón aprendido


class IntentMapper:
    """
    Mapea intenciones de SER a cadenas de tools de EIDOS.
    Usa matching por keywords + LLM de bajo coste para casos ambiguos.
    Aprende y guarda nuevos patrones automáticamente.
    """

    def __init__(self) -> None:
        self._patterns: list[dict]   = []
        self._learned:  list[dict]   = []
        self._ser_profile: dict      = {}
        self._shortcuts:   dict      = {}
        self._load()

    def _load(self) -> None:
        """Carga patterns.json y los patrones aprendidos."""
        try:
            with open(PATTERNS_FILE) as f:
                data = json.load(f)
            self._patterns    = data.get("intent_patterns", [])
            self._ser_profile = data.get("ser_language_profile", {})
            self._shortcuts   = self._ser_profile.get("shortcuts", {})
        except Exception as e:
            print(f"[INTENT] Warn: No se pudo cargar patterns.json: {e}")

        try:
            with open(LEARNED_FILE) as f:
                self._learned = json.load(f)
        except Exception:
            self._learned = []

    def _save_learned(self) -> None:
        """Persiste los patrones aprendidos."""
        try:
            with open(LEARNED_FILE, "w") as f:
                json.dump(self._learned, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[INTENT] Error guardando learned_patterns: {e}")

    # ── Normalización ──────────────────────────────────────────────────────

    def _normalize(self, text: str) -> str:
        """Normaliza el texto: minúsculas, quita puntuación extra, expande abreviaciones."""
        text = text.lower().strip()
        # Expande abreviaciones de SER
        replacements = {
            " xq ": " porque ", " tb ": " también ", " q ": " que ",
            "pq": "porque", "d ": "de ", " pa ": " para ",
            "gral": "general", "stgo": "Santiago",
        }
        for abbr, full in replacements.items():
            text = text.replace(abbr, full)
        return text

    # ── Matching directo por keywords ──────────────────────────────────────

    def _keyword_match(self, text: str) -> list[tuple[dict, float]]:
        """
        Busca patrones cuyas triggers coincidan con el texto.
        Retorna lista de (patrón, score) ordenada por score desc.
        """
        norm = self._normalize(text)
        matches: list[tuple[dict, float]] = []

        all_patterns = self._patterns + self._learned

        for pattern in all_patterns:
            triggers = pattern.get("triggers", [])
            best_score = 0.0
            for trigger in triggers:
                trig_norm = trigger.lower()
                if trig_norm in norm:
                    # Score: qué fracción del trigger está en el texto
                    score = len(trig_norm) / max(len(norm), 1)
                    # Bonus si es match exacto de toda la frase
                    if norm.startswith(trig_norm) or norm == trig_norm:
                        score += 0.3
                    best_score = max(best_score, score)
            if best_score > 0:
                matches.append((pattern, min(best_score, 1.0)))

        matches.sort(key=lambda x: (-x[1], -x[0].get("priority", 1)))  # pyre-ignore[arg-type]
        return matches

    # ── Extracción de variables ────────────────────────────────────────────

    def _extract_variables(self, text: str, pattern: dict) -> dict[str, str]:
        """
        Extrae variables del texto según las hints del patrón.
        Estrategia simple: lo que va después del trigger es el valor.
        """
        extracted: dict[str, str] = {}
        extract_hints = pattern.get("extract", {})
        norm = self._normalize(text)

        for var_name, hint in extract_hints.items():
            # Intentar extraer URL
            if var_name == "url":
                url_match = re.search(r'https?://\S+|www\.\S+', text, re.I)
                if url_match:
                    extracted["url"] = url_match.group(0)
                    continue
                # Si no hay URL explícita, extraer lo que viene después de triggers
                for trig in pattern.get("triggers", []):
                    trig_l = trig.lower()
                    if trig_l in norm:
                        after = norm.split(trig_l, 1)[-1].strip()
                        if after:
                            # Si no empieza por http, añadir https://
                            if not after.startswith("http"):
                                after = "https://" + after
                            extracted["url"] = after
                            break

            # Extraer texto/comando: lo que viene después del trigger
            elif var_name in ("text", "command", "package", "tool", "query"):
                for trig in pattern.get("triggers", []):
                    trig_l = trig.lower()
                    if trig_l in norm:
                        after = text[norm.index(trig_l) + len(trig_l):].strip()
                        if after:
                            extracted[var_name] = after
                            break

            # Extraer ruta de archivo
            elif var_name == "path":
                path_match = re.search(r'[~/][\w/._-]+', text)
                if path_match:
                    extracted["path"] = path_match.group(0)

        # Añadir comando_template si existe
        if "command_template" in pattern:
            extracted["command"] = pattern["command_template"]

        return extracted

    # ── Match principal ────────────────────────────────────────────────────

    def match(self, user_message: str) -> MatchResult:
        """
        Analiza el mensaje de SER y devuelve el MatchResult con el patrón más relevante.

        Pasos:
          1. Comprobar shortcuts directos (hazlo, sigue, etc.)
          2. Keyword matching contra patrones base y aprendidos
          3. Si confianza < 0.3 → llamar a LLM ligero para clasificar
          4. Si es desconocido → devolver MatchResult con intent="unknown"
        """
        norm = self._normalize(user_message)

        # 1. Shortcuts directos
        for shortcut, description in self._shortcuts.items():
            if norm == shortcut or norm.startswith(shortcut + " "):
                return MatchResult(
                    intent      = f"shortcut_{shortcut}",
                    category    = "shortcut",
                    tool_chain  = [],
                    confidence  = 1.0,
                    extracted   = {"shortcut_action": description},
                    description = description,
                )

        # 2. Keyword matching
        matches = self._keyword_match(user_message)
        if matches:
            best_pattern, best_score = matches[0]  # pyre-ignore[arg-type]
            if best_score >= 0.15:  # Umbral bajo — mejor falso positivo que no hacer nada
                extracted = self._extract_variables(user_message, best_pattern)
                return MatchResult(
                    intent      = best_pattern.get("id", "unknown"),
                    category    = best_pattern.get("category", ""),
                    tool_chain  = best_pattern.get("tool_chain", []),
                    confidence  = best_score,
                    extracted   = extracted,
                    description = best_pattern.get("description", ""),
                    command     = extracted.get("command", ""),
                    is_learned  = best_pattern.get("_learned", False),
                )

        # 3. LLM fallback para mensajes ambiguos
        llm_result = self._llm_classify(user_message)
        if llm_result:
            return llm_result

        # 4. Desconocido — dejar al LLM principal decidir
        return MatchResult(
            intent     = "unknown",
            category   = "chat",
            tool_chain = [],
            confidence = 0.0,
            extracted  = {},
            description= "Intención no reconocida — pasar al LLM principal",
        )

    def _llm_classify(self, message: str) -> MatchResult | None:
        """Usa lfm2.5-thinking:1.2b para clasificar intenciones ambiguas."""
        categories_list = "\n".join(
            f"  - {p['id']}: {p['category']} — {p['description']}"
            for p in self._patterns[:15]  # pyre-ignore[arg-type]
        )
        prompt = (
            f"Classifica este mensaje en una de las categorías:\n{categories_list}\n"
            f"  - unknown: no encaja en ninguna\n\n"
            f"Mensaje: \"{message}\"\n"
            f"Responde SOLO con el ID (ej: ip003) o 'unknown'. Sin explicación."
        )
        try:
            payload = json.dumps({
                "model":    "lfm2.5-thinking:1.2b",
                "stream":   False,
                "messages": [{"role": "user", "content": prompt}],
                "options":  {"num_predict": 10, "temperature": 0.1},
            }).encode()
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.load(resp)
            intent_id = result.get("message", {}).get("content", "").strip().lower()
            intent_id = re.sub(r'[^a-z0-9_]', '', intent_id)

            # Buscar el patrón correspondiente
            matched = next((p for p in self._patterns if p["id"] == intent_id), None)
            if matched:
                extracted = self._extract_variables(message, matched)
                return MatchResult(
                    intent     = matched["id"],
                    category   = matched.get("category", ""),
                    tool_chain = matched.get("tool_chain", []),
                    confidence = 0.55,  # LLM classify — confianza media
                    extracted  = extracted,
                    description= matched.get("description", ""),
                    is_learned = False,
                )
        except Exception:
            pass  # error no crítico, continuar
        return None

    # ── Aprendizaje ────────────────────────────────────────────────────────

    def learn(self, message: str, tool_chain: list[str], category: str,
              description: str = "", feedback_positive: bool = True) -> None:
        """
        Guarda un patrón aprendido de una interacción exitosa con SER.
        Solo aprende si el feedback es positivo (SER no corrigió a EIDOS).

        Args:
            message:          Mensaje original de SER
            tool_chain:       Tools que EIDOS usó y funcionaron
            category:         Categoría semántica
            description:      Qué quería SER
            feedback_positive: Si SER quedó satisfecho con el resultado
        """
        if not feedback_positive:
            return

        # Extraer triggers del mensaje (primeras 3-5 palabras)
        words = message.lower().split()
        trigger = " ".join(words[:min(4, len(words))])

        # Evitar duplicados
        existing = [p for p in self._learned if trigger in p.get("triggers", [])]
        if existing:
            # Actualizar frecuencia
            existing[0]["frequency"] = existing[0].get("frequency", 1) + 1  # pyre-ignore[arg-type]
            self._save_learned()
            return

        new_pattern = {
            "id":          f"learned_{int(time.time())}",
            "category":    category,
            "triggers":    [trigger],
            "tool_chain":  tool_chain,
            "description": description or f"SER dijo: '{message[:50]}'",  # pyre-ignore[arg-type]
            "priority":    1,
            "frequency":   1,
            "learned_from": message,
            "learned_at":  time.strftime("%Y-%m-%dT%H:%M:%S"),
            "_learned":    True,
        }
        self._learned.append(new_pattern)
        self._save_learned()
        print(f"[INTENT] 🧠 Nuevo patrón aprendido: '{trigger}' → {tool_chain}")

        # ── F31: Auto-evolución del SOUL.md ──────────────────────────────
        # Cada vez que EIDOS aprende un nuevo patrón de SER,
        # el SOUL.md se regenera automáticamente para reflejarlo.
        try:
            from core.soul_builder import rebuild_if_new_patterns
            if rebuild_if_new_patterns():
                print(f"[SOUL] 🔄 SOUL.md actualizado con nuevo patrón: '{trigger}'")
        except Exception:
            pass  # soul_builder opcional


    def forget(self, pattern_id: str) -> bool:
        """Elimina un patrón aprendido (si SER lo corrige)."""
        before = len(self._learned)
        self._learned = [p for p in self._learned if p.get("id") != pattern_id]
        if len(self._learned) < before:
            self._save_learned()
            return True
        return False

    # ── Contexto para el prompt ────────────────────────────────────────────

    def context_for_prompt(self, top_n: int = 10) -> str:
        """
        Genera texto compacto con los patrones más frecuentes para inyectar
        en el system prompt de EIDOS antes de cada conversación.
        """
        lines = ["## Intenciones frecuentes de SER:\n"]
        sorted_patterns = sorted(
            self._patterns[:top_n],
            key=lambda p: p.get("priority", 1)
        )
        for p in sorted_patterns:
            triggers_sample = ", ".join(f'"{t}"' for t in p["triggers"][:3])  # pyre-ignore[arg-type]
            tools_str = " → ".join(p["tool_chain"])
            lines.append(f'- {triggers_sample} → {tools_str}')

        if self._learned:
            lines.append("\n## Patrones aprendidos de SER:")
            for p in sorted(self._learned, key=lambda x: -x.get("frequency", 1))[:5]:  # pyre-ignore[arg-type]
                lines.append(f'- "{p["triggers"][0]}" → {" → ".join(p["tool_chain"])} (usado {p.get("frequency",1)}x)')  # pyre-ignore[arg-type]

        return "\n".join(lines)

    def status(self) -> str:
        return (
            f"[INTENT MAPPER] {len(self._patterns)} patrones base | "
            f"{len(self._learned)} patrones aprendidos"
        )


# Singleton global
_mapper: IntentMapper | None = None

def get_mapper() -> IntentMapper:
    global _mapper
    if _mapper is None:
        _mapper = IntentMapper()
    return _mapper


# ── CLI rápido ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    mapper = get_mapper()
    print(mapper.status())
    tests = [
        "escanea la red con nmap",
        "ábrelo en firefox",
        "qué ves en pantalla",
        "instala wireshark",
        "ejecuta ls -lh /home/ser",
        "screenshot del móvil",
        "hazlo",
        "cuánta ram tienes",
    ]
    msgs = sys.argv[1:] if len(sys.argv) > 1 else tests  # pyre-ignore[arg-type]
    for msg in msgs:
        r = mapper.match(msg)
        conf = f"{r.confidence:.0%}"
        tools = " → ".join(r.tool_chain) if r.tool_chain else "(sin tools)"
        print(f"\n  \"{msg}\"")
        print(f"  → {r.intent} [{conf}] {tools}")
        if r.extracted:
            print(f"  → extraído: {r.extracted}")
