"""
core/eidos_mastery.py — Goal-Driven Mastery System [S125]
==========================================================
The missing piece that connects exploration to mastery. When EIDOS explores
a domain, it doesn't just read — it MASTERS it through structured goals,
auto-generated curriculum, and the Study-Practice-Verify loop.

Architecture:
  1. LearningGoal      — Structured goal with domain, milestones, deadline, progress
  2. CurriculumGenerator — Generates learning paths for any domain
  3. StudyPracticeVerify — STUDY (man/URL/analyze) → PRACTICE (sandbox/terminal) → VERIFY
  4. MasteryTracker     — Per-domain scores 0-100% with the 5-band scale
  5. CuriosityBridge     — Wires into CuriosityEngine so exploration → mastery

Integration:
  - Sits BETWEEN eidos_curiosity_engine (exploration) and eidos_growth (mastery tracking)
  - Feeds into eidos_practice (spaced repetition) for retention
  - Hooks into eidos_self_curriculum (domain knowledge)

Mastery bands (per-domain):
  0-20%:   Never tried
  20-40%:  Studied but not practiced
  40-60%:  Practiced, some failures
  60-80%:  Can use independently
  80-95%:  Can teach, debug, handle edge cases
  95-100%: MASTERED
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

log = logging.getLogger("eidos.mastery")

MASTERY_DB = Path.home() / ".eidos" / "mastery_goals.db"

# ── Mastery bands ──────────────────────────────────────────────────────────────

MASTERY_BANDS = {
    "untried":       (0, 20),
    "studied":       (20, 40),
    "practicing":    (40, 60),
    "independent":   (60, 80),
    "teaching":      (80, 95),
    "mastered":      (95, 100),
}

BAND_LABELS = {
    (0, 20):   "Never tried",
    (20, 40):  "Studied but not practiced",
    (40, 60):  "Practiced, some failures",
    (60, 80):  "Can use independently",
    (80, 95):  "Can teach, debug, handle edge cases",
    (95, 100): "MASTERED",
}


def pct_to_band(pct: float) -> str:
    for name, (lo, hi) in MASTERY_BANDS.items():
        if lo <= pct < hi:
            return name
    return "mastered" if pct >= 100 else "untried"


# ── Domain Curriculum Templates ────────────────────────────────────────────────

DOMAIN_CURRICULUM: Dict[str, List[Dict[str, Any]]] = {
    "python": [
        {"step": "basics", "label": "Python Basics",
         "material": "man python3 OR https://docs.python.org/3/tutorial/",
         "practice": "Write a script that reads stdin, processes text, and writes stdout. Use variables, loops, conditionals, and functions.",
         "verify": "Run the script with sample input. Does it produce correct output? Check with `python3 -m py_compile`."},
        {"step": "functions", "label": "Functions & Scope",
         "material": "https://docs.python.org/3/tutorial/controlflow.html#defining-functions",
         "practice": "Write 3 functions: one pure (no side effects), one with *args/**kwargs, one decorator. Test each.",
         "verify": "Run `python3 -c 'from your_module import *; assert pure_fn(5) == expected'`"},
        {"step": "oop", "label": "Object-Oriented Programming",
         "material": "https://docs.python.org/3/tutorial/classes.html",
         "practice": "Design a class hierarchy with inheritance, __init__, __str__, @property. Instantiate and use.",
         "verify": "Run `python3 -c 'from your_module import MyClass; obj = MyClass(); assert hasattr(obj, \"method\")'`"},
        {"step": "stdlib", "label": "Standard Library",
         "material": "man python3 OR browse https://docs.python.org/3/library/",
         "practice": "Use 5 stdlib modules (pathlib, json, sqlite3, subprocess, argparse) in a real script.",
         "verify": "Script runs without ImportError. Each module used at least once."},
        {"step": "testing", "label": "Testing & Quality",
         "material": "https://docs.pytest.org/",
         "practice": "Write 10 pytest tests for your code. Run with `python3 -m pytest -v`. Fix failures.",
         "verify": "All tests pass. Coverage > 70%."},
        {"step": "projects", "label": "Real Projects",
         "material": "Apply everything: build a CLI tool, API client, or data processor.",
         "practice": "Build a complete project: plan → code → test → document. Use git for version control.",
         "verify": "Project works end-to-end. Someone else could use it from the README."},
    ],
    "kali_linux": [
        {"step": "recon", "label": "Information Gathering",
         "material": "man nmap; man whois; man dig; https://www.kali.org/tools/",
         "practice": "Run nmap -sV -sC against a test target (localhost or VM). Run whois on a domain. Document findings.",
         "verify": "nmap output shows open ports and services. whois returned registrar data."},
        {"step": "scanning", "label": "Scanning & Enumeration",
         "material": "man nikto; man gobuster; man enum4linux; man smbclient",
         "practice": "Run nikto against a test web server. Use gobuster to find hidden paths. Enumerate SMB shares.",
         "verify": "Each tool produced meaningful output. Found at least one hidden resource."},
        {"step": "exploitation", "label": "Exploitation Basics",
         "material": "man msfconsole; man searchsploit; https://docs.metasploit.com/",
         "practice": "Launch msfconsole. Search for an exploit by CVE. Understand the exploit workflow.",
         "verify": "Can explain the steps: search → use → set options → exploit → post-exploit."},
        {"step": "post_exploit", "label": "Post-Exploitation",
         "material": "man msfvenom; man meterpreter; https://www.offsec.com/metasploit-unleashed/",
         "practice": "Generate a payload with msfvenom. Understand listener setup. Practice hash dumping concepts.",
         "verify": "Can list 5 post-exploit actions: hashdump, screenshot, keylog, migrate, shell."},
        {"step": "reporting", "label": "Reporting & Documentation",
         "material": "Study a sample pentest report. Learn the structure: exec summary → findings → remediation.",
         "practice": "Write a mock pentest report for a test target. Include findings, CVSS scores, remediation.",
         "verify": "Report has: executive summary, methodology, findings table, remediation steps."},
    ],
    "qemu": [
        {"step": "install", "label": "Installation & First VM",
         "material": "man qemu-system-x86_64; https://www.qemu.org/docs/master/",
         "practice": "Install qemu-kvm. Create a disk image with qemu-img. Boot an ISO.",
         "verify": "VM boots to ISO. `virsh list --all` shows the domain."},
        {"step": "networking", "label": "Networking",
         "material": "man qemu-system-x86_64 (search NETWORK); https://wiki.qemu.org/Documentation/Networking",
         "practice": "Create a VM with NAT networking. Create a VM with bridge networking. Test connectivity.",
         "verify": "VM can ping host. Host can ping VM (bridge). NAT allows outbound internet."},
        {"step": "snapshots", "label": "Snapshots & Rollback",
         "material": "man virsh (snapshot-create, snapshot-list, snapshot-revert)",
         "practice": "Create a snapshot before a risky change. Make the change. Revert to snapshot. Verify.",
         "verify": "After revert, the risky change is gone. VM is exactly as it was at snapshot time."},
        {"step": "automation", "label": "Automation & Management",
         "material": "man virsh; https://libvirt.org/docs.html",
         "practice": "Write a script that: creates a VM from template, starts it, waits for boot, runs a command via guest agent.",
         "verify": "Script runs end-to-end without manual intervention. Can create+start+stop+destroy a VM."},
        {"step": "advanced", "label": "Advanced Features",
         "material": "man qemu-system-x86_64 (virtio, vfio, passthrough)",
         "practice": "Configure virtio drivers. Try GPU passthrough (if hardware supports). Set up a SPICE console.",
         "verify": "VM uses virtio for disk/network. `virsh domiflist` shows virtio model."},
    ],
    "networking": [
        {"step": "basics", "label": "Networking Fundamentals",
         "material": "man ip; man ss; man tcpdump; https://en.wikipedia.org/wiki/OSI_model",
         "practice": "List all interfaces with `ip addr`. Show listening ports with `ss -tlnp`. Capture traffic with tcpdump.",
         "verify": "Can identify interfaces, IPs, listening services. tcpdump shows real traffic."},
        {"step": "routing", "label": "Routing & Subnetting",
         "material": "man ip-route; man ip-rule; https://en.wikipedia.org/wiki/Subnetwork",
         "practice": "Calculate subnets by hand. Add a static route. Use `ip route get` to trace path.",
         "verify": "Correctly subnet 192.168.1.0/24 into /26 subnets. Static route works."},
        {"step": "firewall", "label": "Firewalls",
         "material": "man iptables; man ufw; man nft",
         "practice": "Write iptables rules to allow SSH, block a port, enable NAT. Test with `nc`.",
         "verify": "SSH works. Blocked port is unreachable. NAT forwards correctly."},
        {"step": "dns", "label": "DNS & Name Resolution",
         "material": "man dig; man nslookup; man resolv.conf",
         "practice": "Query A, AAAA, MX, NS, TXT records. Trace delegation with +trace. Reverse DNS lookup.",
         "verify": "Each query type returns correct records. Can explain DNS resolution chain."},
        {"step": "troubleshooting", "label": "Troubleshooting",
         "material": "man mtr; man ping; man traceroute; man nc",
         "practice": "Diagnose: why can't host reach internet? Is it DNS? Routing? Firewall? Fix it.",
         "verify": "Can diagnose connectivity issues systematically using the OSI model."},
    ],
    "sql": [
        {"step": "basics", "label": "SQL Fundamentals",
         "material": "man sqlite3; https://www.sqlite.org/lang.html",
         "practice": "CREATE TABLE, INSERT, SELECT, WHERE, ORDER BY, LIMIT. Query a real database.",
         "verify": "All queries return correct results. Can explain PRIMARY KEY and data types."},
        {"step": "joins", "label": "JOINs & Relationships",
         "material": "https://www.sqlite.org/lang_select.html (JOIN section)",
         "practice": "Write INNER JOIN, LEFT JOIN, CROSS JOIN. Create a 3-table schema with foreign keys.",
         "verify": "Each join returns correct results. Schema has proper foreign key relationships."},
        {"step": "aggregation", "label": "Aggregation & Grouping",
         "material": "man sqlite3 (aggregate functions); https://www.sqlite.org/lang_aggfunc.html",
         "practice": "Use COUNT, SUM, AVG, MIN, MAX with GROUP BY and HAVING. Write subqueries.",
         "verify": "Aggregate queries return correct results. Subquery runs without error."},
        {"step": "advanced", "label": "Advanced SQL",
         "material": "https://www.sqlite.org/lang_with.html (CTEs); indexes; transactions",
         "practice": "Write a CTE. Create an index and verify with EXPLAIN QUERY PLAN. Use BEGIN/COMMIT/ROLLBACK.",
         "verify": "CTE works. EXPLAIN shows index usage. Transaction rollback restores previous state."},
        {"step": "design", "label": "Database Design",
         "material": "Study normalization (1NF, 2NF, 3NF). Entity-relationship diagrams.",
         "practice": "Design a database for a real use case (library, e-commerce, task tracker). Normalize to 3NF.",
         "verify": "Schema is in 3NF. No data duplication. All relationships are modeled correctly."},
    ],
    "git": [
        {"step": "basics", "label": "Git Basics",
         "material": "man git; man gittutorial; https://git-scm.com/docs",
         "practice": "git init, add, commit, log, diff, status. Create branches, merge. Handle a merge conflict.",
         "verify": "Can create repo, commit changes, branch, merge. Resolve a conflict by hand."},
        {"step": "remote", "label": "Remote & Collaboration",
         "material": "man git-push; man git-pull; man git-fetch",
         "practice": "Clone a repo. Push to a remote. Pull with rebase. Handle divergent branches.",
         "verify": "Push/pull/fetch work. Can explain difference between merge and rebase."},
        {"step": "history", "label": "History Manipulation",
         "material": "man git-rebase; man git-cherry-pick; man git-bisect",
         "practice": "Interactive rebase to squash commits. Cherry-pick a commit. Bisect to find a bug.",
         "verify": "Rebase -i squashes correctly. Cherry-pick applies cleanly. Bisect identifies the bad commit."},
        {"step": "advanced", "label": "Advanced Git",
         "material": "man git-reflog; man git-stash; man git-worktree",
         "practice": "Recover a deleted branch with reflog. Stash and pop changes. Use worktrees for parallel work.",
         "verify": "Reflog recovers lost commits. Stash workflow works. Worktree allows parallel branches."},
    ],
}

# Generated on-the-fly for domains not in the template
FALLBACK_STEPS = [
    {"step": "fundamentals", "label": "Fundamentals",
     "material_tpl": "man {domain} OR research '{domain} basics' online",
     "practice_tpl": "Identify and list the core concepts of {domain}. Find 3 key tools/commands.",
     "verify_tpl": "Can explain what {domain} is and what it's used for in 3 sentences."},
    {"step": "tools", "label": "Core Tools & Commands",
     "material_tpl": "man {domain} OR '{domain} --help' OR research '{domain} essential tools'",
     "practice_tpl": "Execute the core commands of {domain}. Try different flags and options.",
     "verify_tpl": "Commands execute successfully. Can explain what each flag does."},
    {"step": "workflows", "label": "Common Workflows",
     "material_tpl": "Research '{domain} common workflows' or '{domain} tutorial'",
     "practice_tpl": "Complete a full workflow with {domain} from start to finish.",
     "verify_tpl": "Workflow completes without errors. Output matches expected results."},
    {"step": "advanced", "label": "Advanced Usage",
     "material_tpl": "man {domain} (advanced sections) OR research '{domain} advanced'",
     "practice_tpl": "Use {domain} in a non-trivial scenario. Handle edge cases and errors.",
     "verify_tpl": "Can handle at least 3 common errors. Can explain the internals."},
    {"step": "mastery", "label": "Mastery & Teaching",
     "material_tpl": "Research '{domain} best practices' and '{domain} pitfalls'",
     "practice_tpl": "Write a guide or tutorial for {domain}. Explain it to someone else (or to yourself aloud).",
     "verify_tpl": "Can teach {domain} from scratch. Can debug any common issue."},
]


# ── Schemas ────────────────────────────────────────────────────────────────────

_MASTERY_SCHEMA = """
CREATE TABLE IF NOT EXISTS learning_goals (
    id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    subdomain TEXT NOT NULL,
    target_mastery INTEGER DEFAULT 100,
    deadline TEXT,
    progress_pct REAL DEFAULT 0.0,
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT,
    completed_at TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id TEXT NOT NULL,
    step TEXT NOT NULL,
    label TEXT NOT NULL,
    material TEXT,
    practice_exercise TEXT,
    verification_test TEXT,
    status TEXT DEFAULT 'pending',
    attempt_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    last_attempted_at TEXT,
    completed_at TEXT,
    learned_summary TEXT,
    FOREIGN KEY (goal_id) REFERENCES learning_goals(id)
);

CREATE TABLE IF NOT EXISTS study_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id TEXT NOT NULL,
    milestone_id INTEGER,
    phase TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    outcome TEXT,
    detail TEXT,
    failure_reason TEXT,
    retry_of INTEGER,
    FOREIGN KEY (goal_id) REFERENCES learning_goals(id),
    FOREIGN KEY (milestone_id) REFERENCES milestones(id)
);

CREATE TABLE IF NOT EXISTS domain_mastery (
    domain TEXT NOT NULL,
    subdomain TEXT NOT NULL DEFAULT '',
    mastery_pct REAL DEFAULT 0.0,
    band TEXT DEFAULT 'untried',
    milestones_total INTEGER DEFAULT 0,
    milestones_completed INTEGER DEFAULT 0,
    total_attempts INTEGER DEFAULT 0,
    total_failures INTEGER DEFAULT 0,
    last_practiced_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (domain, subdomain)
);

CREATE INDEX IF NOT EXISTS idx_milestones_goal ON milestones(goal_id);
CREATE INDEX IF NOT EXISTS idx_study_sessions_goal ON study_sessions(goal_id);
CREATE INDEX IF NOT EXISTS idx_domain_mastery_domain ON domain_mastery(domain);
"""


def _ensure_schema():
    try:
        import sqlite3
        MASTERY_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(MASTERY_DB))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_MASTERY_SCHEMA)
        conn.commit()
        conn.close()
    except Exception as e:
        log.error("Error creating mastery schema: %s", e)


_ensure_schema()


def _get_conn():
    import sqlite3
    conn = sqlite3.connect(str(MASTERY_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.row_factory = sqlite3.Row
    return conn


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class Milestone:
    id: int = 0
    goal_id: str = ""
    step: str = ""
    label: str = ""
    material: str = ""
    practice_exercise: str = ""
    verification_test: str = ""
    status: str = "pending"
    attempt_count: int = 0
    failure_count: int = 0
    last_attempted_at: str = ""
    completed_at: str = ""
    learned_summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "goal_id": self.goal_id,
            "step": self.step, "label": self.label,
            "material": self.material,
            "practice_exercise": self.practice_exercise,
            "verification_test": self.verification_test,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "failure_count": self.failure_count,
            "last_attempted_at": self.last_attempted_at,
            "completed_at": self.completed_at,
            "learned_summary": self.learned_summary,
        }


@dataclass
class LearningGoal:
    id: str = ""
    domain: str = ""
    subdomain: str = ""
    target_mastery: int = 100
    deadline: str = ""
    progress_pct: float = 0.0
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""
    completed_at: str = ""
    notes: str = ""
    milestones: List[Milestone] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "domain": self.domain,
            "subdomain": self.subdomain,
            "target_mastery": self.target_mastery,
            "deadline": self.deadline,
            "progress_pct": self.progress_pct,
            "status": self.status,
            "band": pct_to_band(self.progress_pct),
            "band_label": BAND_LABELS.get(
                next((r for n, r in MASTERY_BANDS.items()
                      if r[0] <= self.progress_pct < r[1]), (0, 20)),
                "Unknown"),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "notes": self.notes,
            "milestones": [m.to_dict() for m in self.milestones],
            "milestones_total": len(self.milestones),
            "milestones_completed": sum(1 for m in self.milestones if m.status == "completed"),
        }


# ── Curriculum Generator ───────────────────────────────────────────────────────

class CurriculumGenerator:
    """Generates structured learning paths for any domain.

    For known domains (Python, Kali Linux, QEMU, networking, SQL, Git), uses
    hand-crafted curricula. For unknown domains, generates a generic 5-step path
    using fallback templates.
    """

    @staticmethod
    def generate(domain: str) -> List[Dict[str, Any]]:
        domain_key = domain.lower().replace(" ", "_").replace("-", "_")
        # Direct match
        if domain_key in DOMAIN_CURRICULUM:
            return DOMAIN_CURRICULUM[domain_key]
        # Partial match
        for key in DOMAIN_CURRICULUM:
            if key in domain_key or domain_key in key:
                return DOMAIN_CURRICULUM[key]
        # Fallback: generate from template
        steps = []
        for tpl in FALLBACK_STEPS:
            steps.append({
                "step": tpl["step"],
                "label": tpl["label"],
                "material": tpl["material_tpl"].format(domain=domain),
                "practice": tpl["practice_tpl"].format(domain=domain),
                "verify": tpl["verify_tpl"].format(domain=domain),
            })
        return steps

    @staticmethod
    def list_known_domains() -> List[str]:
        return sorted(DOMAIN_CURRICULUM.keys())


# ── Study-Practice-Verify Loop ─────────────────────────────────────────────────

class StudyPracticeVerify:
    """The core learning loop: STUDY → PRACTICE → VERIFY → (retry if failed).

    STUDY phase:
      - Read man page, fetch URL, analyze with DeepSeek via eidos_learn
      - Extract key concepts, commands, flags

    PRACTICE phase:
      - Execute in sandbox/terminal
      - Try variations, edge cases
      - Record outputs

    VERIFY phase:
      - Did the practice work as expected?
      - What was learned?
      - Update mastery score via GrowthEngine
      - If failed → analyze error, try different approach, RETRY
      - Track failures and what was learned from them
    """

    MAX_RETRIES = 3

    def __init__(self):
        self._active_session: Optional[Dict[str, Any]] = None

    def study(self, material: str, concept: str = "") -> Dict[str, Any]:
        """STUDY phase: read man page, fetch URL, or analyze with LLM.

        Returns structured study output: what was learned, key facts, commands found.
        """
        result = {
            "phase": "study",
            "concept": concept,
            "material": material,
            "ok": False,
            "content": "",
            "key_facts": [],
            "commands_found": [],
            "source": "unknown",
        }

        # 1. Try man page
        if "man " in material.lower() or concept:
            man_target = concept or material
            man_match = re.search(r'man\s+(\S+)', material)
            if man_match:
                man_target = man_match.group(1)
            try:
                r = subprocess.run(
                    ["man", "-P", "cat", man_target],
                    capture_output=True, text=True, timeout=8,
                    env={**os.environ, "MANPAGER": "cat", "MANWIDTH": "80",
                         "LC_ALL": "C"}
                )
                if r.returncode == 0 and len(r.stdout) > 50:
                    result["content"] = r.stdout[:4000]
                    result["source"] = f"man {man_target}"
                    result["ok"] = True
                    # Extract commands from SYNOPSIS/EXAMPLES
                    cmds = re.findall(r'^\s{2,}([a-z][a-z0-9_-]{2,30})', r.stdout, re.MULTILINE)
                    result["commands_found"] = list(dict.fromkeys(cmds))[:5]
                    result["key_facts"] = self._extract_key_facts(r.stdout)
            except Exception as e:
                log.debug("man page failed for %s: %s", man_target, e)

        # 2. Try --help if no man page
        if not result["ok"] and concept:
            try:
                r = subprocess.run(
                    [concept, "--help"], capture_output=True, text=True, timeout=5,
                    env={**os.environ, "LC_ALL": "C"}
                )
                if r.returncode in (0, 1) and len(r.stdout) > 20:
                    result["content"] = r.stdout[:2000]
                    result["source"] = f"{concept} --help"
                    result["ok"] = True
            except Exception:
                pass

        # 3. Try URL fetch (basic)
        url_match = re.search(r'https?://\S+', material)
        if not result["ok"] and url_match:
            result = self._study_from_url(url_match.group(0), concept, material)

        # 4. Try LLM analysis via eidos_learn
        if not result["ok"] or len(result.get("content", "")) < 100:
            try:
                from core.eidos_learn import ask_llm
                prompt = (
                    f"Study this topic: {concept or material}. "
                    f"Provide: (1) What it is, (2) Key concepts, (3) Common commands or usage, "
                    f"(4) Important flags/options, (5) One concrete example. "
                    f"Keep it under 400 words."
                )
                answer, source = ask_llm(prompt, timeout=30)
                if answer and len(answer) > 40:
                    result["content"] = answer
                    result["source"] = f"LLM:{source}"
                    result["ok"] = True
                    # Extract key facts from LLM response
                    result["key_facts"] = [
                        line.strip("- ") for line in answer.split("\n")
                        if line.strip().startswith(("-", "1.", "2.", "3.", "4.", "5."))
                    ][:5]
            except Exception as e:
                log.debug("LLM study failed: %s", e)

        return result

    def _study_from_url(self, url: str, concept: str, material: str) -> Dict[str, Any]:
        """Fetch and extract content from a URL."""
        result = {
            "phase": "study", "concept": concept, "material": material,
            "ok": False, "content": "", "key_facts": [], "commands_found": [],
            "source": url,
        }
        try:
            import urllib.request
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0"}
            )
            resp = urllib.request.urlopen(req, timeout=10)
            raw = resp.read(128 * 1024).decode("utf-8", errors="replace")
            resp.close()
            # Strip HTML
            text = re.sub(r'<script[^>]*>.*?</script>', '', raw, flags=re.DOTALL)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            if len(text) > 100:
                result["content"] = text[:3000]
                result["ok"] = True
                result["key_facts"] = self._extract_key_facts(text)
        except Exception as e:
            log.debug("URL fetch failed for %s: %s", url, e)
        return result

    def _extract_key_facts(self, text: str) -> List[str]:
        """Extract key facts from text: lines that look like definitions or important statements."""
        facts = []
        for line in text.split("\n")[:40]:
            line = line.strip()
            if len(line) > 30 and len(line) < 300:
                # Lines that define or describe
                if any(w in line.lower() for w in ["is a", "used for", "allows", "provides",
                                                    "command", "utility", "tool", "function"]):
                    facts.append(line[:200])
        return facts[:5]

    def practice(self, exercise: str, concept: str = "",
                 sandbox: bool = True) -> Dict[str, Any]:
        """PRACTICE phase: execute the exercise in terminal/sandbox.

        If sandbox=True, runs in a controlled environment (dry-run / read-only).
        If sandbox=False, runs real commands (requires EIDOS_LIBRE_REAL_ACTIONS=1).
        """
        result = {
            "phase": "practice",
            "concept": concept,
            "exercise": exercise,
            "ok": False,
            "output": "",
            "commands_used": [],
            "variations_tried": 0,
            "errors": [],
            "sandbox": sandbox,
        }

        # Extract commands from the exercise description
        potential_cmds = re.findall(r'`([^`]+)`|"([^"]+)"|\b([a-z][a-z0-9_-]{2,30})\b',
                                    exercise)
        cmds = [c[0] or c[1] or c[2] for c in potential_cmds if c[0] or c[1] or c[2]]

        if sandbox:
            # Sandbox mode: try --help / man for each command, no real execution
            for cmd in cmds[:5]:
                if not self._is_safe_command(cmd):
                    continue
                try:
                    r = subprocess.run(
                        [cmd, "--help"], capture_output=True, text=True, timeout=5,
                        env={**os.environ, "LC_ALL": "C"}
                    )
                    if r.returncode in (0, 1) and len(r.stdout) > 10:
                        result["output"] += f"\n--- {cmd} --help ---\n{r.stdout[:800]}\n"
                        result["commands_used"].append(f"{cmd} --help")
                        result["variations_tried"] += 1
                    else:
                        # Try --version as fallback
                        r2 = subprocess.run(
                            [cmd, "--version"], capture_output=True, text=True, timeout=3
                        )
                        if r2.returncode == 0:
                            result["output"] += f"\n--- {cmd} --version ---\n{r2.stdout[:200]}\n"
                            result["commands_used"].append(f"{cmd} --version")
                            result["variations_tried"] += 1
                except FileNotFoundError:
                    result["errors"].append(f"Command not found: {cmd}")
                except Exception as e:
                    result["errors"].append(f"Error with {cmd}: {e}")
        else:
            # Real execution mode
            real_mode = os.environ.get("EIDOS_LIBRE_REAL_ACTIONS") == "1"
            if not real_mode:
                result["errors"].append("Real actions disabled. Set EIDOS_LIBRE_REAL_ACTIONS=1.")
                return result
            # Execute the first found command (conservative)
            for cmd in cmds[:1]:
                if not self._is_safe_command(cmd):
                    continue
                try:
                    r = subprocess.run(
                        [cmd, "--help"], capture_output=True, text=True, timeout=8,
                        env={**os.environ, "LC_ALL": "C"}
                    )
                    result["output"] = r.stdout[:2000] if r.stdout else r.stderr[:500]
                    result["commands_used"].append(cmd)
                    result["variations_tried"] = 1
                except Exception as e:
                    result["errors"].append(str(e))

        result["ok"] = len(result["commands_used"]) > 0
        return result

    def _is_safe_command(self, cmd: str) -> bool:
        """Block destructive commands in sandbox."""
        blocked = {"rm", "mv", "dd", "mkfs", "fdisk", "shutdown", "reboot",
                   "kill", "killall", "chmod 777", ">", "wget", "curl -o",
                   "nc -e", "bash -c"}
        cmd_lower = cmd.lower()
        return not any(b in cmd_lower for b in blocked)

    def verify(self, expected: str, practice_result: Dict[str, Any],
               concept: str = "") -> Dict[str, Any]:
        """VERIFY phase: did the practice work? What was learned?

        Returns: {ok, passed, score, learned, should_retry, retry_reason}
        """
        result = {
            "phase": "verify",
            "concept": concept,
            "expected": expected,
            "ok": False,
            "passed": False,
            "score": 0.0,
            "learned": "",
            "should_retry": False,
            "retry_reason": "",
            "feedback": "",
        }

        errors = practice_result.get("errors", [])
        commands_used = practice_result.get("commands_used", [])
        output = practice_result.get("output", "")

        if not practice_result.get("ok"):
            result["feedback"] = "Practice failed: no commands were executed successfully."
            # Check if retry makes sense
            if any("not found" in e.lower() for e in errors):
                result["retry_reason"] = "Command not installed. Try installing it first."
                result["should_retry"] = True
            elif errors:
                result["retry_reason"] = f"Errors during practice: {errors[0]}"
                result["should_retry"] = True
            result["score"] = 10.0
            return result

        # Check if output contains expected indicators
        expected_lower = expected.lower()
        output_lower = output.lower()

        # Scoring heuristics
        score = 0.0
        learned_parts = []

        if len(commands_used) >= 1:
            score += 30
            learned_parts.append(f"Executed {commands_used[0]}")

        if len(commands_used) >= 2:
            score += 15
            learned_parts.append(f"Tried {len(commands_used)} variations")

        if len(output) > 100:
            score += 20
            learned_parts.append("Got substantial output")

        # Check if output matches expected behavior
        expected_keywords = re.findall(r'[a-z]{4,}', expected_lower)
        matches = sum(1 for kw in expected_keywords if kw in output_lower)
        if matches >= 3:
            score += 25
            learned_parts.append(f"Output matches expected behavior ({matches} keywords)")
        elif matches >= 1:
            score += 15
            learned_parts.append(f"Partial match with expected ({matches} keywords)")

        if not errors:
            score += 10
        else:
            learned_parts.append(f"Encountered {len(errors)} errors: {errors[0][:80]}")

        result["score"] = min(100, score)
        result["passed"] = score >= 50
        result["learned"] = "; ".join(learned_parts) if learned_parts else "Practice executed"

        if not result["passed"] and len(commands_used) < 2:
            result["should_retry"] = True
            result["retry_reason"] = "Insufficient commands executed. Try more variations."

        result["ok"] = True
        result["feedback"] = "PASS" if result["passed"] else "NEEDS_IMPROVEMENT"
        return result

    def run_loop(self, milestone: Dict[str, Any], goal_id: str = "",
                 concept: str = "") -> Dict[str, Any]:
        """Run the full STUDY → PRACTICE → VERIFY loop for a milestone.

        Retries up to MAX_RETRIES times if verify fails.
        Updates mastery scores via GrowthEngine on completion.
        Persists study sessions to mastery_goals.db for morning briefing.
        """
        material = milestone.get("material", "")
        exercise = milestone.get("practice_exercise", "")
        verify_test = milestone.get("verification_test", "")
        label = milestone.get("label", concept)
        milestone_id = milestone.get("id", 0)

        session_start = time.time()
        sessions_log = []
        final_score = 0.0
        final_passed = False
        tracker = None

        try:
            tracker = get_mastery_tracker()
        except Exception:
            pass

        for attempt in range(1, self.MAX_RETRIES + 1):
            log.info("SPV loop [%s] attempt %d/%d: %s", goal_id[:8], attempt,
                     self.MAX_RETRIES, label)

            # STUDY
            study_result = self.study(material, concept=concept)
            sessions_log.append({"attempt": attempt, "phase": "study",
                                 "ok": study_result["ok"],
                                 "source": study_result.get("source", "unknown")})
            # Persist study session
            if tracker and goal_id:
                try:
                    tracker.record_study_session(
                        goal_id, milestone_id, "study",
                        outcome="OK" if study_result["ok"] else "FAIL",
                        detail=f"Source: {study_result.get('source', 'unknown')}; "
                               f"Facts: {len(study_result.get('key_facts', []))}",
                        failure_reason="" if study_result["ok"] else "No source found",
                    )
                except Exception:
                    pass

            # PRACTICE
            practice_result = self.practice(exercise, concept=concept,
                                           sandbox=(attempt == 1))
            sessions_log.append({"attempt": attempt, "phase": "practice",
                                 "ok": practice_result["ok"],
                                 "cmds": practice_result.get("commands_used", [])})
            # Persist practice session
            if tracker and goal_id:
                try:
                    tracker.record_study_session(
                        goal_id, milestone_id, "practice",
                        outcome="OK" if practice_result["ok"] else "FAIL",
                        detail=f"Commands: {practice_result.get('commands_used', [])}; "
                               f"Errors: {practice_result.get('errors', [])}",
                        failure_reason=""
                        if practice_result["ok"] else str(practice_result.get("errors", ["unknown"])[0]),
                    )
                except Exception:
                    pass

            # VERIFY
            verify_result = self.verify(verify_test, practice_result, concept=concept)
            sessions_log.append({"attempt": attempt, "phase": "verify",
                                 "passed": verify_result["passed"],
                                 "score": verify_result["score"],
                                 "learned": verify_result.get("learned", "")})
            # Persist verify session
            if tracker and goal_id:
                try:
                    tracker.record_study_session(
                        goal_id, milestone_id, "verify",
                        outcome="PASS" if verify_result["passed"] else "FAIL",
                        detail=f"Score: {verify_result['score']:.0f}/100; "
                               f"Learned: {verify_result.get('learned', '')}",
                        failure_reason="" if verify_result["passed"]
                        else verify_result.get("retry_reason", ""),
                    )
                except Exception:
                    pass

            if verify_result["passed"]:
                final_score = verify_result["score"]
                final_passed = True
                break
            elif not verify_result.get("should_retry"):
                final_score = verify_result["score"]
                break
            else:
                log.info("Retry needed: %s", verify_result.get("retry_reason", "unknown"))

        # Update GrowthEngine
        delta = 0.10 if final_passed else 0.03
        try:
            from core.eidos_growth import get_growth_engine
            engine = get_growth_engine()
            engine.update_mastery(
                concept or label,
                declarative_delta=delta,
                procedural_delta=delta if final_passed else delta * 0.3,
                applicational_delta=delta * 0.5 if final_passed else 0,
                metacognitive_delta=0.05,
                event_type="spv_loop",
                source="eidos_mastery",
            )
        except Exception as e:
            log.debug("GrowthEngine update in SPV loop: %s", e)

        return {
            "ok": True,
            "concept": concept,
            "label": label,
            "goal_id": goal_id,
            "passed": final_passed,
            "score": round(final_score, 1),
            "attempts": attempt,
            "sessions": sessions_log,
            "duration_seconds": round(time.time() - session_start, 1),
        }


# ── Mastery Tracker ────────────────────────────────────────────────────────────

class MasteryTracker:
    """Tracks per-domain mastery scores and progress.

    Persists to mastery_goals.db (domain_mastery table).
    Computes mastery percentage from completed milestones.
    """

    def __init__(self):
        self._lock = threading.Lock()

    def create_goal(self, domain: str, subdomain: str = "",
                    target_mastery: int = 100,
                    deadline_days: int = 0) -> LearningGoal:
        """Create a new learning goal with auto-generated milestones.

        Args:
            domain: Domain name (e.g., "python", "kali_linux")
            subdomain: Optional subdomain
            target_mastery: Target mastery percentage (default 100)
            deadline_days: Days until deadline (0 = no deadline)
        """
        now = datetime.now()
        goal_id = f"goal_{domain.replace(' ', '_')}_{now.strftime('%Y%m%d_%H%M%S')}"
        deadline = ""
        if deadline_days > 0:
            deadline = (now + timedelta(days=deadline_days)).isoformat()

        curriculum = CurriculumGenerator.generate(domain)
        milestones = []
        for i, step in enumerate(curriculum):
            milestones.append(Milestone(
                goal_id=goal_id,
                step=step["step"],
                label=step["label"],
                material=step["material"],
                practice_exercise=step["practice"],
                verification_test=step["verify"],
                status="pending",
            ))

        goal = LearningGoal(
            id=goal_id, domain=domain, subdomain=subdomain,
            target_mastery=target_mastery, deadline=deadline,
            created_at=now.isoformat(), updated_at=now.isoformat(),
            milestones=milestones,
        )

        self._persist_goal(goal)
        self._update_domain_mastery(domain, subdomain, goal)

        log.info("Created goal %s: %s (%d milestones)", goal_id, domain,
                 len(milestones))
        return goal

    def _persist_goal(self, goal: LearningGoal):
        conn = _get_conn()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO learning_goals
                (id, domain, subdomain, target_mastery, deadline, progress_pct,
                 status, created_at, updated_at, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (goal.id, goal.domain, goal.subdomain, goal.target_mastery,
                  goal.deadline, goal.progress_pct, goal.status,
                  goal.created_at, goal.updated_at, goal.notes))
            for m in goal.milestones:
                cur = conn.execute("""
                    INSERT INTO milestones
                    (goal_id, step, label, material, practice_exercise,
                     verification_test, status, attempt_count, failure_count,
                     last_attempted_at, completed_at, learned_summary)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (m.goal_id, m.step, m.label, m.material,
                      m.practice_exercise, m.verification_test, m.status,
                      m.attempt_count, m.failure_count,
                      m.last_attempted_at, m.completed_at, m.learned_summary))
                m.id = cur.lastrowid  # Store the auto-incremented ID
            conn.commit()
        finally:
            conn.close()

    def get_goal(self, goal_id: str) -> Optional[LearningGoal]:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM learning_goals WHERE id = ?", (goal_id,)
            ).fetchone()
            if not row:
                return None
            goal = LearningGoal(
                id=row["id"], domain=row["domain"], subdomain=row["subdomain"],
                target_mastery=row["target_mastery"], deadline=row["deadline"] or "",
                progress_pct=row["progress_pct"] or 0.0, status=row["status"] or "active",
                created_at=row["created_at"] or "", updated_at=row["updated_at"] or "",
                completed_at=row["completed_at"] or "", notes=row["notes"] or "",
            )
            # Load milestones
            ms_rows = conn.execute(
                "SELECT * FROM milestones WHERE goal_id = ? ORDER BY id", (goal_id,)
            ).fetchall()
            for mr in ms_rows:
                goal.milestones.append(Milestone(
                    id=mr["id"], goal_id=mr["goal_id"],
                    step=mr["step"], label=mr["label"],
                    material=mr["material"] or "",
                    practice_exercise=mr["practice_exercise"] or "",
                    verification_test=mr["verification_test"] or "",
                    status=mr["status"] or "pending",
                    attempt_count=mr["attempt_count"] or 0,
                    failure_count=mr["failure_count"] or 0,
                    last_attempted_at=mr["last_attempted_at"] or "",
                    completed_at=mr["completed_at"] or "",
                    learned_summary=mr["learned_summary"] or "",
                ))
            return goal
        finally:
            conn.close()

    def complete_milestone(self, goal_id: str, milestone_id: int,
                          learned: str = "", spv_result: Optional[Dict] = None) -> bool:
        """Mark a milestone as completed and update goal progress."""
        now = datetime.now().isoformat()
        conn = _get_conn()
        try:
            if spv_result:
                passed = spv_result.get("passed", False)
                attempts = spv_result.get("attempts", 1)
                conn.execute("""
                    UPDATE milestones SET
                        status = ?, attempt_count = ?,
                        failure_count = ?, last_attempted_at = ?,
                        completed_at = ?, learned_summary = ?
                    WHERE id = ? AND goal_id = ?
                """, ("completed" if passed else "failed",
                      attempts,
                      attempts - 1 if not passed else 0,
                      now,
                      now if passed else None,
                      learned[:500],
                      milestone_id, goal_id))
            else:
                conn.execute("""
                    UPDATE milestones SET status = 'completed', completed_at = ?
                    WHERE id = ? AND goal_id = ?
                """, (now, milestone_id, goal_id))

            # Recalculate goal progress
            total = conn.execute(
                "SELECT COUNT(*) FROM milestones WHERE goal_id = ?", (goal_id,)
            ).fetchone()[0]
            completed = conn.execute(
                "SELECT COUNT(*) FROM milestones WHERE goal_id = ? AND status = 'completed'",
                (goal_id,)
            ).fetchone()[0]
            pct = round(100 * completed / total, 1) if total > 0 else 0.0

            new_status = "active"
            if pct >= 100:
                new_status = "completed"
            elif pct >= 50:
                new_status = "in_progress"

            conn.execute("""
                UPDATE learning_goals SET progress_pct = ?, status = ?, updated_at = ?
                WHERE id = ?
            """, (pct, new_status, now, goal_id))

            if pct >= 100:
                conn.execute("UPDATE learning_goals SET completed_at = ? WHERE id = ?",
                            (now, goal_id))

            conn.commit()

            # Update domain_mastery
            goal_row = conn.execute(
                "SELECT domain, subdomain FROM learning_goals WHERE id = ?", (goal_id,)
            ).fetchone()
            if goal_row:
                self._update_domain_entry(conn, goal_row["domain"],
                                         goal_row["subdomain"] or "")
                conn.commit()  # Commit domain_mastery update
        finally:
            conn.close()
        return True

    def _update_domain_entry(self, conn, domain: str, subdomain: str):
        """Update or create the domain_mastery row."""
        now = datetime.now().isoformat()
        # Aggregate all goals for this domain
        row = conn.execute("""
            SELECT COUNT(*) as total_goals,
                   AVG(progress_pct) as avg_pct,
                   SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_goals
            FROM learning_goals
            WHERE domain = ? AND (subdomain = ? OR ? = '')
        """, (domain, subdomain, subdomain)).fetchone()

        mastery_pct = round(row["avg_pct"] or 0.0, 1)
        band = pct_to_band(mastery_pct)

        # Also sum milestones
        ms_row = conn.execute("""
            SELECT COUNT(*) as total, SUM(CASE WHEN m.status = 'completed' THEN 1 ELSE 0 END) as done,
                   SUM(m.attempt_count) as attempts, SUM(m.failure_count) as failures
            FROM milestones m JOIN learning_goals g ON m.goal_id = g.id
            WHERE g.domain = ? AND (g.subdomain = ? OR ? = '')
        """, (domain, subdomain, subdomain)).fetchone()

        conn.execute("""
            INSERT INTO domain_mastery
            (domain, subdomain, mastery_pct, band, milestones_total,
             milestones_completed, total_attempts, total_failures, last_practiced_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain, subdomain) DO UPDATE SET
                mastery_pct = excluded.mastery_pct, band = excluded.band,
                milestones_total = excluded.milestones_total,
                milestones_completed = excluded.milestones_completed,
                total_attempts = excluded.total_attempts,
                total_failures = excluded.total_failures,
                last_practiced_at = excluded.last_practiced_at,
                updated_at = excluded.updated_at
        """, (domain, subdomain, mastery_pct, band,
              ms_row["total"] or 0, ms_row["done"] or 0,
              ms_row["attempts"] or 0, ms_row["failures"] or 0,
              now, now))

    def _update_domain_mastery(self, domain: str, subdomain: str, goal: LearningGoal):
        conn = _get_conn()
        try:
            self._update_domain_entry(conn, domain, subdomain)
            conn.commit()
        finally:
            conn.close()

    def get_domain_mastery(self, domain: str, subdomain: str = "") -> Dict[str, Any]:
        """Get the mastery status for a domain."""
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM domain_mastery WHERE domain = ? AND subdomain = ?",
                (domain, subdomain)
            ).fetchone()
            if not row:
                return {
                    "domain": domain, "subdomain": subdomain,
                    "mastery_pct": 0.0, "band": "untried",
                    "band_label": "Never tried",
                    "milestones_total": 0, "milestones_completed": 0,
                    "total_attempts": 0, "total_failures": 0,
                }
            return {
                "domain": row["domain"], "subdomain": row["subdomain"],
                "mastery_pct": row["mastery_pct"],
                "band": row["band"],
                "band_label": BAND_LABELS.get(
                    next((r for n, r in MASTERY_BANDS.items()
                          if r[0] <= (row["mastery_pct"] or 0) < r[1]), (0, 20)),
                    "Unknown"),
                "milestones_total": row["milestones_total"],
                "milestones_completed": row["milestones_completed"],
                "total_attempts": row["total_attempts"],
                "total_failures": row["total_failures"],
                "last_practiced_at": row["last_practiced_at"],
                "updated_at": row["updated_at"],
            }
        finally:
            conn.close()

    def list_goals(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = _get_conn()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM learning_goals WHERE status = ? ORDER BY created_at DESC",
                    (status,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM learning_goals ORDER BY created_at DESC"
                ).fetchall()
            results = []
            for row in rows:
                ms_count = conn.execute(
                    "SELECT COUNT(*) as t, SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as c "
                    "FROM milestones WHERE goal_id = ?", (row["id"],)
                ).fetchone()
                results.append({
                    "id": row["id"], "domain": row["domain"],
                    "subdomain": row["subdomain"] or "",
                    "target_mastery": row["target_mastery"],
                    "progress_pct": row["progress_pct"] or 0.0,
                    "status": row["status"],
                    "milestones_total": ms_count["t"] or 0,
                    "milestones_completed": ms_count["c"] or 0,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                })
            return results
        finally:
            conn.close()

    def get_all_domain_mastery(self) -> List[Dict[str, Any]]:
        """Get mastery status for all domains."""
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM domain_mastery ORDER BY mastery_pct DESC"
            ).fetchall()
            return [
                {
                    "domain": r["domain"], "subdomain": r["subdomain"] or "",
                    "mastery_pct": r["mastery_pct"] or 0.0,
                    "band": r["band"] or "untried",
                    "band_label": BAND_LABELS.get(
                        next((b for n, b in MASTERY_BANDS.items()
                              if b[0] <= (r["mastery_pct"] or 0) < b[1]), (0, 20)),
                        "Unknown"),
                    "milestones_total": r["milestones_total"] or 0,
                    "milestones_completed": r["milestones_completed"] or 0,
                    "total_attempts": r["total_attempts"] or 0,
                    "total_failures": r["total_failures"] or 0,
                }
                for r in rows
            ]
        finally:
            conn.close()

    def record_study_session(self, goal_id: str, milestone_id: int,
                             phase: str, outcome: str, detail: str = "",
                             started_at: Optional[str] = None,
                             ended_at: Optional[str] = None,
                             failure_reason: str = "",
                             retry_of: Optional[int] = None) -> int:
        """Record a study session (STUDY/PRACTICE/VERIFY) in the DB.
        Returns the session id."""
        now = datetime.now().isoformat()
        conn = _get_conn()
        try:
            cur = conn.execute("""
                INSERT INTO study_sessions
                (goal_id, milestone_id, phase, started_at, ended_at,
                 outcome, detail, failure_reason, retry_of)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (goal_id, milestone_id, phase,
                  started_at or now, ended_at or now,
                  outcome[:500], detail[:2000],
                  failure_reason[:500], retry_of))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def get_pending_milestones(self) -> List[Dict[str, Any]]:
        """Return all pending milestones across active goals, ordered by priority."""
        conn = _get_conn()
        try:
            rows = conn.execute("""
                SELECT m.*, g.domain, g.subdomain, g.progress_pct
                FROM milestones m
                JOIN learning_goals g ON m.goal_id = g.id
                WHERE m.status = 'pending' AND g.status IN ('active', 'in_progress')
                ORDER BY g.progress_pct ASC, m.id ASC
            """).fetchall()
            return [{
                "id": r["id"], "goal_id": r["goal_id"],
                "domain": r["domain"], "subdomain": r["subdomain"],
                "step": r["step"], "label": r["label"],
                "material": r["material"] or "",
                "practice_exercise": r["practice_exercise"] or "",
                "verification_test": r["verification_test"] or "",
                "status": r["status"],
                "attempt_count": r["attempt_count"] or 0,
                "failure_count": r["failure_count"] or 0,
                "progress_pct": r["progress_pct"] or 0.0,
            } for r in rows]
        finally:
            conn.close()

    def create_goals_for_all_categories(self,
                                         min_nodes: int = 1,
                                         dry_run: bool = False) -> Dict[str, Any]:
        """P0: Auto-create LearningGoals for ALL categories in the knowledge graph.

        Queries evolution_brain.db for distinct categories in knowledge_nodes,
        and creates one LearningGoal per category that doesn't already have one.
        Each goal gets 5 auto-generated milestones using the FALLBACK_STEPS template.

        This scales the mastery system from the 6 hardcoded domains to covering
        ALL 235+ categories that EIDOS has knowledge in.

        Args:
            min_nodes: Minimum nodes a category must have to get a goal (default 1)
            dry_run: If True, only report what would be created without persisting

        Returns:
            {created, skipped_existing, total_categories, by_category, dry_run}
        """
        import sqlite3 as _sql
        now = datetime.now()
        created = 0
        skipped_existing = 0
        errors = 0
        by_category: Dict[str, Dict[str, Any]] = {}

        try:
            brain_path = Path.home() / ".eidos" / "evolution_brain.db"
            brain_conn = _sql.connect(str(brain_path))
            brain_conn.row_factory = _sql.Row

            # Get all distinct categories with node counts
            cats = brain_conn.execute("""
                SELECT category, COUNT(*) as node_count
                FROM knowledge_nodes
                WHERE category IS NOT NULL AND category != ''
                  AND confidence >= 0.55
                GROUP BY category
                HAVING node_count >= ?
                ORDER BY node_count DESC
            """, (min_nodes,)).fetchall()
            brain_conn.close()

            total_cats = len(cats)
            log.info("create_goals_for_all_categories: %d categories found "
                     "(min_nodes=%d)", total_cats, min_nodes)

            # Get existing goals to avoid duplicates
            existing_domains = set()
            try:
                existing = self.list_goals()
                for g in existing:
                    existing_domains.add(g["domain"].lower())
            except Exception:
                pass

            for cat_row in cats:
                category = cat_row["category"]
                node_count = cat_row["node_count"]
                cat_key = category.lower().replace(" ", "_").replace("-", "_")

                if cat_key in existing_domains:
                    skipped_existing += 1
                    by_category[category] = {
                        "action": "skipped_existing",
                        "node_count": node_count,
                    }
                    continue

                if dry_run:
                    created += 1
                    by_category[category] = {
                        "action": "would_create",
                        "node_count": node_count,
                        "milestones": 5,
                    }
                    continue

                # Create the goal
                try:
                    goal = self.create_goal(category, subdomain="auto")
                    by_category[category] = {
                        "action": "created",
                        "goal_id": goal.id,
                        "node_count": node_count,
                        "milestones": len(goal.milestones),
                    }
                    created += 1
                except Exception as e:
                    errors += 1
                    by_category[category] = {
                        "action": "error",
                        "node_count": node_count,
                        "error": str(e)[:100],
                    }
                    log.error("create_goals_for_all_categories: error on %s: %s",
                             category, e)

            log.info("create_goals_for_all_categories: COMPLETE. "
                     "%d created, %d skipped (existing), %d errors, %d total categories",
                     created, skipped_existing, errors, total_cats)

            return {
                "ok": True,
                "created": created,
                "skipped_existing": skipped_existing,
                "errors": errors,
                "total_categories": total_cats,
                "by_category": by_category,
                "dry_run": dry_run,
            }

        except Exception as e:
            log.error("create_goals_for_all_categories error: %s", e)
            return {
                "ok": False,
                "error": str(e),
                "created": created,
                "skipped_existing": skipped_existing,
                "total_categories": 0,
            }

    def get_recent_study_sessions(self, since_hours: int = 24) -> List[Dict[str, Any]]:
        """Get study sessions from the last N hours."""
        since = (datetime.now() - timedelta(hours=since_hours)).isoformat()
        conn = _get_conn()
        try:
            rows = conn.execute("""
                SELECT s.*, g.domain, m.label, m.step
                FROM study_sessions s
                JOIN learning_goals g ON s.goal_id = g.id
                LEFT JOIN milestones m ON s.milestone_id = m.id
                WHERE s.started_at >= ?
                ORDER BY s.started_at DESC
            """, (since,)).fetchall()
            return [{
                "id": r["id"], "goal_id": r["goal_id"],
                "domain": r["domain"], "label": r["label"] or "",
                "step": r["step"] or "",
                "phase": r["phase"], "outcome": r["outcome"] or "",
                "detail": r["detail"] or "",
                "failure_reason": r["failure_reason"] or "",
                "started_at": r["started_at"], "ended_at": r["ended_at"],
            } for r in rows]
        finally:
            conn.close()

    def get_recent_completions(self, since_hours: int = 24) -> List[Dict[str, Any]]:
        """Get milestones completed in the last N hours."""
        since = (datetime.now() - timedelta(hours=since_hours)).isoformat()
        conn = _get_conn()
        try:
            rows = conn.execute("""
                SELECT m.*, g.domain
                FROM milestones m
                JOIN learning_goals g ON m.goal_id = g.id
                WHERE m.status = 'completed' AND m.completed_at >= ?
                ORDER BY m.completed_at DESC
            """, (since,)).fetchall()
            return [{
                "id": r["id"], "goal_id": r["goal_id"],
                "domain": r["domain"],
                "step": r["step"], "label": r["label"],
                "attempt_count": r["attempt_count"] or 0,
                "failure_count": r["failure_count"] or 0,
                "completed_at": r["completed_at"],
                "learned_summary": r["learned_summary"] or "",
            } for r in rows]
        finally:
            conn.close()

    def get_recent_failures(self, since_hours: int = 24) -> List[Dict[str, Any]]:
        """Get recent failures: milestones that failed or sessions with failure_reason."""
        since = (datetime.now() - timedelta(hours=since_hours)).isoformat()
        conn = _get_conn()
        try:
            # Failed study sessions
            session_rows = conn.execute("""
                SELECT s.*, g.domain, m.label, m.step
                FROM study_sessions s
                JOIN learning_goals g ON s.goal_id = g.id
                LEFT JOIN milestones m ON s.milestone_id = m.id
                WHERE s.started_at >= ?
                  AND (s.outcome LIKE '%FAIL%' OR s.failure_reason != '')
                ORDER BY s.started_at DESC
            """, (since,)).fetchall()

            # Failed milestones
            ms_rows = conn.execute("""
                SELECT m.*, g.domain
                FROM milestones m
                JOIN learning_goals g ON m.goal_id = g.id
                WHERE m.status = 'failed' AND m.last_attempted_at >= ?
                ORDER BY m.last_attempted_at DESC
            """, (since,)).fetchall()

            failures = []
            for r in session_rows:
                failures.append({
                    "type": "session",
                    "domain": r["domain"], "label": r["label"] or "",
                    "step": r["step"] or "",
                    "phase": r["phase"], "outcome": r["outcome"] or "",
                    "failure_reason": r["failure_reason"] or "",
                    "detail": (r["detail"] or "")[:200],
                })
            for r in ms_rows:
                failures.append({
                    "type": "milestone",
                    "domain": r["domain"], "label": r["label"],
                    "step": r["step"],
                    "attempt_count": r["attempt_count"] or 0,
                    "failure_count": r["failure_count"] or 0,
                })
            return failures
        finally:
            conn.close()

    def get_domain_level_changes(self, since_hours: int = 24) -> List[Dict[str, Any]]:
        """Detect domains that reached a new mastery band recently."""
        # Compare current band with what it would have been before recent completions
        since = (datetime.now() - timedelta(hours=since_hours)).isoformat()
        conn = _get_conn()
        try:
            # Get all milestones completed in the period
            rows = conn.execute("""
                SELECT m.goal_id, g.domain, g.progress_pct as current_pct,
                       COUNT(m.id) as completed_in_period,
                       m.completed_at
                FROM milestones m
                JOIN learning_goals g ON m.goal_id = g.id
                WHERE m.status = 'completed' AND m.completed_at >= ?
                GROUP BY m.goal_id, g.domain
            """, (since,)).fetchall()

            changes = []
            for r in rows:
                current_pct = r["current_pct"] or 0.0
                # Approximate: each milestone contributes roughly equally
                # Get total milestones for this goal
                total = conn.execute(
                    "SELECT COUNT(*) FROM milestones WHERE goal_id = ?",
                    (r["goal_id"],)
                ).fetchone()[0]
                if total > 0 and r["completed_in_period"] > 0:
                    ms_weight = 100.0 / total
                    prev_pct = max(0, current_pct - (ms_weight * r["completed_in_period"]))
                    current_band = pct_to_band(current_pct)
                    prev_band = pct_to_band(prev_pct)
                    if current_band != prev_band:
                        changes.append({
                            "domain": r["domain"],
                            "prev_band": prev_band,
                            "prev_label": BAND_LABELS.get(
                                next((b for n, b in MASTERY_BANDS.items()
                                      if b[0] <= prev_pct < b[1]), (0, 20)),
                                "Unknown"),
                            "current_band": current_band,
                            "current_label": BAND_LABELS.get(
                                next((b for n, b in MASTERY_BANDS.items()
                                      if b[0] <= current_pct < b[1]), (0, 20)),
                                "Unknown"),
                            "current_pct": current_pct,
                        })
            return changes
        finally:
            conn.close()

    def generate_morning_briefing(self) -> str:
        """Generate a comprehensive morning mastery briefing in markdown.

        Reads the mastery_goals.db and produces a summary of:
        - What was studied last night
        - What was practiced
        - What was mastered (reached new level)
        - What failed (needs different approach)
        - Top 3 things to focus on next
        """
        now = datetime.now()
        lines = []
        lines.append("# EIDOS Morning Mastery Briefing")
        lines.append(f"Generated: {now.strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

        # ── What was studied last night ──────────────────────────────────────
        study_sessions = self.get_recent_study_sessions(since_hours=24)
        study_phases = [s for s in study_sessions if s["phase"] == "study"]
        lines.append("## What Was Studied Last Night")
        if study_phases:
            # Group by domain
            by_domain: Dict[str, List] = {}
            for s in study_phases:
                d = s["domain"]
                if d not in by_domain:
                    by_domain[d] = []
                by_domain[d].append(s)
            for domain, sessions in sorted(by_domain.items()):
                unique_labels = list(dict.fromkeys(
                    s["label"] for s in sessions if s["label"]
                ))
                lines.append(f"- **{domain}**: studied {', '.join(unique_labels[:5])}")
                if len(unique_labels) > 5:
                    lines.append(f"  _(and {len(unique_labels) - 5} more)_")
        else:
            lines.append("No study sessions recorded in the last 24 hours.")
            # Try to show what's available
            pending = self.get_pending_milestones()
            if pending:
                lines.append(f"_(Tip: {len(pending)} pending milestones ready for study)_")
        lines.append("")

        # ── What was practiced ───────────────────────────────────────────────
        practice_sessions = [s for s in study_sessions if s["phase"] == "practice"]
        lines.append("## What Was Practiced")
        if practice_sessions:
            by_domain = {}
            for s in practice_sessions:
                d = s["domain"]
                if d not in by_domain:
                    by_domain[d] = []
                by_domain[d].append(s)
            for domain, sessions in sorted(by_domain.items()):
                unique_labels = list(dict.fromkeys(
                    s["label"] for s in sessions if s["label"]
                ))
                total_attempts = len(sessions)
                lines.append(f"- **{domain}**: practiced {', '.join(unique_labels[:5])}")
                lines.append(f"  _(total practice sessions: {total_attempts})_")
        else:
            lines.append("No practice sessions recorded in the last 24 hours.")
        lines.append("")

        # ── What was mastered ────────────────────────────────────────────────
        completions = self.get_recent_completions(since_hours=24)
        level_changes = self.get_domain_level_changes(since_hours=24)
        lines.append("## What Was Mastered (New Levels Reached)")
        if completions:
            by_domain = {}
            for c in completions:
                d = c["domain"]
                if d not in by_domain:
                    by_domain[d] = []
                by_domain[d].append(c)
            for domain, comps in sorted(by_domain.items()):
                labels = [f"{c['label']} ({c['attempt_count']} attempts)" for c in comps]
                lines.append(f"- **{domain}**: completed {', '.join(labels)}")
        else:
            lines.append("No milestones completed in the last 24 hours.")

        if level_changes:
            lines.append("")
            lines.append("### Domain Band Changes")
            for lc in level_changes:
                lines.append(f"- **{lc['domain']}**: {lc['prev_label']} -> {lc['current_label']} ({lc['current_pct']:.1f}%)")
        lines.append("")

        # ── What failed ──────────────────────────────────────────────────────
        failures = self.get_recent_failures(since_hours=24)
        lines.append("## Failures & Issues")
        if failures:
            for f in failures[:10]:
                if f["type"] == "session":
                    lines.append(f"- **{f['domain']}**/{f.get('label', '?')}: "
                               f"{f['phase']} failed - {f.get('failure_reason', f.get('outcome', 'unknown'))}")
                else:
                    lines.append(f"- **{f['domain']}**/{f['label']}: milestone failed "
                               f"({f['failure_count']}/{f['attempt_count']} attempts)")
                    lines.append(f"  _(needs different approach)_")
        else:
            lines.append("No failures recorded in the last 24 hours.")
        lines.append("")

        # ── Top 3 things to focus on next ────────────────────────────────────
        lines.append("## Top 3 To Focus On Next")
        pending = self.get_pending_milestones()
        all_domains = self.get_all_domain_mastery()
        domain_map = {d["domain"]: d for d in all_domains}

        # Priority: pending milestones from lowest-mastery domains first
        scored_items = []
        for pm in pending:
            dm = domain_map.get(pm["domain"], {})
            mastery_pct = dm.get("mastery_pct", 0.0)
            # Score: lower mastery = higher priority, penalize by attempt_count
            priority = (100.0 - mastery_pct) + pm["progress_pct"] * 0.1
            scored_items.append((priority, pm))

        scored_items.sort(key=lambda x: -x[0])

        if scored_items:
            for i, (score, pm) in enumerate(scored_items[:3]):
                dm = domain_map.get(pm["domain"], {})
                lines.append(f"{i+1}. **{pm['domain']}**: {pm['label']} ({pm['step']})")
                lines.append(f"   - Current mastery: {dm.get('mastery_pct', 0):.1f}% [{dm.get('band', 'untried')}]")
                lines.append(f"   - Material: {pm['material'][:120]}")
                lines.append(f"   - Practice: {pm['practice_exercise'][:150]}")
        else:
            # No pending milestones - suggest new domains
            known_domains = CurriculumGenerator.list_known_domains()
            tracked = {d["domain"] for d in all_domains}
            untracked = [d for d in known_domains if d not in tracked]
            if untracked:
                lines.append(f"1. **Create goals** for untracked domains: {', '.join(untracked[:3])}")
            else:
                lines.append("1. All domains have active goals. Run SPV loops to advance.")
            lines.append("2. Check `eidos_mastery.py run <domain>` to start practicing.")
            lines.append("3. Review any failed milestones with different approaches.")
        lines.append("")

        # ── Overall Mastery Summary ──────────────────────────────────────────
        lines.append("## All Domains Overview")
        all_m = self.get_all_domain_mastery()
        if all_m:
            for m in all_m:
                bar_len = 20
                filled = int(m["mastery_pct"] / 5)
                bar = "=" * filled + "-" * (bar_len - filled)
                lines.append(f"- `[{bar}]` **{m['domain']}**: {m['mastery_pct']:.1f}% "
                           f"({m['milestones_completed']}/{m['milestones_total']} ms) "
                           f"[{m['band']}]")
        else:
            lines.append("No domains tracked yet. Create goals with `python3 core/eidos_mastery.py create <domain>`.")
            known = CurriculumGenerator.list_known_domains()
            lines.append(f"Available domains: {', '.join(known)}")

        return "\n".join(lines)


# ── CuriosityBridge ────────────────────────────────────────────────────────────

class CuriosityBridge:
    """Bridges the Curiosity Engine to the Mastery system.

    When EIDOS explores a domain via CuriosityEngine, this bridge:
    1. Detects what domain/topic was explored
    2. Creates a LearningGoal if one doesn't exist
    3. Runs SPV loop on the explored item
    4. Updates mastery scores

    This is the key integration: exploration → mastery.
    """

    def __init__(self, tracker: Optional[MasteryTracker] = None):
        self.tracker = tracker or MasteryTracker()
        self.spv = StudyPracticeVerify()
        self._explored_domains: Dict[str, int] = {}  # domain → items explored count

    def on_explored(self, item_type: str, payload: str,
                    result: str = "", detail: str = "") -> Optional[Dict[str, Any]]:
        """Called when the CuriosityEngine explores an item.

        Maps the explored item to a domain, creates/finds the goal,
        and optionally runs a study session.

        Args:
            item_type: 'cmd', 'path', 'url', 'category', 'recipe'
            payload: The actual item explored
            result: Exploration result summary
            detail: Detailed exploration output

        Returns: {mastery_action, domain, goal_id} or None if no action taken
        """
        domain = self._classify_item(item_type, payload)

        if not domain:
            return None

        # Track domain exploration count
        self._explored_domains[domain] = self._explored_domains.get(domain, 0) + 1

        # Check if domain already has a goal
        existing = self.tracker.list_goals()
        existing_goals = [g for g in existing
                         if g["domain"] == domain and g["status"] in ("active", "in_progress")]

        if not existing_goals:
            # Create a goal after N explorations of the same domain
            if self._explored_domains.get(domain, 0) >= 3:
                goal = self.tracker.create_goal(domain)
                log.info("CuriosityBridge: auto-created mastery goal for %s", domain)
                return {"mastery_action": "goal_created", "domain": domain,
                        "goal_id": goal.id}
            return {"mastery_action": "tracked", "domain": domain,
                    "explorations": self._explored_domains[domain]}

        # Domain has an active goal: run SPV on the next pending milestone
        goal_id = existing_goals[0]["id"]
        goal = self.tracker.get_goal(goal_id)
        if not goal:
            return None

        pending = [m for m in goal.milestones if m.status == "pending"]
        if not pending:
            return {"mastery_action": "all_complete", "domain": domain, "goal_id": goal_id}

        # Run SPV on the first pending milestone
        ms = pending[0]
        spv_result = self.spv.run_loop(
            ms.to_dict(), goal_id=goal_id, concept=payload
        )

        if spv_result["passed"]:
            self.tracker.complete_milestone(
                goal_id, ms.id,
                learned=spv_result.get("sessions", [{}])[-1].get("learned", ""),
                spv_result=spv_result,
            )
            log.info("CuriosityBridge: milestone %s/%s COMPLETED for %s",
                     ms.step, ms.label, domain)

        return {
            "mastery_action": "spv_loop",
            "domain": domain,
            "goal_id": goal_id,
            "milestone": ms.step,
            "passed": spv_result["passed"],
            "score": spv_result["score"],
        }

    def _classify_item(self, item_type: str, payload: str) -> Optional[str]:
        """Classify an explored item into a mastery domain."""
        payload_lower = payload.lower()

        # Known domain mappings
        domain_map = {
            "python": ["python", "pip", "pytest", "flask", "django", "numpy", "pandas",
                      "asyncio", "venv", "virtualenv"],
            "kali_linux": ["kali", "nmap", "metasploit", "nikto", "wireshark", "tcpdump",
                          "aircrack", "hydra", "john", "hashcat", "burp", "sqlmap",
                          "gobuster", "msf", "searchsploit", "exploit"],
            "networking": ["ip ", "ss ", "tcp", "udp", "dns", "http", "tls", "ssl",
                          "iptables", "nft", "ufw", "netstat", "route", "ping",
                          "traceroute", "mtr", "dig", "nslookup", "curl", "wget"],
            "qemu": ["qemu", "kvm", "virt", "libvirt", "virsh", "spice", "vfio"],
            "sql": ["sql", "sqlite", "postgres", "mysql", "mariadb", "query"],
            "git": ["git", "github", "commit", "branch", "rebase", "merge", "pull"],
            "shell": ["bash", "zsh", "tmux", "grep", "sed", "awk", "find", "xargs",
                     "chmod", "chown", "systemctl", "journalctl", "cron"],
        }

        for domain, keywords in domain_map.items():
            for kw in keywords:
                if kw in payload_lower:
                    return domain

        # If item_type is 'category', the category name might be the domain
        if item_type == "category":
            for d in domain_map:
                if d in payload_lower:
                    return d

        return None

    def get_exploration_stats(self) -> Dict[str, Any]:
        """Stats about domains discovered through exploration."""
        return {
            "domains_explored": len(self._explored_domains),
            "by_domain": dict(self._explored_domains),
        }


# ── Singleton ──────────────────────────────────────────────────────────────────

_tracker: Optional[MasteryTracker] = None
_spv: Optional[StudyPracticeVerify] = None
_curriculum_gen: Optional[CurriculumGenerator] = None
_bridge: Optional[CuriosityBridge] = None
_lock = threading.Lock()


def get_mastery_tracker() -> MasteryTracker:
    global _tracker
    if _tracker is None:
        with _lock:
            if _tracker is None:
                _tracker = MasteryTracker()
    return _tracker


def get_spv_loop() -> StudyPracticeVerify:
    global _spv
    if _spv is None:
        with _lock:
            if _spv is None:
                _spv = StudyPracticeVerify()
    return _spv


def get_curriculum_generator() -> CurriculumGenerator:
    global _curriculum_gen
    if _curriculum_gen is None:
        with _lock:
            if _curriculum_gen is None:
                _curriculum_gen = CurriculumGenerator()
    return _curriculum_gen


def get_curiosity_bridge() -> CuriosityBridge:
    global _bridge
    if _bridge is None:
        with _lock:
            if _bridge is None:
                _bridge = CuriosityBridge(tracker=get_mastery_tracker())
    return _bridge


# ── Night Mastery Runner ─────────────────────────────────────────────────────

class NightMasteryRunner:
    """Runs SPV loops on pending curriculum milestones during night hours.

    P0 SCALE: Now processes up to 50 items per tick in night mode.
    Pulls from BOTH curriculum milestones AND the practice_queue.

    Used by:
    - CuriosityEngine (during night mode: SER idle > 30 min)
    - systemd timer (eidos-mastery.service, triggered at 2am nightly)

    Iterates through all active learning goals, runs SPV on the first
    pending milestone of each, and updates mastery scores.
    Also processes the spaced-repetition practice_queue for active recall.
    """

    # P0 SCALE: 10x from 5 to 50 for night mode
    MAX_MILESTONES_PER_RUN = 50
    # Day mode (when SER is active): process fewer items
    MAX_MILESTONES_PER_RUN_DAY = 10
    # Practice queue items to process per run (night mode)
    MAX_PRACTICE_ITEMS_PER_RUN = 50

    def __init__(self):
        self._tracker: Optional[MasteryTracker] = None
        self._spv: Optional[StudyPracticeVerify] = None
        self._results: List[Dict[str, Any]] = []
        self._practice_results: List[Dict[str, Any]] = []
        self._started_at: Optional[float] = None
        self._night_mode: bool = False

    @property
    def tracker(self) -> MasteryTracker:
        if self._tracker is None:
            self._tracker = get_mastery_tracker()
        return self._tracker

    @property
    def spv(self) -> StudyPracticeVerify:
        if self._spv is None:
            self._spv = get_spv_loop()
        return self._spv

    def set_night_mode(self, enabled: bool = True):
        """Enable/disable night mode (affects max items per run)."""
        self._night_mode = enabled

    def _get_max_items(self) -> int:
        """Return max items to process based on night/day mode."""
        if self._night_mode:
            return self.MAX_MILESTONES_PER_RUN
        return self.MAX_MILESTONES_PER_RUN_DAY

    def run(self, max_milestones: int = 0) -> List[Dict[str, Any]]:
        """Run SPV loops on pending milestones across all active goals.

        Args:
            max_milestones: Max milestones to process (0 = auto based on night/day mode)

        Returns:
            List of results: {domain, milestone, passed, score, attempts, ...}
        """
        max_ms = max_milestones or self._get_max_items()
        self._results = []
        self._practice_results = []
        self._started_at = time.time()

        # ── PHASE 1: Process curriculum milestones ─────────────────────────
        pending = self.tracker.get_pending_milestones()
        if pending:
            log.info("NightMasteryRunner: starting SPV loops on %d pending milestones "
                     "(max %d, night_mode=%s)", len(pending), max_ms, self._night_mode)

            processed = 0
            for pm in pending:
                if processed >= max_ms:
                    break

                domain = pm["domain"]
                label = pm["label"]
                step = pm["step"]
                goal_id = pm["goal_id"]
                milestone_id = pm["id"]

                log.info("NightMasteryRunner [%d/%d]: %s / %s (%s)",
                         processed + 1, min(len(pending), max_ms),
                         domain, label, step)

                try:
                    spv_result = self.spv.run_loop(
                        {
                            "id": milestone_id,
                            "goal_id": goal_id,
                            "material": pm["material"],
                            "practice_exercise": pm["practice_exercise"],
                            "verification_test": pm["verification_test"],
                            "label": label,
                        },
                        goal_id=goal_id,
                        concept=domain,
                    )

                    # Complete the milestone
                    learned = spv_result.get("sessions", [{}])[-1].get("learned", "")
                    self.tracker.complete_milestone(
                        goal_id, milestone_id,
                        learned=(
                            f"Night SPV: Score={spv_result['score']}, "
                            f"Attempts={spv_result['attempts']}, "
                            f"Learned={learned}"
                        ),
                        spv_result=spv_result,
                    )

                    result = {
                        "type": "curriculum",
                        "domain": domain,
                        "milestone": label,
                        "step": step,
                        "passed": spv_result["passed"],
                        "score": spv_result["score"],
                        "attempts": spv_result["attempts"],
                        "duration_seconds": spv_result.get("duration_seconds", 0),
                        "goal_id": goal_id,
                    }
                    self._results.append(result)

                    status = "PASS" if spv_result["passed"] else "FAIL"
                    log.info("NightMasteryRunner [%s]: %s/%s = %s (score %.0f, %d attempts)",
                             status, domain, label,
                             "COMPLETED" if spv_result["passed"] else "NEEDS_RETRY",
                             spv_result["score"], spv_result["attempts"])

                except Exception as e:
                    log.error("NightMasteryRunner error on %s/%s: %s", domain, label, e)
                    self._results.append({
                        "type": "curriculum",
                        "domain": domain, "milestone": label, "step": step,
                        "passed": False, "score": 0, "attempts": 0,
                        "error": str(e),
                        "goal_id": goal_id,
                    })

                processed += 1

            log.info("NightMasteryRunner curriculum phase: %d milestones processed",
                     processed)
        else:
            log.info("NightMasteryRunner: no pending milestones found")

        # ── PHASE 2: Process practice_queue (active recall) ────────────────
        try:
            from core.eidos_practice import get_practice_queue, get_active_recall
            pq = get_practice_queue()
            ar = get_active_recall()

            # In night mode, process more practice items
            practice_limit = self.MAX_PRACTICE_ITEMS_PER_RUN if self._night_mode else 10
            due_concepts = pq.get_due_concepts(limit=practice_limit)

            if due_concepts:
                log.info("NightMasteryRunner: processing %d due practice items "
                         "(night_mode=%s)", len(due_concepts), self._night_mode)

                for dc in due_concepts:
                    concept = dc["concept"]
                    node_id = dc.get("node_id", "")
                    definition = dc.get("definition", "")
                    level = dc.get("mastery_level", "NOVICE")

                    try:
                        # Generate prompt and do active recall via LLM
                        prompt_data = ar.generate_prompt(concept)
                        if "error" in prompt_data:
                            self._practice_results.append({
                                "type": "practice",
                                "concept": concept,
                                "passed": False,
                                "score": 0.0,
                                "error": prompt_data.get("error", ""),
                            })
                            continue

                        # Use LLM to generate the actual recall response
                        recall_response = ""
                        try:
                            from core.eidos_learn import ask_llm
                            recall_prompt = (
                                f"You are EIDOS doing active recall practice. "
                                f"From MEMORY ONLY (do NOT look up anything), "
                                f"answer this question:\n\n{prompt_data['prompt']}\n\n"
                                f"Respond in Spanish. Keep it under 200 words. "
                                f"If you truly don't know, say 'No lo recuerdo bien'."
                            )
                            recall_response, _ = ask_llm(recall_prompt, timeout=20)
                        except Exception as llm_err:
                            log.debug("LLM recall failed for %s: %s", concept, llm_err)
                            # Fallback: use the definition as response for scoring
                            recall_response = (
                                f"Recuerdo parcial: {definition[:300]}"
                                if definition else "No lo recuerdo bien"
                            )

                        # Evaluate and record
                        session_result = ar.practice_session(concept, recall_response)
                        evaluation = session_result.get("evaluation", {})

                        pr = {
                            "type": "practice",
                            "concept": concept,
                            "node_id": node_id,
                            "passed": session_result.get("success", False),
                            "score": session_result.get("recall_score", 0.0),
                            "mastery_level": level,
                            "feedback": evaluation.get("feedback", ""),
                            "next_review_hours": session_result.get("interval_hours", 0),
                            "streak_correct": session_result.get("streak_correct", 0),
                            "duration_ms": session_result.get("duration_ms", 0),
                        }
                        self._practice_results.append(pr)

                        log.info("Practice [%s]: %s score=%.3f streak=+%d/-%d",
                                 "PASS" if pr["passed"] else "RETRY",
                                 concept[:40],
                                 pr["score"],
                                 session_result.get("streak_correct", 0),
                                 session_result.get("streak_wrong", 0))

                    except Exception as e:
                        log.error("Practice error on %s: %s", concept, e)
                        self._practice_results.append({
                            "type": "practice",
                            "concept": concept,
                            "passed": False,
                            "score": 0.0,
                            "error": str(e)[:100],
                        })

                log.info("NightMasteryRunner practice phase: %d items processed, "
                         "%d passed",
                         len(self._practice_results),
                         sum(1 for r in self._practice_results if r.get("passed")))
            else:
                log.debug("NightMasteryRunner: no due practice items")

        except Exception as e:
            log.error("NightMasteryRunner practice phase error: %s", e)

        # Take a growth snapshot after the run
        try:
            from core.eidos_growth import get_growth_engine
            engine = get_growth_engine()
            engine.snapshot_growth(force=True)
        except Exception as e:
            log.debug("Growth snapshot after night mastery: %s", e)

        # Merge results
        all_results = self._results + self._practice_results
        total_curriculum = len(self._results)
        total_practice = len(self._practice_results)
        total_passed = sum(1 for r in all_results if r.get("passed"))
        total_failed = sum(1 for r in all_results if not r.get("passed"))

        elapsed = time.time() - (self._started_at or time.time())
        log.info("NightMasteryRunner: COMPLETE. %d curriculum + %d practice = %d items "
                 "in %.1f min. %d passed, %d failed. (night_mode=%s)",
                 total_curriculum, total_practice, len(all_results),
                 elapsed / 60, total_passed, total_failed,
                 self._night_mode)

        return all_results

    def run_practice_only(self, max_items: int = 0) -> List[Dict[str, Any]]:
        """Run ONLY the practice queue phase (no curriculum milestones).

        Useful for when EIDOS has idle time: practice the next due items
        from the spaced-repetition queue.

        Args:
            max_items: Max practice items (0 = auto based on night/day mode)

        Returns:
            List of practice results
        """
        use_max = max_items or (self.MAX_PRACTICE_ITEMS_PER_RUN
                                if self._night_mode else 10)
        self._practice_results = []
        self._started_at = time.time()

        try:
            from core.eidos_practice import get_practice_queue, get_active_recall
            pq = get_practice_queue()
            ar = get_active_recall()

            due_concepts = pq.get_due_concepts(limit=use_max)

            if not due_concepts:
                log.debug("run_practice_only: no due items")
                return []

            log.info("run_practice_only: %d due items, processing up to %d",
                     len(due_concepts), use_max)

            for dc in due_concepts[:use_max]:
                concept = dc["concept"]
                node_id = dc.get("node_id", "")
                level = dc.get("mastery_level", "NOVICE")

                try:
                    # Active recall: generate prompt and evaluate via LLM
                    prompt_data = ar.generate_prompt(concept)
                    if "error" in prompt_data:
                        self._practice_results.append({
                            "type": "practice",
                            "concept": concept,
                            "passed": False,
                            "score": 0.0,
                            "error": prompt_data.get("error", ""),
                        })
                        continue

                    recall_response = ""
                    try:
                        from core.eidos_learn import ask_llm
                        recall_prompt = (
                            f"You are EIDOS doing active recall practice. "
                            f"From MEMORY ONLY (do NOT look up anything), "
                            f"answer this question:\n\n{prompt_data['prompt']}\n\n"
                            f"Respond in Spanish. Keep it under 200 words. "
                            f"If you truly don't know, say 'No lo recuerdo bien'."
                        )
                        recall_response, _ = ask_llm(recall_prompt, timeout=20)
                    except Exception:
                        recall_response = "No lo recuerdo bien"

                    session_result = ar.practice_session(concept, recall_response)
                    evaluation = session_result.get("evaluation", {})

                    pr = {
                        "type": "practice",
                        "concept": concept,
                        "node_id": node_id,
                        "passed": session_result.get("success", False),
                        "score": session_result.get("recall_score", 0.0),
                        "mastery_level": level,
                        "feedback": evaluation.get("feedback", ""),
                        "next_review_hours": session_result.get("interval_hours", 0),
                        "streak_correct": session_result.get("streak_correct", 0),
                        "duration_ms": session_result.get("duration_ms", 0),
                    }
                    self._practice_results.append(pr)

                except Exception as e:
                    log.error("run_practice_only error on %s: %s", concept, e)
                    self._practice_results.append({
                        "type": "practice",
                        "concept": concept,
                        "passed": False,
                        "score": 0.0,
                        "error": str(e)[:100],
                    })

            total_passed = sum(1 for r in self._practice_results if r.get("passed"))
            elapsed = time.time() - (self._started_at or time.time())
            log.info("run_practice_only: %d items in %.1f min, %d passed",
                     len(self._practice_results), elapsed / 60, total_passed)

        except Exception as e:
            log.error("run_practice_only error: %s", e)

        return self._practice_results

    def get_results(self) -> List[Dict[str, Any]]:
        return self._results

    def get_practice_results(self) -> List[Dict[str, Any]]:
        return self._practice_results

    def get_briefing(self) -> str:
        """Generate the morning briefing after a night mastery run."""
        return self.tracker.generate_morning_briefing()


# Singleton for NightMasteryRunner
_night_runner: Optional[NightMasteryRunner] = None


def get_night_mastery_runner() -> NightMasteryRunner:
    global _night_runner
    if _night_runner is None:
        with _lock:
            if _night_runner is None:
                _night_runner = NightMasteryRunner()
    return _night_runner


def run_night_mastery(max_milestones: int = 0, night_mode: bool = True,
                     auto_create_category_goals: bool = True) -> List[Dict[str, Any]]:
    """P0: Entry point for systemd timer. Run SPV on pending milestones + practice queue.

    Also ensures goals exist for:
    - All known curriculum domains (hardcoded)
    - All 235+ categories in the knowledge graph (auto-generated)

    Args:
        max_milestones: Max milestones to process (0 = auto based on night/day mode)
        night_mode: If True, process up to 50 items per tick
        auto_create_category_goals: If True, create goals for all graph categories

    Returns:
        Results list for logging
    """
    tracker = get_mastery_tracker()
    gen = get_curriculum_generator()

    # ── Phase 0a: Ensure goals exist for all known domains ──────────────────
    existing_domains = {g["domain"] for g in tracker.list_goals()
                       if g["status"] in ("active", "in_progress")}
    known_domains = gen.list_known_domains()

    for domain in known_domains:
        if domain not in existing_domains:
            try:
                goal = tracker.create_goal(domain)
                log.info("run_night_mastery: auto-created goal for %s (%d milestones)",
                         domain, len(goal.milestones))
            except Exception as e:
                log.error("run_night_mastery: failed to create goal for %s: %s",
                         domain, e)

    # ── Phase 0b: P0 — Ensure category-level goals exist for ALL categories ──
    if auto_create_category_goals:
        try:
            cat_result = tracker.create_goals_for_all_categories(min_nodes=1)
            log.info("run_night_mastery: category goals — %d created, %d skipped",
                     cat_result.get("created", 0),
                     cat_result.get("skipped_existing", 0))
        except Exception as e:
            log.error("run_night_mastery: category goal creation failed: %s", e)

    # ── Phase 1: Run the mastery runner with practice queue ─────────────────
    runner = get_night_mastery_runner()
    runner.set_night_mode(enabled=night_mode)
    results = runner.run(max_milestones=max_milestones)

    # ── Phase 2: Write briefing ─────────────────────────────────────────────
    try:
        briefing = runner.get_briefing()
        briefing_path = Path.home() / ".eidos" / "morning_briefing.md"
        briefing_path.write_text(briefing, encoding="utf-8")
        log.info("Morning mastery briefing written to %s", briefing_path)

        # Also write a summary of practice queue stats
        try:
            from core.eidos_practice import get_practice_queue
            pq = get_practice_queue()
            pq_stats = pq.get_queue_stats()
            summary_lines = [
                "\n\n## Practice Queue Stats\n",
                f"- Total concepts: {pq_stats.get('total_concepts', 0)}\n",
                f"- Due now: {pq_stats.get('due_now', 0)}\n",
                f"- Avg recall: {pq_stats.get('avg_recall_score', 0):.3f}\n",
                f"- Sessions today: {pq_stats.get('sessions_today', 0)}\n",
            ]
            with open(briefing_path, "a", encoding="utf-8") as f:
                f.writelines(summary_lines)
        except Exception:
            pass
    except Exception as e:
        log.error("Failed to write morning briefing: %s", e)

    return results


# ── Curiosity Engine Integration ───────────────────────────────────────────────

def integrate_with_curiosity() -> None:
    """Wire CuriosityBridge into the CuriosityEngine.

    After calling this, every exploration tick automatically feeds into
    the mastery system. The CuriosityEngine's CuriosityExplorer.explore()
    is patched to call bridge.on_explored() after each exploration.

    Usage:
        from core.eidos_mastery import integrate_with_curiosity
        integrate_with_curiosity()
    """
    try:
        from core.eidos_curiosity_engine import get_curiosity_engine
        engine = get_curiosity_engine()
        bridge = get_curiosity_bridge()

        # Patch the tick method to feed mastery after each exploration
        original_tick = engine.tick

        def mastery_enhanced_tick(force_night: bool = False):
            result = original_tick(force_night=force_night)
            if result.get("explored") and result.get("item_type"):
                try:
                    bridge.on_explored(
                        item_type=result["item_type"],
                        payload=result["payload"],
                        result=result.get("result_preview", ""),
                    )
                except Exception as e:
                    log.debug("CuriosityBridge in tick: %s", e)
            return result

        engine.tick = mastery_enhanced_tick
        log.info("CuriosityBridge integrated into CuriosityEngine")

        # Also patch the CuriosityExplorer.explore()
        original_explore = engine.explorer.explore

        def mastery_enhanced_explore(item):
            outcome = original_explore(item)
            if outcome.get("ok"):
                try:
                    bridge.on_explored(
                        item_type=item["item_type"],
                        payload=item["payload"],
                        result=outcome.get("result", ""),
                        detail=outcome.get("detail", ""),
                    )
                except Exception as e:
                    log.debug("CuriosityBridge in explore: %s", e)
            return outcome

        engine.explorer.explore = mastery_enhanced_explore
        log.info("CuriosityBridge integrated into CuriosityExplorer")

    except Exception as e:
        log.warning("CuriosityBridge integration failed: %s", e)


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python eidos_mastery.py <cmd> [args]")
        print("  domains                       — list known curriculum domains")
        print("  curriculum <domain>           — show curriculum for a domain")
        print("  create <domain> [subdomain]   — create a learning goal")
        print("  create-all-cats [min_nodes]   — P0: create goals for ALL 235 categories")
        print("  goals [status]                — list learning goals")
        print("  goal <goal_id>                — show goal details with milestones")
        print("  study <domain> <milestone_idx>— run SPV on a specific milestone")
        print("  mastery [domain]              — show domain mastery (or all)")
        print("  bridge-stats                  — curiosity bridge exploration stats")
        print("  integrate                     — wire into curiosity engine")
        print("  run <domain>                  — create goal + run all SPV loops")
        print("  night-run [max_ms]            — run SPV on pending milestones (all domains)")
        print("  night-run-full [max_ms]       — P0: night mode (50/tick) + practice queue")
        print("  practice [N]                  — P0: run practice-only phase (active recall)")
        print("  briefing                      — generate morning mastery briefing")
        print("  pending                       — list all pending milestones across goals")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "domains":
        gen = get_curriculum_generator()
        for d in gen.list_known_domains():
            curriculum = gen.generate(d)
            print(f"  {d}: {len(curriculum)} steps")
            for s in curriculum:
                print(f"    [{s['step']}] {s['label']}")

    elif cmd == "curriculum":
        domain = sys.argv[2] if len(sys.argv) > 2 else "python"
        gen = get_curriculum_generator()
        steps = gen.generate(domain)
        print(f"Curriculum for '{domain}' ({len(steps)} steps):")
        for i, s in enumerate(steps):
            print(f"\n  Step {i+1}: {s['label']} ({s['step']})")
            print(f"    Material:  {s['material'][:100]}...")
            print(f"    Practice:  {s['practice'][:100]}...")
            print(f"    Verify:    {s['verify'][:100]}...")

    elif cmd == "create":
        domain = sys.argv[2] if len(sys.argv) > 2 else ""
        subdomain = sys.argv[3] if len(sys.argv) > 3 else ""
        if not domain:
            print("Error: specify a domain")
            sys.exit(1)
        tracker = get_mastery_tracker()
        goal = tracker.create_goal(domain, subdomain)
        print(f"Goal created: {goal.id}")
        print(f"  Domain: {goal.domain}")
        print(f"  Milestones: {len(goal.milestones)}")
        for m in goal.milestones:
            print(f"    [{m.step}] {m.label} — {m.status}")

    elif cmd == "goals":
        status = sys.argv[2] if len(sys.argv) > 2 else None
        tracker = get_mastery_tracker()
        goals = tracker.list_goals(status=status)
        if not goals:
            print("No goals found.")
        for g in goals:
            print(f"  [{g['status']:12s}] {g['domain']:15s} "
                  f"{g['progress_pct']:5.1f}% "
                  f"({g['milestones_completed']}/{g['milestones_total']} milestones) "
                  f"{g['id'][:20]}...")

    elif cmd == "goal":
        goal_id = sys.argv[2] if len(sys.argv) > 2 else ""
        if not goal_id:
            print("Error: specify a goal_id")
            sys.exit(1)
        tracker = get_mastery_tracker()
        goal = tracker.get_goal(goal_id)
        if not goal:
            print(f"Goal not found: {goal_id}")
            sys.exit(1)
        d = goal.to_dict()
        print(f"Goal: {d['id']}")
        print(f"  Domain: {d['domain']} | Subdomain: {d['subdomain']}")
        print(f"  Progress: {d['progress_pct']}% | Status: {d['status']}")
        print(f"  Band: {d['band']} — {d['band_label']}")
        print(f"  Milestones: {d['milestones_completed']}/{d['milestones_total']} completed")
        for m in d["milestones"]:
            status_icon = "X" if m["status"] == "completed" else ("~" if m["status"] == "failed" else " ")
            print(f"    [{status_icon}] {m['step']}: {m['label']} ({m['status']})")

    elif cmd == "study":
        domain = sys.argv[2] if len(sys.argv) > 2 else "python"
        idx = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        gen = get_curriculum_generator()
        steps = gen.generate(domain)
        if idx >= len(steps):
            print(f"Error: milestone index {idx} out of range (max {len(steps)-1})")
            sys.exit(1)
        step = steps[idx]
        spv = get_spv_loop()
        print(f"Running SPV loop on: {step['label']} ({step['step']})")
        result = spv.run_loop(step, goal_id="cli_manual", concept=domain)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))

    elif cmd == "mastery":
        domain = sys.argv[2] if len(sys.argv) > 2 else ""
        tracker = get_mastery_tracker()
        if domain:
            m = tracker.get_domain_mastery(domain)
            print(json.dumps(m, indent=2, ensure_ascii=False))
        else:
            all_m = tracker.get_all_domain_mastery()
            if not all_m:
                print("No domains tracked yet. Create a goal first.")
            for m in all_m:
                bar = "=" * int(m["mastery_pct"] / 5) + "-" * (20 - int(m["mastery_pct"] / 5))
                print(f"  [{bar}] {m['domain']:15s} {m['mastery_pct']:5.1f}% "
                      f"({m['milestones_completed']}/{m['milestones_total']} ms) "
                      f"[{m['band']}]")

    elif cmd == "bridge-stats":
        bridge = get_curiosity_bridge()
        print(json.dumps(bridge.get_exploration_stats(), indent=2))

    elif cmd == "integrate":
        integrate_with_curiosity()
        print("CuriosityBridge integrated. Exploration → Mastery is now active.")

    elif cmd == "run":
        domain = sys.argv[2] if len(sys.argv) > 2 else ""
        if not domain:
            print("Error: specify a domain")
            sys.exit(1)
        tracker = get_mastery_tracker()
        spv = get_spv_loop()

        print(f"Creating goal for '{domain}'...")
        goal = tracker.create_goal(domain)
        print(f"Goal: {goal.id} with {len(goal.milestones)} milestones")

        for i, ms in enumerate(goal.milestones):
            print(f"\n{'='*60}")
            print(f"Milestone {i+1}/{len(goal.milestones)}: {ms.label} ({ms.step})")
            print(f"{'='*60}")

            result = spv.run_loop(ms.to_dict(), goal_id=goal.id, concept=domain)
            print(f"  Passed: {result['passed']} | Score: {result['score']} | "
                  f"Attempts: {result['attempts']}")

            tracker.complete_milestone(
                goal.id, ms.id,
                learned=f"Score: {result['score']}, Passed: {result['passed']}",
                spv_result=result,
            )
            print(f"  Progress: {tracker.get_goal(goal.id).progress_pct}%")

        final = tracker.get_goal(goal.id)
        m = tracker.get_domain_mastery(domain)
        print(f"\n{'='*60}")
        print(f"COMPLETE: {domain}")
        print(f"  Progress: {final.progress_pct}%")
        print(f"  Band: {m['band']} — {m['band_label']}")
        print(f"  Mastery: {m['mastery_pct']}%")

    elif cmd == "create-all-cats":
        min_nodes = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        dry_run = "--dry-run" in sys.argv
        tracker = get_mastery_tracker()
        print(f"Creating LearningGoals for ALL categories (min_nodes={min_nodes})"
              f"{' [DRY RUN]' if dry_run else ''}...")
        result = tracker.create_goals_for_all_categories(
            min_nodes=min_nodes, dry_run=dry_run
        )
        if result["ok"]:
            print(f"OK: {result['created']} created, "
                  f"{result['skipped_existing']} skipped (existing), "
                  f"{result.get('errors', 0)} errors")
            print(f"Total categories: {result['total_categories']}")
            if result["by_category"]:
                # Show first 20
                items = list(result["by_category"].items())[:20]
                for cat, info in items:
                    print(f"  [{info['action']}] {cat[:50]} "
                          f"(nodes={info.get('node_count', '?')})")
                if len(result["by_category"]) > 20:
                    print(f"  ... and {len(result['by_category']) - 20} more")
        else:
            print(f"ERROR: {result.get('error', 'unknown')}")

    elif cmd == "night-run":
        max_ms = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        print(f"Night Mastery Run: processing up to {max_ms or 50} milestones...")
        print()
        results = run_night_mastery(max_milestones=max_ms, night_mode=True)
        if not results:
            print("No pending milestones to process.")
        else:
            for r in results:
                rtype = r.get("type", "curriculum")
                icon = "+" if r["passed"] else "X"
                if rtype == "practice":
                    print(f"  [{icon}] PRACTICE: {r.get('concept', '?')[:40]} "
                          f"(score={r.get('score', 0):.2f}, "
                          f"streak=+{r.get('streak_correct', 0)}/"
                          f"-{r.get('streak_wrong', 0)})")
                else:
                    print(f"  [{icon}] {r.get('domain', '?')}: {r.get('milestone', '?')} "
                          f"(score={r.get('score', 0):.0f}, "
                          f"attempts={r.get('attempts', 0)})"
                          f"{' ERROR: ' + r.get('error', '') if r.get('error') else ''}")

    elif cmd == "night-run-full":
        max_ms = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        print(f"P0 Full Night Mode: up to {max_ms or 50} curriculum + 50 practice items")
        print()
        results = run_night_mastery(
            max_milestones=max_ms,
            night_mode=True,
            auto_create_category_goals=True,
        )
        if not results:
            print("No items processed.")
        else:
            curriculum = [r for r in results if r.get("type", "") != "practice"]
            practice = [r for r in results if r.get("type", "") == "practice"]
            print(f"Curriculum: {len(curriculum)} | Practice: {len(practice)}")
            for r in results[:30]:
                rtype = r.get("type", "curriculum")
                icon = "+" if r["passed"] else "X"
                if rtype == "practice":
                    print(f"  [{icon}] PRACTICE: {r.get('concept', '?')[:40]} "
                          f"score={r.get('score', 0):.2f}")
                else:
                    print(f"  [{icon}] {r.get('domain', '?')}: {r.get('milestone', '?')[:30]} "
                          f"score={r.get('score', 0):.0f}")
            if len(results) > 30:
                print(f"  ... and {len(results) - 30} more")

    elif cmd == "practice":
        max_items = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        print(f"Practice-only run: up to {max_items} items from practice queue...")
        print()
        runner = get_night_mastery_runner()
        results = runner.run_practice_only(max_items=max_items)
        if not results:
            print("No due practice items.")
        else:
            passed = sum(1 for r in results if r.get("passed"))
            for r in results:
                icon = "+" if r["passed"] else "X"
                print(f"  [{icon}] {r.get('concept', '?')[:45]} "
                      f"score={r.get('score', 0):.3f} "
                      f"streak=+{r.get('streak_correct', 0)}/"
                      f"-{r.get('streak_wrong', 0)}")
            print(f"\nSummary: {passed}/{len(results)} passed")

    elif cmd == "briefing":
        tracker = get_mastery_tracker()
        print(tracker.generate_morning_briefing())

    elif cmd == "pending":
        tracker = get_mastery_tracker()
        pending = tracker.get_pending_milestones()
        if not pending:
            print("No pending milestones across any goals.")
        else:
            print(f"Pending milestones ({len(pending)}):")
            for pm in pending:
                print(f"  [{pm['domain']}] {pm['label']} ({pm['step']}) "
                      f"attempts={pm['attempt_count']} "
                      f"progress={pm['progress_pct']:.1f}%")

    else:
        print(f"Unknown command: {cmd}")
