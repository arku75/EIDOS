"""
core/eidos_inject_wordnet.py — Inyecta WordNet inglés + español en el grafo EIDOS

Fase 1 del plan DeepSeek+Claude (S76). Enriquece el grafo con léxico completo
para que EIDOS entienda español coloquial sin depender de APIs externas.

Capacidades resultantes:
  - "¿Qué significa X?" → definición del synset
  - "¿Cómo se dice X en inglés?" → navegación por equivalencia EN↔ES
  - Sinónimos, antónimos, hiperónimos, hipónimos, merónimos

Fuentes: NLTK WordNet (~155k palabras inglés) + Open Multilingual WordNet (~30k synsets español)
Total estimado: ~200k nodos, ~500k aristas

Uso:
    python3 core/eidos_inject_wordnet.py          # Inyección completa
    python3 core/eidos_inject_wordnet.py --dry     # Solo contar (sin insertar)
    python3 core/eidos_inject_wordnet.py --stats   # Estadísticas de lo inyectado
    python3 core/eidos_inject_wordnet.py --reset   # Borrar checkpoint y reinyectar
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from core.db import get_conn

# ── Constantes ─────────────────────────────────────────────────────────────────
DB_PATH = Path.home() / ".eidos" / "evolution_brain.db"
CHECKPOINT_PATH = Path.home() / ".eidos" / "wordnet_checkpoint.json"
BATCH_SIZE = 1000  # Filas por lote INSERT

# Confianza por idioma
EN_CONFIDENCE = 0.85
ES_CONFIDENCE = 0.75
EQUIV_CONFIDENCE = 0.80

# Prefijos para IDs (evitan colisiones con otros nodos)
PREFIX_EN = "wn_en"
PREFIX_ES = "wn_es"
PREFIX_SYNSET = "wn_synset"

# Source y category para estos nodos
SOURCE = "wordnet"
CAT_DICT = "dictionary"
CAT_SYNSET = "synset"


class WordNetInjector:
    """Inyector de WordNet inglés + español en el grafo neuronal de EIDOS.

    Usa NLTK WordNet para inglés y Open Multilingual WordNet (OMW) para español.
    Persiste en lotes con checkpoint para poder resumir tras interrupción.
    """

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.conn: Optional[sqlite3.Connection] = None
        self._nodes: List[Tuple] = []
        self._edges: List[Tuple] = []
        self._node_count = 0
        self._edge_count = 0
        self._checkpoint: Dict[str, Any] = {}
        self._wn_loaded = False

    # ── Setup ──────────────────────────────────────────────────────────────────

    def _ensure_nltk(self) -> bool:
        """Descarga recursos NLTK si faltan. Retorna True si todo listo."""
        try:
            import nltk
            for res in ["wordnet", "omw", "omw-1.4"]:
                try:
                    nltk.data.find(f"corpora/{res}")
                except LookupError:
                    print(f"  Descargando NLTK/{res}...")
                    nltk.download(res, quiet=True)
            self._wn_loaded = True
            return True
        except ImportError:
            print("ERROR: NLTK no instalado. Ejecuta: pip install nltk")
            return False
        except Exception as e:
            print(f"ERROR: NLTK falló: {e}")
            return False

    def _ensure_db(self) -> None:
        """Abre conexión SQLite y asegura que las tablas existan."""
        if not self.dry_run:
            self.conn = get_conn(DB_PATH, timeout=30)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            # Asegurar tablas (ya deberían existir, pero por si acaso)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_nodes (
                    id TEXT PRIMARY KEY,
                    concept TEXT UNIQUE,
                    definition TEXT,
                    category TEXT,
                    confidence REAL,
                    source TEXT
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_edges (
                    from_node TEXT,
                    to_node TEXT,
                    relation_type TEXT,
                    strength REAL,
                    PRIMARY KEY (from_node, to_node, relation_type)
                )
            """)

    def _load_checkpoint(self) -> Dict[str, Any]:
        """Carga checkpoint de sesión anterior."""
        if CHECKPOINT_PATH.exists():
            try:
                return json.loads(CHECKPOINT_PATH.read_text())
            except Exception:
                pass
        return {"en_offset": 0, "es_offset": 0, "crosslingual_done": False}

    def _save_checkpoint(self) -> None:
        """Guarda checkpoint para resume."""
        CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
        CHECKPOINT_PATH.write_text(json.dumps(self._checkpoint, indent=2))

    # ── Batch management ───────────────────────────────────────────────────────

    def _make_node_id(self, prefix: str, name: str) -> str:
        """Crea ID de nodo único con prefijo: ej. wn_en:house, wn_synset:dog.n.01"""
        safe_name = name.replace("'", "_").replace('"', "_")[:100]
        return f"{prefix}:{safe_name}"

    def _add_node(self, node_id: str, concept: str, definition: str,
                  category: str, confidence: float) -> None:
        """Encola un nodo para inserción en lote."""
        if not self.dry_run:
            self._nodes.append((node_id, concept, definition, category, confidence, SOURCE))
            if len(self._nodes) >= BATCH_SIZE:
                self._flush_nodes()
        self._node_count += 1

    def _add_edge(self, from_id: str, to_id: str, rel_type: str,
                  strength: float) -> None:
        """Encola una arista para inserción en lote."""
        if from_id == to_id:
            return
        if not self.dry_run:
            self._edges.append((from_id, to_id, rel_type, strength))
            if len(self._edges) >= BATCH_SIZE:
                self._flush_edges()
        self._edge_count += 1

    def _flush_nodes(self) -> None:
        """Vuelca nodos pendientes a SQLite."""
        if not self._nodes or self.dry_run:
            return
        try:
            self.conn.executemany(
                "INSERT OR IGNORE INTO knowledge_nodes(id, concept, definition, category, confidence, source) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                self._nodes,
            )
            self.conn.commit()
        except Exception as e:
            print(f"  WARN: flush_nodes error: {e}")
        self._nodes.clear()

    def _flush_edges(self) -> None:
        """Vuelca aristas pendientes a SQLite."""
        if not self._edges or self.dry_run:
            return
        try:
            self.conn.executemany(
                "INSERT OR IGNORE INTO knowledge_edges(from_node, to_node, relation_type, strength) "
                "VALUES(?, ?, ?, ?)",
                self._edges,
            )
            self.conn.commit()
        except Exception as e:
            print(f"  WARN: flush_edges error: {e}")
        self._edges.clear()

    def _flush_all(self) -> None:
        """Vuelca todo pendiente."""
        self._flush_nodes()
        self._flush_edges()

    # ── Inyección por idioma ───────────────────────────────────────────────────

    def inject_english(self) -> Tuple[int, int]:
        """Inyecta WordNet inglés: synsets + lemas + relaciones semánticas.

        Retorna (nodos_añadidos, aristas_añadidas).
        """
        from nltk.corpus import wordnet as wn

        start_offset = self._checkpoint.get("en_offset", 0)
        all_synsets = list(wn.all_synsets())
        total = len(all_synsets)

        if start_offset >= total:
            print(f"  Inglés: YA completado ({total} synsets)")
            return 0, 0

        print(f"\n{'[DRY] ' if self.dry_run else ''}Inglés: {total} synsets "
              f"(desde offset {start_offset})")

        nodes_before = self._node_count
        edges_before = self._edge_count

        for i in range(start_offset, total):
            syn = all_synsets[i]
            syn_name = syn.name()
            syn_id = self._make_node_id(PREFIX_SYNSET, syn_name)

            # Nodo synset con definición
            self._add_node(syn_id, syn_name, syn.definition() or "",
                          CAT_SYNSET, EN_CONFIDENCE)

            # Lemas en este synset
            lemmas: List[str] = []
            for lemma in syn.lemmas():
                lname = lemma.name().lower()
                lid = self._make_node_id(PREFIX_EN, lname)
                lemmas.append(lid)

                # Nodo palabra
                self._add_node(lid, lname, "", CAT_DICT, EN_CONFIDENCE)

                # Arista synset ↔ lema
                self._add_edge(syn_id, lid, "contains_lemma", EN_CONFIDENCE)

                # Antónimos
                for ant in lemma.antonyms():
                    ant_id = self._make_node_id(PREFIX_EN, ant.name().lower())
                    self._add_edge(lid, ant_id, "antonym", EN_CONFIDENCE)

            # Sinónimos entre todos los lemas del synset
            for a in range(len(lemmas)):
                for b in range(a + 1, len(lemmas)):
                    self._add_edge(lemmas[a], lemmas[b], "synonym", EN_CONFIDENCE)

            # Hiperónimos (synset → hypernym)
            for hype in syn.hypernyms():
                hype_id = self._make_node_id(PREFIX_SYNSET, hype.name())
                self._add_edge(syn_id, hype_id, "hypernym", EN_CONFIDENCE)

            # Hipónimos (synset → hyponym)
            for hypo in syn.hyponyms():
                hypo_id = self._make_node_id(PREFIX_SYNSET, hypo.name())
                self._add_edge(syn_id, hypo_id, "hyponym", EN_CONFIDENCE)

            # Merónimos (partes)
            for m in syn.part_meronyms():
                mid = self._make_node_id(PREFIX_SYNSET, m.name())
                self._add_edge(syn_id, mid, "meronym", EN_CONFIDENCE)

            # Checkpoint cada 1000 synsets
            if i > 0 and i % 1000 == 0:
                self._checkpoint["en_offset"] = i
                self._save_checkpoint()
                self._flush_all()
                pct = i * 100 / total
                print(f"  EN: {i}/{total} ({pct:.1f}%) | "
                      f"+{self._node_count - nodes_before} nodos, "
                      f"+{self._edge_count - edges_before} aristas")

        self._flush_all()
        self._checkpoint["en_offset"] = total
        self._save_checkpoint()

        added_nodes = self._node_count - nodes_before
        added_edges = self._edge_count - edges_before
        print(f"  Inglés COMPLETO: +{added_nodes} nodos, +{added_edges} aristas")
        return added_nodes, added_edges

    def inject_spanish(self) -> Tuple[int, int]:
        """Inyecta español via OMW: itera synsets ingleses y extrae lemas ES.

        Open Multilingual WordNet no expone synsets independientes para español.
        En su lugar, cada synset inglés tiene lemas en español accesibles via
        syn.lemmas(lang='spa'). Iteramos TODOS los synsets EN para extraer
        los lemas y crear nodos palabra español + equivalencias EN↔ES.

        Retorna (nodos_añadidos, aristas_añadidas).
        """
        from nltk.corpus import wordnet as wn

        start_offset = self._checkpoint.get("es_offset", 0)
        all_synsets = list(wn.all_synsets())
        total = len(all_synsets)

        if start_offset >= total:
            print(f"  Español: YA completado (iterados {total} synsets EN)")
            return 0, 0

        print(f"\n{'[DRY] ' if self.dry_run else ''}Español (via OMW lemas): "
              f"{total} synsets EN (desde offset {start_offset})")

        nodes_before = self._node_count
        edges_before = self._edge_count
        es_lemmas_found = 0

        for i in range(start_offset, total):
            syn = all_synsets[i]
            syn_name = syn.name()
            en_syn_id = self._make_node_id(PREFIX_SYNSET, syn_name)

            # Extraer lemas en español para este synset
            try:
                spa_lemmas = syn.lemmas(lang="spa")
            except Exception:
                spa_lemmas = []

            if spa_lemmas:
                es_lemmas_found += len(spa_lemmas)
                for lemma in spa_lemmas:
                    lname = lemma.name().lower()
                    lid = self._make_node_id(PREFIX_ES, lname)

                    # Nodo palabra español
                    self._add_node(lid, lname, syn.definition() or "",
                                  CAT_DICT, ES_CONFIDENCE)

                    # Arista: palabra ES ↔ synset EN (equivalent)
                    self._add_edge(lid, en_syn_id, "equivalent", EQUIV_CONFIDENCE)

                    # Arista: palabra ES ↔ definición del synset
                    self._add_edge(lid, en_syn_id, "translates_to", ES_CONFIDENCE)

            # Checkpoint cada 2000 synsets
            if i > 0 and i % 2000 == 0:
                self._checkpoint["es_offset"] = i
                self._save_checkpoint()
                self._flush_all()
                pct = i * 100 / total
                print(f"  ES: {i}/{total} ({pct:.1f}%) | "
                      f"{es_lemmas_found} lemas ES encontrados | "
                      f"+{self._node_count - nodes_before} nodos, "
                      f"+{self._edge_count - edges_before} aristas")

        self._flush_all()
        self._checkpoint["es_offset"] = total
        self._save_checkpoint()

        added_nodes = self._node_count - nodes_before
        added_edges = self._edge_count - edges_before
        print(f"  Español COMPLETO: {es_lemmas_found} lemas ES, "
              f"+{added_nodes} nodos, +{added_edges} aristas")
        return added_nodes, added_edges

    def inject_crosslingual(self) -> Tuple[int, int]:
        """Ya integrado en inject_spanish (cada lema ES es equivalence al synset EN).

        Este método ahora solo crea relaciones semánticas entre palabras españolas
        (sinónimos entre lemas que comparten synset).
        """
        from nltk.corpus import wordnet as wn

        if self._checkpoint.get("crosslingual_done", False):
            print("  Cross-lingual: YA completado")
            return 0, 0

        print(f"\n{'[DRY] ' if self.dry_run else ''}Cross-lingual EN↔ES: "
              "sinónimos entre lemas ES del mismo synset...")

        all_synsets = list(wn.all_synsets())
        total = len(all_synsets)

        nodes_before = self._node_count
        edges_before = self._edge_count
        processed = 0

        for i, syn in enumerate(all_synsets):
            try:
                spa_lemmas = syn.lemmas(lang="spa")
            except Exception:
                continue

            if len(spa_lemmas) >= 2:
                lemma_ids = [self._make_node_id(PREFIX_ES, l.name().lower())
                           for l in spa_lemmas]
                # Crear sinónimos entre todos los lemas ES del mismo synset
                for a in range(len(lemma_ids)):
                    for b in range(a + 1, len(lemma_ids)):
                        self._add_edge(lemma_ids[a], lemma_ids[b],
                                      "synonym", ES_CONFIDENCE)
                processed += 1

            if i > 0 and i % 2000 == 0:
                self._flush_all()
                pct = i * 100 / total
                print(f"  XL: {i}/{total} ({pct:.1f}%) | "
                      f"+{self._edge_count - edges_before} aristas")

        self._flush_all()
        self._checkpoint["crosslingual_done"] = True
        self._save_checkpoint()

        added_nodes = self._node_count - nodes_before
        added_edges = self._edge_count - edges_before
        print(f"  Cross-lingual COMPLETO: +{added_nodes} nodos, +{added_edges} aristas")
        return added_nodes, added_edges

    # ── Stats ─────────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Estadísticas de nodos wordnet en la DB."""
        if not DB_PATH.exists():
            return {"error": "DB no existe"}

        conn = get_conn(DB_PATH, timeout=5)
        total_nodes = conn.execute(
            "SELECT COUNT(*) FROM knowledge_nodes WHERE source='wordnet'"
        ).fetchone()[0]
        total_edges = conn.execute(
            "SELECT COUNT(*) FROM knowledge_edges WHERE relation_type IN "
            "('synonym','antonym','hypernym','hyponym','meronym','equivalent','contains_lemma')"
        ).fetchone()[0]
        by_category = {}
        for row in conn.execute(
            "SELECT category, COUNT(*) FROM knowledge_nodes WHERE source='wordnet' "
            "GROUP BY category"
        ).fetchall():
            by_category[row[0]] = row[1]
        by_relation = {}
        for row in conn.execute(
            "SELECT relation_type, COUNT(*) FROM knowledge_edges WHERE relation_type IN "
            "('synonym','antonym','hypernym','hyponym','meronym','equivalent','contains_lemma') "
            "GROUP BY relation_type ORDER BY COUNT(*) DESC"
        ).fetchall():
            by_relation[row[0]] = row[1]

        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "by_category": by_category,
            "by_relation": by_relation,
        }

    # ── Orquestador ────────────────────────────────────────────────────────────

    def run_all(self) -> Dict[str, Any]:
        """Ejecuta inyección completa: EN → ES → cross-lingual.

        Retorna dict con conteos totales.
        """
        t0 = time.time()

        if not self._ensure_nltk():
            return {"error": "NLTK no disponible"}

        self._ensure_db()
        self._checkpoint = self._load_checkpoint()

        print("=" * 60)
        print("EIDOS WordNet Injector — Fase 1")
        print(f"  DB: {DB_PATH}")
        print(f"  Dry run: {self.dry_run}")
        print(f"  Checkpoint: {self._checkpoint}")
        print("=" * 60)

        en_nodes, en_edges = self.inject_english()
        es_nodes, es_edges = self.inject_spanish()
        xl_nodes, xl_edges = self.inject_crosslingual()

        self._flush_all()

        if self.conn:
            self.conn.close()

        elapsed = time.time() - t0
        total_nodes = en_nodes + es_nodes + xl_nodes
        total_edges = en_edges + es_edges + xl_edges

        print(f"\n{'=' * 60}")
        print(f"WordNet INYECCIÓN COMPLETA")
        print(f"  Nodos totales: {total_nodes}")
        print(f"  Aristas totales: {total_edges}")
        print(f"  Tiempo: {elapsed:.0f}s")
        print(f"{'=' * 60}")

        return {
            "nodes": total_nodes,
            "edges": total_edges,
            "en_nodes": en_nodes,
            "en_edges": en_edges,
            "es_nodes": es_nodes,
            "es_edges": es_edges,
            "xl_nodes": xl_nodes,
            "xl_edges": xl_edges,
            "elapsed_s": elapsed,
            "dry_run": self.dry_run,
        }


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="WordNet Injector — Enriquece el grafo EIDOS con léxico EN+ES")
    parser.add_argument("--dry", action="store_true",
                       help="Solo contar synsets, sin insertar")
    parser.add_argument("--stats", action="store_true",
                       help="Mostrar estadísticas de lo ya inyectado")
    parser.add_argument("--reset", action="store_true",
                       help="Borrar checkpoint y empezar de cero")
    args = parser.parse_args()

    if args.reset and CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
        print("Checkpoint borrado.")

    if args.stats:
        inj = WordNetInjector(dry_run=True)
        stats = inj.get_stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        sys.exit(0)

    inj = WordNetInjector(dry_run=args.dry)
    result = inj.run_all()

    if result.get("error"):
        print(f"ERROR: {result['error']}")
        sys.exit(1)
