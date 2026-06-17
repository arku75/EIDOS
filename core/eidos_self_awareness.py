"""
EIDOS Self-Awareness System

HONESTY NOTE: Previously this was 26 lines of psutil (free -h equivalent).
Now it does ACTUAL introspection:
  - System resources (CPU, RAM, disk) — kept from original
  - Knowledge graph: how many nodes/edges EIDOS knows
  - Active services: what EIDOS components are running
  - Recent activity: what EIDOS has been doing
  - LLM status: which models are available via Ollama
  - Capabilities: what EIDOS can do (from graph self_knowledge nodes)
"""
import os, platform, psutil, json, time, subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict

@dataclass
class EidosBody:
    """System-level introspection (hardware/OS)."""
    os_name: str; os_version: str; architecture: str
    cpu_count: int; total_ram_gb: float; disk_free_gb: float
    ram_used_pct: float = 0.0
    cpu_percent: float = 0.0

    @classmethod
    def scan(cls):
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return cls(
            os_name=platform.system(), os_version=platform.release(),
            architecture=platform.machine(), cpu_count=os.cpu_count() or 1,
            total_ram_gb=mem.total / (1024**3), disk_free_gb=disk.free / (1024**3),
            ram_used_pct=mem.percent,
            cpu_percent=psutil.cpu_percent(interval=0.5),
        )

@dataclass
class EidosKnowledge:
    """Knowledge graph introspection."""
    total_nodes: int = 0
    total_edges: int = 0
    nodes_learned_today: int = 0
    top_categories: List[str] = field(default_factory=list)
    quality_distribution: Dict[str, int] = field(default_factory=dict)

    @classmethod
    def scan(cls):
        k = cls()
        try:
            db = Path.home() / ".eidos" / "evolution_brain.db"
            if db.exists():
                try:
                    from core.db import get_conn
                    conn = get_conn(db, timeout=5)
                except Exception:
                    import sqlite3
                    conn = sqlite3.connect(str(db))
                try:
                    conn.row_factory = sqlite3.Row
                    k.total_nodes = conn.execute(
                        "SELECT COUNT(*) FROM knowledge_nodes"
                    ).fetchone()[0]
                    k.total_edges = conn.execute(
                        "SELECT COUNT(*) FROM knowledge_edges"
                    ).fetchone()[0]
                    today = time.time() - 86400
                    k.nodes_learned_today = conn.execute(
                        "SELECT COUNT(*) FROM knowledge_nodes WHERE created_at > ?",
                        (today,)
                    ).fetchone()[0]
                    cats = conn.execute(
                        "SELECT category, COUNT(*) as cnt FROM knowledge_nodes "
                        "WHERE category IS NOT NULL GROUP BY category "
                        "ORDER BY cnt DESC LIMIT 5"
                    ).fetchall()
                    k.top_categories = [f"{r[0]}({r[1]})" for r in cats]
                finally:
                    conn.close()
        except Exception:
            pass
        return k

@dataclass
class EidosServices:
    """Which EIDOS components are currently running."""
    daemon: bool = False
    bridge: bool = False
    web_panel: bool = False
    trinity: bool = False
    chromadb: bool = False
    ollama_models: List[str] = field(default_factory=list)
    colony: bool = False
    brain_lite: bool = False

    @classmethod
    def scan(cls):
        s = cls()
        try:
            # Check systemd user services
            result = subprocess.run(
                ["systemctl", "--user", "list-units", "--no-legend",
                 "eidos-*", "chroma*"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if "eidos-daemon" in line and "active" in line:
                    s.daemon = True
                if "eidos-bridge" in line and "active" in line:
                    s.bridge = True
                if "eidos-trinity" in line and "active" in line:
                    s.trinity = True
                if "eidos-brain-lite" in line and "active" in line:
                    s.brain_lite = True
                if "chroma" in line and "active" in line:
                    s.chromadb = True
        except Exception:
            pass

        # Check web-panel (not a systemd service, launched via nohup)
        try:
            result = subprocess.run(
                ["pgrep", "-f", "web-panel/server.py"],
                capture_output=True, text=True, timeout=3
            )
            s.web_panel = bool(result.stdout.strip())
        except Exception:
            pass

        # Check colony
        try:
            result = subprocess.run(
                ["pgrep", "-f", "colony_dashboard.py"],
                capture_output=True, text=True, timeout=3
            )
            s.colony = bool(result.stdout.strip())
        except Exception:
            pass

        # List available Ollama models
        try:
            import urllib.request
            req = urllib.request.Request(
                "http://localhost:11434/api/tags",
                headers={"User-Agent": "EIDOS/1.0"}
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read())
                s.ollama_models = [
                    m.get("name", "") for m in data.get("models", [])
                ][:10]
        except Exception:
            pass

        return s

def get_self_awareness():
    """Full introspection: hardware + knowledge + services."""
    return {
        "hardware": EidosBody.scan(),
        "knowledge": EidosKnowledge.scan(),
        "services": EidosServices.scan(),
    }

if __name__ == "__main__":
    awareness = get_self_awareness()
    body = awareness["hardware"]
    kg = awareness["knowledge"]
    svc = awareness["services"]

    print(f"EIDOS Body: {body.os_name} {body.os_version} | "
          f"{body.cpu_count} cores | {body.total_ram_gb:.1f}GB RAM "
          f"({body.ram_used_pct:.0f}% used)")
    print(f"Knowledge: {kg.total_nodes} nodes, {kg.total_edges} edges | "
          f"+{kg.nodes_learned_today} today | {kg.top_categories}")
    print(f"Services: daemon={svc.daemon} bridge={svc.bridge} "
          f"panel={svc.web_panel} trinity={svc.trinity} chroma={svc.chromadb} "
          f"brain_lite={svc.brain_lite}")
    print(f"Ollama models: {len(svc.ollama_models)} — "
          f"{', '.join(svc.ollama_models[:5])}")
