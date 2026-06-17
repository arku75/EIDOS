"""
core/eidos_logic.py — Motor de razonamiento LÓGICO para EIDOS (S118).

A diferencia de la "activación neuronal" simulada de knowledge_reasoner.py,
esto es INFERENCIA LÓGICA REAL: reglas, deducción, trazabilidad.

Inspirado en PyReason (lab-v2/pyreason) pero construido desde cero para EIDOS:
  - Sin numba, sin JIT, sin dependencias pesadas
  - Integración directa con knowledge_nodes y knowledge_edges
  - Anotaciones de verdad con intervalos [L, U] (lógica anotada)
  - Trazabilidad COMPLETA: cada conclusión explica POR QUÉ
  - Reglas definidas en YAML o construidas desde el propio grafo

Principios:
  1. Truth-first, language-second (GF-SDM)
  2. Open-world: lo no sabido es [0, 1] (desconocido), no [0, 0] (falso)
  3. Explicable: cada inferencia tiene trazabilidad hacia hechos base
  4. Determinista: misma entrada → misma salida, siempre
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

log = logging.getLogger("eidos.logic")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"

# ── Tipos de verdad anotada ────────────────────────────────────────────────────
# Cada proposición tiene un intervalo [lower, upper] ⊆ [0, 1]
# [1,1] = definitivamente verdadero
# [0,0] = definitivamente falso
# [0,1] = completamente desconocido (open-world)
# [0.7, 0.9] = probablemente cierto


@dataclass
class TruthValue:
    """Valor de verdad anotado: intervalo [lower, upper]."""
    lower: float
    upper: float

    def __post_init__(self):
        self.lower = max(0.0, min(1.0, self.lower))
        self.upper = max(0.0, min(1.0, self.upper))
        if self.lower > self.upper:
            self.lower, self.upper = self.upper, self.lower

    @property
    def is_true(self) -> bool:
        return self.lower > 0.7

    @property
    def is_false(self) -> bool:
        return self.upper < 0.3

    @property
    def is_unknown(self) -> bool:
        return self.lower == 0.0 and self.upper == 1.0

    @property
    def certainty(self) -> float:
        """Certeza: 1 - (upper - lower). 1.0 = certeza total, 0.0 = ignorancia total."""
        return 1.0 - (self.upper - self.lower)

    def combine_and(self, other: "TruthValue") -> "TruthValue":
        """AND lógico: intersección de intervalos."""
        return TruthValue(
            max(self.lower, other.lower),
            min(self.upper, other.upper),
        )

    def combine_or(self, other: "TruthValue") -> "TruthValue":
        """OR lógico: unión de intervalos."""
        return TruthValue(
            min(self.lower, other.lower),
            max(self.upper, other.upper),
        )

    def __repr__(self) -> str:
        return f"[{self.lower:.2f}, {self.upper:.2f}]"

    def to_dict(self) -> Dict[str, float]:
        return {"lower": round(self.lower, 3), "upper": round(self.upper, 3)}


# Constantes de verdad útiles
TRUE = TruthValue(1.0, 1.0)
FALSE = TruthValue(0.0, 0.0)
UNKNOWN = TruthValue(0.0, 1.0)


@dataclass
class InferenceStep:
    """Un paso de inferencia trazable."""
    conclusion: str          # qué se concluyó (ej: "ssh → es_seguro")
    truth: TruthValue         # con qué certeza
    premises: List[str]       # de qué hechos/reglas se derivó
    rule_name: str            # nombre de la regla aplicada
    timestamp: float = field(default_factory=time.time)

    def explain(self) -> str:
        prems = ", ".join(self.premises)
        return (f"{self.conclusion} {self.truth} "
                f"← regla:{self.rule_name} ← {{{prems}}}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conclusion": self.conclusion,
            "truth": self.truth.to_dict(),
            "premises": self.premises,
            "rule": self.rule_name,
        }


# ── Reglas lógicas ────────────────────────────────────────────────────────────

@dataclass
class LogicRule:
    """Una regla de inferencia lógica.

    head: predicado cabeza (lo que se concluye)
    body: lista de predicados cuerpo (lo que debe cumplirse)
    weight: peso de la regla (0-1), reduce la certeza
    description: descripción legible
    """
    name: str
    head: str                # ej: "X es_seguro"
    body: List[str]          # ej: ["X usa_cifrado", "X es_protocolo"]
    weight: float = 1.0
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "head": self.head,
            "body": self.body, "weight": self.weight,
            "description": self.description,
        }


# ── El motor de razonamiento ──────────────────────────────────────────────────

class LogicReasoner:
    """Motor de razonamiento lógico sobre el grafo de conocimiento de EIDOS.

    Uso:
        engine = LogicReasoner()
        engine.load_rules_builtin()
        result = engine.query("¿es SSH seguro?")
        print(result.explanation)
    """

    def __init__(self):
        self.rules: Dict[str, LogicRule] = {}       # todas las reglas
        self.facts: Dict[str, TruthValue] = {}       # hechos: concepto → verdad
        self._trace: List[InferenceStep] = []        # trazabilidad
        self._inferred_cache: Dict[str, TruthValue] = {}
        self._concept_index: Dict[str, List[str]] = defaultdict(list)  # palabra → concepts
        self._node_definitions: Dict[str, str] = {}   # concept → definition
        self._learned_count: int = 0

    # ── Persistencia de hechos aprendidos ──────────────────────────────────────

    def _init_learned_db(self) -> None:
        """Crea la tabla de hechos aprendidos si no existe."""
        from core.db import get_conn
        try:
            conn = get_conn(BRAIN_DB, timeout=30)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS logic_learned_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fact_key TEXT UNIQUE,
                    lower_bound REAL DEFAULT 0.7,
                    upper_bound REAL DEFAULT 0.9,
                    source TEXT DEFAULT 'learned',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    times_used INTEGER DEFAULT 0
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning("logic: error init learned DB: %s", e)

    def learn_fact(self, subject: str, relation: str, target: str = "",
                   confidence: float = 0.75, source: str = "learned") -> bool:
        """Aprende un hecho nuevo y lo persiste. Retorna True si es nuevo.

        Args:
            subject: el sujeto (ej: 'nginx')
            relation: la relación (ej: 'es_servidor_web')
            target: el objeto (opcional, ej: 'servidor web')
        """
        # Limpiar y normalizar
        rel_clean = relation.lower().strip().replace(" ", "_")
        subj_clean = subject.lower().strip()
        if target:
            tgt_clean = target.lower().strip().replace(" ", "_")
            fact_key = f"{subj_clean} {rel_clean} {tgt_clean}"
        else:
            fact_key = f"{subj_clean} {rel_clean}"
        # Simplificar claves largas
        if len(fact_key) > 160:
            fact_key = fact_key[:160]
        if fact_key in self.facts:
            # Ya existe → reforzar confianza
            old = self.facts[fact_key]
            self.facts[fact_key] = TruthValue(
                min(1.0, old.lower + 0.05),
                min(1.0, old.upper + 0.03),
            )
            return False
        # Nuevo hecho
        tv = TruthValue(confidence, min(1.0, confidence + 0.15))
        self.facts[fact_key] = tv
        self._learned_count += 1
        # Indexar palabras del sujeto
        for word in self._tokenize(subject):
            if word not in self._concept_index or subject not in self._concept_index[word]:
                self._concept_index[word].append(subject)
        # Persistir a DB
        try:
            from core.db import get_conn
            self._init_learned_db()
            conn = get_conn(BRAIN_DB, timeout=30)
            conn.execute(
                "INSERT OR IGNORE INTO logic_learned_facts (fact_key, lower_bound, upper_bound, source) "
                "VALUES (?, ?, ?, ?)",
                (fact_key, tv.lower, tv.upper, source),
            )
            conn.commit()
            conn.close()
            log.info("logic: aprendido '%s' [%.0f%%-%.0f%%]", fact_key, tv.lower*100, tv.upper*100)
        except Exception as e:
            log.debug("logic: error persistiendo hecho: %s", e)
        return True

    def _load_learned_facts(self) -> int:
        """Carga hechos aprendidos previamente desde la DB."""
        from core.db import get_conn
        self._init_learned_db()
        count = 0
        try:
            conn = get_conn(BRAIN_DB, timeout=30)
            rows = conn.execute(
                "SELECT fact_key, lower_bound, upper_bound, times_used "
                "FROM logic_learned_facts ORDER BY times_used DESC, id DESC"
            ).fetchall()
            for fact_key, lb, ub, used in rows:
                if fact_key not in self.facts:
                    self.facts[fact_key] = TruthValue(float(lb), float(ub))
                    # Re-indexar el sujeto (primera palabra antes del espacio)
                    parts = fact_key.split()
                    if parts:
                        subject = parts[0]
                        for word in self._tokenize(subject):
                            if subject not in self._concept_index.get(word, []):
                                self._concept_index[word].append(subject)
                    count += 1
            conn.close()
            if count:
                log.info("logic: %d hechos aprendidos cargados desde DB", count)
        except Exception as e:
            log.debug("logic: error cargando learned facts: %s", e)
        return count

    def learn_from_text(self, text: str, subject_hint: str = "",
                         confidence: float = 0.70,
                         use_groq: bool = True) -> int:
        """Extrae hechos de un texto en lenguaje natural y los aprende.

        S119 #240: Dos estrategias en cascada:
        1. Groq (si está disponible y use_groq=True) → extracción estructurada
        2. Regex (fallback rápido, sin API)

        Returns: número de hechos nuevos aprendidos.
        """
        learned = 0

        # ── Estrategia 1: Groq (S119 #240) ──────────────────────────────
        if use_groq:
            groq_learned = self._extract_facts_with_groq(
                text, subject_hint, confidence
            )
            if groq_learned > 0:
                return groq_learned
            # Si Groq falló o no extrajo nada, caer a regex

        # ── Estrategia 2: Regex (fallback) ──────────────────────────────
        return self._extract_facts_regex(text, subject_hint, confidence)

    def _extract_facts_with_groq(self, text: str, subject_hint: str = "",
                                  confidence: float = 0.70) -> int:
        """Extrae hechos usando Groq API (extracción estructurada JSON).

        S119 #240: Reemplaza los 9 patrones regex con comprensión real del
        lenguaje, capturando relaciones que los patrones no ven (voz pasiva,
        frases complejas, inglés, múltiples cláusulas).

        Returns: número de hechos aprendidos (0 si Groq no disponible o falla).
        """
        import json as _json
        learned = 0
        try:
            # Verificar API key
            from pathlib import Path
            secrets_env = Path.home() / ".eidos" / "secrets.env"
            if not secrets_env.exists():
                return 0
            content = secrets_env.read_text()
            if "GROQ_API_KEY" not in content:
                return 0
            # Extraer key
            import re as _re
            m = _re.search(r'GROQ_API_KEY\s*=\s*(\S+)', content)
            if not m:
                return 0
            api_key = m.group(1).strip().strip('"').strip("'")
            if len(api_key) < 10:
                return 0

            # Prompt estructurado
            subject = subject_hint.strip() if subject_hint else ""
            prompt = (
                f"Extrae hechos atómicos del siguiente texto en formato JSON. "
                f"Cada hecho debe ser: {{\"subject\": \"entidad\", "
                f"\"relation\": \"tipo_de_relacion\", \"object\": \"valor\"}}.\n\n"
                f"Reglas:\n"
                f"- Usa relaciones con guiones bajos (ej: es_protocolo, usa_cifrado, "
                f"permite_acceso_remoto, sirve_para_escanear, es_parte_de)\n"
                f"- El subject DEBE ser '{subject}' si aparece en el texto\n"
                f"- Extrae SOLO relaciones explícitas, no inventes\n"
                f"- Si no hay hechos claros, devuelve []\n"
                f"- Responde SOLO el JSON, sin markdown ni explicaciones\n\n"
                f"Texto: {text[:1500]}\n\n"
                f"JSON:"
            )

            # Llamar a Groq
            import urllib.request as _ur
            req = _ur.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                data=_json.dumps({
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 500,
                }).encode(),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            resp = _ur.urlopen(req, timeout=15)
            data = _json.loads(resp.read())
            raw = data["choices"][0]["message"]["content"].strip()

            # Parsear JSON
            facts = _json.loads(raw)
            if not isinstance(facts, list):
                return 0

            for f in facts:
                s = str(f.get("subject", "")).lower().strip()
                rel = str(f.get("relation", "")).lower().strip()
                o = str(f.get("object", "")).lower().strip()
                # Limpiar
                s = _re.sub(r'[^\wáéíóúñü]', '', s)
                rel = _re.sub(r'[^\wáéíóúñü_]', '', rel)
                o = _re.sub(r'[^\wáéíóúñü\s]', '', o).strip()
                if len(s) < 2 or len(rel) < 3 or len(o) < 2:
                    continue
                if self.learn_fact(s, rel, o, confidence):
                    learned += 1

            if learned:
                log.info("logic: %d hechos extraídos vía Groq de '%s'",
                         learned, subject or text[:40])
            return learned

        except Exception as e:
            log.debug("Groq fact extraction falló: %s", e)
            return 0

    def _extract_facts_regex(self, text: str, subject_hint: str = "",
                              confidence: float = 0.70) -> int:
        """Extracción de hechos por regex (fallback rápido, sin API)."""
        import re
        learned = 0
        subject = subject_hint.lower().strip() if subject_hint else ""
        seen_subjects = set()
        patterns = [
            (re.compile(r'(\S+)\s+es\s+(?:un|una)\s+(\S+(?:\s+\S+)?)', re.IGNORECASE),
             lambda s, o: f"es_{o.replace(' ', '_')}"),
            (re.compile(r'(\S+)\s+sirve\s+para\s+(\S+(?:\s+\S+)?)', re.IGNORECASE),
             lambda s, o: f"sirve_para_{o.replace(' ', '_')}"),
            (re.compile(r'(\S+)\s+es\s+(?:un\s+)?tipo\s+de\s+(\S+(?:\s+\S+)?)', re.IGNORECASE),
             lambda s, o: f"es_tipo_de_{o.replace(' ', '_')}"),
            (re.compile(r'(\S+)\s+(?:usa|utiliza)\s+(\S+(?:\s+\S+)?)', re.IGNORECASE),
             lambda s, o: f"usa_{o.replace(' ', '_')}"),
            (re.compile(r'(\S+)\s+permite\s+(\S+(?:\s+\S+)?)', re.IGNORECASE),
             lambda s, o: f"permite_{o.replace(' ', '_')}"),
            (re.compile(r'(\S+)\s+(?:tiene|contiene|posee)\s+(\S+(?:\s+\S+)?)', re.IGNORECASE),
             lambda s, o: f"tiene_{o.replace(' ', '_')}"),
            (re.compile(r'(\S+)\s+significa\s+(\S+(?:\s+\S+)?)', re.IGNORECASE),
             lambda s, o: f"significa_{o.replace(' ', '_')}"),
            (re.compile(r'(\S+)\s+puede\s+(\S+(?:\s+\S+)?)', re.IGNORECASE),
             lambda s, o: f"puede_{o.replace(' ', '_')}"),
            (re.compile(r'(\S+)\s+funciona\s+como\s+(\S+(?:\s+\S+)?)', re.IGNORECASE),
             lambda s, o: f"funciona_como_{o.replace(' ', '_')}"),
        ]
        sentences = re.split(r'[.!?]\s+', text)
        for sentence in sentences:
            for regex, rel_fn in patterns:
                for m in regex.finditer(sentence):
                    s = m.group(1).lower().strip()
                    o = m.group(2).lower().strip()
                    s = re.sub(r'[^\wáéíóúñü]', '', s)
                    o_clean = re.sub(r'[^\wáéíóúñü\s]', '', o).strip()
                    if len(s) < 2 or len(o_clean) < 2 or s == o_clean:
                        continue
                    if subject and s != subject:
                        continue
                    relation = rel_fn(s, o_clean)
                    if self.learn_fact(s, relation, o_clean, confidence):
                        learned += 1
                        seen_subjects.add(s)
                    o_first = o_clean.split()[0] if ' ' in o_clean else o_clean
                    if len(o_first) >= 2 and o_first not in seen_subjects:
                        inv_rel = f"es_caracteristica_de_{s}"
                        if self.learn_fact(o_first, inv_rel, s, confidence * 0.75):
                            learned += 1
        return learned

    # ── Carga de conocimiento desde el grafo existente ────────────────────────

    def load_from_graph(self, max_nodes: int = 5000) -> int:
        """Carga conceptos y definiciones desde knowledge_nodes."""
        import sqlite3
        from core.db import get_conn
        self._concept_index.clear()
        self._node_definitions.clear()
        count = 0
        try:
            conn = get_conn(BRAIN_DB, timeout=30)
            # S119 #239: Usar quality_score si existe, si no fallback a filtro manual
            cols = [r[1] for r in conn.execute("PRAGMA table_info(knowledge_nodes)").fetchall()]
            if "quality_score" in cols:
                rows = conn.execute(
                    "SELECT concept, definition, confidence, source, category "
                    "FROM knowledge_nodes "
                    "WHERE definition IS NOT NULL AND length(definition) >= 30 "
                    "AND quality_score >= 0.35 "
                    "ORDER BY quality_score DESC, confidence DESC LIMIT ?",
                    (max_nodes,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT concept, definition, confidence, source, category "
                    "FROM knowledge_nodes "
                    "WHERE definition IS NOT NULL AND length(definition) >= 30 "
                    "AND source NOT IN ('wordnet', 'code_analyzer', 'graphify', "
                    "'tabula_rasa:path_scan', 'tabula_rasa:ast', "
                    "'mitre_attck', 'circl', 'nvd_nist') "
                    "AND category NOT IN ('dictionary', 'synset', 'code_structure', "
                    "'eidos_function', 'eidos_class', 'eidos_module', 'system_command', "
                    "'mitre_attack', 'cve_recent', 'inferred') "
                    "ORDER BY confidence DESC LIMIT ?",
                    (max_nodes,),
                ).fetchall()
            for concept, definition, confidence, source, category in rows:
                norm = concept.lower().strip()
                conf = float(confidence or 0.5)
                self.facts[norm] = TruthValue(conf, min(1.0, conf + 0.1))
                self._node_definitions[norm] = definition or ""
                # Indexar por palabra para búsqueda rápida
                for word in self._tokenize(norm):
                    self._concept_index[word].append(norm)
                if source and source not in ("graphify",):
                    for word in self._tokenize(source):
                        if len(word) >= 3:
                            self._concept_index[word].append(norm)
                count += 1
            conn.close()
            log.info("logic: cargados %d conceptos desde el grafo", count)
        except Exception as e:
            log.warning("logic: error cargando grafo: %s", e)
        # Extraer hechos implícitos de las definiciones
        self._extract_facts_from_definitions()
        return count

    def _extract_facts_from_definitions(self) -> int:
        """Extrae hechos lógicos de las definiciones de los nodos.
        Ej: 'SSH es un protocolo que usa cifrado' →
            hechos: ssh es_protocolo, ssh usa_cifrado
        """
        import re
        patterns = [
            # "X es un/una Y" → X es_Y
            (re.compile(r'(\S+)\s+es\s+(?:un|una)\s+(\S+)', re.IGNORECASE),
             lambda a, b: f"{a} es_{b}"),
            # "X son Y" → X es_Y
            (re.compile(r'(\S+)\s+son\s+(?:un|una)?\s*(\S+)', re.IGNORECASE),
             lambda a, b: f"{a} es_{b}"),
            # "X usa/utiliza Y" → X usa_Y
            (re.compile(r'(\S+)\s+(?:usa|utiliza)\s+(\S+)', re.IGNORECASE),
             lambda a, b: f"{a} usa_{b}"),
            # "X tiene/contiene Y" → X tiene_Y
            (re.compile(r'(\S+)\s+(?:tiene|contiene|posee)\s+(\S+)', re.IGNORECASE),
             lambda a, b: f"{a} tiene_{b}"),
            # "X permite Y" → X permite_Y
            (re.compile(r'(\S+)\s+permite\s+(\S+)', re.IGNORECASE),
             lambda a, b: f"{a} permite_{b}"),
            # "X es parte de Y" → X es_parte_de Y
            (re.compile(r'(\S+)\s+es\s+parte\s+de\s+(\S+)', re.IGNORECASE),
             lambda a, b: f"{a} es_parte_de_{b}"),
            # "X es un protocolo" → X es_protocolo
            (re.compile(r'(\S+)\s+es\s+(?:un|una)\s+(\S+(?:\s+\S+)?)', re.IGNORECASE),
             lambda a, b: f"{a} es_{b.replace(' ', '_')}"),
        ]
        count = 0
        for concept, definition in self._node_definitions.items():
            if len(definition) < 30:
                continue
            # Tomar solo la primera frase (hasta el primer punto)
            first_sentence = definition.split(".")[0]
            for regex, fact_fn in patterns:
                for m in regex.finditer(first_sentence):
                    a = m.group(1).lower().strip()
                    b = m.group(2).lower().strip()
                    if len(a) >= 2 and len(b) >= 2 and a != b:
                        fact_key = fact_fn(a, b)
                        if fact_key not in self.facts:
                            self.facts[fact_key] = TruthValue(0.7, 0.9)
                            count += 1
        if count:
            log.info("logic: %d hechos extraídos de definiciones", count)
        return count

    def load_edges(self, max_edges: int = 5000) -> int:
        """Carga relaciones semánticas desde knowledge_edges."""
        from core.db import get_conn
        count = 0
        try:
            conn = get_conn(BRAIN_DB, timeout=30)
            rows = conn.execute(
                "SELECT kn1.concept, ke.relation_type, kn2.concept, ke.strength "
                "FROM knowledge_edges ke "
                "JOIN knowledge_nodes kn1 ON ke.from_node = kn1.id "
                "JOIN knowledge_nodes kn2 ON ke.to_node = kn2.id "
                "WHERE ke.relation_type IN ('is_a', 'implies', 'has_property', "
                "'part_of', 'rationale_for', 'synonym', 'related') "
                "LIMIT ?",
                (max_edges,),
            ).fetchall()
            for from_c, rel, to_c, strength in rows:
                fc = from_c.lower().strip()
                tc = to_c.lower().strip()
                s = float(strength or 0.8)
                # Cada arista se convierte en un hecho relacional
                if rel == "is_a":
                    self.facts[f"{fc} es_un {tc}"] = TruthValue(s, min(1.0, s + 0.1))
                elif rel == "implies":
                    self.facts[f"{fc} implica {tc}"] = TruthValue(s, min(1.0, s + 0.1))
                elif rel == "has_property":
                    self.facts[f"{fc} tiene {tc}"] = TruthValue(s, min(1.0, s + 0.1))
                elif rel == "part_of":
                    self.facts[f"{fc} es_parte_de {tc}"] = TruthValue(s, min(1.0, s + 0.1))
                elif rel == "synonym":
                    self.facts[f"{fc} es_similar_a {tc}"] = TruthValue(s, min(1.0, s + 0.1))
                count += 1
            conn.close()
            log.info("logic: cargadas %d relaciones desde knowledge_edges", count)
        except Exception as e:
            log.warning("logic: error cargando edges: %s", e)
        return count

    # ── Reglas built-in ────────────────────────────────────────────────────
    def add_rule(self, rule: LogicRule) -> None:
        """Añade una regla al motor."""
        self.rules[rule.name] = rule

    def load_seed_facts(self) -> int:
        """Carga hechos semilla desde el conocimiento curado de EIDOS.
        Estos son hechos de alta confianza que no están explícitamente en el grafo
        pero que EIDOS conoce."""
        seed = {
            # Redes y protocolos
            "ssh es_protocolo": TruthValue(0.95, 0.99),
            "ssh usa_cifrado": TruthValue(0.90, 0.98),
            "ssh permite_acceso_remoto": TruthValue(0.90, 0.98),
            "http es_protocolo": TruthValue(0.95, 0.99),
            "https es_protocolo": TruthValue(0.95, 0.99),
            "https usa_cifrado": TruthValue(0.90, 0.98),
            "dns es_protocolo": TruthValue(0.90, 0.98),
            "tcp es_protocolo": TruthValue(0.95, 0.99),
            "udp es_protocolo": TruthValue(0.95, 0.99),
            "ftp es_protocolo": TruthValue(0.90, 0.98),
            # Herramientas
            "nmap es_herramienta": TruthValue(0.90, 0.98),
            "nmap es_para escaneo_de_red": TruthValue(0.85, 0.95),
            "wireshark es_herramienta": TruthValue(0.90, 0.98),
            "wireshark es_para analisis_de_trafico": TruthValue(0.85, 0.95),
            "metasploit es_herramienta": TruthValue(0.90, 0.98),
            "metasploit es_para pentesting": TruthValue(0.85, 0.95),
            "burpsuite es_herramienta": TruthValue(0.85, 0.95),
            "burpsuite es_para pruebas_de_seguridad_web": TruthValue(0.85, 0.95),
            # Navegadores
            "firefox es_navegador": TruthValue(0.95, 0.99),
            "chromium es_navegador": TruthValue(0.90, 0.98),
            "chrome es_navegador": TruthValue(0.85, 0.95),
            # SO y sistemas
            "linux es_sistema_operativo": TruthValue(0.95, 0.99),
            "kali es_sistema_operativo": TruthValue(0.90, 0.98),
            "kali es_distribucion_de linux": TruthValue(0.90, 0.98),
            "kali es_para seguridad_informatica": TruthValue(0.85, 0.95),
            "windows es_sistema_operativo": TruthValue(0.90, 0.98),
            "docker es_herramienta": TruthValue(0.85, 0.95),
            "docker es_para contenedores": TruthValue(0.85, 0.95),
            "virtualbox es_herramienta": TruthValue(0.85, 0.95),
            "virtualbox es_para virtualizacion": TruthValue(0.85, 0.95),
            "vmware es_herramienta": TruthValue(0.85, 0.95),
            "vmware es_para virtualizacion": TruthValue(0.85, 0.95),
            # EIDOS
            "eidos es_asistente": TruthValue(0.90, 0.98),
            "eidos tiene grafo_de_conocimiento": TruthValue(0.90, 0.98),
            "eidos puede_abrir_urls": TruthValue(0.80, 0.90),
            "eidos puede_leer_pantalla": TruthValue(0.80, 0.90),
            # Lenguajes
            "python es_lenguaje_de_programacion": TruthValue(0.95, 0.99),
            "javascript es_lenguaje_de_programacion": TruthValue(0.90, 0.98),
            "bash es_lenguaje_de_shell": TruthValue(0.90, 0.98),
            "sql es_lenguaje_de_consultas": TruthValue(0.90, 0.98),
        }
        count = 0
        for key, tv in seed.items():
            if key not in self.facts:
                self.facts[key] = tv
                count += 1
        log.info("logic: %d hechos semilla cargados", count)
        return count

    def load_rules_builtin(self) -> None:
        """Carga reglas de razonamiento predefinidas (sentido común)."""
        builtins = [
            # Transitividad de "es_un" (herencia de propiedades)
            LogicRule(
                name="herencia_propiedades",
                head="X tiene Y",
                body=["X es_un Z", "Z tiene Y"],
                weight=0.85,
                description="Si X es un tipo de Z, y Z tiene la propiedad Y, entonces X tiene Y",
            ),
            # Transitividad de implicación
            LogicRule(
                name="trans_implica",
                head="X implica Z",
                body=["X implica Y", "Y implica Z"],
                weight=0.80,
                description="Si X implica Y e Y implica Z, entonces X implica Z",
            ),
            # Uso de cifrado → seguro
            LogicRule(
                name="cifrado_es_seguro",
                head="X es_seguro",
                body=["X usa_cifrado"],
                weight=0.75,
                description="Si algo usa cifrado, es considerado seguro",
            ),
            # Protocolo + cifrado → protocolo seguro
            LogicRule(
                name="protocolo_seguro",
                head="X es_protocolo_seguro",
                body=["X es_protocolo", "X usa_cifrado"],
                weight=0.90,
                description="Un protocolo que usa cifrado es un protocolo seguro",
            ),
            # Herramienta de ataque → potencialmente peligrosa
            LogicRule(
                name="herramienta_ofensiva",
                head="X es_ofensiva",
                body=["X es_herramienta", "X es_para ataque"],
                weight=0.85,
                description="Una herramienta diseñada para ataque es ofensiva",
            ),
            # Parte de → pertenencia
            LogicRule(
                name="parte_de_pertenece",
                head="X pertenece_a Z",
                body=["X es_parte_de Z"],
                weight=0.90,
                description="Si X es parte de Z, entonces X pertenece a Z",
            ),
            # Navegador → puede abrir URLs
            LogicRule(
                name="navegador_abre_urls",
                head="X puede_abrir_urls",
                body=["X es_navegador"],
                weight=0.95,
                description="Un navegador puede abrir URLs",
            ),
            # Sinónimos → conceptos intercambiables
            LogicRule(
                name="sinonimo_propaga",
                head="X tiene Y",
                body=["X es_similar_a Z", "Z tiene Y"],
                weight=0.70,
                description="Si X es similar a Z, y Z tiene Y, entonces X probablemente tiene Y",
            ),
        ]
        for rule in builtins:
            self.add_rule(rule)
        log.info("logic: %d reglas built-in cargadas", len(builtins))

    # ── Búsqueda ───────────────────────────────────────────────────────────

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Tokeniza texto en palabras significativas."""
        import re
        return [t.lower() for t in re.findall(r"[a-záéíóúñü0-9]{2,}", text.lower())]

    def find_concepts(self, query: str, limit: int = 10) -> List[str]:
        """Encuentra conceptos relevantes para una query."""
        tokens = self._tokenize(query)
        # Boost masivo para palabras que aparecen como conceptos indexados
        direct_boost = 5.0
        scores: Dict[str, float] = defaultdict(float)
        # Puntuar por coincidencia de tokens
        for token in tokens:
            # Coincidencia EXACTA de concepto (el token ES un concepto indexado)
            if token in self._concept_index:
                for concept in self._concept_index[token]:
                    scores[concept] += direct_boost
            # Coincidencia parcial
            for word, concepts in self._concept_index.items():
                if word != token and (token in word or word in token):
                    boost = 2.0 if len(token) >= 4 else 1.0
                    for concept in concepts:
                        scores[concept] += boost
        # También buscar en las definiciones
        query_lower = query.lower()
        for concept, definition in self._node_definitions.items():
            if any(tok in definition.lower() for tok in tokens if len(tok) >= 4):
                scores[concept] += 0.5
            if query_lower in definition.lower():
                scores[concept] += 1.5
        # Ordenar y devolver top
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        return [c for c, s in ranked[:limit] if s > 0]

    def get_definition(self, concept: str) -> Optional[str]:
        """Devuelve la definición de un concepto."""
        return self._node_definitions.get(concept.lower().strip())

    def get_truth(self, proposition: str) -> TruthValue:
        """Consulta el valor de verdad de una proposición (hecho o inferido)."""
        key = proposition.lower().strip()
        # 1. Hecho explícito
        if key in self.facts:
            return self.facts[key]
        # 2. Inferido previamente
        if key in self._inferred_cache:
            return self._inferred_cache[key]
        # 3. ¿El concepto existe? → [0, 1] si no
        tokens = self._tokenize(key)
        for token in tokens:
            if token in self._concept_index:
                return UNKNOWN  # existe el concepto pero no la proposición
        return UNKNOWN

    # ── Inferencia ──────────────────────────────────────────────────────────

    def apply_rule(self, rule: LogicRule) -> List[InferenceStep]:
        """Aplica UNA regla a todos los hechos conocidos. Devuelve conclusiones nuevas."""
        conclusions: List[InferenceStep] = []
        # Encontrar todas las instanciaciones posibles de las variables en el body
        # Variables: palabras que empiezan con mayúscula o son placeholders
        # Por simplicidad: X, Y, Z son variables universales
        variables = {"X", "Y", "Z", "W"}
        # Encontrar candidatos para cada variable
        all_concepts = list(self._concept_index.keys())
        # Buscar conceptos que aparezcan en hechos que coincidan con el body
        body_matches: List[Dict[str, str]] = []
        for fact_key, truth in self.facts.items():
            if not truth.is_true:
                continue
            # Intentar unificar cada literal del body con este hecho
            for body_literal in rule.body:
                binding = self._unify(body_literal, fact_key)
                if binding:
                    body_matches.append(binding)

        if not body_matches:
            return conclusions

        # Para cada binding único, evaluar si TODOS los literales del body se satisfacen
        seen = set()
        for binding in body_matches:
            bh = frozenset(binding.items())
            if bh in seen:
                continue
            seen.add(bh)
            # Verificar cada literal del body con este binding
            all_satisfied = True
            premises = []
            combined_truth = TRUE
            for body_literal in rule.body:
                # Sustituir variables
                literal_key = body_literal
                for var, val in binding.items():
                    literal_key = literal_key.replace(var, val)
                tv = self.get_truth(literal_key)
                if not tv.is_true:
                    all_satisfied = False
                    break
                premises.append(literal_key)
                combined_truth = combined_truth.combine_and(tv)

            if all_satisfied and premises:
                # Sustituir en la cabeza
                head_conclusion = rule.head
                for var, val in binding.items():
                    head_conclusion = head_conclusion.replace(var, val)
                # Aplicar peso de la regla
                final_truth = TruthValue(
                    combined_truth.lower * rule.weight,
                    min(1.0, combined_truth.upper * rule.weight + 0.1),
                )
                if final_truth not in (self.facts.get(head_conclusion), self._inferred_cache.get(head_conclusion)):
                    step = InferenceStep(
                        conclusion=head_conclusion,
                        truth=final_truth,
                        premises=premises,
                        rule_name=rule.name,
                    )
                    conclusions.append(step)
        return conclusions

    def _unify(self, pattern: str, fact: str) -> Optional[Dict[str, str]]:
        """Intenta unificar un patrón (con variables X, Y, Z) con un hecho.
        Ej: unify("X tiene Y", "ssh tiene cifrado") → {"X": "ssh", "Y": "cifrado"}
        """
        # Tokenizar ambos
        p_tokens = pattern.split()
        f_tokens = fact.split()
        # Si no coinciden en estructura, intentar matching flexible
        binding: Dict[str, str] = {}
        # Estrategia 1: El patrón contiene variables → reemplazar
        pat_vars = [t for t in p_tokens if t in {"X", "Y", "Z", "W"}]
        if not pat_vars:
            return binding if pattern == fact else None
        # Estrategia 2: Buscar el patrón como subestructura
        # "X tiene Y" debe coincidir con "A tiene B" donde A, B son cualquier cosa
        # Convertir patrón a regex
        import re
        regex_parts = []
        group_to_var = {}  # mapea número de grupo (1-based) → nombre de variable
        group_idx = 0
        for tok in p_tokens:
            if tok in {"X", "Y", "Z", "W"}:
                regex_parts.append(r"(\S+)")
                group_idx += 1
                group_to_var[group_idx] = tok
            else:
                regex_parts.append(re.escape(tok))
        regex_str = r"\s*".join(regex_parts)
        m = re.match("^" + regex_str + "$", fact)
        if m:
            for gi, var_name in group_to_var.items():
                binding[var_name] = m.group(gi)
            return binding
        return None

    def reason(self, max_iterations: int = 3) -> List[InferenceStep]:
        """Aplica TODAS las reglas iterativamente hasta punto fijo (o max_iterations)."""
        all_new: List[InferenceStep] = []
        for iteration in range(max_iterations):
            new_this_round = 0
            for rule_name, rule in self.rules.items():
                steps = self.apply_rule(rule)
                for step in steps:
                    key = step.conclusion.lower().strip()
                    if key not in self._inferred_cache:
                        self._inferred_cache[key] = step.truth
                        all_new.append(step)
                        new_this_round += 1
            if new_this_round == 0:
                break  # punto fijo alcanzado
        log.info("logic: %d conclusiones inferidas en %d iteraciones",
                 len(all_new), iteration + 1)
        return all_new

    # ── Query API ───────────────────────────────────────────────────────────

    def query(self, question: str, max_results: int = 5,
              explain: bool = True) -> Dict[str, Any]:
        """Responde una pregunta en lenguaje natural con razonamiento lógico.

        Args:
            question: La pregunta en español (o inglés)
            max_results: Máximo de conclusiones a devolver
            explain: Si True, incluye trazabilidad de cada conclusión

        Returns:
            Dict con: answer, conclusions, trace, elapsed_s
        """
        t0 = time.time()
        # Lazy-load: si no hay conceptos cargados, cargar del grafo
        if len(self._concept_index) < 100:
            self.load_from_graph(max_nodes=5000)
            self.load_edges(max_edges=5000)
            self.load_seed_facts()
        concepts = self.find_concepts(question, limit=10)
        # Ejecutar razonamiento
        inferences = self.reason(max_iterations=3)
        # Filtrar conclusiones relevantes a la pregunta
        question_tokens = set(self._tokenize(question))
        relevant = []
        for inf in inferences:
            score = 0
            for tok in question_tokens:
                if tok in inf.conclusion.lower():
                    score += 2
            for prem in inf.premises:
                for tok in question_tokens:
                    if tok in prem.lower():
                        score += 1
            if score > 0:
                relevant.append((score, inf))
        relevant.sort(key=lambda x: -x[0])
        # Construir respuesta
        conclusions = []
        for score, inf in relevant[:max_results]:
            conclusions.append({
                "statement": inf.conclusion,
                "confidence": inf.truth.to_dict(),
                "because": [p for p in inf.premises],
                "rule_applied": inf.rule_name,
            })
        # También incluir definiciones directas encontradas
        direct_facts = []
        # Usar más conceptos para no perder los aprendidos
        for concept in concepts[:max(10, max_results * 2)]:
            definition = self._node_definitions.get(concept)
            # Buscar hechos aprendidos sobre este concepto
            related_facts = []
            for fact_key, tv in self.facts.items():
                if fact_key.startswith(concept + " ") and tv.is_true:
                    related_facts.append(fact_key)
            if definition or related_facts:
                direct_facts.append({
                    "concept": concept,
                    "definition": (definition or "")[:400],
                    "confidence": self.facts.get(concept, UNKNOWN).to_dict(),
                    "learned_facts": related_facts[:8],
                    "_score": len(related_facts) + (1 if definition and len(definition)>=30 else 0),
                })
        # Ordenar: priorizar conceptos con hechos aprendidos
        direct_facts.sort(key=lambda d: -d["_score"])
        # Generar respuesta textual
        answer_parts = []
        shown = 0
        for df in direct_facts:
            if shown >= 2:
                break
            df_def = df.get("definition", "")
            df_learned = df.get("learned_facts", [])
            # Saltar conceptos irrelevantes (puntuación baja)
            if df.get("_score", 0) == 0:
                continue
            if df_learned:
                # Mostrar hechos aprendidos como definición sintética
                facts_display = "; ".join(
                    f.replace(df['concept'] + ' ', '').replace('_', ' ')
                    for f in df_learned[:5]
                )
                answer_parts.append(f"• {df['concept']}: {facts_display}.")
                shown += 1
            elif df_def and len(df_def) >= 30:
                answer_parts.append(f"• {df['concept']}: {df_def[:300]}")
                shown += 1
            answer_parts.append("─ Razonamiento lógico:")
            for c in conclusions[:3]:
                answer_parts.append(f"• {c['statement']} [{c['confidence']['lower']:.0%}–{c['confidence']['upper']:.0%}]")
                if explain:
                    answer_parts.append(f"  ↳ porque: {', '.join(c['because'][:3])}")
        if not answer_parts:
            answer_parts.append(
                "No tengo suficiente información en mi grafo de conocimiento "
                "para responder esto con certeza. " + (
                    f"Sí conozco estos conceptos relacionados: {', '.join(concepts[:5])}"
                    if concepts else ""
                )
            )
        return {
            "answer": "\n".join(answer_parts),
            "conclusions": conclusions,
            "direct_facts": direct_facts,
            "concepts_found": concepts,
            "elapsed_s": round(time.time() - t0, 3),
            "total_inferences": len(inferences),
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

_logic_reasoner: Optional[LogicReasoner] = None


def get_logic_reasoner() -> LogicReasoner:
    """Devuelve la instancia singleton del motor lógico."""
    global _logic_reasoner
    if _logic_reasoner is None:
        _logic_reasoner = LogicReasoner()
        _logic_reasoner.load_rules_builtin()
    return _logic_reasoner


def reload_knowledge(force: bool = False) -> LogicReasoner:
    """Recarga todo el conocimiento desde el grafo."""
    global _logic_reasoner
    _logic_reasoner = LogicReasoner()
    _logic_reasoner.load_rules_builtin()
    _logic_reasoner.load_from_graph(max_nodes=8000)    # PRIMERO: puebla _concept_index
    _logic_reasoner.load_edges(max_edges=8000)
    _logic_reasoner.load_seed_facts()                   # LUEGO: hechos semilla
    _logic_reasoner._load_learned_facts()               # ÚLTIMO: hechos aprendidos (pisan)
    log.info("logic: recarga completa — %d conceptos, %d reglas",
             len(_logic_reasoner._concept_index),
             len(_logic_reasoner.rules))
    return _logic_reasoner
