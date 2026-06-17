"""
core/eidos_character_lens.py — Lente de Sesgo para Personajes Colony [S98]

"Como gafas que tiñen el mundo según quien mira" — DeepSeek

Cada personaje Colony ve el grafo de conocimiento a través de su propio
CharacterLens: una matriz de sesgo ligero que amplifica (1.5x) los conceptos
afines y atenúa (0.5x) los que le disgustan.

No es censura — es perspectiva. El mismo nodo del grafo activa diferente
según quien lo mire. Esto genera diversidad real en el diálogo interno.

La matriz de sesgo se inicializa con tags manuales y APRENDE por uso:
cuando un personaje interviene repetidamente sobre un tema, su afinidad
por ese tema crece.

Uso:
    lens = CharacterLens(char_id="coder_01", char_data={...})
    adjusted = lens.apply("docker", base_strength=0.8)  # → 1.2 si es afin
    lens.learn_from_use("docker")  # refuerza afinidad
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger("eidos.character_lens")

LENS_DB = Path.home() / ".eidos" / "character_lenses.json"

# ── Tags por defecto para personajes seed ────────────────────────────────────
DEFAULT_AFFINITY = {
    "Curador": ["memoria", "historia", "aprendizaje", "documentacion",
                "conocimiento", "archivo", "preservar", "recordar", "pasado",
                "identidad", "legado", "cultura"],
    "Explorador": ["descubrimiento", "nuevo", "exploracion", "busqueda",
                   "investigacion", "curiosidad", "desconocido", "aventura",
                   "expansion", "frontera", "mapear", "territorio"],
    "Crítico": ["error", "fallo", "inconsistencia", "verificacion",
                "prueba", "validacion", "limite", "borde", "restriccion",
                "seguridad", "riesgo", "condicion"],
    "Coder": ["codigo", "algoritmo", "implementacion", "funcion",
              "clase", "modulo", "sintaxis", "compilacion", "ejecucion",
              "debug", "refactor", "arquitectura"],
    "Analyst": ["dato", "patron", "analisis", "estadistica", "tendencia",
                "correlacion", "hipotesis", "modelo", "prediccion",
                "metrica", "grafico", "inferencia"],
    "Vision": ["imagen", "vision", "visual", "reconocimiento", "patron",
               "forma", "color", "espacio", "perspectiva", "diseño",
               "estetica", "representacion"],
    "Lumen": ["emocion", "sentimiento", "intuicion", "empatia", "conexion",
              "relacion", "confianza", "calma", "energia", "luz",
              "sombra", "esencia"],
}

DEFAULT_AVERSION = {
    "Curador": ["borrar", "perder", "olvido", "caos", "desorden",
                "fragmentacion", "desaparicion"],
    "Explorador": ["estancamiento", "repeticion", "limite", "barrera",
                   "conformismo", "rutina", "encierro"],
    "Crítico": ["dogma", "fe_ciega", "asumir", "sobreoptimismo",
                "sesgo", "complacencia", "precipitacion"],
    "Coder": ["codigo_duro", "magia", "redundancia", "acoplamiento",
              "global_state", "race_condition", "memory_leak"],
    "Analyst": ["ruido", "outlier_no_verificado", "correlacion_espuria",
                "sobreajuste", "muestra_pequeña", "sesgo_muestreo"],
    "Vision": ["ceguera", "opacidad", "distorsion", "espejismo",
               "pixelacion", "borrosidad", "falta_de_contexto"],
    "Lumen": ["frialdad", "indiferencia", "desconexion", "aislamiento",
              "apatia", "crueldad", "vacio_emocional"],
}

# Umbral de aprendizaje
LEARN_THRESHOLD = 3  # intervenciones para empezar a aprender afinidad
MAX_LEARNED_TAGS = 20
LEARNED_BOOST = 0.3  # boost incremental por uso (máx 0.5 adicional)


class CharacterLens:
    """Lente de sesgo para un personaje Colony.

    Amplifica (1.5x) tags afines, atenúa (0.5x) tags aversivos,
    y aprende afinidades por uso repetido.
    """

    def __init__(self, char_id: str, char_data: Dict[str, Any] = None):
        self.char_id = char_id
        self.name = (char_data or {}).get("name", char_id)
        self.affinity_tags: Set[str] = set()
        self.aversion_tags: Set[str] = set()
        self.learned_affinity: Dict[str, float] = {}  # tag → boost acumulado
        self.topic_usage: Dict[str, int] = {}  # tag → veces usado en diálogo
        self.intervention_count: int = 0  # total de intervenciones

        # Cargar defaults
        name = self.name
        if name in DEFAULT_AFFINITY:
            self.affinity_tags.update(DEFAULT_AFFINITY[name])
        if name in DEFAULT_AVERSION:
            self.aversion_tags.update(DEFAULT_AVERSION[name])

        # Cargar aprendido de disco
        self._load()

    # ═══════════════════════════════════════════════════════════════════════════
    # Aplicación del sesgo
    # ═══════════════════════════════════════════════════════════════════════════

    def apply(self, concept: str, base_strength: float) -> float:
        """Aplica el sesgo de este personaje a un concepto/topic.

        Args:
            concept: el concepto o topic a evaluar
            base_strength: fuerza base (0.0-1.0) antes del sesgo

        Returns:
            fuerza ajustada (0.0-1.0) después del sesgo
        """
        concept_lower = concept.lower()
        multiplier = 1.0

        # 1. Afinidad explícita
        for tag in self.affinity_tags:
            if tag in concept_lower or concept_lower in tag:
                multiplier = 1.5
                break

        # 2. Aversión explícita
        for tag in self.aversion_tags:
            if tag in concept_lower or concept_lower in tag:
                multiplier = 0.5
                break

        # 3. Afinidad aprendida (se suma al boost, máx 2.0 total)
        learned_boost = 0.0
        for tag, boost in self.learned_affinity.items():
            if tag in concept_lower or concept_lower in tag:
                learned_boost = max(learned_boost, boost)
        multiplier += learned_boost

        # 4. Si hay conflicto afinidad+aversión, gana la afinidad
        # (los personajes son optimistas por defecto)

        adjusted = min(1.0, max(0.1, base_strength * multiplier))
        return round(adjusted, 4)

    def apply_batch(self, concepts: List[Tuple[str, float]]
                   ) -> List[Tuple[str, float]]:
        """Aplica sesgo a una lista de (concepto, fuerza)."""
        return [(c, self.apply(c, s)) for c, s in concepts]

    # ═══════════════════════════════════════════════════════════════════════════
    # Aprendizaje por uso
    # ═══════════════════════════════════════════════════════════════════════════

    def learn_from_use(self, topic: str):
        """Aprende afinidad por un tema al usarlo en diálogo.

        Cada intervención del personaje sobre un tema refuerza
        su afinidad aprendida por ese tema.
        """
        topic_lower = topic.lower().strip()
        self.intervention_count += 1
        self.topic_usage[topic_lower] = self.topic_usage.get(topic_lower, 0) + 1

        count = self.topic_usage[topic_lower]
        if count >= LEARN_THRESHOLD:
            # Boost incremental: cada intervención añade 0.3, máx 0.5 total
            boost = min(0.5, (count - LEARN_THRESHOLD + 1) * LEARNED_BOOST)
            self.learned_affinity[topic_lower] = boost

            # Limpiar tags muy viejos si hay demasiados
            if len(self.learned_affinity) > MAX_LEARNED_TAGS:
                # Eliminar los de menor boost
                sorted_tags = sorted(
                    self.learned_affinity.items(), key=lambda x: x[1])
                for old_tag, _ in sorted_tags[
                    :len(self.learned_affinity) - MAX_LEARNED_TAGS
                ]:
                    del self.learned_affinity[old_tag]

        self._save()

    def get_perspective_name(self, topic: str) -> str:
        """Nombra la perspectiva de este personaje sobre un tema."""
        topic_lower = topic.lower()
        for tag in self.affinity_tags:
            if tag in topic_lower or topic_lower in tag:
                return f"afinidad natural por '{tag}'"
        for tag in self.aversion_tags:
            if tag in topic_lower or topic_lower in tag:
                return f"desconfianza hacia '{tag}'"
        for tag, boost in self.learned_affinity.items():
            if tag in topic_lower or topic_lower in tag:
                return f"interés cultivado por '{tag}' (boost +{boost:.1f})"
        return "perspectiva neutra"

    # ═══════════════════════════════════════════════════════════════════════════
    # Sesgo inverso (para antítesis en debate)
    # ═══════════════════════════════════════════════════════════════════════════

    def invert(self, concept: str, base_strength: float) -> float:
        """Aplica el sesgo INVERSO: atenúa afinidades, amplifica aversiones.

        Útil para generar la antítesis en un debate interno.
        """
        concept_lower = concept.lower()
        multiplier = 1.0

        for tag in self.affinity_tags:
            if tag in concept_lower or concept_lower in tag:
                multiplier = 0.5  # afinidad se vuelve duda
                break
        for tag in self.aversion_tags:
            if tag in concept_lower or concept_lower in tag:
                multiplier = 1.5  # aversión se vuelve curiosidad
                break

        adjusted = min(1.0, max(0.1, base_strength * multiplier))
        return round(adjusted, 4)

    # ═══════════════════════════════════════════════════════════════════════════
    # Persistencia
    # ═══════════════════════════════════════════════════════════════════════════

    def to_dict(self) -> Dict[str, Any]:
        return {
            "char_id": self.char_id,
            "name": self.name,
            "affinity_tags": sorted(self.affinity_tags),
            "aversion_tags": sorted(self.aversion_tags),
            "learned_affinity": self.learned_affinity,
            "topic_usage": self.topic_usage,
            "intervention_count": self.intervention_count,
        }

    def _save(self):
        try:
            all_lenses = {}
            if LENS_DB.exists():
                all_lenses = json.loads(LENS_DB.read_text())
            all_lenses[self.char_id] = self.to_dict()
            LENS_DB.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(str(LENS_DB.parent), 0o700)
            LENS_DB.write_text(json.dumps(all_lenses, indent=2, ensure_ascii=False))
        except Exception:
            pass

    def _load(self):
        try:
            if LENS_DB.exists():
                all_lenses = json.loads(LENS_DB.read_text())
                if self.char_id in all_lenses:
                    data = all_lenses[self.char_id]
                    self.affinity_tags.update(data.get("affinity_tags", []))
                    self.aversion_tags.update(data.get("aversion_tags", []))
                    self.learned_affinity = data.get("learned_affinity", {})
                    self.topic_usage = data.get("topic_usage", {})
                    self.intervention_count = data.get("intervention_count", 0)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════════════════
    # Stats
    # ═══════════════════════════════════════════════════════════════════════════

    def stats(self) -> Dict[str, Any]:
        return {
            "char_id": self.char_id,
            "name": self.name,
            "affinity_count": len(self.affinity_tags),
            "aversion_count": len(self.aversion_tags),
            "learned_count": len(self.learned_affinity),
            "intervention_count": self.intervention_count,
            "top_topics": sorted(
                self.topic_usage.items(), key=lambda x: x[1], reverse=True
            )[:5],
        }


# ── Singleton Manager ──────────────────────────────────────────────────────────

class CharacterLensManager:
    """Gestiona las lentes de todos los personajes Colony."""

    def __init__(self):
        self._lenses: Dict[str, CharacterLens] = {}
        self._load_all()

    def get_lens(self, char_id: str, char_data: Dict[str, Any] = None
                ) -> CharacterLens:
        """Obtiene o crea la lente para un personaje."""
        if char_id not in self._lenses:
            self._lenses[char_id] = CharacterLens(char_id, char_data)
        elif char_data:
            # Actualizar nombre si cambió
            self._lenses[char_id].name = char_data.get(
                "name", self._lenses[char_id].name)
        return self._lenses[char_id]

    def get_all_lenses(self) -> List[CharacterLens]:
        """Todas las lentes cargadas."""
        return list(self._lenses.values())

    def get_affinity_matrix(self, topic: str
                           ) -> Dict[str, Tuple[float, str]]:
        """Matriz de afinidad: {char_id: (strength, perspectiva)} para un topic."""
        matrix = {}
        for char_id, lens in self._lenses.items():
            strength = lens.apply(topic, 0.7)  # fuerza base 0.7
            perspective = lens.get_perspective_name(topic)
            matrix[char_id] = (strength, perspective)
        return matrix

    def learn_all(self, topic: str, participants: List[str]):
        """Registra aprendizaje para todos los participantes de un diálogo."""
        for char_id in participants:
            if char_id in self._lenses:
                self._lenses[char_id].learn_from_use(topic)

    def _load_all(self):
        """Carga todas las lentes desde disco."""
        try:
            from core.eidos_character_system import list_characters
            chars = list_characters()
            for c in chars:
                self.get_lens(c["char_id"], c)
        except Exception:
            pass

    def stats(self) -> Dict[str, Any]:
        return {
            "total_lenses": len(self._lenses),
            "lenses": {cid: lens.stats() for cid, lens in self._lenses.items()},
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
_lens_manager: Optional[CharacterLensManager] = None


def get_lens_manager() -> CharacterLensManager:
    global _lens_manager
    if _lens_manager is None:
        _lens_manager = CharacterLensManager()
    return _lens_manager


def get_character_lens(char_id: str,
                       char_data: Dict[str, Any] = None) -> CharacterLens:
    """Obtiene la lente para un personaje específico."""
    return get_lens_manager().get_lens(char_id, char_data)


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO)

    p = argparse.ArgumentParser(description="EIDOS CharacterLens")
    p.add_argument("--char", type=str, help="ID o nombre del personaje")
    p.add_argument("--topic", type=str, default="memoria",
                   help="Tema a evaluar (default: memoria)")
    p.add_argument("--learn", action="store_true",
                   help="Registrar aprendizaje sobre el tema")
    p.add_argument("--matrix", action="store_true",
                   help="Mostrar matriz de afinidad completa")
    p.add_argument("--stats", action="store_true",
                   help="Estadísticas de todas las lentes")
    args = p.parse_args()

    mgr = get_lens_manager()

    if args.matrix:
        matrix = mgr.get_affinity_matrix(args.topic)
        print(f"\nMatriz de afinidad para '{args.topic}':")
        for cid, (strength, perspective) in matrix.items():
            bar = "█" * int(strength * 10) + "░" * (10 - int(strength * 10))
            print(f"  {cid:15s} [{bar}] {strength:.2f}  ({perspective})")
    elif args.char:
        lens = get_character_lens(args.char)
        strength = lens.apply(args.topic, 0.7)
        perspective = lens.get_perspective_name(args.topic)
        print(f"{lens.name}: {strength:.2f} ({perspective})")
        if args.learn:
            lens.learn_from_use(args.topic)
            print(f"  ✓ Aprendizaje registrado sobre '{args.topic}'")
    elif args.stats:
        print(json.dumps(mgr.stats(), indent=2, ensure_ascii=False))
    else:
        p.print_help()
