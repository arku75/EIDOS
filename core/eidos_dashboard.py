#!/usr/bin/env python3
"""
core/eidos_dashboard.py — EIDOS Neural Brain Dashboard [S125+]
================================================================
CLI dashboard en tiempo real que muestra el grafo de conocimiento vivo,
actividad cerebral, estado VAD, metas activas e indicadores de salud.

Comandos:
  python3 core/eidos_dashboard.py live          — Dashboard en vivo (refresh 2s)
  python3 core/eidos_dashboard.py snapshot      — Instantánea única
  python3 core/eidos_dashboard.py top-concepts  — Top conceptos más conectados
  python3 core/eidos_dashboard.py health        — Solo indicadores de salud

Fuentes de datos:
  - ~/.eidos/evolution_brain.db  (grafo de conocimiento)
  - ~/.eidos/alive_stream.log    (stream de conciencia)
  - ~/.eidos/affect_state.json   (estado VAD)
  - Sistema (CPU, RAM, disco)
"""
from __future__ import annotations

import json
import math
import os
import random
import signal
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.db import get_conn

# ── ANSI / Terminal ──────────────────────────────────────────────────────────

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_REVERSE = "\033[7m"
ANSI_CLEAR = "\033[2J\033[H"
ANSI_HIDE_CURSOR = "\033[?25l"
ANSI_SHOW_CURSOR = "\033[?25h"

# ── Colores por dominio (de eidos_skill_tree.py) ────────────────────────────

DOMAIN_COLORS_ANSI: Dict[str, str] = {
    "security":       "\033[31m",
    "network":        "\033[34m",
    "system":         "\033[32m",
    "programming":    "\033[33m",
    "web":            "\033[35m",
    "database":       "\033[36m",
    "ai":             "\033[95m",
    "tool":           "\033[90m",
    "concept":        "\033[37m",
    "language":       "\033[91m",
    "kali":           "\033[96m",
    "eidos":          "\033[93m",
    "general":        "\033[37m",
    "research":       "\033[92m",
    "skill_general":  "\033[93m",
    "inferred":       "\033[37m",
}

CATEGORY_TO_DOMAIN: Dict[str, str] = {
    "security_tool": "security", "vulnerability": "security", "exploit": "security",
    "network_scan": "network", "network": "network", "protocol": "network",
    "system_command": "system", "system": "system", "os": "system", "kernel": "system",
    "programming_language": "programming", "library": "programming", "framework": "programming",
    "web": "web", "browser": "web", "http": "web",
    "database": "database", "sql": "database",
    "machine_learning": "ai", "deep_learning": "ai", "llm": "ai",
    "tool": "tool", "cli_tool": "tool", "gui_tool": "tool",
    "kali_tool": "kali", "kali": "kali",
    "language": "language",
    "skill:general": "skill_general", "skill_general": "skill_general",
    "eidos": "eidos", "researched": "research", "inferred": "inferred",
    "eidos_self": "eidos", "eidos_module": "eidos",
    "kali_training": "kali", "kali_navigation": "kali", "kali_recon": "kali",
    "code_structure": "programming", "ai_architecture": "ai", "ai_tool": "ai",
    "ser_deep_patterns": "general", "user_profile": "general",
    "lifecycle": "system", "hardware": "system",
}

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
STREAM_LOG = Path.home() / ".eidos" / "alive_stream.log"
AFFECT_FILE = Path.home() / ".eidos" / "affect_state.json"


def _cat_to_domain(category: str) -> str:
    """Map a knowledge_nodes.category to a display domain color key."""
    cat_lower = (category or "").lower().strip()
    if cat_lower in CATEGORY_TO_DOMAIN:
        return CATEGORY_TO_DOMAIN[cat_lower]
    for key in sorted(CATEGORY_TO_DOMAIN, key=len, reverse=True):
        if cat_lower.startswith(key):
            return CATEGORY_TO_DOMAIN[key]
    return "general"


def _domain_color(domain: str) -> str:
    return DOMAIN_COLORS_ANSI.get(domain, DOMAIN_COLORS_ANSI["general"])


# ── Data fetchers ────────────────────────────────────────────────────────────

def _db_connect(readonly: bool = True):
    """Open a connection to evolution_brain.db via get_conn (proper PRAGMAs)."""
    conn = get_conn(BRAIN_DB, timeout=10, read_only=readonly)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_graph_stats() -> Dict[str, Any]:
    """Fetch total nodes, edges, density from the knowledge graph."""
    try:
        conn = _db_connect()
        total_nodes = conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
        total_edges = conn.execute("SELECT COUNT(*) FROM knowledge_edges").fetchone()[0]
        cm_nodes = conn.execute("SELECT COUNT(*) FROM concept_mastery").fetchone()[0]
        density = round(total_edges / max(total_nodes, 1), 2)
        conn.close()
        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "mastery_tracked": cm_nodes,
            "density": density,
        }
    except Exception as e:
        return {"total_nodes": 0, "total_edges": 0, "mastery_tracked": 0, "density": 0, "error": str(e)}


def fetch_top_connected(n: int = 5) -> List[Dict[str, Any]]:
    """Fetch top N most connected concepts by edge count."""
    try:
        conn = _db_connect()
        rows = conn.execute("""
            SELECT kn.concept, COUNT(ke.id) as edge_count, kn.category
            FROM knowledge_edges ke
            JOIN knowledge_nodes kn ON (ke.from_node = kn.id OR ke.to_node = kn.id)
            GROUP BY kn.id
            ORDER BY edge_count DESC
            LIMIT ?
        """, [n]).fetchall()
        conn.close()
        return [
            {
                "concept": r["concept"][:60] if r["concept"] else "?",
                "edges": r["edge_count"],
                "domain": _cat_to_domain(r["category"] or ""),
            }
            for r in rows
        ]
    except Exception as e:
        return [{"concept": f"DB error: {e}", "edges": 0, "domain": "general"}]


def fetch_top_mastery(n: int = 10) -> List[Dict[str, Any]]:
    """Fetch top N concepts by overall mastery score."""
    try:
        conn = _db_connect()
        rows = conn.execute("""
            SELECT cm.concept, cm.declarative_score, cm.procedural_score,
                   cm.applicational_score, cm.metacognitive_score,
                   cm.cross_domain_edges, cm.executions_success, kn.category
            FROM concept_mastery cm
            JOIN knowledge_nodes kn ON cm.node_id = kn.id
            ORDER BY (cm.declarative_score + cm.procedural_score
                      + cm.applicational_score + cm.metacognitive_score) DESC
            LIMIT ?
        """, [n]).fetchall()
        conn.close()
        return [
            {
                "concept": r["concept"][:60] if r["concept"] else "?",
                "overall": round((r["declarative_score"] + r["procedural_score"]
                                  + r["applicational_score"] + r["metacognitive_score"]) / 4.0, 3),
                "domain": _cat_to_domain(r["category"] or ""),
                "cross": r["cross_domain_edges"] or 0,
                "exec": r["executions_success"] or 0,
            }
            for r in rows
        ]
    except Exception as e:
        return [{"concept": f"DB error: {e}", "overall": 0, "domain": "general", "cross": 0, "exec": 0}]


def fetch_domain_distribution() -> Dict[str, int]:
    """Count concepts per domain (from concept_mastery)."""
    try:
        conn = _db_connect()
        rows = conn.execute("""
            SELECT kn.category, COUNT(*) as cnt
            FROM concept_mastery cm
            JOIN knowledge_nodes kn ON cm.node_id = kn.id
            GROUP BY kn.category
        """).fetchall()
        conn.close()
        dist: Dict[str, int] = defaultdict(int)
        for r in rows:
            domain = _cat_to_domain(r["category"] or "")
            dist[domain] += r["cnt"]
        return dict(sorted(dist.items(), key=lambda x: -x[1]))
    except Exception:
        return {}


def fetch_active_goals() -> List[Dict[str, Any]]:
    """Fetch active/pending goals."""
    try:
        conn = _db_connect()
        rows = conn.execute("""
            SELECT id, description, priority, status, created_at
            FROM goals
            WHERE status IN ('active', 'pending')
            ORDER BY priority DESC
            LIMIT 10
        """).fetchall()
        conn.close()
        return [
            {
                "id": r["id"][:20],
                "description": (r["description"] or "")[:80],
                "priority": r["priority"],
                "status": r["status"],
            }
            for r in rows
        ]
    except Exception:
        return []


def fetch_affect_state() -> Dict[str, Any]:
    """Read VAD affect state from JSON file."""
    try:
        if AFFECT_FILE.exists():
            data = json.loads(AFFECT_FILE.read_text())
            return {
                "valence": data.get("valence", 0.5),
                "arousal": data.get("arousal", 0.5),
                "dominance": data.get("dominance", 0.5),
                "mood": data.get("mood", "neutral"),
                "goals_completed": data.get("goals_completed", 0),
                "goals_failed": data.get("goals_failed", 0),
                "skills_learned": data.get("skills_learned", 0),
                "novelty_score": data.get("novelty_score", 0.5),
            }
    except Exception:
        pass
    return {"valence": 0.5, "arousal": 0.5, "dominance": 0.5, "mood": "?",
            "goals_completed": 0, "goals_failed": 0, "skills_learned": 0, "novelty_score": 0.5}


def fetch_recent_thoughts(n: int = 15) -> List[Dict[str, Any]]:
    """Read the last N meaningful thoughts from alive_stream.log (JSONL)."""
    thoughts: List[Dict[str, Any]] = []
    try:
        if not STREAM_LOG.exists():
            return thoughts
        with open(STREAM_LOG, "r") as f:
            f.seek(0, 2)
            file_size = f.tell()
            chunk_size = min(file_size, 65536)
            f.seek(max(0, file_size - chunk_size))
            raw = f.read()
        # Split into lines, parse JSON, collect interesting kinds
        interesting = {"introspect", "synthesize", "decision", "meta_cognition",
                       "learn", "research", "filesystem", "recall", "perceive"}
        for line in reversed(raw.strip().split("\n")):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                kind = d.get("kind", "")
                if kind in interesting:
                    thoughts.append({
                        "ts": d.get("ts", "")[:19],
                        "cycle": d.get("cycle", 0),
                        "kind": kind,
                        "message": (d.get("message", ""))[:120],
                    })
                if len(thoughts) >= n:
                    break
            except json.JSONDecodeError:
                continue
    except Exception:
        pass
    return thoughts


def fetch_health() -> Dict[str, Any]:
    """Collect system health indicators."""
    health = {
        "cpu_load": 0.0,
        "cpu_cores": os.cpu_count() or 1,
        "ram_used_pct": 0.0,
        "ram_total_gb": 0.0,
        "ram_used_gb": 0.0,
        "disk_used_pct": 0.0,
        "disk_total_gb": 0.0,
        "disk_free_gb": 0.0,
        "uptime_hours": 0.0,
    }
    try:
        health["cpu_load"] = round(os.getloadavg()[0], 2)
    except Exception:
        pass
    try:
        with open("/proc/meminfo", "r") as f:
            mem = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    mem[parts[0].strip()] = parts[1].strip().split()[0]
            total_kb = int(mem.get("MemTotal", 0))
            avail_kb = int(mem.get("MemAvailable", 0))
            used_kb = total_kb - avail_kb
            health["ram_total_gb"] = round(total_kb / 1024 / 1024, 1)
            health["ram_used_gb"] = round(used_kb / 1024 / 1024, 1)
            health["ram_used_pct"] = round(used_kb / max(total_kb, 1) * 100, 1)
    except Exception:
        pass
    try:
        stat = os.statvfs(Path.home())
        total_bytes = stat.f_frsize * stat.f_blocks
        free_bytes = stat.f_frsize * stat.f_bavail
        used_bytes = total_bytes - free_bytes
        health["disk_total_gb"] = round(total_bytes / 1024 / 1024 / 1024, 1)
        health["disk_free_gb"] = round(free_bytes / 1024 / 1024 / 1024, 1)
        health["disk_used_pct"] = round(used_bytes / max(total_bytes, 1) * 100, 1)
    except Exception:
        pass
    try:
        with open("/proc/uptime", "r") as f:
            health["uptime_hours"] = round(float(f.readline().split()[0]) / 3600, 1)
    except Exception:
        pass
    return health


def fetch_domain_nodes(n_per_domain: int = 6) -> List[Dict[str, Any]]:
    """Fetch representative concept nodes per domain for visualization."""
    try:
        conn = _db_connect()
        rows = conn.execute("""
            SELECT cm.concept, cm.declarative_score, cm.procedural_score,
                   cm.applicational_score, cm.metacognitive_score,
                   cm.cross_domain_edges, kn.category
            FROM concept_mastery cm
            JOIN knowledge_nodes kn ON cm.node_id = kn.id
            WHERE (cm.declarative_score + cm.procedural_score
                   + cm.applicational_score + cm.metacognitive_score) / 4.0 > 0.01
            ORDER BY (cm.declarative_score + cm.procedural_score
                      + cm.applicational_score + cm.metacognitive_score) DESC
            LIMIT 120
        """).fetchall()
        conn.close()
        nodes = []
        for r in rows:
            overall = (r["declarative_score"] + r["procedural_score"]
                       + r["applicational_score"] + r["metacognitive_score"]) / 4.0
            domain = _cat_to_domain(r["category"] or "")
            nodes.append({
                "concept": (r["concept"] or "?")[:40],
                "overall": overall,
                "domain": domain,
                "cross": r["cross_domain_edges"] or 0,
            })
        return nodes
    except Exception:
        return []


# ── Force-directed layout ────────────────────────────────────────────────────

def _compute_layout(nodes: List[Dict[str, Any]],
                    width: int = 70, height: int = 18) -> Dict[int, Tuple[float, float]]:
    """Simple force-directed 2D layout for concept nodes.

    Nodes attract to their domain center, repel from each other.
    Returns dict of node-index -> (x, y) floating-point coordinates.
    """
    if not nodes:
        return {}

    # Group node indices by domain
    domains = defaultdict(list)
    for i, n in enumerate(nodes):
        domains[n["domain"]].append(i)

    # Assign one center per domain on a circle
    domain_list = sorted(domains.keys())
    n_domains = len(domain_list)
    domain_centers: Dict[str, Tuple[float, float]] = {}
    for di, d in enumerate(domain_list):
        angle = 2 * math.pi * di / max(n_domains, 1)
        cx = width / 2 + (width / 3) * math.cos(angle)
        cy = height / 2 + (height / 3.5) * math.sin(angle)
        domain_centers[d] = (cx, cy)

    # Seed positions near domain centers with small random offset
    rng = random.Random(42)
    positions: Dict[int, List[float]] = {}
    for i, n in enumerate(nodes):
        dc = domain_centers.get(n["domain"], (width / 2, height / 2))
        positions[i] = [dc[0] + rng.uniform(-3, 3), dc[1] + rng.uniform(-2, 2)]

    # Run force simulation
    for _step in range(30):
        forces: Dict[int, List[float]] = {i: [0.0, 0.0] for i in positions}
        indices = list(positions.keys())

        # Repulsion between all pairs (simplified: within cutoff)
        for a_idx in range(len(indices)):
            i = indices[a_idx]
            for b_idx in range(a_idx + 1, len(indices)):
                j = indices[b_idx]
                dx = positions[i][0] - positions[j][0]
                dy = positions[i][1] - positions[j][1]
                dist = math.sqrt(dx * dx + dy * dy) + 0.01
                if dist < 6:
                    force = 2.5 / (dist * dist)
                    fx = (dx / dist) * force
                    fy = (dy / dist) * force
                    forces[i][0] += fx
                    forces[i][1] += fy
                    forces[j][0] -= fx
                    forces[j][1] -= fy

        # Attraction to domain center
        for i, n in enumerate(nodes):
            dc = domain_centers.get(n["domain"], (width / 2, height / 2))
            forces[i][0] += (dc[0] - positions[i][0]) * 0.1
            forces[i][1] += (dc[1] - positions[i][1]) * 0.1

        # Apply forces with damping
        for i in positions:
            positions[i][0] += forces[i][0] * 0.3
            positions[i][1] += forces[i][1] * 0.3
            positions[i][0] = max(0.5, min(width - 0.5, positions[i][0]))
            positions[i][1] = max(0.5, min(height - 0.5, positions[i][1]))

    return {i: (positions[i][0], positions[i][1]) for i in positions}


# ── Bar helpers ──────────────────────────────────────────────────────────────

def _bar(value: float, width: int = 10, filled_char: str = "█",
         empty_char: str = "░") -> str:
    """Render a proportional progress bar."""
    filled = max(0, min(width, int(round(value * width))))
    return f"{filled_char * filled}{empty_char * (width - filled)}"


def _health_color(value: float, warn: float = 70, crit: float = 90) -> str:
    """Return ANSI color for a health percentage."""
    if value >= crit:
        return "\033[31m"
    elif value >= warn:
        return "\033[33m"
    return "\033[32m"


def _mastery_bar(score: float, width: int = 8) -> str:
    filled = int(round(score * width))
    return f"{'█' * filled}{'░' * (width - filled)}"


def _level_indicator(score: float) -> str:
    """Mastery level as star symbols."""
    if score >= 0.90:
        return "★★★"
    elif score >= 0.75:
        return "★★☆"
    elif score >= 0.55:
        return "★☆☆"
    elif score >= 0.33:
        return "•☆☆"
    return "···"


def _trim(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len - 1] + "…"


# ── Renderers ────────────────────────────────────────────────────────────────

def render_brain_canvas(nodes: List[Dict[str, Any]],
                        width: int = 70, height: int = 18) -> str:
    """Render a force-directed brain activity canvas in ANSI.

    Each concept = a colored dot. Brightness/size = mastery, color = domain.
    """
    if not nodes:
        return "  (no concept data)\n"

    layout = _compute_layout(nodes, width, height)

    # Build a character grid: (row, col) -> list of (concept, domain, overall)
    grid: Dict[Tuple[int, int], List[Tuple[str, str, float]]] = defaultdict(list)
    for i, (x, y) in enumerate(layout.values()):
        if i >= len(nodes):
            break
        row = int(y)
        col = int(x)
        grid[(row, col)].append((nodes[i]["concept"], nodes[i]["domain"],
                                  nodes[i]["overall"]))

    lines = []
    for row in range(height):
        line_chars = []
        for col in range(width):
            cell = grid.get((row, col), [])
            if cell:
                best = max(cell, key=lambda c: c[2])
                color = _domain_color(best[1])
                ov = best[2]
                if ov >= 0.70:
                    symbol = f"{ANSI_BOLD}●{ANSI_RESET}"
                elif ov >= 0.45:
                    symbol = "◉"
                elif ov >= 0.20:
                    symbol = "○"
                else:
                    symbol = "·"
                line_chars.append(f"{color}{symbol}{ANSI_RESET}")
            else:
                line_chars.append(" ")
        lines.append("".join(line_chars))
    return "\n".join(lines)


def render_snapshot() -> str:
    """Render a complete one-shot dashboard snapshot."""
    stats = fetch_graph_stats()
    top_conn = fetch_top_connected(5)
    thoughts = fetch_recent_thoughts(12)
    affect = fetch_affect_state()
    goals = fetch_active_goals()
    health = fetch_health()
    domain_dist = fetch_domain_distribution()
    top_mastery = fetch_top_mastery(8)
    nodes = fetch_domain_nodes(6)

    lines = []
    header_w = 72
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Header ──
    lines.append(
        f"{ANSI_BOLD}{ANSI_REVERSE}  EIDOS NEURAL BRAIN DASHBOARD  "
        f"{' ' * (header_w - 32)}{now}  {ANSI_RESET}"
    )
    lines.append(f"{ANSI_DIM}{'─' * header_w}{ANSI_RESET}")
    lines.append("")

    # ── Graph State ──
    lines.append(f"{ANSI_BOLD}  GRAPH STATE{ANSI_RESET}")
    lines.append(
        f"    Nodes: {stats['total_nodes']:,}  |  Edges: {stats['total_edges']:,}  "
        f"|  Density: {stats['density']}  |  Tracked: {stats['mastery_tracked']:,}"
    )
    lines.append("")

    # ── VAD ──
    v_pct = int(affect["valence"] * 100)
    a_pct = int(affect["arousal"] * 100)
    d_pct = int(affect["dominance"] * 100)
    lines.append(
        f"{ANSI_BOLD}  VAD AFFECT STATE   {ANSI_RESET}"
        f"Mood: {affect['mood'][:20]}  |  Novelty: {affect['novelty_score']:.2f}"
    )
    lines.append(
        f"    Valence   [{_bar(affect['valence'], 22)}"
        f"{ANSI_DIM}] {v_pct:>3}%{ANSI_RESET}"
    )
    lines.append(
        f"    Arousal   [{_bar(affect['arousal'], 22)}"
        f"{ANSI_DIM}] {a_pct:>3}%{ANSI_RESET}"
    )
    lines.append(
        f"    Dominance [{_bar(affect['dominance'], 22)}"
        f"{ANSI_DIM}] {d_pct:>3}%{ANSI_RESET}"
    )
    lines.append(
        f"    Goals: {affect['goals_completed']} completed  |  "
        f"{affect['goals_failed']} failed  |  "
        f"Skills: {affect['skills_learned']}"
    )
    lines.append("")

    # ── Top Connected ──
    lines.append(f"{ANSI_BOLD}  TOP 5 MOST CONNECTED CONCEPTS{ANSI_RESET}")
    for i, tc in enumerate(top_conn):
        color = _domain_color(tc["domain"])
        lines.append(
            f"    {i + 1}. {color}{_trim(tc['concept'], 48)}{ANSI_RESET}  "
            f"[{tc['domain'][:12]}]  {ANSI_BOLD}{tc['edges']:,} edges{ANSI_RESET}"
        )
    lines.append("")

    # ── Top Mastery ──
    lines.append(f"{ANSI_BOLD}  TOP MASTERY{ANSI_RESET}")
    for i, tm in enumerate(top_mastery[:6]):
        color = _domain_color(tm["domain"])
        bar = _mastery_bar(tm["overall"], 10)
        lines.append(
            f"    {color}{_level_indicator(tm['overall'])} "
            f"{_trim(tm['concept'], 35)}{ANSI_RESET} "
            f"{ANSI_DIM}{bar}{ANSI_RESET} {tm['overall']:.3f}"
        )
    lines.append("")

    # ── Domain Distribution ──
    max_dom_cnt = max(domain_dist.values()) if domain_dist else 1
    lines.append(f"{ANSI_BOLD}  DOMAIN DISTRIBUTION{ANSI_RESET}")
    for domain, cnt in list(domain_dist.items())[:12]:
        color = _domain_color(domain)
        bar_w = max(1, int(cnt / max_dom_cnt * 16))
        lines.append(
            f"    {color}{domain[:15]:>15s}{ANSI_RESET} │"
            f"{'█' * bar_w}{'░' * (16 - bar_w)}│ {cnt:>4d}"
        )
    lines.append("")

    # ── Health ──
    lines.append(f"{ANSI_BOLD}  SYSTEM HEALTH{ANSI_RESET}")
    cpu_pct = health["cpu_load"] / max(health["cpu_cores"], 1) * 100
    cpu_color = _health_color(cpu_pct, 50, 80)
    ram_color = _health_color(health["ram_used_pct"], 70, 90)
    disk_color = _health_color(health["disk_used_pct"], 70, 90)
    lines.append(
        f"    CPU:  {cpu_color}Load {health['cpu_load']:.2f} "
        f"({health['cpu_cores']} cores){ANSI_RESET}"
    )
    lines.append(
        f"    RAM:  {ram_color}{health['ram_used_gb']:.1f}/{health['ram_total_gb']:.1f} GB "
        f"({health['ram_used_pct']:.0f}%){ANSI_RESET}"
    )
    lines.append(
        f"    Disk: {disk_color}{health['disk_used_pct']:.0f}% used "
        f"({health['disk_free_gb']:.0f} GB free){ANSI_RESET}"
    )
    lines.append(f"    Up:   {health['uptime_hours']:.1f}h")
    lines.append("")

    # ── Active Goals ──
    lines.append(f"{ANSI_BOLD}  ACTIVE GOALS{ANSI_RESET}")
    if goals:
        for g in goals:
            status_color = "\033[33m" if g["status"] == "active" else "\033[37m"
            lines.append(
                f"    {status_color}[{g['status'][:7]:7s}]{ANSI_RESET} "
                f"{_trim(g['description'], 52)}  P={g['priority']:.2f}"
            )
    else:
        lines.append(f"    {ANSI_DIM}(no active goals){ANSI_RESET}")
    lines.append("")

    # ── Recent Thoughts ──
    lines.append(f"{ANSI_BOLD}  RECENT STREAM OF CONSCIOUSNESS{ANSI_RESET}")
    kind_icons = {
        "introspect": "[i]", "synthesize": "[S]", "decision": "[!]",
        "meta_cognition": "[M]", "learn": "[L]", "research": "[R]",
        "filesystem": "[F]", "recall": "[r]", "perceive": "[P]",
    }
    for t in thoughts[:8]:
        icon = kind_icons.get(t["kind"], "[?]")
        ts = t["ts"][-8:] if len(t["ts"]) >= 8 else t["ts"]
        lines.append(
            f"    {ANSI_DIM}{ts}{ANSI_RESET} {icon} [{t['kind'][:12]:12s}] "
            f"{_trim(t['message'], 55)}"
        )
    lines.append("")

    # ── Brain Activity Map ──
    lines.append(f"{ANSI_BOLD}  BRAIN ACTIVITY MAP (concept clusters){ANSI_RESET}")
    lines.append(f"  {ANSI_DIM}(●=master/expert  ◉=journeyman  ○=apprentice  ·=novice){ANSI_RESET}")
    canvas = render_brain_canvas(nodes, width=68, height=16)
    for cline in canvas.split("\n"):
        lines.append(f"  {cline}")
    lines.append("")

    # ── Domain Legend ──
    lines.append(f"{ANSI_BOLD}  DOMAIN LEGEND{ANSI_RESET}")
    legend_items = []
    domain_order = ["security", "network", "system", "programming", "web",
                    "database", "ai", "kali", "eidos", "tool", "concept", "research"]
    for d in domain_order:
        if domain_dist and domain_dist.get(d, 0) > 0:
            color = _domain_color(d)
            legend_items.append(f"{color}{d[:12]}{ANSI_RESET}")
    if legend_items:
        lines.append("    " + "  ".join(legend_items))
    lines.append("")

    lines.append(f"{ANSI_DIM}{'─' * header_w}{ANSI_RESET}")
    lines.append(f"{ANSI_DIM}  EIDOS Neural Dashboard — {now}{ANSI_RESET}")

    return "\n".join(lines)


def render_health() -> str:
    """Render health-only compact view."""
    health = fetch_health()
    stats = fetch_graph_stats()
    affect = fetch_affect_state()

    cpu_pct = health["cpu_load"] / max(health["cpu_cores"], 1) * 100
    cpu_color = _health_color(cpu_pct, 50, 80)
    ram_color = _health_color(health["ram_used_pct"], 70, 90)
    disk_color = _health_color(health["disk_used_pct"], 70, 90)

    lines = [
        f"{ANSI_BOLD}EIDOS HEALTH CHECK{ANSI_RESET}",
        f"{ANSI_DIM}{'─' * 50}{ANSI_RESET}",
        f"  CPU:     {cpu_color}Load {health['cpu_load']:.2f} "
        f"({health['cpu_cores']} cores){ANSI_RESET}",
        f"  RAM:     {ram_color}{health['ram_used_gb']:.1f}/{health['ram_total_gb']:.1f} GB "
        f"({health['ram_used_pct']:.0f}%){ANSI_RESET}",
        f"  Disk:    {disk_color}{health['disk_used_pct']:.0f}% used "
        f"({health['disk_free_gb']:.0f} GB free){ANSI_RESET}",
        f"  Uptime:  {health['uptime_hours']:.1f}h",
        f"  Graph:   {stats['total_nodes']:,} nodes, "
        f"{stats['total_edges']:,} edges, density={stats['density']}",
        f"  VAD:     V={affect['valence']:.2f} A={affect['arousal']:.2f} "
        f"D={affect['dominance']:.2f} mood={affect['mood']}",
        f"  Goals:   {affect['goals_completed']} done, "
        f"{affect['goals_failed']} failed",
        f"  Skills:  {affect['skills_learned']} learned",
    ]
    return "\n".join(lines)


def render_top_concepts(n: int = 20) -> str:
    """Render top connected and highest-mastery concepts."""
    top_conn = fetch_top_connected(n)
    top_mastery = fetch_top_mastery(n)

    lines = [
        f"{ANSI_BOLD}EIDOS TOP CONCEPTS{ANSI_RESET}",
        f"{ANSI_DIM}{'─' * 72}{ANSI_RESET}",
        "",
    ]

    lines.append(f"{ANSI_BOLD}  BY EDGE COUNT (hubs):{ANSI_RESET}")
    for i, tc in enumerate(top_conn):
        color = _domain_color(tc["domain"])
        lines.append(
            f"    {i + 1:2d}. {color}{_trim(tc['concept'], 48)}{ANSI_RESET}  "
            f"[{tc['domain'][:12]:12s}]  {tc['edges']:>6,} edges"
        )
    lines.append("")

    lines.append(f"{ANSI_BOLD}  BY MASTERY SCORE:{ANSI_RESET}")
    for i, tm in enumerate(top_mastery):
        color = _domain_color(tm["domain"])
        bar = _mastery_bar(tm["overall"], 12)
        lines.append(
            f"    {i + 1:2d}. {color}{_level_indicator(tm['overall'])} "
            f"{_trim(tm['concept'], 38)}{ANSI_RESET} "
            f"{ANSI_DIM}{bar}{ANSI_RESET} {tm['overall']:.3f}  "
            f"x-domain:{tm['cross']} exec:{tm['exec']}"
        )
    lines.append("")
    return "\n".join(lines)


# ── Live mode ────────────────────────────────────────────────────────────────

_live_running = True


def _on_sigint(signum, frame):
    global _live_running
    _live_running = False


def run_live(refresh_s: float = 2.0):
    """Run dashboard in live-refresh mode. Press Ctrl+C to exit."""
    global _live_running
    _live_running = True
    signal.signal(signal.SIGINT, _on_sigint)
    signal.signal(signal.SIGTERM, _on_sigint)

    sys.stdout.write(ANSI_HIDE_CURSOR)
    sys.stdout.flush()

    try:
        while _live_running:
            output = ANSI_CLEAR + render_snapshot()
            sys.stdout.write(output)
            sys.stdout.flush()
            # Sleep in small increments to stay responsive to SIGINT
            for _ in range(int(refresh_s * 10)):
                if not _live_running:
                    break
                time.sleep(0.1)
    finally:
        sys.stdout.write(ANSI_SHOW_CURSOR)
        sys.stdout.flush()


# ── CLI ──────────────────────────────────────────────────────────────────────

def _print_usage():
    print("EIDOS Neural Brain Dashboard")
    print("")
    print("Usage: python3 core/eidos_dashboard.py <command>")
    print("")
    print("Commands:")
    print("  live          Real-time dashboard (refresh every 2s, Ctrl+C to exit)")
    print("  snapshot      Single snapshot of the dashboard")
    print("  top-concepts  Top 20 most connected and highest-mastery concepts")
    print("  health        System health indicators only")


def main():
    if len(sys.argv) < 2:
        _print_usage()
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "live":
        refresh = 2.0
        if len(sys.argv) > 2:
            try:
                refresh = float(sys.argv[2])
            except ValueError:
                pass
        run_live(refresh)

    elif cmd == "snapshot":
        print(render_snapshot())

    elif cmd == "top-concepts":
        n = 20
        if len(sys.argv) > 2:
            try:
                n = int(sys.argv[2])
            except ValueError:
                pass
        print(render_top_concepts(n))

    elif cmd == "health":
        print(render_health())

    else:
        print(f"Unknown command: {cmd}")
        _print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
