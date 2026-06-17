"""
EIDOS Smart Router — Routing LLM con 15 dimensiones de scoring
Inspirado en ClawRouter: analiza cada request y enruta al modelo óptimo.

Perfiles: ECO (más barato), AUTO (balance), PREMIUM (más potente)
"""

import os
import re
import json
import sqlite3
import time
import math
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any
from dataclasses import dataclass, field
from core.db import get_conn

# ─── Constantes ──────────────────────────────────────────────────────────────

EIDOS_DIR = Path.home() / ".eidos"
DB_PATH = EIDOS_DIR / "router.db"

# ─── Dimensiones ─────────────────────────────────────────────────────────────

DIMENSIONS = [
    "complexity", "code_generation", "reasoning_depth", "creativity",
    "factual_precision", "context_length", "tool_use", "multimodal",
    "speed_sensitivity", "domain_expertise", "conversation_depth",
    "safety_sensitivity", "language_complexity", "math_logic", "memory_need"
]

# ─── Modelos disponibles ─────────────────────────────────────────────────────

MODELS = {
    # ── FAST/DEFAULT: lfm2.5-thinking:1.2b — conversación natural, memoria persistente ──
    # Hermes está específicamente entrenado para conversación y memoria.
    # Es el modelo "por defecto" de EIDOS: rápido, natural, entiende el contexto.
    "lfm2.5-thinking:1.2b": {
        "tier": "FAST",
        "capabilities": {
            "complexity": 0.65, "code_generation": 0.6, "reasoning_depth": 0.65,
            "creativity": 0.8, "factual_precision": 0.7, "context_length": 0.8,
            "tool_use": 0.65, "multimodal": 0.0, "speed_sensitivity": 0.8,
            "domain_expertise": 0.7, "conversation_depth": 0.95,
            "safety_sensitivity": 0.6, "language_complexity": 0.8,
            "math_logic": 0.6, "memory_need": 0.85
        },
        "speed": 0.75, "cost": 0.3, "ram_mb": 4800
    },
    # ── BALANCED: general, coding, análisis ───────────────────────────────────
    "lfm2.5-thinking:1.2b": {
        "tier": "BALANCED",
        "capabilities": {
            "complexity": 0.65, "code_generation": 0.75, "reasoning_depth": 0.65,
            "creativity": 0.6, "factual_precision": 0.7, "context_length": 0.7,
            "tool_use": 0.7, "multimodal": 0.0, "speed_sensitivity": 0.7,
            "domain_expertise": 0.65, "conversation_depth": 0.7,
            "safety_sensitivity": 0.6, "language_complexity": 0.7,
            "math_logic": 0.7, "memory_need": 0.6
        },
        "speed": 0.7, "cost": 0.35, "ram_mb": 4800
    },
    "llama3.1:8b": {
        "tier": "BALANCED",
        "capabilities": {
            "complexity": 0.7, "code_generation": 0.65, "reasoning_depth": 0.7,
            "creativity": 0.7, "factual_precision": 0.72, "context_length": 0.75,
            "tool_use": 0.7, "multimodal": 0.0, "speed_sensitivity": 0.65,
            "domain_expertise": 0.7, "conversation_depth": 0.75,
            "safety_sensitivity": 0.65, "language_complexity": 0.75,
            "math_logic": 0.65, "memory_need": 0.65
        },
        "speed": 0.65, "cost": 0.35, "ram_mb": 5000
    },
    "dolphin3:8b": {
        "tier": "BALANCED",
        "capabilities": {
            "complexity": 0.65, "code_generation": 0.6, "reasoning_depth": 0.65,
            "creativity": 0.7, "factual_precision": 0.65, "context_length": 0.7,
            "tool_use": 0.65, "multimodal": 0.0, "speed_sensitivity": 0.7,
            "domain_expertise": 0.65, "conversation_depth": 0.75,
            "safety_sensitivity": 0.5, "language_complexity": 0.7,
            "math_logic": 0.55, "memory_need": 0.6
        },
        "speed": 0.7, "cost": 0.3, "ram_mb": 5000
    },
    # ── HEAVY: razonamiento profundo, tareas complejas ────────────────────────
    "deepseek-r1:14b": {
        "tier": "HEAVY",
        "capabilities": {
            "complexity": 0.95, "code_generation": 0.85, "reasoning_depth": 0.98,
            "creativity": 0.75, "factual_precision": 0.9, "context_length": 0.85,
            "tool_use": 0.8, "multimodal": 0.0, "speed_sensitivity": 0.2,
            "domain_expertise": 0.9, "conversation_depth": 0.85,
            "safety_sensitivity": 0.8, "language_complexity": 0.85,
            "math_logic": 0.95, "memory_need": 0.9
        },
        "speed": 0.3, "cost": 0.8, "ram_mb": 9500
    },
    # ── VISION: multimodal ────────────────────────────────────────────────────
    "moondream:latest": {
        "tier": "VISION",
        "capabilities": {
            "complexity": 0.65, "code_generation": 0.6, "reasoning_depth": 0.65,
            "creativity": 0.65, "factual_precision": 0.7, "context_length": 0.7,
            "tool_use": 0.6, "multimodal": 1.0, "speed_sensitivity": 0.5,
            "domain_expertise": 0.65, "conversation_depth": 0.65,
            "safety_sensitivity": 0.6, "language_complexity": 0.7,
            "math_logic": 0.6, "memory_need": 0.6
        },
        "speed": 0.5, "cost": 0.5, "ram_mb": 6200
    },
    # ── EMBED: embeddings ─────────────────────────────────────────────────────
    "nomic-embed-text": {
        "tier": "EMBED",
        "capabilities": {
            "complexity": 0.1, "code_generation": 0.0, "reasoning_depth": 0.0,
            "creativity": 0.0, "factual_precision": 0.1, "context_length": 0.3,
            "tool_use": 0.0, "multimodal": 0.0, "speed_sensitivity": 1.0,
            "domain_expertise": 0.1, "conversation_depth": 0.0,
            "safety_sensitivity": 0.0, "language_complexity": 0.1,
            "math_logic": 0.0, "memory_need": 0.8
        },
        "speed": 1.0, "cost": 0.05, "ram_mb": 300
    }
}

PROFILES = {
    "eco": {"speed_weight": 0.4, "cost_weight": 0.5, "quality_weight": 0.1},
    "auto": {"speed_weight": 0.2, "cost_weight": 0.2, "quality_weight": 0.6},
    "premium": {"speed_weight": 0.0, "cost_weight": 0.0, "quality_weight": 1.0},
}

# ─── Patterns para análisis ─────────────────────────────────────────────────

CODE_PATTERNS = [
    r'\bwrite\s+(a\s+)?code\b', r'\bfunction\b.*\bthat\b', r'\bimplement\b',
    r'\bclass\b.*\bfor\b', r'\bscript\b', r'\bprogram\b', r'\bapi\b',
    r'\bfix\b.*\bbug\b', r'\brefactor\b', r'\bdebug\b',
    r'\bpython\b', r'\bjavascript\b', r'\brush\b', r'\bgo\b.*\bcode\b',
    r'```', r'\bdef\s+\w+', r'\bclass\s+\w+',
    # español
    r'\brefactoriza\b', r'\bimplementa\b', r'\bc[oó]digo\b', r'\bfunci[oó]n\b',
    r'\bescribe\b.*\bc[oó]digo\b', r'\barregla\b', r'\boptimiza\b',
]

REASONING_PATTERNS = [
    r'\bwhy\b', r'\bexplain\b', r'\banalyze\b', r'\bcompare\b',
    r'\bpros\s+and\s+cons\b', r'\btrade.?off\b', r'\breason\b',
    r'\bthink\s+through\b', r'\bstep\s+by\s+step\b', r'\barchitect\b',
    # español
    r'\bexplica\b', r'\banaliza\b', r'\bpor\s+qu[eé]\b', r'\bcompara\b',
    r'\bqu[eé]\s+sabes\b', r'\bqu[eé]\s+es\b', r'\bc[oó]mo\s+funciona\b',
    r'\bdescribe\b', r'\bqu[eé]\s+diferencia\b',
]

RESEARCH_PATTERNS = [
    r'\binvestiga\b', r'\bbusca\b', r'\bresearch\b', r'\blook\s+up\b',
    r'\bfind\s+information\b', r'\bhabre\b.*\bnavegador\b', r'\babre\b.*\bnavegador\b',
    r'\bnavega\b', r'\bbrowse\b', r'\bopenclaw\b', r'\bdocs?\b.*\burl\b',
    r'https?://', r'\binstalaci[oó]n\b', r'\bdocumentaci[oó]n\b',
    r'\binstall\b', r'\bsetup\b', r'\bconfigura\b', r'\bwebsite\b',
]

GREETING_PATTERNS = [
    r'^hola\b', r'^hola\s+eidos\b', r'^hey\b', r'^buenos\s+d[ií]as\b',
    r'^buenas\s+tardes\b', r'^buenas\s+noches\b', r'^hi\b', r'^hello\b',
    r'^c[oó]mo\s+est[aá]s\b', r'^qu[eé]\s+tal\b', r'^saludos\b',
    r'^qu[eé]\s+haces\b', r'^qu[eé]\s+puedes\b',
]

CREATIVE_PATTERNS = [
    r'\bcreate\b', r'\bgenerate\b', r'\bwrite\s+a\s+story\b',
    r'\bimagine\b', r'\bdesign\b', r'\binvent\b', r'\bbrainstorm\b',
]

MATH_PATTERNS = [
    r'\bcalculate\b', r'\bformula\b', r'\bequation\b', r'\bproof\b',
    r'\bmath\b', r'\balgorithm\b', r'\boptimize\b', r'\bstatistic\b',
    r'\d+\s*[+\-*/^]\s*\d+',
]

SAFETY_PATTERNS = [
    r'\bhack\b', r'\bexploit\b', r'\bvulnerability\b', r'\bpassword\b',
    r'\binjection\b', r'\bmalware\b', r'\breverse\s+shell\b',
]


@dataclass
class RoutingResult:
    model: str
    tier: str
    scores: Dict[str, float]
    reasoning: str
    confidence: float
    profile_used: str


class SmartRouter:
    """
    Router inteligente que analiza prompts en 15 dimensiones
    y selecciona el modelo Ollama óptimo.
    """

    def __init__(self, models: Dict = None):
        EIDOS_DIR.mkdir(parents=True, exist_ok=True)
        self.models = models or MODELS
        self._adjustments = {}  # feedback-based adjustments - MUST initialize BEFORE _init_db
        self.db = get_conn(str(DB_PATH), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS routing_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_hash TEXT,
                model_chosen TEXT,
                profile TEXT,
                scores TEXT,
                confidence REAL,
                feedback TEXT DEFAULT NULL,
                timestamp REAL
            );
            CREATE TABLE IF NOT EXISTS model_feedback (
                model TEXT,
                dimension TEXT,
                adjustment REAL DEFAULT 0,
                sample_count INTEGER DEFAULT 0,
                PRIMARY KEY (model, dimension)
            );
        """)
        self.db.commit()
        self._load_adjustments()

    def _load_adjustments(self):
        """Carga ajustes de feedback previo"""
        rows = self.db.execute("SELECT * FROM model_feedback").fetchall()
        for r in rows:
            key = f"{r['model']}_{r['dimension']}"
            self._adjustments[key] = r["adjustment"]

    def _count_patterns(self, text: str, patterns: List[str]) -> float:
        """Cuenta matches de patrones y normaliza a 0-1"""
        text_lower = text.lower()
        count = sum(1 for p in patterns if re.search(p, text_lower))
        return min(count / max(len(patterns) * 0.3, 1), 1.0)

    def analyze(self, prompt: str, context: str = None) -> Dict[str, float]:
        """
        Analiza un prompt en las 15 dimensiones.

        Returns:
            Dict con score 0-1 para cada dimensión.
        """
        full_text = prompt + (" " + context if context else "")
        text_lower = full_text.lower()
        word_count = len(full_text.split())

        scores = {}

        # 1. Complexity (basado en longitud y estructura)
        scores["complexity"] = min(word_count / 200, 1.0)

        # 2. Code generation
        scores["code_generation"] = self._count_patterns(full_text, CODE_PATTERNS)

        # 3. Reasoning depth
        scores["reasoning_depth"] = self._count_patterns(full_text, REASONING_PATTERNS)

        # 4. Creativity
        scores["creativity"] = self._count_patterns(full_text, CREATIVE_PATTERNS)

        # 5. Factual precision
        has_factual = any(w in text_lower for w in
                         ["exactly", "precisely", "accurate", "fact", "correct", "true"])
        scores["factual_precision"] = 0.7 if has_factual else 0.3

        # 6. Context length
        scores["context_length"] = min(word_count / 500, 1.0)

        # 7. Tool use
        has_tool = any(w in text_lower for w in
                       ["run", "execute", "shell", "command", "install", "scan", "file"])
        scores["tool_use"] = 0.8 if has_tool else 0.2

        # 8. Multimodal
        has_visual = any(w in text_lower for w in
                         ["image", "screenshot", "photo", "picture", "visual", "see", "look"])
        scores["multimodal"] = 0.9 if has_visual else 0.0

        # 9. Speed sensitivity
        has_urgent = any(w in text_lower for w in
                         ["quick", "fast", "rapid", "immediately", "asap", "rápido", "ya"])
        scores["speed_sensitivity"] = 0.9 if has_urgent else 0.3

        # 10. Domain expertise
        domain_terms = ["kubernetes", "docker", "nmap", "metasploit", "burp",
                        "wireshark", "systemd", "nginx", "postgresql", "redis"]
        domain_count = sum(1 for t in domain_terms if t in text_lower)
        scores["domain_expertise"] = min(domain_count / 3, 1.0)

        # 11. Conversation depth
        scores["conversation_depth"] = 0.3 if word_count < 20 else min(word_count / 100, 0.9)

        # 12. Safety sensitivity
        scores["safety_sensitivity"] = self._count_patterns(full_text, SAFETY_PATTERNS)

        # 13. Language complexity
        avg_word_len = sum(len(w) for w in full_text.split()) / max(word_count, 1)
        scores["language_complexity"] = min(avg_word_len / 8, 1.0)

        # 14. Math/Logic
        scores["math_logic"] = self._count_patterns(full_text, MATH_PATTERNS)

        # 15. Memory need
        has_memory = any(w in text_lower for w in
                         ["remember", "recall", "previous", "before", "history", "last time"])
        scores["memory_need"] = 0.8 if has_memory else 0.2

        return scores

    def route(self, prompt: str, context: str = None,
              profile: str = "auto") -> RoutingResult:
        """
        Analiza el prompt y selecciona el modelo óptimo.
        Verifica disponibilidad real en Ollama antes de devolver.

        Args:
            prompt: El prompt del usuario
            context: Contexto adicional
            profile: "eco", "auto", o "premium"

        Returns:
            RoutingResult con modelo, scores, reasoning
        """
        if profile not in PROFILES:
            profile = "auto"

        scores = self.analyze(prompt, context)
        profile_weights = PROFILES[profile]
        text_lower = prompt.lower()

        # Forzar modelo de visión si es multimodal
        if scores["multimodal"] > 0.5:
            return RoutingResult(
                model="moondream:latest",
                tier="VISION",
                scores=scores,
                reasoning="Multimodal content detected → vision model required",
                confidence=0.95,
                profile_used=profile
            )

        # Shortcut: investigación / web research → modelo balanceado (tiene prioridad sobre saludo)
        is_research = any(re.search(p, text_lower) for p in RESEARCH_PATTERNS)
        if is_research:
            return RoutingResult(
                model=self._ensure_available("lfm2.5-thinking:1.2b"),
                tier="BALANCED",
                scores=scores,
                reasoning="Research/web/docs task detected → balanced model",
                confidence=0.88,
                profile_used=profile
            )

        # Shortcut: saludo/pregunta simple → modelo rápido (solo si no hay research)
        is_greeting = any(re.search(p, text_lower) for p in GREETING_PATTERNS)
        word_count = len(prompt.split())
        if is_greeting and word_count <= 12:
            return RoutingResult(
                model=self._ensure_available("lfm2.5-thinking:1.2b"),
                tier="FAST",
                scores=scores,
                reasoning="Greeting/simple question detected → fast model",
                confidence=0.92,
                profile_used=profile
            )

        # Shortcut: código complejo / refactoring / sistema → heavy
        heavy_code_patterns = [
            r'\brefactori[czs]\w*\b',       # refactoriza, refactorices, refactorizas...
            r'\brefactor\b',
            r'\bimplementa\s+\w+\s*sistema\b', r'\bimplementa\s+sistema\b',
            r'\bsistema\s+completo\b', r'\bsistema\s+de\s+\w+', r'\bfull\s+system\b',
            r'\bc[oó]digo\s+completo\b', r'\bimplementaci[oó]n\s+completa\b',
            r'\bimplement\s+(?:a\s+)?(?:full|complete|complex)\b',
            r'\barchitect\b', r'\bdesign\s+(?:a\s+)?system\b',
        ]
        is_heavy_code = any(re.search(p, text_lower) for p in heavy_code_patterns)
        if is_heavy_code:
            return RoutingResult(
                model=self._ensure_available("deepseek-r1:14b"),
                tier="HEAVY",
                scores=scores,
                reasoning="Complex code/system task detected → heavy model",
                confidence=0.85,
                profile_used=profile
            )

        # Shortcut: análisis / explicación detallada → modelo balanceado
        analysis_patterns = [
            r'\banaliza\b', r'\banalyze\b',
            r'\bexplica\b', r'\bexplain\b',
            r'\bc[oó]mo\s+funciona\b', r'\bpor\s+qu[eé]\b',
            r'\bqu[eé]\s+hace\b', r'\bqu[eé]\s+es\b',
            r'\bpaso\s+a\s+paso\b', r'\bstep\s+by\s+step\b',
            r'\bdetalladamente\b', r'\ben\s+profundidad\b',
            r'\bdiferencia\b', r'\bcompara\b', r'\bcompare\b',
            r'\bventajas\b', r'\bdesventajas\b', r'\bpros\b.*\bcons\b',
            r'\bqu[eé]\s+diferencia\b', r'\bcuál\s+es\s+mejor\b',
        ]
        is_analysis = any(re.search(p, text_lower) for p in analysis_patterns)
        if is_analysis and word_count >= 3:
            return RoutingResult(
                model=self._ensure_available("lfm2.5-thinking:1.2b"),
                tier="BALANCED",
                scores=scores,
                reasoning="Analysis/explanation task detected → balanced model",
                confidence=0.82,
                profile_used=profile
            )

        # Forzar embeddings si solo necesita embedding
        if scores["memory_need"] > 0.7 and scores["complexity"] < 0.2:
            return RoutingResult(
                model="nomic-embed-text",
                tier="EMBED",
                scores=scores,
                reasoning="Embedding/memory task detected",
                confidence=0.9,
                profile_used=profile
            )

        # Scoring de cada modelo
        best_model = None
        best_score = -1
        model_scores = {}

        for model_name, model_info in self.models.items():
            if model_info["tier"] in ["EMBED"]:
                continue  # Solo para embedding explícito

            caps = model_info["capabilities"]

            # Quality score: qué tan bien satisface las dimensiones requeridas
            quality = 0
            total_weight = 0
            for dim in DIMENSIONS:
                need = scores.get(dim, 0)
                capability = caps.get(dim, 0)
                # Aplicar ajustes de feedback
                adj_key = f"{model_name}_{dim}"
                capability += self._adjustments.get(adj_key, 0)
                capability = max(0, min(1, capability))

                weight = need  # Dimensiones más necesitadas pesan más
                quality += min(capability / max(need, 0.1), 1.0) * weight
                total_weight += weight

            quality = quality / max(total_weight, 1)

            # Score final ponderado por perfil
            speed = model_info["speed"]
            cost = 1 - model_info["cost"]  # Invertir: menor costo = mejor
            final = (
                quality * profile_weights["quality_weight"] +
                speed * profile_weights["speed_weight"] +
                cost * profile_weights["cost_weight"]
            )

            model_scores[model_name] = {"quality": quality, "speed": speed,
                                         "cost": cost, "final": final}

            if final > best_score:
                best_score = final
                best_model = model_name

        # Calcular confianza
        sorted_scores = sorted(model_scores.values(), key=lambda x: x["final"], reverse=True)
        if len(sorted_scores) >= 2:
            confidence = sorted_scores[0]["final"] - sorted_scores[1]["final"]
            confidence = min(confidence * 5, 1.0)  # Normalizar
        else:
            confidence = 1.0

        # Generar reasoning
        top_dims = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:3]
        dim_str = ", ".join(f"{d}={v:.2f}" for d, v in top_dims)
        reasoning = (
            f"Top dimensions: {dim_str}. "
            f"Profile {profile}: {best_model} "
            f"(quality={model_scores[best_model]['quality']:.2f}, "
            f"speed={model_scores[best_model]['speed']:.2f})"
        )

        result = RoutingResult(
            model=best_model,
            tier=self.models[best_model]["tier"],
            scores=scores,
            reasoning=reasoning,
            confidence=confidence,
            profile_used=profile
        )

        # Log en DB
        prompt_hash = str(hash(prompt))[:12]
        self.db.execute("""
            INSERT INTO routing_history (prompt_hash, model_chosen, profile, scores, confidence, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (prompt_hash, best_model, profile, json.dumps(scores), confidence, time.time()))
        self.db.commit()

        # Verificar disponibilidad real en Ollama (Mac :11435 vía OLLAMA_URL)
        result.model = self._ensure_available(result.model)

        return result

    def _ensure_available(self, model: str) -> str:
        """Comprueba si el modelo está en Ollama (Mac); si no, hace fallback
        a lfm2.5-thinking:1.2b o el primer modelo de chat disponible en el Mac."""
        try:
            import urllib.request, json as _json
            ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11435")
            with urllib.request.urlopen(f"{ollama_url}/api/tags", timeout=3) as r:
                tags = _json.loads(r.read())
                available = {m["name"].split(":")[0] for m in tags.get("models", [])}
                available |= {m["name"] for m in tags.get("models", [])}
            model_base = model.split(":")[0]
            if model_base in available or model in available:
                return model
            # Fallback preferente: lfm2.5 (modelos locales disponibles)
            if "lfm2.5" in str(available):
                return "lfm2.5-thinking:1.2b"
            # Si no, el primer modelo de chat disponible (no embeddings)
            for m in tags.get("models", []):
                name = m["name"]
                if "nomic" not in name and "embed" not in name:
                    return name
            return "lfm2.5-thinking:1.2b"
        except Exception:
            return model  # si no puede verificar, confiar en el original

    def record_feedback(self, model: str, success: bool, dimensions: Dict[str, float] = None):
        """
        Registra feedback sobre un modelo para mejorar routing futuro.

        Args:
            model: Nombre del modelo
            success: ¿El resultado fue exitoso?
            dimensions: Scores de dimensiones donde falló/acertó
        """
        if not dimensions:
            return

        adjustment = 0.05 if success else -0.05

        for dim, score in dimensions.items():
            if score > 0.3:  # Solo ajustar dimensiones relevantes
                key = f"{model}_{dim}"
                current = self._adjustments.get(key, 0)
                new_adj = current + adjustment
                new_adj = max(-0.3, min(0.3, new_adj))  # Limitar ajuste
                self._adjustments[key] = new_adj

                self.db.execute("""
                    INSERT OR REPLACE INTO model_feedback (model, dimension, adjustment, sample_count)
                    VALUES (?, ?, ?, COALESCE(
                        (SELECT sample_count + 1 FROM model_feedback WHERE model = ? AND dimension = ?), 1
                    ))
                """, (model, dim, new_adj, model, dim))

        self.db.commit()

    def get_stats(self) -> Dict:
        """Estadísticas de routing"""
        total = self.db.execute("SELECT COUNT(*) FROM routing_history").fetchone()[0]
        by_model = {}
        for row in self.db.execute(
            "SELECT model_chosen, COUNT(*) as c FROM routing_history GROUP BY model_chosen"
        ).fetchall():
            by_model[row["model_chosen"]] = row["c"]

        by_profile = {}
        for row in self.db.execute(
            "SELECT profile, COUNT(*) as c FROM routing_history GROUP BY profile"
        ).fetchall():
            by_profile[row["profile"]] = row["c"]

        return {
            "total_routes": total,
            "by_model": by_model,
            "by_profile": by_profile,
            "adjustments": len(self._adjustments),
        }

    def __del__(self):
        try:
            self.db.close()
        except Exception:
            pass  # error no crítico, continuar
# ─── Test ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Test Smart Router ===\n")

    router = SmartRouter()

    tests = [
        ("Write a Python function that sorts a list", "auto", "code task"),
        ("What is the capital of France?", "eco", "simple factual"),
        ("Analyze this screenshot and tell me what you see", "auto", "vision task"),
        ("Explain step by step how TCP/IP handshake works and compare with UDP", "premium", "deep reasoning"),
        ("quick status check", "eco", "fast simple"),
        ("Calculate the eigenvalues of matrix [[1,2],[3,4]]", "auto", "math task"),
        ("Remember what we discussed last time about the database", "auto", "memory task"),
        ("Run nmap scan on the target network", "auto", "tool use + security"),
    ]

    for prompt, profile, desc in tests:
        result = router.route(prompt, profile=profile)
        print(f"📝 {desc}")
        print(f"   Prompt: \"{prompt[:50]}...\"")
        print(f"   Profile: {profile} → Model: {result.model} ({result.tier})")
        print(f"   Confidence: {result.confidence:.2f}")
        print(f"   {result.reasoning}")
        print()

    # Test feedback
    print("=== Test Feedback ===")
    router.record_feedback("lfm2.5-thinking:1.2b", True, {"reasoning_depth": 0.8, "complexity": 0.7})
    router.record_feedback("lfm2.5-thinking:1.2b", False, {"reasoning_depth": 0.7})
    print("  ✅ Feedback registrado\n")

    # Stats
    stats = router.get_stats()
    print(f"=== Stats ===")
    print(f"  Total routes: {stats['total_routes']}")
    print(f"  By model: {stats['by_model']}")
    print(f"  By profile: {stats['by_profile']}")

    print("\n✅ Smart Router funcional")
