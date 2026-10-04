"""
core/colony_pc_explorer.py — Exploración del PC por dominio de personaje

Cada personaje de Colony tiene un dominio del PC del que aprende:

  colony_coder    → repos git, pip/npm, archivos .py/.ts/.js
  colony_operator → zsh_history, procesos, puertos, servicios, logs
  colony_analyst  → documentos .md/.pdf/.yaml/.json, configuraciones
  colony_vision   → imágenes, capturas de pantalla, archivos visuales
  colony_ser      → actividad reciente de SER, SER_BRAIN.md, sesiones
  colony_lumen    → evolution_brain.db, broadcasts, learning_log
  colony_general  → exploración aleatoria cruzada

Uso:
    from core.colony_pc_explorer import get_explorer
    exp = get_explorer()
    result = exp.explore("colony_coder")
    print(result.summary)   # resumen del hallazgo
    print(result.learned)   # concepto guardado
"""
from __future__ import annotations

import os
import time
import random
import hashlib
import sqlite3
import logging
import subprocess
import threading
from pathlib import Path

from core.paths import REPO_ROOT
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Callable
from core.db import get_conn

log = logging.getLogger("eidos.pc_explorer")

HOME       = Path.home()
EIDOS_DIR  = REPO_ROOT
BRAIN_DB   = HOME / ".eidos" / "evolution_brain.db"
HISTORY    = HOME / ".zsh_history"
MAX_CHARS  = 1500   # máximo de texto por exploración


@dataclass
class ExplorationResult:
    character:  str
    domain:     str
    summary:    str
    raw:        str = ""
    learned:    str = ""
    elapsed:    float = 0.0


class PCExplorer:
    """Cada personaje explora su dominio del PC y aprende de él."""

    def __init__(self):
        self._domains: Dict[str, Callable[[], ExplorationResult]] = {
            "colony_coder":    self._explore_coder,
            "colony_operator": self._explore_operator,
            "colony_analyst":  self._explore_analyst,
            "colony_vision":   self._explore_vision,
            "colony_ser":      self._explore_ser,
            "colony_lumen":    self._explore_lumen,
            "colony_general":  self._explore_general,
        }

    def explore(self, character: str) -> Optional[ExplorationResult]:
        """Explora el dominio del personaje (PC local + web) y guarda el aprendizaje."""
        fn = self._domains.get(character)
        if not fn:
            return None
        t0 = time.time()
        try:
            result = fn()
            # Enriquecimiento web: Colony aprende también del mundo exterior
            web_knowledge = self._web_enrich(character)
            if web_knowledge:
                result.summary = (result.summary + "\n\n" + web_knowledge)[:MAX_CHARS]
            result.elapsed = time.time() - t0
            if result.summary and len(result.summary) > 20:
                result.learned = self._save_knowledge(character, result)
            log.info("[%s] exploró %s → %d chars", character, result.domain, len(result.summary))
            return result
        except Exception as e:
            log.debug("explore %s falló: %s", character, e)
            return None

    def _web_enrich(self, character: str) -> str:
        """Cada personaje aprende algo del mundo exterior vía APIs gratuitas."""
        try:
            from core.colony_web_learner import get_web_learner
            learner = get_web_learner()

            web_fns = {
                "colony_coder":    [learner.learn_code_patterns, learner.learn_vocabulary,
                                    lambda: learner.github_code("python async patterns")],
                "colony_operator": [learner.learn_docker, learner.learn_linux,
                                    lambda: learner.wikipedia_en("Linux kernel namespaces")],
                "colony_analyst":  [learner.learn_ai_concept,
                                    lambda: learner.wikipedia("análisis de datos"),
                                    lambda: learner.open_library("data analysis")],
                "colony_vision":   [lambda: learner.wikipedia_en("Computer vision"),
                                    lambda: learner.wikipedia("visión por computador"),
                                    lambda: learner.learn_ai_concept()],
                "colony_ser":      [lambda: learner.wikipedia("filosofía de la mente"),
                                    lambda: learner.wikipedia_en("Consciousness philosophy"),
                                    lambda: learner.learn_ai_concept()],
                "colony_lumen":    [learner.learn_ai_concept,
                                    lambda: learner.wikipedia_en("Knowledge representation"),
                                    lambda: learner.wikipedia("síntesis del conocimiento")],
                "colony_general":  [learner.learn_tech_news,
                                    lambda: learner.hackernews(limit=4, story_type="best"),
                                    learner.learn_vocabulary],
            }

            fns = web_fns.get(character, [])
            if not fns:
                return ""

            # Elegir aleatoriamente una de las funciones del dominio
            fn = random.choice(fns)
            result = fn()
            if result and len(result) > 30:
                return f"[Web knowledge]\n{result[:500]}"
        except Exception as e:
            log.debug("web_enrich %s: %s", character, e)
        return ""

    # ── DOMINIOS ────────────────────────────────────────────────────────────

    def _explore_coder(self) -> ExplorationResult:
        """Coder rota entre 4 tipos de exploración en cada llamada."""
        import subprocess as _sp

        mode = int(time.time() / 30) % 4  # cambia cada 30s

        if mode == 0:
            # Librerías Python del HOST (no Docker — mucho más rico)
            try:
                r = _sp.run(["pip3", "list", "--format=columns"],
                            capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    lines = r.stdout.strip().splitlines()
                    sample = random.sample(lines[2:], min(10, len(lines)-2)) if len(lines) > 2 else lines
                    summary = "Librerías Python (host):\n" + "\n".join(sample)
                    return ExplorationResult("colony_coder", "codigo", summary[:MAX_CHARS])
            except Exception:
                pass  # error no crítico, continuar
        elif mode == 1:
            # Git log del repo EIDOS + archivos core/ más recientes
            findings = []
            try:
                r = _sp.run(["git", "-C", str(EIDOS_DIR), "log", "--oneline", "-8"],
                            capture_output=True, text=True, timeout=4)
                if r.returncode == 0 and r.stdout.strip():
                    findings.append(f"EIDOS git log:\n{r.stdout.strip()[:400]}")
            except Exception:
                pass  # error no crítico, continuar
            try:
                py_files = sorted(EIDOS_DIR.glob("core/*.py"),
                                  key=lambda p: p.stat().st_mtime, reverse=True)[:8]
                if py_files:
                    findings.append("core/ más recientes:\n" + "\n".join(p.name for p in py_files))
            except Exception:
                pass  # error no crítico, continuar
            if findings:
                return ExplorationResult("colony_coder", "codigo",
                                         "\n\n".join(findings)[:MAX_CHARS])

        elif mode == 2:
            # Archivos Python recientes del PC completo
            try:
                r = _sp.run(
                    ["find", str(HOME), "-name", "*.py", "-newer",
                     str(HOME / ".eidos" / "evolution_brain.db"),
                     "-not", "-path", "*/.*", "-not", "-path", "*/node_modules/*",
                     "-printf", "%f\\n"],
                    capture_output=True, text=True, timeout=5
                )
                if r.stdout.strip():
                    files = r.stdout.strip().splitlines()[:15]
                    summary = f"Archivos .py modificados recientemente ({len(files)}):\n" + "\n".join(files)
                    return ExplorationResult("colony_coder", "codigo", summary[:MAX_CHARS])
            except Exception:
                pass  # error no crítico, continuar
        else:  # mode == 3
            # Estadísticas de EIDOS: líneas de código, num módulos
            try:
                r = _sp.run(
                    ["find", str(EIDOS_DIR / "core"), "-name", "*.py",
                     "-exec", "wc", "-l", "{}", "+"],
                    capture_output=True, text=True, timeout=5
                )
                if r.stdout.strip():
                    lines = r.stdout.strip().splitlines()
                    total = lines[-1].strip() if lines else "?"
                    sample = random.sample(lines[:-1], min(8, len(lines)-1)) if len(lines) > 1 else lines
                    summary = f"Líneas de código EIDOS ({total} total):\n" + "\n".join(
                        l.strip() for l in sample)
                    return ExplorationResult("colony_coder", "codigo", summary[:MAX_CHARS])
            except Exception:
                pass  # error no crítico, continuar
        return ExplorationResult("colony_coder", "codigo", "Sin hallazgos en dominio código")

    def _explore_operator(self) -> ExplorationResult:
        """Operator explora historial de comandos, procesos, puertos, servicios."""
        findings = []

        # 1. Últimos comandos únicos del historial zsh
        try:
            if HISTORY.exists():
                raw = HISTORY.read_text(errors="ignore")
                # El formato zsh history puede tener prefijos ': timestamp:0;cmd'
                cmds = []
                for line in raw.splitlines()[-200:]:
                    if line.startswith(": "):
                        parts = line.split(";", 1)
                        if len(parts) == 2:
                            cmds.append(parts[1].strip())
                    else:
                        cmds.append(line.strip())
                # Filtrar duplicados y comandos vacíos
                seen = set()
                unique = []
                for c in reversed(cmds):
                    if c and c not in seen and len(c) > 3:
                        seen.add(c)
                        unique.append(c)
                    if len(unique) >= 15:
                        break
                if unique:
                    findings.append("Últimos comandos de SER:\n" + "\n".join(unique[:10]))
        except Exception:
            pass  # error no crítico, continuar
        # Fase 3: usar sandbox unshare de colony_operator para explorar el sistema
        try:
            from core.character_sandbox import get_sandbox
            sb = get_sandbox("colony_operator")

            # 2. Procesos relevantes via sandbox unshare (solo lectura)
            r = sb.run("ps aux --sort=-%cpu 2>/dev/null | grep -E 'python|ollama|docker|node|eidos' | head -6", timeout=5, learn=False)
            if r.success and r.output.strip():
                findings.append("Procesos relevantes:\n" + r.output.strip()[:400])

            # 3. Puertos activos
            r2 = sb.run("ss -tlnp 2>/dev/null | head -10", timeout=4, learn=False)
            if r2.success and r2.output.strip():
                findings.append("Puertos:\n" + r2.output.strip()[:300])

            # 4. Servicios systemd
            r3 = sb.run("systemctl --user list-units --state=running --no-pager 2>/dev/null | head -10", timeout=5, learn=False)
            if r3.success and r3.output.strip():
                findings.append("Servicios activos:\n" + r3.output.strip()[:300])
        except Exception:
            pass  # error no crítico, continuar
        summary = "\n\n".join(findings) if findings else "Sin hallazgos en dominio sistema"
        return ExplorationResult("colony_operator", "sistema", summary[:MAX_CHARS])

    def _explore_analyst(self) -> ExplorationResult:
        """Analyst explora documentos, configs, YAML, JSON del sistema."""
        findings = []

        # 1. Archivos .md recientes en EIDOS
        try:
            mds = sorted(
                list(EIDOS_DIR.glob("*.md")) + list(EIDOS_DIR.glob("**/*.md")),
                key=lambda p: p.stat().st_mtime, reverse=True
            )[:5]
            if mds:
                for md in mds[:2]:
                    content = md.read_text(errors="ignore")[:400]
                    findings.append(f"Doc: {md.name}\n{content}")
        except Exception:
            pass  # error no crítico, continuar
        # 2. Archivos YAML/JSON de configuración
        try:
            configs = []
            for pattern in ["**/*.yaml", "**/*.yml", "**/*.json", "**/*.toml"]:
                configs += list(EIDOS_DIR.glob(pattern))
            if configs:
                cfg = random.choice(configs[:20])
                content = cfg.read_text(errors="ignore")[:500]
                findings.append(f"Config: {cfg.name}\n{content[:300]}")
        except Exception:
            pass  # error no crítico, continuar
        # 3. Sesiones de Claude (si existen) — aprender de conversaciones
        try:
            sessions_dir = HOME / ".eidos" / "sessions"
            if sessions_dir.exists():
                session_files = sorted(
                    sessions_dir.glob("*.md"),
                    key=lambda p: p.stat().st_mtime, reverse=True
                )[:3]
                for sf in session_files[:1]:
                    content = sf.read_text(errors="ignore")[:500]
                    findings.append(f"Sesión: {sf.name}\n{content}")
        except Exception:
            pass  # error no crítico, continuar
        # 4. Estadísticas del brain DB
        try:
            conn = get_conn(BRAIN_DB, timeout=3)
            total = conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
            top = conn.execute(
                "SELECT source, COUNT(*) as cnt FROM knowledge_nodes "
                "GROUP BY source ORDER BY cnt DESC LIMIT 5"
            ).fetchall()
            pass  # S109: get_conn no necesita close()
            stats = f"knowledge_nodes total: {total}\nTop fuentes:\n"
            stats += "\n".join(f"  {r[0]}: {r[1]}" for r in top)
            findings.append(stats)
        except Exception:
            pass  # error no crítico, continuar
        summary = "\n\n".join(findings) if findings else "Sin hallazgos en dominio análisis"
        return ExplorationResult("colony_analyst", "documentos", summary[:MAX_CHARS])

    def _explore_vision(self) -> ExplorationResult:
        """Vision explora imágenes, capturas, archivos visuales del sistema."""
        findings = []

        # 1. Capturas de pantalla recientes
        try:
            pic_dirs = [
                HOME / "Pictures", HOME / "Desktop", HOME / "Imágenes",
                HOME / "Screenshots", Path("/tmp")
            ]
            images = []
            for d in pic_dirs:
                if d.exists():
                    images += list(d.glob("*.png")) + list(d.glob("*.jpg"))
            images = sorted(images, key=lambda p: p.stat().st_mtime, reverse=True)[:5]
            if images:
                names = [f"{p.name} ({p.stat().st_size//1024}KB)" for p in images]
                findings.append("Imágenes recientes:\n" + "\n".join(names))
        except Exception:
            pass  # error no crítico, continuar
        # 2. Archivos SVG/recursos gráficos en EIDOS
        try:
            svgs = list(EIDOS_DIR.glob("**/*.svg")) + list(EIDOS_DIR.glob("**/*.png"))
            if svgs:
                sample = random.sample(svgs, min(5, len(svgs)))
                findings.append("Recursos visuales en EIDOS:\n" + "\n".join(p.name for p in sample))
        except Exception:
            pass  # error no crítico, continuar
        # 3. Disponibilidad de herramientas de visión
        try:
            tools = []
            for cmd in ["ffmpeg", "imagemagick", "scrot", "xwd", "maim"]:
                r = subprocess.run(["which", cmd], capture_output=True, text=True, timeout=2)
                if r.returncode == 0:
                    tools.append(cmd)
            if tools:
                findings.append(f"Herramientas visuales disponibles: {', '.join(tools)}")
        except Exception:
            pass  # error no crítico, continuar
        # 4. Modelos de visión en Ollama
        try:
            r = subprocess.run(
                ["ollama", "list"],
                capture_output=True, text=True, timeout=4
            )
            if r.returncode == 0:
                vision_models = [l for l in r.stdout.splitlines() if "vision" in l.lower() or "llava" in l.lower()]
                if vision_models:
                    findings.append("Modelos de visión en Ollama:\n" + "\n".join(vision_models))
                else:
                    findings.append("Sin modelos de visión en Ollama aún.")
        except Exception:
            pass  # error no crítico, continuar
        summary = "\n\n".join(findings) if findings else "Sin hallazgos en dominio visual"
        return ExplorationResult("colony_vision", "visual", summary[:MAX_CHARS])

    def _explore_ser(self) -> ExplorationResult:
        """SER explora su propia actividad: archivos recientes, SER_BRAIN, conversaciones."""
        findings = []

        # 1. SER_BRAIN.md — la voz de SER
        try:
            brain_file = EIDOS_DIR / "SER_BRAIN.md"
            if brain_file.exists():
                content = brain_file.read_text(errors="ignore")[:600]
                findings.append(f"SER_BRAIN.md:\n{content}")
        except Exception:
            pass  # error no crítico, continuar
        # 2. Archivos modificados recientemente por SER
        try:
            r = subprocess.run(
                ["find", str(EIDOS_DIR), "-newer", str(EIDOS_DIR / "eidos"),
                 "-name", "*.py", "-not", "-path", "*/__pycache__/*"],
                capture_output=True, text=True, timeout=5
            )
            if r.stdout.strip():
                lines = r.stdout.strip().splitlines()[:8]
                findings.append("Archivos EIDOS modificados recientemente:\n" + "\n".join(
                    Path(l).name for l in lines
                ))
        except Exception:
            pass  # error no crítico, continuar
        # 3. Diario de aprendizaje
        try:
            log_file = HOME / ".eidos" / "learning_log.md"
            if log_file.exists():
                content = log_file.read_text(errors="ignore")
                last_lines = content.splitlines()[-15:]
                findings.append("Diario de aprendizaje (últimas líneas):\n" + "\n".join(last_lines))
        except Exception:
            pass  # error no crítico, continuar
        # 4. Pulso de Colony
        try:
            from core.colony_broadcast import get_broadcast
            pulse = get_broadcast().get_colony_pulse(last_minutes=60)
            total = pulse.get("total_messages", 0)
            per   = pulse.get("per_character", {})
            summary_parts = [f"Colony mensajes última hora: {total}"]
            for char, info in per.items():
                name = char.replace("colony_", "")
                summary_parts.append(f"  {name}: {info['count']} msg")
            findings.append("\n".join(summary_parts))
        except Exception:
            pass  # error no crítico, continuar
        summary = "\n\n".join(findings) if findings else "Sin actividad reciente de SER"
        return ExplorationResult("colony_ser", "actividad_ser", summary[:MAX_CHARS])

    def _explore_lumen(self) -> ExplorationResult:
        """Lumen explora el cerebro de Colony: knowledge_nodes, patrones, síntesis."""
        findings = []

        # 1. Nodos de alta confianza recientes
        try:
            conn = get_conn(BRAIN_DB, timeout=3)
            rows = conn.execute(
                "SELECT concept, definition, confidence FROM knowledge_nodes "
                "WHERE confidence > 0.75 ORDER BY last_used DESC LIMIT 8"
            ).fetchall()
            pass  # S109: get_conn no necesita close()
            if rows:
                lines = [f"[{r[2]:.0%}] {r[0][:50]}: {r[1][:80]}" for r in rows]
                findings.append("Conocimiento de alta confianza:\n" + "\n".join(lines))
        except Exception:
            pass  # error no crítico, continuar
        # 2. Patrones de aprendizaje — fuentes más activas
        try:
            conn = get_conn(BRAIN_DB, timeout=3)
            rows = conn.execute(
                "SELECT source, COUNT(*) as cnt, AVG(confidence) as avg_conf "
                "FROM knowledge_nodes GROUP BY source ORDER BY cnt DESC LIMIT 8"
            ).fetchall()
            pass  # S109: get_conn no necesita close()
            if rows:
                lines = [f"  {r[0]}: {r[1]} nodos (conf {r[2]:.0%})" for r in rows]
                findings.append("Fuentes de aprendizaje de Colony:\n" + "\n".join(lines))
        except Exception:
            pass  # error no crítico, continuar
        # 3. Broadcasts recientes — lo que Colony está pensando ahora
        try:
            from core.colony_broadcast import get_broadcast
            msgs = get_broadcast().read_new("colony_lumen", since_seconds=3600)
            if msgs:
                lines = [f"[{m['from_char'].replace('colony_','')}] {m['message'][:80]}" for m in msgs[:6]]
                findings.append("Pensamientos recientes de Colony:\n" + "\n".join(lines))
        except Exception:
            pass  # error no crítico, continuar
        # 4. Síntesis anterior de Lumen
        try:
            conn = get_conn(BRAIN_DB, timeout=3)
            rows = conn.execute(
                "SELECT definition FROM knowledge_nodes "
                "WHERE source='lumen_synthesis' ORDER BY created_at DESC LIMIT 3"
            ).fetchall()
            pass  # S109: get_conn no necesita close()
            if rows:
                findings.append("Mis síntesis anteriores:\n" + "\n".join(r[0][:120] for r in rows))
        except Exception:
            pass  # error no crítico, continuar
        summary = "\n\n".join(findings) if findings else "Sin datos para sintetizar aún"
        return ExplorationResult("colony_lumen", "cerebro_colony", summary[:MAX_CHARS])

    def _explore_general(self) -> ExplorationResult:
        """General hace una exploración aleatoria cruzada del PC."""
        fns = [
            self._explore_coder,
            self._explore_operator,
            self._explore_analyst,
        ]
        fn = random.choice(fns)
        result = fn()
        result.character = "colony_general"
        result.domain = f"general:{result.domain}"
        return result

    # ── PERSISTENCIA ────────────────────────────────────────────────────────

    def _save_knowledge(self, character: str, result: ExplorationResult) -> str:
        """Guarda el hallazgo como knowledge_node."""
        try:
            concept  = f"pc_explorer:{character}:{result.domain}:{result.summary[:40]}"
            node_id  = hashlib.md5(concept.encode()).hexdigest()[:16]
            now      = time.time()
            conn     = get_conn(BRAIN_DB, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_nodes "
                "(id, concept, definition, source, confidence, created_at, last_used, usage_count) "
                "VALUES (?,?,?,?,?,?,?,1)",
                (node_id, concept[:120], result.summary[:500],
                 f"pc_explorer:{character}", 0.7, now, now)
            )
            conn.commit()
            pass  # S109: get_conn no necesita close()
            # Broadcastear el hallazgo a Colony
            try:
                from core.colony_broadcast import get_broadcast
                get_broadcast().broadcast(
                    character,
                    f"[PC] {result.summary[:200]}",
                    msg_type="exploration"
                )
            except Exception:
                pass  # error no crítico, continuar
            return concept
        except Exception as e:
            log.debug("save_knowledge falló: %s", e)
            return ""

    def get_exploration_stats(self) -> Dict:
        """Cuánto ha explorado cada personaje."""
        try:
            conn  = get_conn(BRAIN_DB, timeout=3)
            rows  = conn.execute(
                "SELECT source, COUNT(*) FROM knowledge_nodes "
                "WHERE source LIKE 'pc_explorer:%' GROUP BY source"
            ).fetchall()
            pass  # S109: get_conn no necesita close()
            return {r[0].replace("pc_explorer:", ""): r[1] for r in rows}
        except Exception:
            return {}


# Singleton
_instance: Optional[PCExplorer] = None
_lock     = threading.Lock()


def get_explorer() -> PCExplorer:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = PCExplorer()
    return _instance
