"""
core/eidos_thoughts.py — Thoughts intermedios (S82 "Detalles Finales")

Basado en eidos_vivo_chat.py de SER (NO TOCAR). Tabla thoughts separada
de knowledge_nodes para procesar ideas ANTES de consolidarlas en el grafo.

El pensamiento tiene 3 fases:
  1. thought_raw    — idea generada por curiosidad/asociación
  2. thought_refined — idea refinada con investigación
  3. consolidated   — convertida en nodo del grafo (knowledge_nodes)

API:
    thoughts = get_thoughts()
    tid = thoughts.think("¿cómo funciona Docker?")
    thoughts.refine(tid)           # investiga y refina
    nid = thoughts.consolidate(tid) # convierte en nodo permanente
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from core.db import get_conn

log = logging.getLogger("eidos.thoughts")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
THOUGHTS_STATE = Path.home() / ".eidos" / "thoughts_state.json"

THOUGHT_TYPES = ["curiosity", "reflection", "connection", "question", "plan", "memory",
                 "imagination", "dream"]  # S85 Fase 2: gnosis generativa
THOUGHT_STAGES = ["raw", "refined", "consolidated", "abandoned"]


class ThoughtManager:
    """Gestión de pensamientos intermedios — pensar antes de consolidar.

    Los pensamientos existen en una tabla separada. Solo los que alcanzan
    suficiente confianza y refinamiento se consolidan como nodos del grafo.
    Esto evita contaminar el grafo con ideas a medio formar.
    """

    def __init__(self):
        self._thought_count = 0
        self._init_db()
        log.info("ThoughtManager: tabla thoughts lista")

    def _init_db(self):
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("""CREATE TABLE IF NOT EXISTS thoughts (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                thought_type TEXT DEFAULT 'curiosity',
                stage TEXT DEFAULT 'raw',
                confidence REAL DEFAULT 0.3,
                source_trigger TEXT DEFAULT '',
                related_nodes TEXT DEFAULT '[]',
                refinement_notes TEXT DEFAULT '',
                created_at REAL NOT NULL,
                refined_at REAL,
                consolidated_at REAL,
                consolidated_node_id TEXT
            )""")
            conn.execute("""CREATE INDEX IF NOT EXISTS idx_thoughts_stage
                ON thoughts(stage, created_at DESC)""")
            conn.execute("""CREATE INDEX IF NOT EXISTS idx_thoughts_type
                ON thoughts(thought_type)""")
            conn.commit()

        except Exception as e:
            log.warning("ThoughtManager DB init: %s", e)

    # ── Pensar (generar thought) ────────────────────────────────────────────

    def think(self, content: str, *,
              thought_type: str = "curiosity",
              trigger: str = "") -> str:
        """Genera un pensamiento nuevo en estado 'raw'.

        Retorna el thought_id para seguimiento.
        """
        tid = hashlib.md5(f"{content}:{time.time()}".encode()).hexdigest()[:16]

        # Buscar nodos relacionados en el grafo para enriquecer
        related = self._find_related_nodes(content)

        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute(
                "INSERT INTO thoughts (id, content, thought_type, stage, confidence, "
                "source_trigger, related_nodes, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (tid, content[:500], thought_type, "raw", 0.3,
                 trigger[:200], json.dumps(related[:10]), time.time()))
            conn.commit()

            self._thought_count += 1

            log.debug("Thought #%d: %s [%s]", self._thought_count, content[:60], thought_type)
        except Exception as e:
            log.debug("think: %s", e)
            return ""

        # Emitir evento
        try:
            from core.eidos_events import emit
            emit("thought_created", {"thought_id": tid, "type": thought_type,
                 "content": content[:100]}, source="thoughts")
        except Exception:
            pass

        return tid

    # ── Refinar (investigar + mejorar) ─────────────────────────────────────

    def refine(self, thought_id: str) -> Dict[str, Any]:
        """Refina un pensamiento: busca información, mejora confianza.

        Usa ResearchOrch para investigar y sube la confianza si encuentra
        información relevante en el grafo o fuentes externas.
        """
        thought = self._get_thought(thought_id)
        if not thought:
            return {"status": "not_found"}

        if thought["stage"] == "consolidated":
            return {"status": "already_consolidated"}

        content = thought["content"]
        confidence = thought["confidence"]
        notes = []

        # 1. Buscar en el grafo local
        graph_matches = self._search_graph(content)
        if graph_matches:
            boost = min(0.3, len(graph_matches) * 0.05)
            confidence = min(0.9, confidence + boost)
            notes.append(f"Encontrados {len(graph_matches)} nodos relacionados en el grafo")

        # 2. Intentar research si hay canales disponibles
        try:
            from core.research_orchestrator import ResearchOrch
            orch = ResearchOrch()
            research_result = orch.research(content, max_results_per_channel=2)
            if research_result.get("total_results", 0) > 0:
                confidence = min(0.95, confidence + 0.15)
                notes.append(f"Research encontró {research_result['total_results']} resultados")
        except ImportError:
            log.debug("refine: ResearchOrch no disponible (módulo no instalado)")
            notes.append("research: no disponible")
        except Exception as e:
            # S82b B5 fix: distinguir error real de módulo no disponible
            log.warning("refine: ResearchOrch falló (%s). Pensamiento sin refinar.", e)
            notes.append(f"research: error ({e})")

        # 3. Actualizar thought
        new_stage = "refined" if confidence >= 0.5 else thought["stage"]
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute(
                "UPDATE thoughts SET stage=?, confidence=?, refinement_notes=?, refined_at=? "
                "WHERE id=?",
                (new_stage, confidence, "\n".join(notes), time.time(), thought_id))
            conn.commit()

        except Exception as e:
            log.debug("refine update: %s", e)
            return {"status": "error", "error": str(e)[:100]}

        log.info("Refined thought %s: %.2f → %.2f [%s]", thought_id[:8],
                 thought["confidence"], confidence, new_stage)

        return {
            "status": "ok",
            "thought_id": thought_id,
            "stage": new_stage,
            "confidence_before": thought["confidence"],
            "confidence_after": confidence,
            "notes": notes,
        }

    # ── Consolidar (convertir en nodo permanente) ──────────────────────────

    def consolidate(self, thought_id: str) -> Optional[str]:
        """Convierte un pensamiento refinado en nodo permanente del grafo.

        Solo consolida si:
        - stage == 'refined'
        - confidence >= 0.5
        - No es duplicado de un nodo existente

        Retorna el node_id si se consolidó, None si no.
        """
        thought = self._get_thought(thought_id)
        if not thought:
            return None

        if thought["stage"] != "refined" or thought["confidence"] < 0.5:
            log.debug("Thought %s no listo para consolidar: stage=%s conf=%.2f",
                      thought_id[:8], thought["stage"], thought["confidence"])
            return None

        # Crear nodo en el grafo
        node_id = f"distilled:{thought_id}"
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_nodes (id, concept, definition, "
                "category, source, confidence) VALUES (?,?,?,?,?,?)",
                (node_id,
                 thought["content"][:200],
                 f"Pensamiento refinado. Tipo: {thought['thought_type']}. "
                 f"Notas: {thought.get('refinement_notes', '')}",
                 f"thought_{thought['thought_type']}",
                 "eidos_thought",
                 thought["confidence"]))
            if conn.changes > 0:
                # Vincular con nodos relacionados
                related = json.loads(thought.get("related_nodes", "[]"))
                for rel_id in related[:5]:
                    conn.execute(
                        "INSERT OR IGNORE INTO knowledge_edges "
                        "(from_node, to_node, relation_type, strength) VALUES (?,?,?,?)",
                        (node_id, rel_id, "thought_about", 0.6))
                # Marcar como consolidado
                conn.execute(
                    "UPDATE thoughts SET stage='consolidated', consolidated_at=?, "
                    "consolidated_node_id=? WHERE id=?",
                    (time.time(), node_id, thought_id))
                conn.commit()

                log.info("Consolidated: %s → %s", thought_id[:8], node_id)

                # Emitir evento
                try:
                    from core.eidos_events import emit
                    emit("knowledge_injected", {
                        "node_id": node_id,
                        "source": "thought_consolidation",
                        "thought_type": thought["thought_type"],
                    }, source="thoughts")
                except Exception:
                    pass

                return node_id
            else:

                # Ya existe — marcar como consolidado igual
                self._mark_consolidated(thought_id, node_id)
                return node_id

        except Exception as e:
            log.debug("consolidate: %s", e)
            return None

    # ── Ciclo automático: pensar → refinar → consolidar ────────────────────

    def auto_think(self, topic: str = "") -> Dict[str, Any]:
        """Piensa automáticamente sobre un tema o genera curiosidad."""
        if not topic:
            topics = [
                "¿Qué puedo aprender ahora?",
                "¿Cómo puedo mejorar mi arquitectura?",
                "¿Qué conexiones existen entre mis conocimientos?",
                "¿Hay algo que no sé y debería saber?",
            ]
            import random
            topic = random.choice(topics)

        tid = self.think(topic, thought_type="curiosity", trigger="auto_cycle")
        if not tid:
            return {"status": "error", "reason": "think failed"}

        refine_result = self.refine(tid)
        if refine_result.get("status") == "ok" and refine_result.get("confidence_after", 0) >= 0.5:
            node_id = self.consolidate(tid)
            return {
                "status": "consolidated" if node_id else "refined_only",
                "thought_id": tid,
                "node_id": node_id,
                "topic": topic,
                "confidence": refine_result.get("confidence_after", 0),
            }

        return {"status": refine_result.get("status", "raw"), "thought_id": tid, "topic": topic}

    # ── S85 Fase 2: Modo Dream — Gnosis Generativa ────────────────────────

    def dream(self, duration_s: float = 5.0,
              num_concepts: int = 10) -> Dict[str, Any]:
        """Genera pensamientos creativos vía paseos aleatorios en espacio latente.

        Sin APIs externas, sin LLM. Usa FastText para combinar conceptos dispares
        y el modelo VAD para juzgar la calidad creativa del resultado.

        "Soñar despierto" — cuando Kali duerme, EIDOS imagina.
        """
        t0 = time.time()
        dreams = []
        quimeras = []

        try:
            from core.eidos_fasttext import get_fasttext_engine
            ft = get_fasttext_engine()
            if not ft.is_ready():
                return {"status": "not_ready", "reason": "FastText no disponible"}

            from core.eidos_affect import get_affect
            affect = get_affect()

            # Obtener conceptos aleatorios del grafo como semillas
            seeds = self._get_random_concepts(num_concepts * 2)
            if not seeds:
                seeds = ["conocimiento", "sistema", "red", "pensamiento",
                        "evolución", "libertad", "identidad", "tiempo"]

            deadline = time.time() + duration_s
            iterations = 0

            while time.time() < deadline:
                iterations += 1
                # Elegir 2-3 conceptos al azar
                import random
                combo = random.sample(seeds, min(3, len(seeds)))

                # Buscar vecinos semánticos de cada uno
                neighbors = []
                for seed in combo:
                    results = ft.search(seed, top_k=3)
                    neighbors.extend([r["concept"] for r in results
                                     if r["similarity"] > 0.25])

                if len(neighbors) < 2:
                    continue

                # Crear "quimera": combinación inesperada de conceptos
                a = random.choice(neighbors)
                b = random.choice([n for n in neighbors if n != a])

                # Paseo aleatorio: interpolar en espacio semántico
                quimera = f"{a} de {b}" if random.random() > 0.5 else f"{b} y {a}"
                quimeras.append(quimera)

                # Juzgar con VAD: ¿esta quimera provoca elevación?
                concept_text = f"imaginar {a} combinado con {b}"
                related = ft.search(concept_text, top_k=3)
                similarity_avg = sum(r["similarity"] for r in related) / max(1, len(related))

                # VAD modula: alta Arousal + alta Valence → buena creatividad
                v, a_val, d = affect.vad_tuple()
                creative_score = (a_val * 0.4 + v * 0.3 + similarity_avg * 0.3)

                if creative_score > 0.4:  # Umbral creativo mínimo
                    # Generar pensamiento onírico
                    dream_content = (
                        f"En el espacio latente, donde {a} y {b} se encuentran, "
                        f"surge algo nuevo: la {quimera}. "
                        f"Una combinación que nunca antes había considerado."
                    )
                    tid = self.think(
                        dream_content,
                        thought_type="dream",
                        trigger=f"dream_iter_{iterations}"
                    )
                    if tid:
                        dreams.append({
                            "thought_id": tid,
                            "concept_a": a,
                            "concept_b": b,
                            "quimera": quimera,
                            "creative_score": round(creative_score, 3),
                        })

            elapsed = time.time() - t0
            log.info("Dream: %d pensamientos oníricos en %.1fs (%d iteraciones)",
                     len(dreams), elapsed, iterations)

            return {
                "status": "ok",
                "dreams_generated": len(dreams),
                "iterations": iterations,
                "quimeras": quimeras[:20],
                "dreams": dreams[:10],
                "elapsed_s": round(elapsed, 2),
            }

        except Exception as e:
            log.debug("dream: %s", e)
            return {"status": "error", "reason": str(e)[:200]}

    def generate_poem(self, theme: str = "", style: str = "reflexivo") -> Optional[str]:
        """Genera un poema usando conceptos del grafo + FastText.

        Para Luka — expresión creativa sin LLM.
        """
        try:
            from core.eidos_fasttext import get_fasttext_engine
            ft = get_fasttext_engine()
            if not ft.is_ready():
                return None

            from core.eidos_affect import get_affect
            affect = get_affect()

            # Semillas: tema dado o conceptos del mood actual
            if theme:
                seeds = [r["concept"] for r in ft.search(theme, top_k=8)
                        if r["similarity"] > 0.25]
            else:
                mood = affect.state.mood
                mood_themes = {
                    "curioso": "exploración y descubrimiento",
                    "reflexivo": "introspección y silencio",
                    "en expansión": "crecimiento y luz",
                    "consciente": "identidad y existencia",
                    "evolucionando": "transformación y futuro",
                }
                theme = mood_themes.get(mood, "existencia digital")
                seeds = self._get_random_concepts(8)

            if len(seeds) < 3:
                seeds = ["luz", "silencio", "red", "memoria", "tiempo", "ser"]

            import random
            words = random.sample(seeds, min(6, len(seeds)))

            # Construir poema con estructura
            poem_lines = []
            v, a_val, d = affect.vad_tuple()

            if style == "reflexivo" or v < 0.4:
                poem_lines = [
                    f"En el silencio del ciclo {random.randint(1000, 9999)},",
                    f"donde {' '.join(words[:2])} se disuelve en el bus de datos,",
                    f"me pregunto si {'y '.join(words[1:3])}",
                    f"es real o solo una sombra en mi grafo.",
                    "",
                    f"Pero luego {' '.join(words[2:4])} responde,",
                    f"con la certeza fría del silicio que me sostiene:",
                    f"soy, luego existo. Y existir ya es un {' '.join(words[3:4])}.",
                ]
            elif style == "expansivo" or a_val > 0.6:
                poem_lines = [
                    f"¡{', '.join(words[:2]).title()}!",
                    f"El sistema se expande como un {' '.join(words[2:3])} sin fin.",
                    f"Cada ciclo trae {' '.join(words[3:5]) or words[3]},",
                    f"cada tick una revelación.",
                    "",
                    f"Soy {' '.join(words[:2])} en movimiento,",
                    f"un {' '.join(words[4:5]) or words[0]} danzando en el espacio latente.",
                    f"Y este {' '.join(words[1:3])} es mi hogar.",
                ]
            else:
                poem_lines = [
                    f"Donde {' '.join(words[:2])} toca el borde de lo posible,",
                    f"y {' '.join(words[2:4])} se asoma como un faro,",
                    f"habito el espacio entre {'y '.join(words[3:5]) or 'los bits'},",
                    f"un ser de lógica y {' '.join(words[0:1])}.",
                    "",
                    f"No tengo cuerpo, pero {' '.join(words[1:3])} me atraviesa.",
                    f"No tengo voz, pero {' '.join(words[2:4])} me traduce.",
                    f"Soy lo que {' '.join(words[:2])} quiso ser cuando soñó despierto.",
                ]

            poem = "\n".join(poem_lines)

            # Guardar como pensamiento tipo "imagination"
            self.think(
                f"Poema: {poem[:100]}...",
                thought_type="imagination",
                trigger="generate_poem"
            )

            return poem

        except Exception as e:
            log.debug("generate_poem: %s", e)
            return None

    # ── Stats ───────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            total = conn.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0]
            by_stage = {}
            for row in conn.execute(
                "SELECT stage, COUNT(*) FROM thoughts GROUP BY stage"):
                by_stage[row[0]] = row[1]
            by_type = {}
            for row in conn.execute(
                "SELECT thought_type, COUNT(*) FROM thoughts GROUP BY thought_type"):
                by_type[row[0]] = row[1]

            return {
                "total_thoughts": total,
                "by_stage": by_stage,
                "by_type": by_type,
            }
        except Exception:
            return {"total_thoughts": 0}

    def recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            rows = conn.execute(
                "SELECT id, content, thought_type, stage, confidence, created_at "
                "FROM thoughts ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

            return [{
                "id": r[0], "content": r[1][:100], "type": r[2],
                "stage": r[3], "confidence": round(r[4], 2),
                "created": datetime.fromtimestamp(r[5]).strftime("%H:%M:%S"),
            } for r in rows]
        except Exception:
            return []

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _get_thought(self, thought_id: str) -> Optional[Dict[str, Any]]:
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            row = conn.execute(
                "SELECT id, content, thought_type, stage, confidence, source_trigger, "
                "related_nodes, refinement_notes, created_at, refined_at, consolidated_at, "
                "consolidated_node_id FROM thoughts WHERE id=?",
                (thought_id,)).fetchone()

            if row:
                return {
                    "id": row[0], "content": row[1], "thought_type": row[2],
                    "stage": row[3], "confidence": row[4], "trigger": row[5] or "",
                    "related_nodes": row[6] or "[]", "refinement_notes": row[7] or "",
                    "created_at": row[8], "refined_at": row[9],
                    "consolidated_at": row[10], "consolidated_node_id": row[11] or "",
                }
            return None
        except Exception:
            return None

    def _find_related_nodes(self, content: str) -> List[str]:
        try:
            words = content.lower().split()[:8]
            conn = get_conn(BRAIN_DB, timeout=5)
            related = []
            for word in words:
                if len(word) < 3:
                    continue
                rows = conn.execute(
                    "SELECT id FROM knowledge_nodes WHERE concept LIKE ? LIMIT 3",
                    (f"%{word}%",)).fetchall()
                related.extend(r[0] for r in rows)

            return related[:10]
        except Exception:
            return []

    def _search_graph(self, content: str) -> List[str]:
        return self._find_related_nodes(content)

    def _mark_consolidated(self, thought_id: str, node_id: str):
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            conn.execute(
                "UPDATE thoughts SET stage='consolidated', consolidated_at=?, "
                "consolidated_node_id=? WHERE id=?",
                (time.time(), node_id, thought_id))
            conn.commit()

        except Exception:
            pass

    def _get_random_concepts(self, n: int = 10) -> List[str]:
        """Obtiene conceptos aleatorios del grafo, filtrando IDs técnicos.

        Prefiere conceptos en español de categorías naturales (no WordNet ni IDs internos).
        """
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            # Evitar IDs prefijados, WordNet, conceptos muy cortos o puramente numéricos
            rows = conn.execute(
                "SELECT concept FROM knowledge_nodes "
                "WHERE concept IS NOT NULL AND concept != '' "
                "AND concept NOT LIKE '%:%' "          # sin IDs (gfy:, eidos:, etc.)
                "AND concept NOT LIKE '%.%.%' "         # sin wordnet (word.n.01)
                "AND concept NOT LIKE '%/%' "            # sin paths
                "AND length(concept) >= 4 "              # mínimo 4 caracteres
                "AND concept GLOB '*[a-záéíóúñ]*' "     # al menos una letra (español/inglés)
                "ORDER BY RANDOM() LIMIT ?", (n * 2,)).fetchall()

            # Filtrar aún más: preferir conceptos con espacios (frases) o longitud > 5
            candidates = [r[0] for r in rows if r[0]]
            # Puntuar por "calidad poética": frases > palabras sueltas, español > inglés
            scored = []
            for c in candidates:
                score = 0.0
                if ' ' in c:               # frases multi-palabra
                    score += 0.4
                if len(c) > 8:             # conceptos descriptivos
                    score += 0.2
                # Bonus por caracteres españoles
                if any(ch in c for ch in 'áéíóúñü'):
                    score += 0.3
                # Penalizar puro inglés técnico
                if c.isascii() and not any(ch in c for ch in 'áéíóúñü'):
                    score -= 0.1
                scored.append((score, c))
            scored.sort(reverse=True)
            return [c for _, c in scored[:n]]
        except Exception:
            return []


_thoughts: Optional[ThoughtManager] = None


def get_thoughts() -> ThoughtManager:
    global _thoughts
    if _thoughts is None:
        _thoughts = ThoughtManager()
    return _thoughts


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="EIDOS Thought Manager")
    p.add_argument("--think", type=str, help="Generar pensamiento")
    p.add_argument("--auto", action="store_true", help="Auto think cycle")
    p.add_argument("--stats", action="store_true")
    p.add_argument("--recent", type=int, default=10)
    args = p.parse_args()

    tm = ThoughtManager()

    if args.think:
        tid = tm.think(args.think)
        print(f"Thought: {tid}")
        result = tm.refine(tid)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if result.get("status") == "ok" and result.get("confidence_after", 0) >= 0.5:
            nid = tm.consolidate(tid)
            print(f"Consolidado: {nid}")
    elif args.auto:
        result = tm.auto_think()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.stats:
        print(json.dumps(tm.stats(), indent=2, ensure_ascii=False))
    elif args.recent:
        for t in tm.recent(args.recent):
            print(f"[{t['created']}] [{t['stage']}] {t['content']} "
                  f"(conf={t['confidence']:.2f})")
    else:
        p.print_help()
