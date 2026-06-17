"""
core/colony_genealogy.py — Árbol genealógico de Colony

Muestra visualmente las relaciones entre personajes:
- Personajes originales (nacidos con EIDOS)
- Personajes nacidos de conexiones externas
- Hijos nacidos de reproducción entre personajes

Uso:
    from core.colony_genealogy import get_genealogy_tree
    tree = get_genealogy_tree()
    print(tree.render())
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from core.db import get_conn
from typing import Any, Dict, List, Optional


LIFECYCLE_DB = Path.home() / ".eidos" / "lifecycle.db"

ORIGINAL_CHARACTERS = {
    "colony_coder":    ("💻", "Coder"),
    "colony_analyst":  ("🔍", "Analyst"),
    "colony_vision":   ("👁️", "Vision"),
    "colony_operator": ("⚙️", "Operator"),
    "colony_general":  ("🤖", "General"),
    "colony_ser":      ("👑", "SER"),
    "colony_lumen":    ("💡", "Lumen"),
}


class GenealogyTree:

    def get_tree(self) -> Dict[str, Any]:
        born = self._get_born_characters()
        rels  = self._get_genealogy()
        return {
            "original": [
                {"name": k, "emoji": v[0], "display": v[1]}
                for k, v in ORIGINAL_CHARACTERS.items()
            ],
            "born": born,
            "genealogy": rels,
        }

    def render(self, last_minutes: int = 0) -> str:
        """Devuelve representación visual del árbol en terminal."""
        lines = []
        lines.append("╔══════════════════════════════════════════════╗")
        lines.append("║          ÁRBOL GENEALÓGICO DE COLONY          ║")
        lines.append("╠══════════════════════════════════════════════╣")

        # Personajes originales
        lines.append("║  🌳 PERSONAJES ORIGINALES                     ║")
        for char_id, (emoji, display) in ORIGINAL_CHARACTERS.items():
            lines.append(f"║    {emoji}  {display:<40}║")

        # Personajes nacidos
        born = self._get_born_characters()
        if born:
            lines.append("╠══════════════════════════════════════════════╣")
            lines.append("║  🌱 NACIDOS DE CONEXIONES                     ║")
            for c in born:
                pct = c.get("absorption_pct", 0.0)
                status_icon = "👑" if c["status"] == "sovereign" else ("🌱" if pct < 0.5 else "🌿")
                bar_filled = int(pct * 10)
                bar = "█" * bar_filled + "░" * (10 - bar_filled)
                name_short = c["name"].replace("colony_", "").title()[:12]
                conn = c.get("connection_type", "?")[:8]
                lines.append(f"║    {c['emoji']} {name_short:<12} [{bar}] {pct*100:3.0f}% {conn:<8} {status_icon}║")
                if c.get("parent1"):
                    p1 = c["parent1"].replace("colony_", "").title()
                    p2 = (c.get("parent2") or "").replace("colony_", "").title()
                    lines.append(f"║      └─ hijos de: {p1} × {p2:<20}║")

        # Genealogy relations
        rels = self._get_genealogy()
        if rels:
            lines.append("╠══════════════════════════════════════════════╣")
            lines.append("║  🧬 REPRODUCCIONES                            ║")
            for r in rels:
                p1 = r["parent1"].replace("colony_", "").title()[:10]
                p2 = (r.get("parent2") or "?").replace("colony_", "").title()[:10]
                child = r["child"].replace("colony_", "").title()[:10]
                date_str = time.strftime("%d/%m", time.localtime(r["birth_date"]))
                lines.append(f"║    {p1} × {p2} → {child} ({date_str}){'':>10}║")

        lines.append("╚══════════════════════════════════════════════╝")
        return "\n".join(lines)

    def get_lineage(self, character_name: str) -> Dict[str, Any]:
        """Devuelve ancestros y descendientes de un personaje."""
        if not LIFECYCLE_DB.exists():
            return {"character": character_name, "ancestors": [], "descendants": []}
        db = get_conn(LIFECYCLE_DB, timeout=5)

        ancestors: List[Dict] = []
        char = dict(db.execute(
            "SELECT * FROM characters WHERE name=?", (character_name,)
        ).fetchone() or {})
        if char.get("parent1"):
            p1 = db.execute("SELECT * FROM characters WHERE name=?", (char["parent1"],)).fetchone()
            if p1:
                ancestors.append(dict(p1))
        if char.get("parent2"):
            p2 = db.execute("SELECT * FROM characters WHERE name=?", (char["parent2"],)).fetchone()
            if p2:
                ancestors.append(dict(p2))

        descendants = [
            dict(r) for r in db.execute(
                "SELECT child FROM genealogy WHERE parent1=? OR parent2=?",
                (character_name, character_name),
            ).fetchall()
        ]
        return {
            "character": character_name,
            "info": char,
            "ancestors": ancestors,
            "descendants": descendants,
        }

    def _get_born_characters(self) -> List[Dict[str, Any]]:
        if not LIFECYCLE_DB.exists():
            return []
        try:
            db = get_conn(LIFECYCLE_DB, timeout=5)
            rows = db.execute("SELECT * FROM characters ORDER BY birth_date").fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _get_genealogy(self) -> List[Dict[str, Any]]:
        if not LIFECYCLE_DB.exists():
            return []
        try:
            db = get_conn(LIFECYCLE_DB, timeout=5)
            rows = db.execute("SELECT * FROM genealogy ORDER BY birth_date").fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []


_tree_instance: Optional[GenealogyTree] = None


def get_genealogy_tree() -> GenealogyTree:
    global _tree_instance
    if _tree_instance is None:
        _tree_instance = GenealogyTree()
    return _tree_instance
