"""
EIDOS core/skill_evolver.py — Autonomous Skill Evolution System
================================================================
Genera nuevas skills automáticamente desde interacciones fallidas.
Basado en:
  - MetaClaw: Skill Evolver (genera skills desde fallos)
  - ClosedClaw: Shadow Factory (gap analysis + tool generation)

Proceso:
  1. Detecta fallos en tool calls o tareas
  2. Analiza el patrón del fallo
  3. Genera una nueva skill que resuelve el problema
  4. La almacena para uso futuro

Uso:
    from core.skill_evolver import SkillEvolver

    evolver = SkillEvolver()
    evolver.record_failure("nmap_scan", "timeout en puerto 443", context="scan HTTPS")
    evolver.record_success("nmap_scan", context="scan rápido -T4")

    # Análisis periódico genera skills nuevas
    new_skills = evolver.evolve()
"""
from __future__ import annotations

import json
import time
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from collections import Counter

import logging
log = logging.getLogger("eidos.skill_evolver")

SKILLS_DIR = Path.home() / ".eidos" / "skills" / "evolved"
FAILURES_LOG = Path.home() / ".eidos" / "skill_evolver_failures.jsonl"
EVOLVED_LOG = Path.home() / ".eidos" / "skill_evolver_evolved.jsonl"

# Keywords de categorización (de MetaClaw)
CATEGORY_KEYWORDS = {
    "security": ["vulnerability", "exploit", "auth", "injection", "xss", "csrf",
                 "audit", "penetration", "threat", "scan", "nmap", "metasploit",
                 "brute", "payload", "shell", "privilege", "escalation", "bypass",
                 "firewall", "ids", "waf", "sqli", "rce", "lfi", "rfi", "ssrf"],
    "network": ["port", "tcp", "udp", "dns", "http", "ssl", "tls", "ip",
                "socket", "proxy", "vpn", "routing", "firewall", "packet",
                "wireshark", "tcpdump", "netcat", "ncat"],
    "coding": ["python", "script", "function", "class", "module", "import",
               "error", "exception", "debug", "test", "refactor", "compile",
               "syntax", "runtime", "code", "program"],
    "system": ["process", "service", "daemon", "systemctl", "cron", "permission",
               "filesystem", "disk", "memory", "cpu", "kernel", "driver",
               "package", "install", "configure"],
    "recon": ["osint", "whois", "subdomain", "enum", "discover", "fingerprint",
              "banner", "version", "technology", "cms", "framework"],
    "forensics": ["artifact", "evidence", "timeline", "carve", "recover",
                  "image", "dump", "analyze", "hash", "integrity"],
}


@dataclass
class FailureRecord:
    tool: str
    error: str
    context: str
    timestamp: float = field(default_factory=time.time)
    category: str = ""


@dataclass
class EvolvedSkill:
    name: str
    description: str
    category: str
    trigger: str        # Cuándo activar esta skill
    solution: str       # Qué hacer (comando, script, o patrón)
    source_failures: int  # Cuántos fallos generaron esta skill
    created_at: float = field(default_factory=time.time)


class SkillEvolver:
    """
    Evoluciona skills de EIDOS desde fallos detectados.

    Gap Analysis (Shadow Factory):
      - Analiza patterns de fallos recurrentes
      - Identifica gaps en las capacidades de EIDOS
      - Genera skills que llenan esos gaps
    """

    def __init__(self):
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        self._failures: list[FailureRecord] = []
        self._evolved: list[EvolvedSkill] = []
        self._load_history()

    def _load_history(self):
        """Carga historial de fallos y skills evolucionadas."""
        if FAILURES_LOG.exists():
            try:
                with open(FAILURES_LOG) as f:
                    for line in f:
                        data = json.loads(line.strip())
                        self._failures.append(FailureRecord(**data))
            except Exception:
                pass  # error no crítico, continuar
        if EVOLVED_LOG.exists():
            try:
                with open(EVOLVED_LOG) as f:
                    for line in f:
                        data = json.loads(line.strip())
                        self._evolved.append(EvolvedSkill(**data))
            except Exception:
                pass  # error no crítico, continuar
        log.info(f"🧬 [SkillEvolver] Loaded {len(self._failures)} failures, {len(self._evolved)} evolved skills")

    def _categorize(self, text: str) -> str:
        """Categoriza texto por keywords."""
        text_lower = text.lower()
        scores = {}
        for cat, keywords in CATEGORY_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > 0:
                scores[cat] = score
        return max(scores, key=scores.get) if scores else "general"

    def record_failure(self, tool: str, error: str, context: str = "") -> None:
        """Registra un fallo para análisis posterior."""
        record = FailureRecord(
            tool=tool, error=error, context=context,
            category=self._categorize(f"{tool} {error} {context}")
        )
        self._failures.append(record)

        # Persistir
        try:
            with open(FAILURES_LOG, 'a') as f:
                f.write(json.dumps({
                    "tool": record.tool, "error": record.error,
                    "context": record.context, "timestamp": record.timestamp,
                    "category": record.category,
                }) + "\n")
        except Exception:
            pass  # error no crítico, continuar
    def record_success(self, tool: str, context: str = "") -> None:
        """Registra un éxito (para contrast con fallos)."""
        # Los éxitos reducen la prioridad de evolucionar ese tool
        pass

    def analyze_gaps(self) -> list[dict]:
        """
        Gap Analysis (Shadow Factory pattern).

        Identifica patrones recurrentes de fallos y gaps en capacidades.
        """
        if len(self._failures) < 3:
            return []

        # Contar fallos por tool
        tool_failures = Counter(f.tool for f in self._failures)

        # Contar fallos por categoría
        cat_failures = Counter(f.category for f in self._failures)

        # Encontrar patrones de error recurrentes
        error_patterns = Counter()
        for f in self._failures:
            # Normalizar errores (quitar detalles específicos)
            normalized = f.error.lower()
            for noise in ["0x", "/home/", "/tmp/", "192.168."]:
                if noise in normalized:
                    normalized = normalized.split(noise)[0] + "..."
            error_patterns[normalized[:80]] += 1

        gaps = []
        # Tools que fallan mucho → gap de skill
        for tool, count in tool_failures.most_common(5):
            if count >= 2:
                recent_errors = [f.error for f in self._failures if f.tool == tool][-3:]
                gaps.append({
                    "type": "tool_gap",
                    "tool": tool,
                    "failure_count": count,
                    "recent_errors": recent_errors,
                    "category": self._categorize(tool),
                    "priority": min(count / 2.0, 5.0),
                })

        # Errores recurrentes → patrón a resolver
        for error, count in error_patterns.most_common(3):
            if count >= 3:
                gaps.append({
                    "type": "error_pattern",
                    "pattern": error,
                    "count": count,
                    "priority": min(count / 3.0, 5.0),
                })

        return sorted(gaps, key=lambda g: -g.get("priority", 0))

    def evolve(self) -> list[EvolvedSkill]:
        """
        Genera nuevas skills desde los gaps detectados.

        Sin LLM: usa heurísticas y templates.
        Con LLM: puede generar skills más sofisticadas (futuro).
        """
        gaps = self.analyze_gaps()
        if not gaps:
            return []

        new_skills = []

        for gap in gaps[:3]:  # Max 3 skills por evolución
            if gap["type"] == "tool_gap":
                skill = self._evolve_tool_gap(gap)
            elif gap["type"] == "error_pattern":
                skill = self._evolve_error_pattern(gap)
            else:
                continue

            if skill:
                new_skills.append(skill)
                self._evolved.append(skill)
                self._save_skill(skill)

        return new_skills

    def _evolve_tool_gap(self, gap: dict) -> Optional[EvolvedSkill]:
        """Genera skill para tool gap."""
        tool = gap["tool"]
        errors = gap.get("recent_errors", [])
        category = gap.get("category", "general")

        # Heurísticas basadas en tipo de error
        solution = ""
        trigger = f"when {tool} fails"
        name = f"fix_{tool}_failures"
        desc = f"Auto-generated skill to handle common {tool} failures"

        # Detectar timeout → sugerir flags de velocidad
        if any("timeout" in e.lower() for e in errors):
            solution = f"# Retry con timeout extendido y flags de velocidad\n"
            if "nmap" in tool:
                solution += f"nmap -T4 --max-retries 2 --host-timeout 60s"
            else:
                solution += f"timeout 120 {tool}"
            trigger = f"{tool} timeout"
            name = f"retry_{tool}_timeout"

        # Detectar permission denied → sugerir sudo
        elif any("permission" in e.lower() or "denied" in e.lower() for e in errors):
            solution = f"sudo {tool}"
            trigger = f"{tool} permission denied"
            name = f"sudo_{tool}"

        # Detectar not found → sugerir instalación
        elif any("not found" in e.lower() or "no such" in e.lower() for e in errors):
            solution = f"sudo apt install -y {tool} && {tool}"
            trigger = f"{tool} not found"
            name = f"install_{tool}"

        # Generic fallback
        else:
            solution = f"# Revisar errores comunes de {tool}\n# Últimos errores: {'; '.join(e[:50] for e in errors[:2])}"

        return EvolvedSkill(
            name=name, description=desc, category=category,
            trigger=trigger, solution=solution,
            source_failures=gap["failure_count"],
        )

    def _evolve_error_pattern(self, gap: dict) -> Optional[EvolvedSkill]:
        """Genera skill para error pattern recurrente."""
        pattern = gap["pattern"]

        return EvolvedSkill(
            name=f"handle_{pattern[:20].replace(' ', '_')}",
            description=f"Auto-handler for recurring error: {pattern[:60]}",
            category="general",
            trigger=pattern[:60],
            solution=f"""# Auto-generated handler for pattern: {pattern}
# Generated from {gap['count']} failures
# Suggestion: Add try/except or input validation around:
# {pattern[:200]}
""",
            source_failures=gap["count"],
        )

    def _save_skill(self, skill: EvolvedSkill):
        """Guarda skill evolucionada a disco."""
        # JSONL log
        try:
            with open(EVOLVED_LOG, 'a') as f:
                f.write(json.dumps({
                    "name": skill.name, "description": skill.description,
                    "category": skill.category, "trigger": skill.trigger,
                    "solution": skill.solution, "source_failures": skill.source_failures,
                    "created_at": skill.created_at,
                }) + "\n")
        except Exception:
            pass  # error no crítico, continuar
        # Skill file
        skill_path = SKILLS_DIR / f"{skill.name}.skill"
        try:
            with open(skill_path, 'w') as f:
                f.write(f"# EVOLVED SKILL: {skill.name}\n")
                f.write(f"# Category: {skill.category}\n")
                f.write(f"# Trigger: {skill.trigger}\n")
                f.write(f"# Generated from {skill.source_failures} failures\n")
                f.write(f"# Created: {time.strftime('%Y-%m-%d %H:%M', time.localtime(skill.created_at))}\n")
                f.write(f"# Description: {skill.description}\n\n")
                f.write(skill.solution + "\n")
            log.info(f"🧬 [SkillEvolver] New skill: {skill.name} → {skill_path}")
        except Exception as e:
            log.warning(f"[SkillEvolver] Save failed: {e}")

    @property
    def stats(self) -> dict:
        return {
            "total_failures": len(self._failures),
            "total_evolved": len(self._evolved),
            "categories": dict(Counter(f.category for f in self._failures)),
            "top_failing_tools": dict(Counter(f.tool for f in self._failures).most_common(5)),
        }

    # ── Sesión 49: integración con ReAct + learning loop tipo Hermes ────────

    def learn_from_failure(self, task: str, error: str) -> None:
        """Registra un fallo de ReAct para evolucionar skills que lo eviten."""
        import time as _t
        rec = FailureRecord(
            task=task,
            error=error,
            tool="react_engine",
            category="general",
            timestamp=_t.time(),
        )
        self._failures.append(rec)
        try:
            with open(FAILURES_LOG, "a") as f:
                f.write(json.dumps(rec.__dict__) + "\n")
        except Exception:
            pass

    def learn_from_success(self, task: str, result: str) -> None:
        """Guarda un éxito notable como candidato a skill futura."""
        import time as _t
        # Añadir al brain como insight de alta confianza
        try:
            import sqlite3 as _sq
            conn = _sq.connect(str(Path.home() / ".eidos" / "evolution_brain.db"), timeout=4)
            concept = f"EIDOS éxito: {task[:60]}"
            ex = conn.execute("SELECT 1 FROM knowledge_nodes WHERE concept=?", (concept,)).fetchone()
            if not ex:
                conn.execute(
                    "INSERT INTO knowledge_nodes (concept,definition,category,confidence,source,created_at) VALUES (?,?,?,?,?,?)",
                    (concept, result[:600], "insight", 0.92, "react_success", _t.time())
                )
                conn.commit()
            conn.close()
        except Exception:
            pass

    def evolve_cycle(self, limit: int = 5) -> int:
        """Genera skills nuevas a partir de los últimos fallos acumulados."""
        new_skills = self.evolve()
        return len(new_skills)


# ═══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

_evolver: Optional[SkillEvolver] = None

def get_skill_evolver() -> SkillEvolver:
    global _evolver
    if _evolver is None:
        _evolver = SkillEvolver()
    return _evolver
