"""
core/eidos_distiller.py — Destilador de respuestas Ollama → tripletas en brain.db

Convierte respuestas de texto plano de Ollama en nodos estructurados
(concepto → definición → categoría) con relaciones explícitas en knowledge_graph.db.

Flujo:
  1. Recibe texto de respuesta de Ollama
  2. Extrae conceptos clave (NLP básico: sustantivos, frases nominales)
  3. Busca si ya existen en brain.db
  4. Si no existen: los crea como nodos con confidence inicial 0.7
  5. Crea relaciones entre conceptos en knowledge_graph.db
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from core.db import get_conn

log = logging.getLogger("eidos.distiller")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
KNOWLEDGE_GRAPH_DB = Path.home() / ".eidos" / "knowledge_graph.db"


class TripletDistiller:
    """Destila respuestas Ollama en nodos de conocimiento estructurados."""

    # Palabras de relación comunes español/inglés
    RELATION_VERBS = {
        "es", "son", "era", "fueron", "ser", "está", "están",
        "tiene", "tienen", "usa", "usan", "utiliza", "utilizan",
        "genera", "procesa", "contiene", "incluye", "compone",
        "define", "significa", "representa", "permite", "requiere",
        "is", "are", "was", "were", "be", "has", "have", "had",
        "uses", "uses", "contains", "includes", "comprises",
        "defines", "means", "represents", "allows", "requires",
        "creates", "produces", "transforms", "converts",
        "previene", "prevents", "protege", "protects",
        "ataca", "attacks", "explota", "exploits",
    }

    # Patrones para extraer definiciones
    DEF_PATTERNS = [
        re.compile(r'([A-Z][A-Za-z0-9_\-]+(?:\s+[A-Z][A-Za-z0-9_\-]+)*)\s+es\s+(.+?)(?:\.|$)', re.UNICODE),
        re.compile(r'([A-Z][A-Za-z0-9_\-]+(?:\s+[A-Z][A-Za-z0-9_\-]+)*)\s+is\s+(?:a|an|the)\s+(.+?)(?:\.|$)', re.UNICODE),
        re.compile(r'([A-Z][A-Za-z0-9_\-]+(?:\s+[A-Z][A-Za-z0-9_\-]+)*)\s+se\s+(define|conoce|entiende)\s+como\s+(.+?)(?:\.|$)', re.UNICODE),
        re.compile(r'(LLM|VLM|SLM|RAG|API|SSH|HTTP|DNS|TCP|IP|CVE|SQL|XSS)\s+(?:es|is)\s+(.+?)(?:\.|$)', re.UNICODE),
    ]

    # Palabras vacías
    STOPWORDS = {"el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al", "en",
                 "con", "sin", "por", "para", "y", "e", "o", "a", "que", "es", "se", "no", "su",
                 "the", "a", "an", "of", "in", "to", "for", "with", "and", "or", "is", "are",
                 "this", "that", "these", "those", "it", "its", "they", "them"}

    def __init__(self):
        self._stats = {"total_distilled": 0, "concepts_extracted": 0, "relations_created": 0}

    def distill(self, question: str, ollama_response: str, category: str = "distilled") -> Dict[str, int]:
        """
        Destila una respuesta de Ollama en nodos de conocimiento.
        
        Args:
            question: La pregunta original
            ollama_response: La respuesta de Ollama
            category: Categoría para los nuevos nodos
            
        Returns:
            Dict con stats de la destilación
        """
        t0 = time.time()
        concepts = self._extract_concepts(question, ollama_response)
        relations = self._extract_relations(concepts, ollama_response)
        
        added_nodes = self._store_nodes(concepts, category)
        added_relations = self._store_relations(relations)
        
        self._stats["total_distilled"] += 1
        self._stats["concepts_extracted"] += len(concepts)
        self._stats["relations_created"] += len(relations)
        
        elapsed = time.time() - t0
        log.info(f"Destilado: {len(concepts)} conceptos, {len(relations)} relaciones en {elapsed:.2f}s")
        
        return {
            "concepts": len(concepts),
            "relations": len(relations),
            "added_nodes": added_nodes,
            "added_relations": added_relations,
            "elapsed_s": round(elapsed, 2),
        }

    def _extract_concepts(self, question: str, response: str) -> List[Dict]:
        """Extrae conceptos de la pregunta y respuesta."""
        concepts = []
        seen = set()

        # 1. Extraer definiciones con patrones regex
        for pattern in self.DEF_PATTERNS:
            for match in pattern.finditer(response[:500]):  # Solo primeras 500 chars
                concept = match.group(1).strip()
                definition = match.group(2).strip() if match.lastindex >= 2 else ""
                if concept and len(concept) > 2 and concept.lower() not in seen:
                    score = 0.7  # confidence inicial
                    concepts.append({
                        "name": concept,
                        "definition": definition[:300],
                        "confidence": score,
                    })
                    seen.add(concept.lower())

        # 2. Extraer conceptos de la pregunta
        q_words = question.replace("?", "").replace("¿", "").split()
        q_concept = " ".join([w for w in q_words if w.lower() not in self.STOPWORDS and len(w) > 3])[:100]
        if q_concept and q_concept.lower() not in seen:
            concepts.append({
                "name": q_concept,
                "definition": response[:300],
                "confidence": 0.6,
            })
            seen.add(q_concept.lower())

        # 3. Extraer siglas/términos técnicos (LLM, VLM, API, SSH, etc.)
        for match in re.finditer(r'\b([A-Z]{2,}(?:-[A-Z0-9]+)*)\b', response[:500]):
            term = match.group(1)
            if term.lower() not in seen and len(term) >= 2:
                # Buscar definición cercana
                idx = match.start()
                context = response[max(0, idx-5):idx+80]
                concepts.append({
                    "name": term,
                    "definition": context[:200],
                    "confidence": 0.55,
                })
                seen.add(term.lower())

        return concepts[:8]  # máximo 8 conceptos por destilación

    def _extract_relations(self, concepts: List[Dict], response: str) -> List[Tuple]:
        """Extrae relaciones entre conceptos."""
        relations = []
        if len(concepts) < 2:
            return relations

        names = [c["name"] for c in concepts]
        response_lower = response.lower()

        for i, name_a in enumerate(names):
            for j, name_b in enumerate(names):
                if i >= j:
                    continue
                # Buscar verbos de relación entre los dos conceptos
                for verb in self.RELATION_VERBS:
                    pattern_a = re.escape(name_a.lower())
                    pattern_b = re.escape(name_b.lower())
                    # "A es B" o "A usa B"
                    if re.search(rf'{pattern_a}\s+{verb}\s+{pattern_b}', response_lower):
                        relations.append((name_a, name_b, verb))
                        break
                    # "B es A" o "B usa A"
                    if re.search(rf'{pattern_b}\s+{verb}\s+{pattern_a}', response_lower):
                        relations.append((name_b, name_a, verb))
                        break

        return relations

    def _store_nodes(self, concepts: List[Dict], category: str) -> int:
        """Guarda conceptos en evolution_brain.db."""
        added = 0
        try:
            con = get_conn(BRAIN_DB)
            for c in concepts:
                try:
                    con.execute(
                        "INSERT OR IGNORE INTO knowledge_nodes (concept, definition, category, source, confidence, usage_count) VALUES (?,?,?,?,?,?)",
                        (c["name"], c["definition"], category, "distilled", c["confidence"], 1)
                    )
                    if con.total_changes > 0:
                        added += 1
                except Exception:
                    pass
            con.commit()
            con.close()
        except Exception as e:
            log.error(f"Error storing nodes: {e}")
        return added

    def _store_relations(self, relations: List[Tuple]) -> int:
        """Guarda relaciones en knowledge_graph.db."""
        added = 0
        try:
            con = get_conn(KNOWLEDGE_GRAPH_DB)
            con.execute("CREATE TABLE IF NOT EXISTS edges (source TEXT, target TEXT, edge_type TEXT, weight REAL DEFAULT 1.0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            for source, target, edge_type in relations:
                try:
                    con.execute(
                        "INSERT OR IGNORE INTO edges (source, target, edge_type, weight) VALUES (?,?,?,?)",
                        (source, target, edge_type, 0.8)
                    )
                    if con.total_changes > 0:
                        added += 1
                except Exception:
                    pass
            con.commit()
            con.close()
        except Exception as e:
            log.error(f"Error storing relations: {e}")
        return added

    def get_stats(self) -> Dict:
        return dict(self._stats)


# Singleton
_instance = None

def get_distiller() -> TripletDistiller:
    global _instance
    if _instance is None:
        _instance = TripletDistiller()
    return _instance


# CLI test
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    d = get_distiller()
    q = "¿qué es un LLM?"
    r = "Large Language Model (LLM) es un modelo transformer entrenado en texto masivo. Genera texto autocompletando tokens. Ejemplos: GPT-4, Llama, DeepSeek."
    result = d.distill(q, r, "test_distillation")
    print(f"Resultado: {result}")
