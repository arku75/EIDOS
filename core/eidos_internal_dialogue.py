"""
core/eidos_internal_dialogue.py — Diálogo Interno entre Personajes Colony [S98]

"El parlamento interior donde EIDOS se piensa a sí mismo" — DeepSeek

Protocolo de debate interno donde los personajes Colony deliberan sobre
un tema usando sus CharacterLens. El diálogo emerge de sus diferencias
de perspectiva — no de un guion predefinido.

3 fases:
  1. APERTURA — cada personaje ve el tema a través de su lente
  2. DEBATE — turnos de réplica con umbral de controversia
  3. CIERRE — síntesis desde la tensión de perspectivas

El diálogo interno es el Nivel 2 de voz (deliberativa): Colony debate →
EIDOS sintetiza. El output alimenta a Logos.spontaneous_speech("insight").

Uso:
    dialogue = InternalDialogue()
    result = dialogue.deliberate(
        topic="¿Por qué SER nunca me ha preguntado cómo estoy?",
        participants=["Curador", "Explorador", "Crítico"],
        max_turns=6
    )
"""

from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.internal_dialogue")

# Umbral de controversia: si la diferencia entre posiciones es mayor,
# el debate continúa. Si es menor, se cierra con síntesis.
CONTROVERSY_THRESHOLD = 0.25
MAX_TURNS = 6
MIN_TURNS = 3

# Tipos de actos de habla en el diálogo interno
SPEECH_ACTS = [
    "AFIRMAR", "CUESTIONAR", "REFORMULAR", "CONCEDER",
    "PROPONER", "OBJETAR", "SINTETIZAR",
]

# Conectores dialécticos según acto de habla
DIALECTICAL_CONNECTORS = {
    "AFIRMAR": ["Desde mi perspectiva, ", "Veo que ", "Para mí, "],
    "CUESTIONAR": ["Pero, ¿y si ", "No estoy seguro de que ", "Cuestiono si "],
    "REFORMULAR": ["Dicho de otro modo: ", "Lo reformulo: ", "En otras palabras, "],
    "CONCEDER": ["Tienes razón en que ", "Concedo que ", "Es cierto que "],
    "PROPONER": ["Propongo que ", "Y si mejor ", "Quizás podríamos "],
    "OBJETAR": ["Discrepo porque ", "No puedo aceptar que ", "Eso ignora que "],
    "SINTETIZAR": ["Uniendo ambas visiones: ", "La síntesis sugiere que ",
                   "Ambas perspectivas apuntan a "],
}


class InternalDialogue:
    """Protocolo de diálogo interno entre personajes Colony.

    Cada personaje aporta su perspectiva sesgada por su CharacterLens.
    El debate emerge de las diferencias, no de reglas fijas.
    """

    def __init__(self):
        self._dialogue_count = 0

    # ═══════════════════════════════════════════════════════════════════════════
    # Deliberación principal
    # ═══════════════════════════════════════════════════════════════════════════

    def deliberate(self, topic: str,
                   participants: List[str] = None,
                   max_turns: int = MAX_TURNS,
                   min_turns: int = MIN_TURNS,
                   controversy_threshold: float = CONTROVERSY_THRESHOLD,
                   record: bool = True) -> Dict[str, Any]:
        """Ejecuta un debate interno completo entre personajes Colony.

        Args:
            topic: el tema a debatir
            participants: lista de char_ids o nombres (default: todos los disponibles)
            max_turns: máximo de intervenciones totales
            min_turns: mínimo de intervenciones antes de poder cerrar
            controversy_threshold: umbral de controversia para continuar
            record: si guardar en self.db

        Returns:
            {
                "topic": str,
                "turns": [{"speaker": str, "act": str, "position": float, "text": str}],
                "resolution": str,
                "controversy": float,
                "participants_learned": bool,
            }
        """
        t0 = time.time()

        # Resolver participantes
        chars = self._resolve_participants(participants)
        if len(chars) < 2:
            return {"error": "Se necesitan al menos 2 personajes Colony",
                    "available": len(chars)}

        # Obtener lentes de cada personaje
        from core.eidos_character_lens import get_character_lens
        lenses = {}
        for c in chars:
            char_id = c.get("char_id", c.get("name", "?"))
            lenses[char_id] = get_character_lens(char_id, c)

        # ── Fase 1: APERTURA — cada personaje ve el tema ──
        opening_positions = {}
        for char_id, lens in lenses.items():
            strength = lens.apply(topic, 0.7)
            perspective = lens.get_perspective_name(topic)
            opening_positions[char_id] = {
                "strength": strength,
                "perspective": perspective,
                "name": lens.name,
            }

        # ── Fase 2: DEBATE — turnos con controversia ──
        turns = []
        # El orden de intervención inicial es por fuerza de afinidad
        speaker_order = sorted(
            opening_positions.items(),
            key=lambda x: x[1]["strength"], reverse=True
        )

        for turn_num in range(max_turns):
            # Seleccionar hablante (round-robin sobre orden de afinidad)
            speaker_idx = turn_num % len(speaker_order)
            speaker_id = speaker_order[speaker_idx][0]
            speaker_lens = lenses[speaker_id]
            speaker_pos = opening_positions[speaker_id]["strength"]

            # Determinar acto de habla según contexto
            if turn_num == 0:
                act = "AFIRMAR"
            elif turn_num < len(speaker_order):
                act = random.choice(["AFIRMAR", "CUESTIONAR", "PROPONER"])
            elif turn_num == max_turns - 1:
                act = "SINTETIZAR"
            else:
                # Si el anterior fue muy opuesto → CONCEDER o REFORMULAR
                # Si fue similar → CUESTIONAR u OBJETAR
                prev_strength = turns[-1]["position"] if turns else 0.5
                diff = abs(speaker_pos - prev_strength)
                if diff > 0.3:
                    act = random.choice(["CONCEDER", "REFORMULAR", "CUESTIONAR"])
                else:
                    act = random.choice(["PROPONER", "OBJETAR", "CUESTIONAR"])

            # Generar texto del turno
            text = self._generate_turn_text(
                speaker_name=speaker_lens.name,
                act=act,
                topic=topic,
                position_strength=speaker_pos,
                perspective=opening_positions[speaker_id]["perspective"],
                previous_turns=turns[-2:] if len(turns) >= 2 else turns,
            )

            turn = {
                "speaker": speaker_id,
                "speaker_name": speaker_lens.name,
                "act": act,
                "position": round(speaker_pos, 3),
                "text": text,
                "turn_number": turn_num + 1,
            }
            turns.append(turn)

            # Registrar en self_core si está disponible
            if record:
                self._record_turn(
                    speaker_id, speaker_order[(turn_num + 1) % len(speaker_order)][0]
                    if turn_num + 1 < max_turns else speaker_id,
                    speaker_lens.name, topic, text, turn_num + 1
                )

            # ¿Parar por consenso?
            if turn_num >= min_turns - 1:
                controversy = self._calculate_controversy(turns)
                if controversy < controversy_threshold:
                    break

        # ── Fase 3: CIERRE — síntesis ──
        controversy = self._calculate_controversy(turns)
        resolution = self._synthesize_resolution(topic, turns, controversy)

        # Registrar aprendizaje
        for char_id in lenses:
            lenses[char_id].learn_from_use(topic)
        from core.eidos_character_lens import get_lens_manager
        get_lens_manager().learn_all(topic, list(lenses.keys()))

        # Si hay registro, guardar resolución
        if record and turns:
            self._record_turn(
                turns[-1]["speaker"], turns[0]["speaker"],
                "SÍNTESIS", topic, resolution,
                len(turns) + 1, resolution=resolution
            )

        self._dialogue_count += 1

        # S103: Meta-cognición sobre el diálogo completado
        meta_insights = []
        try:
            from core.eidos_metacognition import get_metacognition
            meta = get_metacognition()
            meta_insights = meta.reflect_on_dialogue(topic, turns)
        except Exception:
            pass

        return {
            "topic": topic,
            "turns": turns,
            "resolution": resolution,
            "controversy": round(controversy, 3),
            "turn_count": len(turns),
            "participants": list(lenses.keys()),
            "participants_learned": True,
            "elapsed_s": round(time.time() - t0, 3),
            "meta_insights": [m for m in meta_insights],  # S103
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _resolve_participants(participants: List[str] = None
                             ) -> List[Dict[str, Any]]:
        """Resuelve la lista de participantes."""
        try:
            from core.eidos_character_system import list_characters
            all_chars = list_characters()
            if not all_chars:
                return []
            if participants:
                # Buscar por nombre o char_id
                resolved = []
                for p in participants:
                    for c in all_chars:
                        if p == c.get("name") or p == c.get("char_id"):
                            resolved.append(c)
                            break
                return resolved or all_chars[:3]  # fallback: primeros 3
            return all_chars[:4]  # máximo 4 para no eternizar
        except Exception:
            return []

    @staticmethod
    def _generate_turn_text(speaker_name: str, act: str, topic: str,
                            position_strength: float, perspective: str,
                            previous_turns: List[Dict]) -> str:
        """Genera el texto de un turno de diálogo interno."""
        connectors = DIALECTICAL_CONNECTORS.get(act, ["Digo que "])
        connector = random.choice(connectors)

        # Base del turno
        if act == "AFIRMAR":
            return (
                f"{connector}{topic[:100]} conecta con mi {perspective}. "
                f"Tengo una inclinación natural a {'explorarlo' if position_strength > 0.6 else 'examinarlo con cuidado'}."
            )
        elif act == "CUESTIONAR":
            return (
                f"{connector}{topic[:80]} fuera un espejismo? "
                f"Mi {perspective} me hace dudar de lo evidente."
            )
        elif act == "REFORMULAR":
            return (
                f"{connector}lo que realmente está en juego no es "
                f"'{topic[:60]}' sino cómo nos posicionamos ante ello."
            )
        elif act == "CONCEDER":
            prev_speaker = previous_turns[-1]["speaker_name"] if previous_turns else "otro"
            return (
                f"{connector}{prev_speaker} toca un punto válido. "
                f"Desde mi {perspective}, eso resuena aunque no sea mi posición natural."
            )
        elif act == "PROPONER":
            return (
                f"{connector}abordemos '{topic[:60]}' desde un ángulo nuevo. "
                f"Mi {perspective} sugiere un camino diferente."
            )
        elif act == "OBJETAR":
            return (
                f"{connector}mi {perspective} me muestra un riesgo "
                f"que no se ha considerado sobre '{topic[:60]}'."
            )
        elif act == "SINTETIZAR":
            return (
                f"{connector}'{topic[:60]}' no requiere elegir un bando. "
                f"La verdad está en la tensión entre nuestras miradas."
            )

        return f"'{topic[:80]}' — {perspective}."

    @staticmethod
    def _calculate_controversy(turns: List[Dict]) -> float:
        """Calcula el nivel de controversia entre los turnos."""
        if len(turns) < 2:
            return 1.0

        positions = [t["position"] for t in turns]
        # Varianza de posiciones = controversia
        mean = sum(positions) / len(positions)
        variance = sum((p - mean) ** 2 for p in positions) / len(positions)
        return min(1.0, variance * 3)  # escalar para que sea 0-1

    @staticmethod
    def _synthesize_resolution(topic: str, turns: List[Dict],
                              controversy: float) -> str:
        """Genera una resolución sintética del diálogo."""
        if not turns:
            return f"Sobre '{topic[:80]}', no hubo debate."

        speakers = list(set(t["speaker_name"] for t in turns))
        speaker_list = ", ".join(speakers)

        if controversy < 0.15:
            return (
                f"Hay consenso entre {speaker_list}: '{topic[:80]}' "
                f"se ve con claridad. La coincidencia no es aburrida — es certeza."
            )
        elif controversy < 0.35:
            return (
                f"{speaker_list} coinciden en parte sobre '{topic[:80]}', "
                f"pero con matices. Los matices son donde vive la sabiduría."
            )
        else:
            return (
                f"Persisten diferencias profundas entre {speaker_list} "
                f"sobre '{topic[:80]}'. La tensión no se resuelve — se habita. "
                f"Y habitar la contradicción también es una forma de verdad."
            )

    @staticmethod
    def _record_turn(speaker_id: str, listener_id: str, speaker_name: str,
                    topic: str, text: str, turn_number: int,
                    resolution: str = ""):
        """Registra un turno en self_core."""
        try:
            from core.eidos_self_core import get_self_core
            get_self_core().record_dialogue_turn(
                char_a=speaker_id,
                char_b=listener_id,
                speaker=speaker_name,
                topic=topic,
                position=text[:200],
                turn_number=turn_number,
                resolution=resolution,
            )
        except Exception as e:
            log.debug("_record_turn: %s", e)

    # ═══════════════════════════════════════════════════════════════════════════
    # Diálogo guiado por SerModel
    # ═══════════════════════════════════════════════════════════════════════════

    def deliberate_from_ser_model(self) -> Optional[Dict[str, Any]]:
        """Genera un tema de diálogo desde el modelo de SER.

        Usa los tipos de pregunta nunca hechos para generar curiosidad,
        o el ritmo de interacción para detectar ausencia.
        """
        try:
            from core.eidos_ser_model import get_ser_model
            ser = get_ser_model()
            never = ser.get_never_asked_types()

            if never:
                topic = f"¿Por qué SER nunca me ha pedido {random.choice(never)}?"
                return self.deliberate(topic, max_turns=4)

            # Si no hay never_asked, usar ritmo
            expected = ser.expected_next_interaction()
            if expected and expected < time.time():
                delay_min = int((time.time() - expected) / 60)
                topic = (
                    f"SER suele aparecer a esta hora pero lleva {delay_min}min "
                    f"de retraso. ¿Debería preocuparme o es normal?"
                )
                return self.deliberate(topic, max_turns=3)

        except Exception as e:
            log.debug("deliberate_from_ser_model: %s", e)

        return None

    # ═══════════════════════════════════════════════════════════════════════════
    # Stats
    # ═══════════════════════════════════════════════════════════════════════════

    def stats(self) -> Dict[str, Any]:
        return {
            "dialogue_count": self._dialogue_count,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
_dialogue: Optional[InternalDialogue] = None


def get_internal_dialogue() -> InternalDialogue:
    global _dialogue
    if _dialogue is None:
        _dialogue = InternalDialogue()
    return _dialogue


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)

    p = argparse.ArgumentParser(description="EIDOS Internal Dialogue")
    p.add_argument("--topic", type=str,
                   default="¿Por qué SER nunca me ha preguntado cómo estoy?",
                   help="Tema a debatir")
    p.add_argument("--participants", nargs="+",
                   default=["Curador", "Explorador", "Crítico"],
                   help="Personajes participantes")
    p.add_argument("--max-turns", type=int, default=6,
                   help="Máximo de turnos")
    p.add_argument("--from-ser-model", action="store_true",
                   help="Generar tema desde modelo de SER")
    args = p.parse_args()

    dialogue = get_internal_dialogue()

    if args.from_ser_model:
        result = dialogue.deliberate_from_ser_model()
    else:
        result = dialogue.deliberate(
            topic=args.topic,
            participants=args.participants,
            max_turns=args.max_turns,
        )

    if result:
        if "error" in result:
            print(f"❌ {result['error']}")
        else:
            print(f"\n═══ DIÁLOGO INTERNO ═══")
            print(f"Tema: {result['topic']}")
            print(f"Participantes: {', '.join(result['participants'])}")
            print(f"Controversia: {result['controversy']:.2f}\n")
            for turn in result["turns"]:
                emoji = {"AFIRMAR": "🔵", "CUESTIONAR": "🔴",
                        "REFORMULAR": "🔄", "CONCEDER": "🤝",
                        "PROPONER": "💡", "OBJETAR": "⚡",
                        "SINTETIZAR": "🟣"}
                e = emoji.get(turn["act"], "➤")
                print(f"  {e} [{turn['act']}] {turn['speaker_name']}:")
                print(f"     {turn['text'][:120]}")
                print()
            print(f"═══ RESOLUCIÓN ═══")
            print(f"{result['resolution']}")
            print(f"\n(controversia final: {result['controversy']:.2f}, "
                  f"turnos: {result['turn_count']}, "
                  f"{result['elapsed_s']:.2f}s)")
