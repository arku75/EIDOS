"""
core/character_neuron.py — Neurona propia por personaje [S123-C]
================================================================
Visión de SER (2026-06-10): "que cada personaje tenga su propia neurona
artificial para que estén más vivos que nunca, al igual que el personaje
de Colony EIDOS que es todos — y él sabrá qué o cuál elegir".

Cada personaje YA aprende nodos propios (knowledge_nodes.character; los
hijos los heredan al nacer). Este módulo añade lo que faltaba:

  1. SINAPSIS PROPIAS — pesos hebbianos POR personaje (character_synapses):
     cada vez que un personaje usa un nodo, ESA conexión se refuerza en ÉL
     (no en todos). Lo que Coder usa se refuerza en Coder. Con decaimiento
     temporal: lo que un personaje no usa, en él se debilita (olvido).
  2. RESONANCIA — cuánto "vibra" la neurona de un personaje ante una
     pregunta: coincidencias en su subgrafo (nodos propios + nodos que él
     usa) ponderadas por peso sináptico, calidad del nodo y dominio.
  3. EIDOS-QUE-ES-TODOS COMO ROUTER — choose_character(): mide la
     resonancia de todos los personajes vivos y elige quién responde.
     Al elegir, los nodos que resonaron DISPARAN (refuerzo hebbiano):
     "neurons that fire together wire together", ahora por personaje.

Sin LLM, sin max_tokens, sin duplicar grafos: la neurona de cada personaje
ES su subgrafo del cerebro común + sus pesos sinápticos propios.

API:
    ns = get_neuron_system()
    ns.resonance("colony_coder", "qué es un decorador python")
    ns.choose_character("qué es un decorador python")   # → el elegido
    ns.record_use("colony_coder", [node_ids])           # refuerzo manual
    ns.inherit_synapses("colony_omelum", "colony_omega", "colony_lumen")
    ns.stats()                                          # neuronas de todos
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.db import get_conn_ctx

log = logging.getLogger("eidos.neuron")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
LIFECYCLE_DB = Path.home() / ".eidos" / "lifecycle.db"

# ── Parámetros hebbianos ──────────────────────────────────────────────────────
LEARNING_RATE = 0.15      # cuánto se refuerza una sinapsis al disparar
MAX_WEIGHT = 5.0          # techo del peso sináptico
HALF_LIFE_DAYS = 45.0     # cada 45 días sin uso, el peso efectivo se reduce a la mitad
MIN_SCORE_TO_ROUTE = 1.25  # S125-L: bajado de 2.0. Con 1 keyword de ADN basta para entrar.

# Dominios innatos de los personajes BASE (su "ADN"): garantizan que el
# router nunca haga peor que el selector por keywords clásico. La neurona
# crece por encima de esto con el uso real.
_BASE_DOMAINS: Dict[str, List[str]] = {
    "colony_coder":     ["código", "code", "python", "javascript", "función",
                         "function", "script", "bug", "programar", "implementa",
                         "clase", "decorador", "api", "git"],
    "colony_analyst":   ["analiza", "analyze", "compara", "ventajas",
                         "desventajas", "por qué", "estadística", "datos",
                         "rendimiento", "métricas"],
    "colony_vision":    ["imagen", "image", "visual", "diseño", "color",
                         "pantalla", "screenshot", "ocr", "diagrama"],
    "colony_operator":  ["comando", "command", "ejecuta", "servidor", "server",
                         "sistema", "terminal", "systemd", "proceso", "linux",
                         "kali", "red", "network"],
    "colony_general":   [],  # generalista: resuena por sinapsis, no por ADN
    "colony_ser":       ["perspectiva", "criterio", "prioriza", "opinión",
                         "qué harías", "decisión"],
    "colony_lumen":     ["razona", "filosofía", "dilema", "deberíamos",
                         "consejo", "duda", "piensa", "sentido"],
    "colony_forge":     ["debug", "arquitectura", "refactor", "test",
                         "infraestructura", "deploy"],
    "colony_centinela": ["seguridad", "vigila", "alerta", "complejidad",
                         "riesgo", "vulnerabilidad", "log"],
}

_STOPWORDS = {
    "que", "qué", "como", "cómo", "para", "por", "con", "una", "uno", "unos",
    "unas", "los", "las", "del", "este", "esta", "esto", "ese", "esa", "eso",
    "the", "and", "for", "what", "how", "why", "is", "are", "can", "does",
    "dime", "dame", "hazme", "quiero", "puedes", "sobre", "entre", "desde",
    "hasta", "muy", "más", "menos", "pero", "también", "cuando", "donde",
    "dónde", "cuál", "cual", "quien", "quién", "ser", "estar", "hay", "tiene",
    "linea", "línea", "frase", "explica", "explícame",
}


def _tokenize(text: str) -> List[str]:
    """Tokens significativos de una pregunta (sin stopwords, >2 chars)."""
    words = re.findall(r"[a-záéíóúüñ0-9_.-]+", text.lower())
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS][:12]


class CharacterNeuronSystem:
    """La neurona propia de cada personaje: subgrafo + sinapsis hebbianas."""

    def __init__(self, brain_db: Path = BRAIN_DB):
        self.brain_db = brain_db
        self._lock = threading.Lock()
        self._init_db()

    # ── Infraestructura ──────────────────────────────────────────────────────
    # Regla CLAUDE.md: NUNCA sqlite3.connect directo en core/ — core.db
    # aplica busy_timeout/WAL/mmap a TODA conexión.

    @contextmanager
    def _conn(self):
        with get_conn_ctx(self.brain_db, cache=False) as conn:
            yield conn

    def _init_db(self) -> None:
        try:
            with self._lock, self._conn() as conn:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS character_synapses (
                        character       TEXT NOT NULL,
                        node_id         TEXT NOT NULL,
                        weight          REAL NOT NULL DEFAULT 1.0,
                        activations     INTEGER NOT NULL DEFAULT 0,
                        last_activation REAL,
                        PRIMARY KEY (character, node_id)
                    )"""
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_synapses_char "
                    "ON character_synapses(character)"
                )
        except Exception as e:
            log.debug("character_synapses init: %s", e)

    @staticmethod
    def _time_decay(last_activation: Optional[float]) -> float:
        """Peso efectivo: decaimiento exponencial por días sin disparar."""
        if not last_activation:
            return 1.0
        days_idle = max(0.0, (time.time() - last_activation) / 86400.0)
        return math.pow(0.5, days_idle / HALF_LIFE_DAYS)

    # ── Candidatos vivos ─────────────────────────────────────────────────────

    def _alive_characters(self) -> List[str]:
        """Personajes que pueden responder: base + nacidos no retirados."""
        alive = list(_BASE_DOMAINS.keys())
        try:
            with get_conn_ctx(LIFECYCLE_DB, cache=False) as lc:
                rows = lc.execute(
                    "SELECT name FROM characters WHERE retired_at IS NULL"
                ).fetchall()
            for (name,) in rows:
                cid = name if name.startswith("colony_") else f"colony_{name.lower()}"
                if cid not in alive:
                    alive.append(cid)
        except Exception as e:
            log.debug("_alive_characters: %s", e)
        return alive

    # ── Núcleo: resonancia ───────────────────────────────────────────────────

    def resonance(self, character: str, question: str,
                  tokens: Optional[List[str]] = None) -> Dict[str, Any]:
        """Cuánto resuena la neurona de un personaje con la pregunta.

        Suma tres voces:
          - ADN: keywords innatas del personaje base presentes en la pregunta
          - nodos PROPIOS (knowledge_nodes.character) que casan con la pregunta
          - sinapsis: nodos (de quien sea) que ESTE personaje usa, con su peso
        """
        toks = tokens if tokens is not None else _tokenize(question)
        result: Dict[str, Any] = {"character": character, "score": 0.0,
                                   "matched": [], "node_ids": []}
        if not toks:
            return result

        score = 0.0
        q_lower = question.lower()

        # 1. ADN del personaje base (precisión alta: 1.25 por keyword)
        for kw in _BASE_DOMAINS.get(character, []):
            if kw in q_lower:
                score += 1.25
                result["matched"].append(f"adn:{kw}")

        # Contribuciones de nodos: precisión por fracción de tokens que casan
        # y rendimientos DECRECIENTES (1, 1/2, 1/3...) — tener los nodos JUSTOS
        # gana a tener muchos nodos con coincidencias sueltas.
        contributions: List[tuple] = []  # (valor, node_id, etiqueta)
        try:
            like_clauses = " OR ".join(["concept LIKE ?"] * len(toks))
            like_params = [f"%{t}%" for t in toks]
            with self._conn() as conn:
                # 2. Nodos propios que casan
                rows = conn.execute(
                    f"SELECT id, concept, COALESCE(quality_score, 0.3) "
                    f"FROM knowledge_nodes WHERE character = ? AND ({like_clauses}) "
                    f"LIMIT 40",
                    [character] + like_params,
                ).fetchall()
                for nid, concept, quality in rows:
                    c_lower = (concept or "").lower()
                    frac = sum(1 for t in toks if t in c_lower) / len(toks)
                    contributions.append(
                        ((0.5 + quality) * frac, nid, f"propio:{concept[:40]}"))

                # 3. Sinapsis propias sobre cualquier nodo
                rows = conn.execute(
                    f"SELECT kn.id, kn.concept, COALESCE(kn.quality_score, 0.3), "
                    f"cs.weight, cs.last_activation "
                    f"FROM character_synapses cs "
                    f"JOIN knowledge_nodes kn ON kn.id = cs.node_id "
                    f"WHERE cs.character = ? AND ({like_clauses}) LIMIT 40",
                    [character] + like_params,
                ).fetchall()
                for nid, concept, quality, weight, last_act in rows:
                    c_lower = (concept or "").lower()
                    frac = sum(1 for t in toks if t in c_lower) / len(toks)
                    effective = weight * self._time_decay(last_act)
                    contributions.append(
                        (effective * (0.3 + quality) * frac, nid,
                         f"sinapsis:{concept[:40]}"))
        except Exception as e:
            log.debug("resonance(%s): %s", character, e)

        contributions.sort(key=lambda c: c[0], reverse=True)
        seen_nodes = set()
        rank = 0
        for value, nid, label in contributions:
            if nid in seen_nodes:
                continue
            seen_nodes.add(nid)
            rank += 1
            score += value / rank  # decreciente: 1, 1/2, 1/3...
            result["node_ids"].append(nid)
            if len(result["matched"]) < 10:
                result["matched"].append(label)

        result["score"] = round(score, 2)
        return result

    # ── EIDOS-que-es-todos: el router ────────────────────────────────────────

    def choose_character(self, question: str,
                         candidates: Optional[List[str]] = None,
                         fire: bool = True) -> Optional[Dict[str, Any]]:
        """EIDOS (que es todos) elige qué personaje responde.

        Mide la resonancia de cada neurona y devuelve la más fuerte si
        supera MIN_SCORE_TO_ROUTE. Si fire=True, los nodos del ganador
        DISPARAN (refuerzo hebbiano en su neurona).
        """
        toks = _tokenize(question)
        if not toks:
            return None
        cands = candidates if candidates is not None else self._alive_characters()
        best: Optional[Dict[str, Any]] = None
        for char in cands:
            r = self.resonance(char, question, tokens=toks)
            if best is None or r["score"] > best["score"]:
                best = r
        if not best or best["score"] < MIN_SCORE_TO_ROUTE:
            return None
        # S125-L: fire incluso sin node_ids si el ADN solo ya puntúa bien.
        # Así los personajes nuevos empiezan a construir sinapsis desde su primer match.
        if fire and best.get("matched"):
            if not best["node_ids"]:
                best["node_ids"] = [f"adn:{best['character']}"]
            self.record_use(best["character"], best["node_ids"])
        return best

    # ── Refuerzo hebbiano ────────────────────────────────────────────────────

    def record_use(self, character: str, node_ids: List[str]) -> int:
        """Los nodos dispararon en este personaje → reforzar SUS sinapsis."""
        if not node_ids:
            return 0
        now = time.time()
        n = 0
        try:
            with self._lock, self._conn() as conn:
                for nid in node_ids[:60]:
                    conn.execute(
                        """INSERT INTO character_synapses
                           (character, node_id, weight, activations, last_activation)
                           VALUES (?, ?, 1.0 + ?, 1, ?)
                           ON CONFLICT(character, node_id) DO UPDATE SET
                             weight = MIN(weight + ?, ?),
                             activations = activations + 1,
                             last_activation = ?""",
                        (character, nid, LEARNING_RATE, now,
                         LEARNING_RATE, MAX_WEIGHT, now),
                    )
                    n += 1
        except Exception as e:
            log.debug("record_use(%s): %s", character, e)
        return n

    def inherit_synapses(self, child: str, parent1: str, parent2: str) -> int:
        """Al nacer, el hijo hereda las sinapsis de sus padres (al 50%)."""
        try:
            with self._lock, self._conn() as conn:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO character_synapses
                       (character, node_id, weight, activations, last_activation)
                       SELECT ?, node_id, MAX(weight) * 0.5, 0, ?
                       FROM character_synapses WHERE character IN (?, ?)
                       GROUP BY node_id""",
                    (child, time.time(), parent1, parent2),
                )
                return cur.rowcount or 0
        except Exception as e:
            log.debug("inherit_synapses(%s): %s", child, e)
            return 0

    # ── Estado ───────────────────────────────────────────────────────────────

    def stats(self, character: Optional[str] = None) -> Dict[str, Any]:
        """Estado de la(s) neurona(s): nodos propios, sinapsis, fuerza."""
        out: Dict[str, Any] = {}
        try:
            with self._conn() as conn:
                if character:
                    chars = [character]
                else:
                    owned = [r[0] for r in conn.execute(
                        "SELECT DISTINCT character FROM knowledge_nodes "
                        "WHERE character IS NOT NULL").fetchall()]
                    syn = [r[0] for r in conn.execute(
                        "SELECT DISTINCT character FROM character_synapses").fetchall()]
                    chars = sorted(set(owned) | set(syn) | set(_BASE_DOMAINS))
                for char in chars:
                    own = conn.execute(
                        "SELECT COUNT(*) FROM knowledge_nodes WHERE character=?",
                        (char,)).fetchone()[0]
                    srow = conn.execute(
                        "SELECT COUNT(*), COALESCE(SUM(weight),0), "
                        "COALESCE(SUM(activations),0) "
                        "FROM character_synapses WHERE character=?",
                        (char,)).fetchone()
                    out[char] = {
                        "nodos_propios": own,
                        "sinapsis": srow[0],
                        "fuerza_total": round(srow[1], 1),
                        "disparos": srow[2],
                        "adn": len(_BASE_DOMAINS.get(char, [])),
                    }
        except Exception as e:
            log.debug("stats: %s", e)
        return out


_instance: Optional[CharacterNeuronSystem] = None
_instance_lock = threading.Lock()


def get_neuron_system() -> CharacterNeuronSystem:
    """Singleton del sistema de neuronas por personaje."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = CharacterNeuronSystem()
        return _instance
