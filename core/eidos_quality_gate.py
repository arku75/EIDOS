"""
core/eidos_quality_gate.py — PORTERO DE CALIDAD del grafo de conocimiento [S121]
================================================================================
Problema que resuelve: el grafo se llenaba de ruido (94% basura) porque los
nodos entraban SIN control de calidad — todos con quality_score=0.3 por defecto,
y la calidad real solo se calculaba DESPUÉS, en la curación manual.

Este módulo es el "portero" en la PUERTA: evalúa cada nodo ANTES de insertarlo,
reutilizando la lógica de calidad que ya existe (eidos_curate_graph._compute_quality),
y decide si entra, con qué quality_score real, o si se rechaza por basura.

NO reimplementa el scoring: lo reutiliza. Solo añade reglas de contenido
(definiciones vacías/cortas, conceptos vacíos) y un umbral de admisión.

Uso típico (en cualquier insertador de nodos):
    from core.eidos_quality_gate import gate
    verdict = gate.evaluate(concept, definition, source, category, confidence)
    if verdict.admit:
        conn.execute("INSERT ... quality_score) VALUES (..., ?)", (..., verdict.quality_score))
    # else: descartar (verdict.reason explica por qué)

El umbral es configurable vía env EIDOS_GATE_MIN_QUALITY (default 0.20) para que
SER pueda afinarlo sin tocar código.
"""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("eidos.quality_gate")

# Umbral mínimo de calidad para admitir un nodo. Configurable por SER.
# 0.20 = deja fuera wordnet (0.05), reasoned ruidoso (0.10), mitre/circl/nvd (0.0);
# deja pasar research, docs, kali_tools, distilled, auto_learner, etc.
MIN_QUALITY = float(os.environ.get("EIDOS_GATE_MIN_QUALITY", "0.20"))

# Reglas de contenido (basura evidente independientemente de la fuente)
MIN_DEFINITION_LEN = 15   # una definición útil tiene al menos ~15 chars
MIN_CONCEPT_LEN = 2       # un concepto válido tiene al menos 2 chars
MAX_CONCEPT_LEN = 200     # conceptos absurdamente largos suelen ser ruido


@dataclass
class Verdict:
    """Resultado de evaluar un nodo en el portero."""
    admit: bool
    quality_score: float
    reason: str


def _compute_quality_safe(source: str, category: str, confidence: float) -> float:
    """Reutiliza _compute_quality del curador. Si no está disponible, fallback conservador."""
    try:
        from core.eidos_curate_graph import _compute_quality
        return _compute_quality(source or "", category or "", confidence or 0.5)
    except Exception as e:  # noqa: BLE001
        log.debug("no se pudo importar _compute_quality (%s), fallback", e)
        # Fallback: usar la confianza como proxy, penalizada
        return min(0.5, (confidence or 0.5) * 0.5)


class QualityGate:
    """Portero de calidad: decide qué nodos entran al grafo."""

    def __init__(self, min_quality: float = MIN_QUALITY):
        self.min_quality = min_quality
        self.stats = {"admitted": 0, "rejected": 0}

    def evaluate(self, concept: str, definition: str, source: str,
                 category: str = "general", confidence: float = 0.5) -> Verdict:
        """Evalúa un nodo candidato. No toca la DB — solo decide."""
        concept = (concept or "").strip()
        definition = (definition or "").strip()

        # 1. Reglas de contenido (basura evidente)
        if len(concept) < MIN_CONCEPT_LEN:
            return self._reject(0.0, "concepto vacío o muy corto")
        if len(concept) > MAX_CONCEPT_LEN:
            return self._reject(0.0, "concepto absurdamente largo")
        if len(definition) < MIN_DEFINITION_LEN:
            return self._reject(0.0, f"definición < {MIN_DEFINITION_LEN} chars")

        # 2. Calidad calculada (reutiliza la lógica del curador)
        q = _compute_quality_safe(source, category, confidence)

        # 3. Umbral de admisión
        if q < self.min_quality:
            return self._reject(q, f"quality {q:.3f} < umbral {self.min_quality:.2f} (fuente '{source}')")

        self.stats["admitted"] += 1
        return Verdict(admit=True, quality_score=round(q, 3),
                       reason=f"admitido (quality {q:.3f})")

    def _reject(self, q: float, reason: str) -> Verdict:
        self.stats["rejected"] += 1
        return Verdict(admit=False, quality_score=round(q, 3), reason=reason)

    def get_stats(self) -> dict:
        total = self.stats["admitted"] + self.stats["rejected"]
        rate = self.stats["admitted"] / total if total else 0.0
        return {**self.stats, "total": total, "admit_rate": round(rate, 3)}


# Singleton para uso directo: from core.eidos_quality_gate import gate
gate = QualityGate()


def evaluate(concept: str, definition: str, source: str,
             category: str = "general", confidence: float = 0.5) -> Verdict:
    """Atajo a gate.evaluate()."""
    return gate.evaluate(concept, definition, source, category, confidence)


if __name__ == "__main__":
    # Asegurar que core.* es importable al ejecutar standalone
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # Autotest: casos buenos y malos
    print(f"=== Portero de calidad EIDOS (umbral={MIN_QUALITY}) ===\n")
    casos = [
        ("nginx", "Servidor web y proxy inverso de alto rendimiento usado para servir contenido.", "kali_tools", "general", 0.9),
        ("ssh", "Protocolo de red para acceso remoto seguro mediante cifrado.", "research:man", "general", 0.8),
        ("xyz", "es algo", "reasoned", "inferred", 0.5),            # def muy corta
        ("foo", "Una inferencia de baja confianza sobre un tema oscuro.", "reasoned", "inferred", 0.5),  # reasoned ruido
        ("dog", "A domesticated carnivorous mammal (synset).", "wordnet", "synset", 0.5),  # wordnet basura
        ("", "definición sin concepto", "research:wikipedia", "general", 0.7),  # concepto vacío
        ("kubernetes", "Sistema de orquestación de contenedores para automatizar despliegues.", "research:active:wikipedia", "general", 0.75),
    ]
    for concept, defn, src, cat, conf in casos:
        v = evaluate(concept, defn, src, cat, conf)
        flag = "✅ ADMITE" if v.admit else "❌ RECHAZA"
        print(f"  {flag}  '{concept[:20]}' [{src}] q={v.quality_score} — {v.reason}")
    print(f"\n  Stats: {gate.get_stats()}")
