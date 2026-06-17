"""
EIDOS Self-Curriculum — Plan de auto-estudio sin LLM.

Define topics que EIDOS debe aprender en background. Para cada tema una lista
de conceptos. Cada concepto se investiga vía `_self_research` multicanal
(code → man → --help → apt → DuckDuckGo → Wikipedia) y se inyecta al grafo.

Pensado para correr nightly o como daemon de baja prioridad. NO bloquea,
NO usa LLM. Solo el cerebro propio de EIDOS.

Goal del usuario: "dale los pasos para que aprenda Kali Linux, OS, terminal,
matemáticas, física, lenguaje, comunicación — sé tú mismo EIDOS, auto-estudia".
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Standalone path setup
_EIDOS_ROOT = Path(__file__).resolve().parent.parent
if str(_EIDOS_ROOT) not in sys.path:
    sys.path.insert(0, str(_EIDOS_ROOT))

log = logging.getLogger("eidos.curriculum")
PROGRESS_FILE = Path.home() / ".eidos" / "curriculum_progress.json"

# ─── TOPICS Y SUS CONCEPTOS ─────────────────────────────────────────────────

CURRICULUM: Dict[str, List[str]] = {
    # ── Kali Linux / pentesting (lo que SER usa profesionalmente) ──
    "kali_linux_core": [
        "kali-linux", "metasploit", "nmap", "nikto", "wireshark", "tcpdump",
        "aircrack-ng", "hydra", "john", "hashcat", "burpsuite", "sqlmap",
        "gobuster", "ffuf", "wpscan", "enum4linux", "smbclient", "rpcclient",
        "msfvenom", "msfconsole", "searchsploit", "exploit-db",
    ],
    "linux_shell_terminal": [
        "bash", "zsh", "tmux", "screen", "ssh", "scp", "rsync",
        "grep", "sed", "awk", "cut", "sort", "uniq", "wc", "tr",
        "xargs", "tee", "find", "locate", "which", "type",
        "ls", "cd", "pwd", "mkdir", "rmdir", "touch", "rm", "cp", "mv",
        "chmod", "chown", "umask", "ln", "stat",
        "ps", "top", "htop", "kill", "killall", "pgrep", "lsof",
        "systemctl", "journalctl", "service", "crontab",
        "ip", "ifconfig", "route", "netstat", "ss", "iptables", "ufw",
        "curl", "wget", "dig", "nslookup", "host", "ping", "traceroute",
        "tar", "zip", "unzip", "gzip", "gunzip", "bzip2",
    ],
    "linux_os_filesystem": [
        "filesystem hierarchy standard", "/etc", "/var", "/usr", "/opt",
        "/proc", "/sys", "/dev", "/tmp",
        "inode", "symlink", "hardlink", "mount", "umount", "lsblk",
        "fstab", "ext4", "btrfs", "xfs", "swap",
        "uid", "gid", "sudoers", "selinux", "apparmor",
    ],
    "python_essentials": [
        "python", "pip", "virtualenv", "venv", "conda",
        "asyncio", "threading", "multiprocessing", "subprocess",
        "requests", "urllib", "json", "yaml", "toml",
        "sqlite3", "psycopg2", "redis-py",
        "flask", "fastapi", "django",
        "numpy", "pandas", "matplotlib",
        "pytest", "unittest", "mypy", "ruff", "black",
    ],
    "math_basics": [
        "algebra", "geometry", "trigonometry", "calculus",
        "derivative", "integral", "limit",
        "linear-algebra", "matrix", "vector", "eigenvalue",
        "probability", "statistics", "bayes-theorem",
        "discrete-mathematics", "set-theory", "graph-theory",
        "boolean-algebra", "logic",
    ],
    "physics_basics": [
        "physics", "mechanics", "kinematics", "dynamics",
        "newton's-laws", "energy", "momentum", "torque",
        "thermodynamics", "entropy", "heat",
        "electromagnetism", "electric-field", "magnetic-field",
        "voltage", "current", "resistance", "capacitance",
        "optics", "wave", "frequency", "wavelength",
        "quantum-mechanics", "relativity",
    ],
    "language_communication": [
        "linguistics", "syntax", "semantics", "pragmatics",
        "phonology", "morphology", "etymology",
        "natural-language-processing", "tokenization", "parsing",
        "regex", "regular-expression",
        "ascii", "unicode", "utf-8", "encoding",
        "protocol", "http", "tcp", "udp", "websocket",
        "rest", "graphql", "rpc", "json-rpc",
    ],
    "ai_ml_basics": [
        "machine-learning", "neural-network", "deep-learning",
        "supervised-learning", "unsupervised-learning",
        "reinforcement-learning",
        "transformer", "attention-mechanism", "embedding",
        "gradient-descent", "backpropagation",
        "overfitting", "regularization", "dropout",
        "knowledge-graph", "rag", "vector-database",
    ],
    "security_concepts": [
        "owasp", "xss", "csrf", "sql-injection", "rce",
        "buffer-overflow", "race-condition",
        "tls", "ssl", "certificate", "x509", "pki",
        "encryption", "aes", "rsa", "ed25519", "hmac",
        "authentication", "authorization", "oauth", "jwt",
        "zero-trust", "least-privilege",
    ],
    "eidos_self_knowledge": [
        # Auto-referenciales: EIDOS aprende sobre sí mismo
        "EIDOS", "Colony", "brain-lite", "knowledge_reasoner",
        "inference engine", "screen scanner", "action system",
        "episodic memory", "constitution.toml",
        "OCR", "tesseract", "pyautogui", "wmctrl",
        "knowledge graph", "spreading activation",
    ],
}


def _load_progress() -> Dict:
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text())
        except Exception:
            pass
    return {"topics": {}, "started_at": datetime.now().isoformat()}


def _save_progress(progress: Dict):
    try:
        PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROGRESS_FILE.write_text(json.dumps(progress, indent=2, ensure_ascii=False))
    except Exception as e:
        log.error("save_progress falló: %s", e)


def study_concept(concept: str) -> int:
    """Investiga un concepto. Retorna nº de nodos añadidos."""
    try:
        from core.knowledge_reasoner import get_reasoner
        r = get_reasoner()
        if not r._ready:
            log.warning("reasoner no listo")
            return 0
        added = r._self_research(concept)
        return added
    except Exception as e:
        log.error("study_concept(%s) falló: %s", concept, e)
        return 0


def study_topic(topic: str, max_concepts: int = 0) -> Dict:
    """Investiga todos los conceptos de un topic. Retorna stats."""
    if topic not in CURRICULUM:
        return {"error": "topic_unknown"}
    concepts = CURRICULUM[topic]
    if max_concepts > 0:
        concepts = concepts[:max_concepts]

    stats = {"topic": topic, "concepts_total": len(concepts),
             "added": 0, "already_known": 0, "failed": 0,
             "details": []}
    for concept in concepts:
        try:
            added = study_concept(concept)
            if added > 0:
                stats["added"] += added
                stats["details"].append({"concept": concept, "added": added})
                log.info("📚 [%s] aprendido +%d nodos", concept, added)
            else:
                stats["already_known"] += 1
            time.sleep(0.5)  # cortesía con DuckDuckGo + Wikipedia
        except Exception as e:
            stats["failed"] += 1
            log.warning("study fail %s: %s", concept, e)
    return stats


def study_all(max_per_topic: int = 0,
              skip_topics: Optional[List[str]] = None) -> Dict:
    """Estudia todos los topics. Persiste progreso para reanudar."""
    progress = _load_progress()
    skip = set(skip_topics or [])
    overall = {"topics_studied": 0, "concepts_added": 0,
               "concepts_already_known": 0,
               "started_at": datetime.now().isoformat(),
               "results": {}}

    for topic in CURRICULUM.keys():
        if topic in skip:
            continue
        if progress["topics"].get(topic, {}).get("completed_at"):
            log.info("⏭  %s ya completado", topic)
            continue

        log.info("📖 Estudiando topic: %s", topic)
        stats = study_topic(topic, max_concepts=max_per_topic)

        progress["topics"][topic] = {
            "completed_at": datetime.now().isoformat(),
            "added": stats["added"],
            "already_known": stats["already_known"],
        }
        _save_progress(progress)

        overall["topics_studied"] += 1
        overall["concepts_added"] += stats["added"]
        overall["concepts_already_known"] += stats["already_known"]
        overall["results"][topic] = stats
        log.info("✅ %s: +%d nuevos, %d ya conocidos",
                 topic, stats["added"], stats["already_known"])

    overall["finished_at"] = datetime.now().isoformat()
    log.info("🎓 Curriculum completo: %d topics, +%d nodos nuevos",
             overall["topics_studied"], overall["concepts_added"])
    return overall


def study_background(max_per_topic: int = 0, daemon: bool = True):
    """Lanza el curriculum en thread background. No bloquea."""
    def _run():
        log.info("🌙 Curriculum iniciado en background")
        try:
            result = study_all(max_per_topic=max_per_topic)
            # Persistir resumen
            summary_path = Path.home() / ".eidos" / "curriculum_summary.json"
            summary_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
            log.info("Curriculum guardado en %s", summary_path)
        except Exception as e:
            log.error("Curriculum background falló: %s", e)

    t = threading.Thread(target=_run, daemon=daemon)
    t.start()
    return t


def status() -> Dict:
    """Devuelve estado actual del curriculum."""
    progress = _load_progress()
    total_topics = len(CURRICULUM)
    completed = sum(1 for t in progress["topics"].values()
                    if t.get("completed_at"))
    total_concepts = sum(len(v) for v in CURRICULUM.values())
    learned = sum(t.get("added", 0) for t in progress["topics"].values())
    return {
        "total_topics": total_topics,
        "completed_topics": completed,
        "pending_topics": total_topics - completed,
        "total_concepts": total_concepts,
        "concepts_learned": learned,
        "progress_pct": round(100 * completed / total_topics, 1),
        "details": progress["topics"],
    }


if __name__ == "__main__":
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    p = argparse.ArgumentParser()
    p.add_argument("--topic", help="Estudiar solo un topic")
    p.add_argument("--all", action="store_true", help="Estudiar todo")
    p.add_argument("--background", action="store_true",
                   help="Background mode (no bloquea)")
    p.add_argument("--status", action="store_true", help="Ver progreso")
    p.add_argument("--max", type=int, default=0,
                   help="Máx conceptos por topic")
    p.add_argument("--list", action="store_true", help="Listar topics")
    args = p.parse_args()

    if args.list:
        for topic, concepts in CURRICULUM.items():
            print(f"📚 {topic}: {len(concepts)} conceptos")
            print(f"   {', '.join(concepts[:5])}…")
    elif args.status:
        print(json.dumps(status(), indent=2, ensure_ascii=False))
    elif args.topic:
        result = study_topic(args.topic, max_concepts=args.max)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.all:
        if args.background:
            t = study_background(max_per_topic=args.max)
            print(f"Background thread: {t.name}")
            # En modo background standalone, mantener vivo
            while t.is_alive():
                time.sleep(10)
                print(f"  Progress: {status()['progress_pct']}%")
        else:
            result = study_all(max_per_topic=args.max)
            print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        p.print_help()
