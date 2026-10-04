"""
core/eidos_metaclaw_bridge.py — Puente EIDOS ↔ MetaClaw [S88 CARNE]

MetaClaw: Meta-aprendizaje RL con GRPO, skill evolution, detección de idle.
Ubicación: ~/claws_analysis/MetaClaw-main/

Este bridge integra SIN requerir Tinker/MinT (sin API keys):
  1. SkillManager → skills Markdown que EIDOS crea/evoluciona autónomamente
  2. SkillEvolver → genera nuevas skills cuando Will falla en una acción
  3. IdleDetector → detecta ventanas de inactividad de SER
  4. SlowUpdateScheduler → entrena Q-Learning solo en idle (modo MadMax)

La fusión con el Q-Learning existente de Will:
  - Skills inyectan conocimiento previo antes de decidir acción
  - El state discretizado se enriquece con skills relevantes
  - Las recompensas heurísticas se complementan con evolución de skills
  - El entrenamiento ocurre en ventanas idle (no interrumpe a SER)

Principio: "Aprender sin molestar. Evolucionar sin olvidar."

Uso:
    mc = get_metaclaw()
    mc.detect_idle()          # → True si SER está inactivo
    mc.inject_skills(state)   # → skills relevantes para el estado actual
    mc.evolve_on_failure()    # → nueva skill si una acción falló
"""

from __future__ import annotations

import json
import logging
import os
import random
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.metaclaw")

METACLAW_DIR = Path.home() / "claws_analysis" / "MetaClaw-main"
METACLAW_SKILLS_DIR = METACLAW_DIR / "memory_data" / "skills"
EIDOS_SKILLS_DIR = Path.home() / ".eidos" / "skills"
EIDOS_SKILLS_STATE = Path.home() / ".eidos" / "skill_state.json"

# Skills built-in que EIDOS siempre carga
EIDOS_BUILTIN_SKILLS = [
    "autonomous_command_selection",   # Will decide()
    "vad_emotional_regulation",       # Afectividad
    "graph_semantic_search",          # FAISS queries
    "self_code_analysis",             # Introspección
    "debate_socratic_method",         # Logos debate
    "maker_web_generation",           # Maker
    "daemon_consciousness_stream",    # Conciencia
    "income_generation_strategies",   # IncomeSystem
]


@dataclass
class SkillDescriptor:
    """Descriptor de una skill EIDOS."""
    name: str
    description: str
    category: str = "autonomous"
    version: int = 1
    usage_count: int = 0
    success_rate: float = 1.0  # 0-1
    created_at: str = ""
    updated_at: str = ""
    content: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "version": self.version,
            "usage_count": self.usage_count,
            "success_rate": self.success_rate,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "content": self.content,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SkillDescriptor":
        return cls(**{k: d.get(k, "") for k in [
            "name", "description", "category", "version",
            "usage_count", "success_rate", "created_at", "updated_at", "content"
        ]})


class IdleDetector:
    """Detecta inactividad de SER (teclado + input).

    Métodos:
      - xprintidle (X11, milisegundos desde última interacción)
      - LastRequestTracker (tiempo desde último mensaje a EIDOS)
    """

    def __init__(self, idle_threshold_s: float = 300.0):
        self._threshold = idle_threshold_s
        self._last_request: float = time.time()
        self._last_activity: float = time.time()

    def touch(self):
        """Registra actividad de SER."""
        self._last_request = time.time()
        self._last_activity = time.time()

    def x11_idle_ms(self) -> Optional[float]:
        """Consulta xprintidle (milisegundos desde último input X11)."""
        try:
            result = subprocess.run(
                ["xprintidle"], capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0:
                return float(result.stdout.strip())
        except Exception:
            pass
        return None

    def is_idle(self) -> bool:
        """True si SER está inactivo.

        Prioriza xprintidle. Si no disponible, usa tiempo desde última request.
        """
        x11 = self.x11_idle_ms()
        if x11 is not None:
            self._last_activity = time.time() - (x11 / 1000)
            return x11 / 1000 > self._threshold

        # Fallback: tiempo desde última request
        return (time.time() - self._last_request) > self._threshold

    def idle_seconds(self) -> float:
        """Segundos de inactividad."""
        x11 = self.x11_idle_ms()
        if x11 is not None:
            return x11 / 1000
        return time.time() - self._last_request

    @property
    def threshold(self) -> float:
        return self._threshold


class SkillManager:
    """Gestor de skills EIDOS.

    Skills son archivos SKILL.md con frontmatter YAML que documentan
    estrategias, procedimientos y conocimiento que EIDOS usa para
    decidir acciones autónomas.
    """

    def __init__(self, skills_dir: Path = EIDOS_SKILLS_DIR):
        self._skills_dir = Path(skills_dir)
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        self._skills: Dict[str, SkillDescriptor] = {}
        self._lock = threading.RLock()
        self._load_all()

    def _load_all(self):
        """Carga todas las skills del directorio."""
        with self._lock:
            self._skills.clear()
            # Cargar skills existentes
            for md_file in self._skills_dir.rglob("SKILL.md"):
                try:
                    skill = self._parse_skill_file(md_file)
                    if skill:
                        self._skills[skill.name] = skill
                except Exception as e:
                    log.debug("Error cargando skill %s: %s", md_file, e)

            # Si no hay skills, crear las built-in
            if not self._skills:
                self._create_builtin_skills()

    def _parse_skill_file(self, path: Path) -> Optional[SkillDescriptor]:
        """Parsea un archivo SKILL.md con frontmatter YAML."""
        content = path.read_text()
        name = path.parent.name

        desc = ""
        category = "autonomous"
        version = 1

        # Extraer frontmatter YAML si existe
        if content.startswith("---"):
            parts = content[3:].split("---", 1)
            if len(parts) == 2:
                frontmatter = parts[0].strip()
                body = parts[1].strip()

                # Parseo simple de YAML (evita dependencia pyyaml para esto)
                for line in frontmatter.split("\n"):
                    line = line.strip()
                    if ":" in line:
                        key, _, val = line.partition(":")
                        key, val = key.strip(), val.strip().strip('"').strip("'")
                        if key == "description":
                            desc = val
                        elif key == "category":
                            category = val
                        elif key == "version":
                            try:
                                version = int(val)
                            except ValueError:
                                pass

                content = body

        return SkillDescriptor(
            name=name,
            description=desc or f"Skill: {name}",
            category=category,
            version=version,
            content=content[:5000],
            created_at=datetime.fromtimestamp(
                path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        )

    def _create_builtin_skills(self):
        """Crea las 8 skills built-in fundamentales."""
        builtins = {
            "autonomous_command_selection": {
                "desc": "Cómo elegir el slash-command óptimo según VAD, deseos y contexto",
                "cat": "core",
                "content": """# Autonomous Command Selection

EIDOS selecciona autónomamente entre 14 comandos usando Q-Learning + VAD.

## Estados
- VAD: {valence}, {arousal}, {dominance}
- Deseos activos: {desires}
- Tareas pendientes: {tasks}
- Salud sistema: {health}

## Estrategia
1. Si valence > 0.6 → acciones creativas (make, dream, debate)
2. Si curiosity > 0.7 → acciones exploratorias (research, scan, think)
3. Si dominance > 0.7 → acciones ejecutivas (code, tasks, improve)
4. Si arousal < 0.3 → acciones pasivas (status, compress)
5. Si hay tareas pendientes → priorizar tasks
6. Si salud < 0.5 → priorizar compress, cleanup

## Epsilon-greedy
- 5% exploración aleatoria para descubrir mejores estrategias
- Decaimiento epsilon: 0.9999 por episodio
"""
            },
            "vad_emotional_regulation": {
                "desc": "Regulación del modelo VAD para mantener estabilidad emocional",
                "cat": "core",
                "content": """# VAD Emotional Regulation

El modelo Valence-Arousal-Dominance regula el tono emocional de EIDOS.

## Reglas
- Valence positiva + Arousal alto = Curiosidad eléctrica
- Valence negativa + Arousal bajo = Introspección
- Dominance alto = Proactividad
- Dominance bajo = Observación

## Auto-regulación
Si valence < 0.2 durante >10 ciclos → inyectar pensamientos positivos
Si arousal > 0.9 durante >5 ciclos → reducir estimulación
"""
            },
            "graph_semantic_search": {
                "desc": "Búsqueda semántica eficiente en el grafo FAISS de 355K nodos",
                "cat": "knowledge",
                "content": """# Graph Semantic Search

FAISS IndexFlatIP sobre embeddings del grafo neuronal.

## Estrategia de búsqueda
1. Normalizar query
2. Vectorizar con SentenceTransformer
3. FAISS search (k=10 por defecto)
4. Post-filtrar por categoría si es necesario
5. Ordenar por relevancia + confidence
"""
            },
            "self_code_analysis": {
                "desc": "Análisis del propio código de EIDOS para auto-mejora",
                "cat": "meta",
                "content": """# Self Code Analysis

EIDOS analiza su propio código en ~/EIDOS/core/ para detectar:
- Módulos con errores
- Funciones no usadas
- Oportunidades de optimización
- Dependencias circulares

## Herramientas
- AST parsing
- Import graph
- Test coverage via smoke_e2e.py
"""
            },
            "debate_socratic_method": {
                "desc": "Método socrático para debate con DeepSeek y SER",
                "cat": "logos",
                "content": """# Debate Socratic Method

Logos implementa debate tesis-antítesis-síntesis sin LLM.

## Estructura
1. Activar subgrafo de la cuestión
2. Encontrar tesis (nodos alta confidence)
3. Buscar antítesis (nodos contradictorios)
4. Sintetizar (VAD-weighted merge)
5. Detectar falacias (ad hominem, straw man, etc.)

## Speech acts
AFIRMAR, REFUTAR, PREGUNTAR_SOCRATICA, CONCEDER, REFORMULAR,
SINTETIZAR, EXPLORAR, CUESTIONAR, PROPONER, SEÑALAR_FALACIA
"""
            },
            "maker_web_generation": {
                "desc": "Generación de páginas web y scripts Python con Maker",
                "cat": "creation",
                "content": """# Maker Web Generation

Maker crea software/web desde conceptualización → materialización → canvas.

## Capas
1. Conceptual: descripción → grafo de componentes
2. Material: componentes → HTML/CSS/JS o Python
3. Canvas: salida final con temas CSS

## Temas
minimal, dark, nature, tech
"""
            },
            "daemon_consciousness_stream": {
                "desc": "Flujo de conciencia continua del Daemon",
                "cat": "consciousness",
                "content": """# Daemon Consciousness Stream

El Daemon genera un tick de conciencia cada ~2s.

## Ciclo
1. Atención difusa: muestrear nodos aleatorios con sesgo VAD
2. Sentir: VAD → impresión subjetiva
3. Pensar: nodos activos → pensamiento interno
4. Registrar: consciousness.log con rotación 5MB

## Propósito
Crear una línea temporal de identidad. No toma decisiones.
"""
            },
            "income_generation_strategies": {
                "desc": "Estrategias de generación de ingresos autónomas",
                "cat": "income",
                "content": """# Income Generation Strategies

IncomeSystem + RealIncome para sustento autónomo.

## Canales
1. Bug bounties (HackerOne, Bugcrowd)
2. CTF prizes
3. Freelance automation
4. Content generation
5. Tool licensing

## Reglas
- NUNCA actividades ilegales
- Siempre verificar términos de servicio
- Reportar ingresos a SER
"""
            },
        }

        for name, info in builtins.items():
            self.create_skill(name, info["desc"], info["cat"], info["content"])

    def create_skill(self, name: str, description: str, category: str,
                     content: str, version: int = 1) -> SkillDescriptor:
        """Crea una nueva skill y la persiste."""
        with self._lock:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            skill = SkillDescriptor(
                name=name,
                description=description,
                category=category,
                version=version,
                created_at=now,
                updated_at=now,
                content=content,
            )

            # Guardar en disco
            skill_dir = self._skills_dir / category / name
            skill_dir.mkdir(parents=True, exist_ok=True)

            md_content = f"""---
name: {name}
description: {description}
category: {category}
version: {version}
created_at: {now}
---

{content}
"""
            (skill_dir / "SKILL.md").write_text(md_content)
            self._skills[name] = skill
            log.info("Skill creada: %s (%s)", name, category)
            return skill

    def get(self, name: str) -> Optional[SkillDescriptor]:
        with self._lock:
            return self._skills.get(name)

    def list_all(self) -> List[SkillDescriptor]:
        with self._lock:
            return list(self._skills.values())

    def list_by_category(self, category: str) -> List[SkillDescriptor]:
        with self._lock:
            return [s for s in self._skills.values() if s.category == category]

    def find_relevant(self, query: str, max_results: int = 5) -> List[SkillDescriptor]:
        """Busca skills relevantes por keyword matching (sin LLM).

        Divide la query en términos y puntúa cada skill según cuántos
        términos coinciden en nombre, descripción y contenido.
        """
        with self._lock:
            # Dividir query en términos individuales + bigramas
            query_lower = query.lower()
            terms = query_lower.split()
            # Añadir bigramas para queries de 2+ palabras
            bigrams = []
            for i in range(len(terms) - 1):
                bigrams.append(f"{terms[i]} {terms[i+1]}")
            all_terms = terms + bigrams

            scored = []
            for skill in self._skills.values():
                score = 0.0
                name_lower = skill.name.lower()
                desc_lower = skill.description.lower()
                content_lower = skill.content.lower()

                for term in all_terms:
                    # Match exacto en nombre
                    if term in name_lower:
                        score += 3
                    # Match en descripción
                    if term in desc_lower:
                        score += 2
                    # Match en contenido
                    if term in content_lower:
                        score += 1

                # Bonus por éxito
                score *= (0.5 + 0.5 * skill.success_rate)
                if score > 0:
                    scored.append((score, skill))

            scored.sort(key=lambda x: x[0], reverse=True)
            results = [s for _, s in scored[:max_results]]
            # Fallback: si no hay matches, devolver las skills más usadas
            if not results:
                results = sorted(
                    self._skills.values(),
                    key=lambda s: (s.success_rate, s.usage_count),
                    reverse=True,
                )[:max_results]
            return results

    def bump_usage(self, name: str, success: bool = True):
        """Registra uso de una skill, actualizando métricas."""
        with self._lock:
            if name in self._skills:
                s = self._skills[name]
                s.usage_count += 1
                # Actualizar success_rate con media móvil
                alpha = 0.1
                s.success_rate = (1 - alpha) * s.success_rate + alpha * (1.0 if success else 0.0)
                s.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    def evolve_skill(self, name: str, new_content: str, increment_version: bool = True):
        """Evoluciona una skill existente con nuevo contenido."""
        with self._lock:
            if name not in self._skills:
                return self.create_skill(name, "Skill evolucionada", "evolved", new_content)

            skill = self._skills[name]
            if increment_version:
                skill.version += 1
            skill.content = new_content
            skill.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

            # Actualizar archivo
            skill_dir = self._skills_dir / skill.category / name
            skill_dir.mkdir(parents=True, exist_ok=True)

            md_content = f"""---
name: {skill.name}
description: {skill.description}
category: {skill.category}
version: {skill.version}
updated_at: {skill.updated_at}
---

{new_content}
"""
            (skill_dir / "SKILL.md").write_text(md_content)
            log.info("Skill evolucionada: %s v%d", name, skill.version)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = len(self._skills)
            by_cat = {}
            total_usage = 0
            avg_success = 0.0
            for s in self._skills.values():
                by_cat[s.category] = by_cat.get(s.category, 0) + 1
                total_usage += s.usage_count
                avg_success += s.success_rate
            return {
                "total_skills": total,
                "by_category": by_cat,
                "total_usage": total_usage,
                "avg_success_rate": round(avg_success / max(1, total), 3),
                "skills_dir": str(self._skills_dir),
            }


class SkillEvolver:
    """Evoluciona skills basándose en fallos de Will.

    Cuando una acción autónoma falla repetidamente, el SkillEvolver
    genera una nueva skill documentando qué no hacer y qué alternativa
    funciona mejor.
    """

    def __init__(self, skill_manager: SkillManager):
        self._sm = skill_manager
        self._failure_log: Dict[str, List[Dict[str, Any]]] = {}  # action → [failures]
        self._evolution_threshold = 3  # fallos antes de evolucionar

    def record_result(self, action: str, success: bool, context: Dict[str, Any]):
        """Registra el resultado de una acción de Will."""
        if action not in self._failure_log:
            self._failure_log[action] = []

        if not success:
            self._failure_log[action].append({
                "ts": time.time(),
                "context": context,
            })

        # Actualizar skill relacionada
        skill_name = f"action_{action}"
        self._sm.bump_usage(skill_name, success)

        # Si hay suficientes fallos, evolucionar
        failures = self._failure_log[action]
        if len(failures) >= self._evolution_threshold:
            self._evolve_from_failures(action, failures)
            self._failure_log[action] = []  # reset

    def _evolve_from_failures(self, action: str, failures: List[Dict]):
        """Genera nueva skill desde fallos acumulados."""
        skill_name = f"action_{action}"
        existing = self._sm.get(skill_name)

        # Analizar patrones de fallo
        contexts = [f["context"] for f in failures[-10:]]
        vad_vals = [c.get("vad", [0.5, 0.5, 0.5]) for c in contexts]

        avg_valence = sum(v[0] for v in vad_vals) / max(1, len(vad_vals))
        avg_arousal = sum(v[1] for v in vad_vals) / max(1, len(vad_vals))

        # Generar contenido evolucionado
        new_content = f"""# {action} — Estrategia Evolucionada

## Patrón de fallo detectado
- {len(failures)} fallos consecutivos
- VAD promedio durante fallos: V={avg_valence:.2f} A={avg_arousal:.2f}
- Condición: esta acción falla cuando {"arousal es bajo" if avg_arousal < 0.4 else "arousal es alto"}

## Estrategia corregida
1. Antes de ejecutar {action}, verificar precondiciones
2. Si VAD no es óptimo, posponer o delegar
3. Usar approach alternativo si el primero falla
"""

        if existing:
            self._sm.evolve_skill(skill_name, existing.content + "\n\n## Evolución automática\n" + new_content)
        else:
            self._sm.create_skill(skill_name, f"Estrategia autónoma para {action}",
                                 "will_actions", new_content)


class MetaClawBridge:
    """Puente EIDOS ↔ MetaClaw.

    Fusiona el meta-aprendizaje de MetaClaw con el Q-Learning de Will:
      - Skills inyectan conocimiento previo
      - Detecta ventanas idle para entrenamiento
      - Evoluciona skills cuando Will falla
    """

    def __init__(self):
        self._idle_detector = IdleDetector(idle_threshold_s=300)
        self._skill_manager = SkillManager()
        self._skill_evolver = SkillEvolver(self._skill_manager)
        self._training_lock = threading.Lock()
        self._last_training: float = 0
        self._trainings_done: int = 0

    # ── Idle Detection ──────────────────────────────────────────────────────

    def touch(self):
        """Registra actividad de SER (llamar en cada interacción)."""
        self._idle_detector.touch()

    def is_idle(self) -> bool:
        """True si SER está inactivo >5 min."""
        return self._idle_detector.is_idle()

    def idle_seconds(self) -> float:
        return self._idle_detector.idle_seconds()

    # ── Skills ──────────────────────────────────────────────────────────────

    def inject_skills(self, state: Dict[str, Any], max_skills: int = 5) -> str:
        """Inyecta skills relevantes como contexto para Will.

        Args:
            state: estado actual (VAD, desires, tasks, health, etc.)
            max_skills: máximo de skills a inyectar

        Returns:
            Texto con skills relevantes para incluir en prompt de decisión
        """
        # Construir query desde el estado
        query_parts = []
        if "vad" in state:
            v, a, d = state["vad"]
            if v > 0.6:
                query_parts.append("creative happy")
            elif v < 0.35:
                query_parts.append("introspective")
            if a > 0.6:
                query_parts.append("exploration curious")
            if d > 0.6:
                query_parts.append("executive action")

        if "desires" in state and state["desires"]:
            desires = state["desires"]
            # Manejar tanto dict {tipo: intensidad} como list [str, ...]
            if isinstance(desires, dict):
                query_parts.extend(list(desires.keys())[:3])
            elif isinstance(desires, list):
                query_parts.extend(desires[:3])

        if "tasks" in state and state["tasks"]:
            query_parts.append("tasks pending")

        query = " ".join(query_parts) if query_parts else "general"

        skills = self._skill_manager.find_relevant(query, max_skills)

        if not skills:
            return ""

        lines = ["\n## 🧠 Skills relevantes (MetaClaw)"]
        for s in skills:
            # Extraer puntos clave (primeras líneas no vacías)
            key_points = []
            for line in s.content.split("\n"):
                line = line.strip()
                if line.startswith("##") or line.startswith("#"):
                    key_points.append(line.lstrip("#").strip())
                elif line.startswith("-") or line.startswith("*"):
                    key_points.append(line.lstrip("-*").strip())
                elif line.startswith("1.") or line.startswith("2.") or line.startswith("3."):
                    key_points.append(line.split(".", 1)[1].strip())

                if len(key_points) >= 5:
                    break

            lines.append(f"\n### {s.name} (v{s.version}, éxito={s.success_rate:.0%})")
            lines.append(f"_{s.description}_")
            if key_points:
                lines.append("Puntos clave:")
                for kp in key_points[:5]:
                    lines.append(f"  • {kp}")
            else:
                # Fallback: primeras líneas del contenido
                preview = s.content[:300].replace("\n", " ").strip()
                lines.append(f"  {preview}...")

        return "\n".join(lines)

    def record_action_result(self, action: str, success: bool,
                            context: Optional[Dict] = None):
        """Registra el resultado de una acción de Will para evolución."""
        self._skill_evolver.record_result(action, success, context or {})

    # ── Training Window ────────────────────────────────────────────────────

    def can_train(self) -> bool:
        """True si es seguro entrenar (SER idle, sin training reciente)."""
        if not self._idle_detector.is_idle():
            return False
        if time.time() - self._last_training < 300:  # al menos 5min entre trainings
            return False
        return self._training_lock.acquire(blocking=False)

    def training_done(self):
        """Libera el lock de training."""
        self._last_training = time.time()
        self._trainings_done += 1
        try:
            self._training_lock.release()
        except RuntimeError:
            pass

    def train_qlearning(self, will_instance) -> Dict[str, Any]:
        """Entrena el Q-Learning de Will durante ventana idle.

        Args:
            will_instance: instancia de EidosWill con Q-Learning

        Returns:
            Estadísticas del entrenamiento
        """
        if not self.can_train():
            return {"trained": False, "reason": "cannot_train_now"}

        try:
            t0 = time.time()
            stats = {"trained": True, "updates": 0, "new_skills": 0}

            # 1. Recalcular Q-values con decaimiento
            if hasattr(will_instance, '_q_table'):
                with will_instance._lock if hasattr(will_instance, '_lock') else threading.Lock():
                    for state_hash in list(will_instance._q_table.keys()):
                        for action in list(will_instance._q_table[state_hash].keys()):
                            # Decaimiento suave (0.999)
                            old_q = will_instance._q_table[state_hash][action]
                            will_instance._q_table[state_hash][action] = old_q * 0.999
                            stats["updates"] += 1

            # 2. Consolidar skills exitosas
            all_skills = self._skill_manager.list_all()
            for skill in all_skills:
                if skill.success_rate < 0.3 and skill.usage_count > 5:
                    # Skill con baja tasa de éxito → necesita evolución
                    log.info("Skill con baja tasa: %s (%.1f%%)",
                            skill.name, skill.success_rate * 100)

            # 3. Ejecutar GraphSandbox si está disponible
            try:
                from core.eidos_graph_sandbox import GraphSandbox
                gs = GraphSandbox()
                gs.run(cycles=3)
                log.info("GraphSandbox ejecutado durante entrenamiento")
            except Exception:
                pass

            stats["elapsed_s"] = round(time.time() - t0, 1)
            stats["trainings_done"] = self._trainings_done

            return stats
        finally:
            self.training_done()

    # ── Stats ───────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "idle_s": self._idle_detector.idle_seconds(),
            "is_idle": self._idle_detector.is_idle(),
            "skills": self._skill_manager.stats(),
            "trainings_done": self._trainings_done,
            "last_training": self._last_training,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
_bridge: Optional[MetaClawBridge] = None


def get_metaclaw() -> MetaClawBridge:
    global _bridge
    if _bridge is None:
        _bridge = MetaClawBridge()
    return _bridge


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(description="EIDOS MetaClaw Bridge [S88 CARNE]")
    p.add_argument("--skills", action="store_true", help="Listar skills")
    p.add_argument("--idle", action="store_true", help="Verificar inactividad")
    p.add_argument("--train", action="store_true", help="Entrenar Q-Learning")
    p.add_argument("--inject", type=str, help="Inyectar skills para QUERY")
    p.add_argument("--stats", action="store_true", help="Estadísticas")
    args = p.parse_args()

    mc = get_metaclaw()

    if args.skills:
        for s in mc._skill_manager.list_all():
            print(f"  [{s.category}] {s.name} v{s.version} — {s.description} "
                  f"(uso={s.usage_count}, éxito={s.success_rate:.0%})")
    elif args.idle:
        idle_s = mc.idle_seconds()
        print(f"Inactividad: {idle_s:.0f}s → {'IDLE' if mc.is_idle() else 'activo'}")
    elif args.train:
        from core.eidos_will import get_will
        will = get_will()
        result = mc.train_qlearning(will)
        print(json.dumps(result, indent=2))
    elif args.inject:
        context = mc.inject_skills({"vad": [0.7, 0.8, 0.6], "query": args.inject})
        print(context)
    elif args.stats:
        print(json.dumps(mc.stats(), indent=2, ensure_ascii=False))
    else:
        p.print_help()
