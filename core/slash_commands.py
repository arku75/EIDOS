"""
EIDOS core/slash_commands.py — Sistema de Slash Commands
==========================================================
Comandos estilo CLI para EIDOS con prefijo '/'.

Comandos Disponibles:
  /help       - Muestra ayuda completa
  /config     - Abre TUI de configuración interactiva
  /status     - Muestra estado del sistema
  /skills     - Lista skills aprendidas
  /sandbox    - Estado del sandbox
  /learn      - Ver aprendizajes recientes
  /checkpoint - Gestionar checkpoints
  /phoenix    - Estado de Phoenix Guardian
  /mirror     - Estadísticas de Mirror
  /mode       - Cambiar modo (PLAN/EDIT/PLAN+EDIT)
  /clear      - Limpiar pantalla
  /exit       - Salir de EIDOS

Uso:
    from core.slash_commands import SlashCommandHandler

    handler = SlashCommandHandler()
    result = handler.handle("/config")
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, Callable


# ── Tipos básicos ────────────────────────────────────────────────────────────

@dataclass
class CommandResult:
    """Resultado de ejecutar un slash command."""
    success: bool
    output: str
    action: Optional[str] = None  # open_tui, change_mode, exit, etc.
    data: Optional[Dict[str, Any]] = None


# ── Slash Command Handler ────────────────────────────────────────────────────

class SlashCommandHandler:
    """
    Manejador de slash commands para EIDOS.
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self._commands: Dict[str, Callable] = {
            "help": self._cmd_help,
            "config": self._cmd_config,
            "status": self._cmd_status,
            "skills": self._cmd_skills,
            "sandbox": self._cmd_sandbox,
            "learn": self._cmd_learn,
            "checkpoint": self._cmd_checkpoint,
            "phoenix": self._cmd_phoenix,
            "mirror": self._cmd_mirror,
            "mode": self._cmd_mode,
            "clear": self._cmd_clear,
            "exit": self._cmd_exit,
            "goals": self._cmd_goals,
            "brain": self._cmd_brain,
            "autodidact": self._cmd_autodidact,
            "autonomy": self._cmd_autonomy,
            "telegram": self._cmd_telegram,
            "network": self._cmd_network,
            "alerts": self._cmd_alerts,
            "prioritize": self._cmd_prioritize,
            "services": self._cmd_services,
            "plugins": self._cmd_plugins,
            "docs": self._cmd_docs,
            "schedule": self._cmd_schedule,
            "lang": self._cmd_lang,
            # Session 10: 11 new commands from Claw integration
            "economy": self._cmd_economy,
            "governance": self._cmd_governance,
            "graph": self._cmd_graph,
            "graphify": self._cmd_graphify,
            "healing": self._cmd_healing,
            "router": self._cmd_router,
            "mcp": self._cmd_mcp,
            "decay": self._cmd_decay,
            "compression": self._cmd_compression,
            "dyntools": self._cmd_dyntools,
            "madmax": self._cmd_madmax,
            "oauth": self._cmd_oauth,
            # Session 11: Vision-Learner Bridge + Rust Bridge
            "vlearn": self._cmd_vlearn,
            "rust": self._cmd_rust,
            # Session 12: Colony Engine + Extension Intelligence + IPC Bridge + Wake Word
            "colony": self._cmd_colony,
            "extensions": self._cmd_extensions,
            "ipc": self._cmd_ipc,
            "wake": self._cmd_wake,
            # Session 13: Visual-to-Code + Interleaved Thinking + Agent Swarm + Colony Community (Kimi K2.5 inspired)
            "v2code": self._cmd_v2code,
            "think": self._cmd_think,
            "swarm": self._cmd_swarm,
            "comunidad": self._cmd_comunidad,
            # Session 27: Deep Crawler + DevTools Bridge
            "crawl": self._cmd_crawl,
            "devtools": self._cmd_devtools,
            # Sesión 49+50: Claude Code → EIDOS + Dominios de trabajo
            "compress":  self._cmd_compress,
            "setup":     self._cmd_setup,
            "improve":   self._cmd_improve,
            # Dominios de trabajo (Red/Blue Team, IT, SEO, Marketing, n8n, VM)
            "hack":      self._cmd_hack,
            "scan":      self._cmd_scan,
            "defend":    self._cmd_defend,
            "seo":       self._cmd_seo,
            "workflow":  self._cmd_workflow,
            "earn":      self._cmd_earn,
            "vm":        self._cmd_vm,
            "plan":      self._cmd_plan,
            "research":  self._cmd_research,
            "code":      self._cmd_code,
            "react":     self._cmd_react,
            "tasks":     self._cmd_tasks,
            "task":      self._cmd_task,
            "tools":     self._cmd_tools_list,
            "curiosity": self._cmd_curiosity,
            "sync":      self._cmd_sync,
            "harvest":   self._cmd_harvest,
            "screen":    self._cmd_screen,
            # Sesión 53: Self-review + Binary Ninja
            "review":    self._cmd_review,
            "binja":     self._cmd_binja,
        }

    def is_slash_command(self, text: str) -> bool:
        """Verifica si el texto es un slash command."""
        return text.strip().startswith("/")

    def handle(self, text: str) -> CommandResult:
        """
        Procesa un slash command y retorna el resultado.

        Args:
            text: Texto del comando (ej: "/help" o "/mode PLAN")

        Returns:
            CommandResult con el output y acción
        """
        text = text.strip()

        if not self.is_slash_command(text):
            return CommandResult(
                success=False,
                output="❌ No es un slash command válido"
            )

        # Parsear comando y argumentos
        parts = text[1:].split(maxsplit=1)  # Quitar '/' inicial
        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        # Buscar handler
        handler = self._commands.get(cmd_name)

        if handler is None:
            return CommandResult(
                success=False,
                output=f"❌ Comando desconocido: /{cmd_name}\n\nUsa /help para ver comandos disponibles."
            )

        # Ejecutar comando
        try:
            return handler(args)
        except Exception as e:
            return CommandResult(
                success=False,
                output=f"❌ Error ejecutando /{cmd_name}: {e}"
            )

    # ── Comandos ─────────────────────────────────────────────────────────────

    def _cmd_help(self, args: str) -> CommandResult:
        """Muestra ayuda completa de comandos."""
        help_text = """
╔═══════════════════════════════════════════════════════════════════════╗
║                        EIDOS SLASH COMMANDS                           ║
╚═══════════════════════════════════════════════════════════════════════╝

COMANDOS DISPONIBLES:

  /help              Muestra esta ayuda
  /config            Abre configuración interactiva (TUI)
  /status            Estado completo del sistema
  /skills            Lista de skills aprendidas
  /sandbox           Estado del sandbox de pruebas
  /learn             Aprendizajes recientes del Training System
  /checkpoint        Gestión de checkpoints
  /phoenix           Estado de Phoenix Guardian
  /mirror            Estadísticas de Mirror Guardian
  /mode <modo>       Cambiar modo (PLAN / EDIT / PLAN+EDIT)
  /goals             Goals activos y planes
  /brain             Estado BrainMemory, MetaLearner, Feedback
  /autodidact        Gestión de APIs aprendidas
  /autonomy          Estado autonomía (mood, thoughts, goals)
  /telegram          Control bot Telegram
  /network           Network discovery y hosts
  /alerts            Alertas unificadas del sistema
  /prioritize        Smart ranking de goals (multi-factor)
  /services          Monitor de servicios systemd
  /plugins           Sistema de plugins extensible
  /docs              Aprender de documentos (ePub, PDF, MD)
  /schedule          Programador de tareas internas
  /lang <es|en|pt>   Cambiar idioma de interfaz
  /clear             Limpiar pantalla
  /exit              Salir de EIDOS

ATAJOS DE TECLADO:

  Shift+TAB          Cambiar entre modos (PLAN → EDIT → PLAN+EDIT)
  Ctrl+C             Interrumpir operación actual
  Ctrl+D             Salir de EIDOS

MODOS DE OPERACIÓN:

  PLAN               Solo observar/estudiar - NO modificar nada
  EDIT               Auto-mejora en sandbox únicamente
  PLAN+EDIT          Control completo (modo por defecto)

EJEMPLOS:

  /mode PLAN         Cambiar a modo PLAN (solo lectura)
  /checkpoint list   Listar checkpoints guardados
  /skills python     Buscar skills de Python

EIDOS es un sistema soberano y privado de SER. No distribuir.
"""
        return CommandResult(
            success=True,
            output=help_text
        )

    def _cmd_config(self, args: str) -> CommandResult:
        """Abre el TUI de configuración."""
        return CommandResult(
            success=True,
            output="🎛️  Abriendo configuración interactiva...",
            action="open_tui"
        )

    def _cmd_status(self, args: str) -> CommandResult:
        """Muestra estado completo del sistema."""
        lines = []
        lines.append("╔═══════════════════════════════════════════════════════════════════════╗")
        lines.append("║                         EIDOS SYSTEM STATUS                           ║")
        lines.append("╚═══════════════════════════════════════════════════════════════════════╝\n")

        # 1. Sistema base
        lines.append("🖥️  SISTEMA BASE:")
        try:
            from core.kernel import DeterministicKernel
            lines.append("  ✅ Kernel: Operacional")
            lines.append("  ✅ Tools: Disponibles")
        except Exception as e:
            lines.append(f"  ❌ Kernel: Error - {e}")

        # 2. Guardians
        lines.append("\n🛡️  GUARDIANS:")

        try:
            from core.phoenix import get_phoenix
            phoenix = get_phoenix()
            status = phoenix.status()
            phoenix_active = status["phoenix"]["is_active"]
            resurrections = status["phoenix"]["resurrections"]
            lines.append(f"  {'✅' if phoenix_active else '❌'} Phoenix: {'Activo' if phoenix_active else 'Inactivo'} ({resurrections} resurrecciones)")
        except Exception as e:
            lines.append(f"  ⚠️  Phoenix: Error - {e}")

        try:
            from core.mirror import get_mirror
            mirror = get_mirror()
            stats = mirror.get_validation_stats()
            lines.append(f"  ✅ Mirror: {stats['total']} validaciones ({stats['approval_rate']:.1f}% aprobadas)")
        except Exception as e:
            lines.append(f"  ⚠️  Mirror: Error - {e}")

        # 3. Optimizaciones
        lines.append("\n⚡ OPTIMIZACIONES:")

        try:
            from core.ram_guardian import get_ram_status
            ram = get_ram_status()
            icon = "✅" if ram.percent < 70 else "⚠️" if ram.percent < 85 else "❌"
            lines.append(f"  {icon} RAM: {ram.percent:.1f}% ({ram.available_mb:.0f} MB disponibles)")
        except Exception:
            lines.append("  ⚠️  RAM Guardian: No disponible")

        try:
            from core.smart_cache import smart_cache
            stats = smart_cache.get_stats()
            hit_rate = (stats["hits"] / stats["total"]) * 100 if stats["total"] > 0 else 0
            lines.append(f"  ✅ Cache: {stats['entries']} entradas ({hit_rate:.1f}% hit rate)")
        except Exception:
            lines.append("  ⚠️  Smart Cache: No disponible")

        try:
            from core.trainer import get_trainer
            trainer = get_trainer()
            stats = trainer.get_stats()
            lines.append(f"  ✅ Training: {stats['learnings_count']} learnings guardados")
        except Exception:
            lines.append("  ⚠️  Training System: No disponible")

        # 4. Checkpoints
        lines.append("\n💾 CHECKPOINTS:")

        try:
            from core.checkpoint import list_checkpoints
            checkpoints = list_checkpoints(limit=1)
            if checkpoints:
                latest = checkpoints[0]
                lines.append(f"  ✅ Último checkpoint: {latest['id']}")
                lines.append(f"     Creado: {latest['created_at']}")
                lines.append(f"     Modo: {latest['mode']}")
            else:
                lines.append("  ⚠️  No hay checkpoints guardados")
        except Exception as e:
            lines.append(f"  ⚠️  Checkpoints: Error - {e}")

        # 5. Purple Team Arsenal
        lines.append("\n🔧 PURPLE TEAM:")
        try:
            from core.tools import TOOLS
            tool_count = len(TOOLS)
            lines.append(f"  ✅ Tools disponibles: {tool_count}")
        except Exception:
            lines.append("  ⚠️  Arsenal: No disponible")

        lines.append("\n" + "═" * 73)
        lines.append(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        return CommandResult(
            success=True,
            output="\n".join(lines)
        )

    def _cmd_skills(self, args: str) -> CommandResult:
        """Lista skills aprendidas."""
        try:
            from core.trainer import get_trainer
            trainer = get_trainer()
            learnings = trainer.learnings

            if not learnings:
                return CommandResult(
                    success=True,
                    output="📚 No hay skills aprendidas todavía.\n\nEIDOS aprenderá automáticamente de cada acción ejecutada."
                )

            # Filtrar por búsqueda si hay args
            if args:
                filtered = [l for l in learnings if args.lower() in str(l).lower()]
            else:
                filtered = learnings

            lines = []
            lines.append("╔═══════════════════════════════════════════════════════════════════════╗")
            lines.append("║                         SKILLS APRENDIDAS                             ║")
            lines.append("╚═══════════════════════════════════════════════════════════════════════╝\n")

            for i, learning in enumerate(filtered[:20], 1):
                pattern = learning.get("pattern", "N/A")
                recommendation = learning.get("recommendation", "N/A")
                confidence = learning.get("confidence", 0.0)

                lines.append(f"{i}. 📖 {pattern}")
                lines.append(f"   💡 {recommendation}")
                lines.append(f"   🎯 Confianza: {confidence:.1%}\n")

            if len(filtered) > 20:
                lines.append(f"... y {len(filtered) - 20} más")

            lines.append(f"\nTotal: {len(filtered)} skills")

            if args:
                lines.append(f"Filtrado por: '{args}'")

            return CommandResult(
                success=True,
                output="\n".join(lines)
            )

        except Exception as e:
            return CommandResult(
                success=False,
                output=f"❌ Error obteniendo skills: {e}"
            )

    def _cmd_sandbox(self, args: str) -> CommandResult:
        """Estado del sandbox."""
        try:
            from pathlib import Path

            sandbox_dir = Path.home() / ".eidos" / "mirror" / "sandbox"

            if not sandbox_dir.exists():
                return CommandResult(
                    success=True,
                    output="📦 Sandbox: No hay sandboxes activos"
                )

            sandboxes = list(sandbox_dir.glob("sandbox_*"))

            lines = []
            lines.append("╔═══════════════════════════════════════════════════════════════════════╗")
            lines.append("║                          SANDBOX STATUS                               ║")
            lines.append("╚═══════════════════════════════════════════════════════════════════════╝\n")

            if not sandboxes:
                lines.append("📦 No hay sandboxes activos")
            else:
                lines.append(f"📦 Sandboxes activos: {len(sandboxes)}\n")

                for sb in sandboxes[:10]:
                    size_mb = sum(f.stat().st_size for f in sb.rglob("*") if f.is_file()) / (1024 * 1024)
                    lines.append(f"  • {sb.name} ({size_mb:.1f} MB)")

            return CommandResult(
                success=True,
                output="\n".join(lines)
            )

        except Exception as e:
            return CommandResult(
                success=False,
                output=f"❌ Error: {e}"
            )

    def _cmd_learn(self, args: str) -> CommandResult:
        """Ver aprendizajes recientes."""
        try:
            from core.trainer import get_trainer
            trainer = get_trainer()
            stats = trainer.get_stats()

            recent = stats.get("recent_learnings", [])[:10]

            lines = []
            lines.append("╔═══════════════════════════════════════════════════════════════════════╗")
            lines.append("║                      APRENDIZAJES RECIENTES                           ║")
            lines.append("╚═══════════════════════════════════════════════════════════════════════╝\n")

            if not recent:
                lines.append("🎓 No hay aprendizajes recientes")
            else:
                for i, learning in enumerate(recent, 1):
                    pattern = learning.get("pattern", "N/A")
                    recommendation = learning.get("recommendation", "N/A")
                    lines.append(f"{i}. {pattern}")
                    lines.append(f"   → {recommendation}\n")

            lines.append(f"Total learnings: {stats.get('learnings_count', 0)}")

            return CommandResult(
                success=True,
                output="\n".join(lines)
            )

        except Exception as e:
            return CommandResult(
                success=False,
                output=f"❌ Error: {e}"
            )

    def _cmd_checkpoint(self, args: str) -> CommandResult:
        """Gestión de checkpoints."""
        try:
            from core.checkpoint import list_checkpoints, load_latest_checkpoint

            if not args or args == "list":
                # Listar checkpoints
                checkpoints = list_checkpoints(limit=10)

                lines = []
                lines.append("╔═══════════════════════════════════════════════════════════════════════╗")
                lines.append("║                            CHECKPOINTS                                ║")
                lines.append("╚═══════════════════════════════════════════════════════════════════════╝\n")

                if not checkpoints:
                    lines.append("💾 No hay checkpoints guardados")
                else:
                    for cp in checkpoints:
                        lines.append(f"📁 {cp['id']}")
                        lines.append(f"   Creado: {cp['created_at']}")
                        lines.append(f"   Modo: {cp['mode']} | Tarea: {cp['task'] or 'N/A'}")
                        lines.append(f"   Size: {cp['size_mb']:.2f} MB | Mensajes: {cp['messages']}\n")

                return CommandResult(
                    success=True,
                    output="\n".join(lines)
                )

            elif args == "latest":
                # Mostrar último checkpoint
                cp = load_latest_checkpoint()

                if not cp:
                    return CommandResult(
                        success=True,
                        output="💾 No hay checkpoints guardados"
                    )

                lines = []
                lines.append(f"📁 Último checkpoint: {cp.id}")
                lines.append(f"   Creado: {datetime.fromtimestamp(cp.created_at)}")
                lines.append(f"   Modo: {cp.mode}")
                lines.append(f"   Tarea: {cp.current_task or 'N/A'}")
                lines.append(f"   Mensajes: {len(cp.conversation_history)}")
                lines.append(f"   Skills: {len(cp.learned_skills)}")

                return CommandResult(
                    success=True,
                    output="\n".join(lines)
                )

            else:
                return CommandResult(
                    success=False,
                    output=f"❌ Subcomando desconocido: {args}\n\nUso: /checkpoint [list|latest]"
                )

        except Exception as e:
            return CommandResult(
                success=False,
                output=f"❌ Error: {e}"
            )

    def _cmd_phoenix(self, args: str) -> CommandResult:
        """Estado de Phoenix Guardian."""
        try:
            from core.phoenix import get_phoenix
            phoenix = get_phoenix()
            status = phoenix.status()

            lines = []
            lines.append("╔═══════════════════════════════════════════════════════════════════════╗")
            lines.append("║                      🔥 PHOENIX GUARDIAN                              ║")
            lines.append("╚═══════════════════════════════════════════════════════════════════════╝\n")

            p = status["phoenix"]
            lines.append("Phoenix Status:")
            lines.append(f"  Active:          {'✅ YES' if p['is_active'] else '❌ NO'}")
            lines.append(f"  Started:         {p['started_at']}")
            lines.append(f"  Resurrections:   {p['resurrections']}")
            lines.append(f"  Last Resurr:     {p['last_resurrection'] or 'N/A'}")
            lines.append(f"  Daemon PID:      {p['daemon_pid'] or 'N/A'}")

            e = status.get("eidos")
            if e:
                lines.append("\nEIDOS Status:")
                lines.append(f"  Alive:           {'✅ YES' if e['is_alive'] else '❌ NO'}")
                lines.append(f"  Last Heartbeat:  {e['last_heartbeat']}")
                lines.append(f"  Mode:            {e['mode']}")
                lines.append(f"  Task:            {e['task'] or 'N/A'}")

            return CommandResult(
                success=True,
                output="\n".join(lines)
            )

        except Exception as e:
            return CommandResult(
                success=False,
                output=f"❌ Error: {e}"
            )

    def _cmd_mirror(self, args: str) -> CommandResult:
        """Estadísticas de Mirror Guardian."""
        try:
            from core.mirror import get_mirror
            mirror = get_mirror()
            stats = mirror.get_validation_stats()

            lines = []
            lines.append("╔═══════════════════════════════════════════════════════════════════════╗")
            lines.append("║                      🪞 MIRROR GUARDIAN                               ║")
            lines.append("╚═══════════════════════════════════════════════════════════════════════╝\n")

            lines.append("📊 Estadísticas de Validación:")
            lines.append(f"  Total:           {stats['total']}")
            lines.append(f"  Aprobadas:       {stats['approved']}")
            lines.append(f"  Rechazadas:      {stats['rejected']}")
            lines.append(f"  Tasa aprobación: {stats['approval_rate']:.1f}%")

            return CommandResult(
                success=True,
                output="\n".join(lines)
            )

        except Exception as e:
            return CommandResult(
                success=False,
                output=f"❌ Error: {e}"
            )

    def _cmd_mode(self, args: str) -> CommandResult:
        """Cambiar modo de operación."""
        valid_modes = ["PLAN", "EDIT", "PLAN+EDIT"]

        if not args:
            return CommandResult(
                success=False,
                output=f"❌ Especifica un modo: {', '.join(valid_modes)}\n\nEjemplo: /mode PLAN"
            )

        mode = args.upper()

        if mode not in valid_modes:
            return CommandResult(
                success=False,
                output=f"❌ Modo inválido: {mode}\n\nModos válidos: {', '.join(valid_modes)}"
            )

        return CommandResult(
            success=True,
            output=f"🔄 Cambiando a modo: {mode}",
            action="change_mode",
            data={"mode": mode}
        )

    def _cmd_clear(self, args: str) -> CommandResult:
        """Limpiar pantalla."""
        os.system("clear" if os.name != "nt" else "cls")
        return CommandResult(
            success=True,
            output="",
            action="clear"
        )

    def _cmd_exit(self, args: str) -> CommandResult:
        """Salir de EIDOS."""
        return CommandResult(
            success=True,
            output="👋 Saliendo de EIDOS...",
            action="exit"
        )

    def _cmd_goals(self, args: str) -> CommandResult:
        """Muestra goals activos y planes del GoalEngine."""
        lines = ["╔═══ EIDOS GOALS ═══╗\n"]
        try:
            from core.autonomous import get_autonomous
            auto = get_autonomous()
            goals = auto.get_active_goals(10)
            if goals:
                lines.append("📎 Goals Activos:")
                for g in goals:
                    lines.append(f"  [{g.priority}] {g.title} ({g.status.value})")
                    if g.description:
                        lines.append(f"      {g.description[:60]}")
            else:
                lines.append("  (sin goals activos)")
        except Exception as e:
            lines.append(f"  Error cargando goals: {e}")

        try:
            from core.goal_engine import get_goal_engine
            ge = get_goal_engine()
            plans = ge.get_active_plans()
            if plans:
                lines.append(f"\n📋 Planes activos ({len(plans)}):")
                for p in plans[:3]:
                    lines.append(f"  {p.title} ({p.progress_pct:.0f}%)")
                    nxt = p.next_step()
                    if nxt:
                        lines.append(f"    → Próximo: {nxt.action}")
        except Exception:
            pass  # error no crítico, continuar
        return CommandResult(success=True, output="\n".join(lines))

    def _cmd_brain(self, args: str) -> CommandResult:
        """Muestra estado de la memoria cerebral."""
        lines = ["╔═══ EIDOS BRAIN MEMORY ═══╗\n"]
        try:
            from core.brain_memory import get_brain_memory
            bm = get_brain_memory()
            s = bm.stats
            lines.append(f"🧠 Working Memory: {s['working']['entries']} entries")
            focus = s['working'].get('focus') or s['working'].get('focus_topics')
            if focus:
                lines.append(f"   Focus: {', '.join(focus) if isinstance(focus, list) else str(focus)}")
            lines.append(f"🔮 Semantic Memory: {'Active' if s['semantic'] else 'Disabled'}")
            lines.append(f"📝 Episodic Memory: {'Active' if s['episodic'] else 'Disabled'}")
            lines.append(f"⚙️  Procedural Memory: {'Active' if s['procedural'] else 'Disabled'}")
        except Exception as e:
            lines.append(f"  Error: {e}")

        try:
            from core.meta_learner import get_meta_learner
            ml = get_meta_learner()
            ms = ml.get_stats()
            lines.append(f"\n📚 MetaLearner: {ms['total_interactions']} interactions, {ms['total_lessons']} lessons")
        except Exception:
            pass  # error no crítico, continuar
        try:
            from core.feedback_loop import get_feedback_loop
            fl = get_feedback_loop()
            fs = fl.get_stats()
            lines.append(f"📊 Feedback: {fs['total']} evaluations, avg={fs.get('avg_overall', 0):.1f}")
        except Exception:
            pass  # error no crítico, continuar
        return CommandResult(success=True, output="\n".join(lines))

    def _cmd_autodidact(self, args: str) -> CommandResult:
        """Muestra/aprende librerías. Uso: /autodidact [learn <lib>] [lookup <lib> <query>]"""
        try:
            from core.library_autodidact import get_autodidact
            ad = get_autodidact()
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

        if not args:
            # Mostrar estado
            s = ad.stats
            known = ad.list_known()
            lines = [f"📚 Library Autodidact: {s['libraries']} libs, {s['total_entries']} APIs\n"]
            for lib in known:
                lines.append(f"  {lib['name']:20s} v{lib['version'][:10]:10s} ({lib['entries']} entries)")
            return CommandResult(success=True, output="\n".join(lines))

        parts = args.split(maxsplit=2)
        action = parts[0].lower()

        if action == "learn" and len(parts) >= 2:
            result = ad.learn(parts[1], depth=2)
            return CommandResult(success=True, output=f"Aprendido: {result}")

        elif action == "lookup" and len(parts) >= 3:
            results = ad.lookup(parts[1], parts[2])
            lines = [f"Resultados para '{parts[2]}' en {parts[1]}:"]
            for r in results[:10]:
                lines.append(f"  {r['name']}{r['signature'][:50]}")
                if r['docstring']:
                    lines.append(f"    {r['docstring'][:80]}")
            return CommandResult(success=True, output="\n".join(lines))

        elif action == "summary" and len(parts) >= 2:
            return CommandResult(success=True, output=ad.summarize(parts[1]))

        return CommandResult(success=False, output="Uso: /autodidact [learn <lib>] [lookup <lib> <query>] [summary <lib>]")

    def _cmd_autonomy(self, args: str) -> CommandResult:
        """Muestra estado de autonomía. Uso: /autonomy [thoughts] [goals]"""
        lines = ["╔═══ EIDOS AUTONOMY ═══╗\n"]
        try:
            from core.autonomous import get_autonomous
            auto = get_autonomous()
            lines.append(f"Mood: {auto.mood_badge}")
            lines.append(f"Running: {auto._running}")

            thoughts = auto.get_recent_thoughts(5)
            if thoughts:
                lines.append(f"\n💭 Recent thoughts:")
                for t in thoughts:
                    lines.append(f"  {t[:80]}")

            goals = auto.get_active_goals(5)
            if goals:
                lines.append(f"\n🎯 Active goals ({len(goals)}):")
                for g in goals:
                    lines.append(f"  [{g.priority}] {g.title}")
        except Exception as e:
            lines.append(f"Error: {e}")

        try:
            from core.system_health import get_health_monitor
            hm = get_health_monitor()
            lines.append(f"\n🏥 System: {hm.get_summary_line()}")
        except Exception:
            pass  # error no crítico, continuar
        try:
            from core.proactive import get_proactive_agent
            pa = get_proactive_agent()
            lines.append(f"⚡ Proactive: {pa.stats['total_actions']} actions, threshold={pa.stats['threshold']}")
        except Exception:
            pass  # error no crítico, continuar
        return CommandResult(success=True, output="\n".join(lines))

    def _cmd_telegram(self, args: str) -> CommandResult:
        """Telegram bot control. Uso: /telegram [start|status|stop]"""
        import os
        token = os.environ.get("EIDOS_TELEGRAM_TOKEN", "")
        if not token:
            return CommandResult(success=True, output=(
                "Telegram Bot — NO CONFIGURADO\n\n"
                "Setup:\n"
                "  1. Habla con @BotFather en Telegram, crea un bot\n"
                "  2. export EIDOS_TELEGRAM_TOKEN='tu_token'\n"
                "  3. (Opcional) export EIDOS_TELEGRAM_ALLOWED='tu_chat_id'\n"
                "  4. /telegram start\n\n"
                "Para obtener tu chat_id: envia /start a @userinfobot"
            ))

        sub = args.strip().lower()
        if sub == "start":
            try:
                import subprocess
                subprocess.Popen(
                    ["python3", os.path.expanduser("~/EIDOS/core/telegram_bot.py")],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
                return CommandResult(success=True, output="Telegram bot launched in background")
            except Exception as e:
                return CommandResult(success=False, output=f"Error launching: {e}")

        # Default: show status
        allowed = os.environ.get("EIDOS_TELEGRAM_ALLOWED", "ALL")
        return CommandResult(success=True, output=(
            f"Telegram Bot — CONFIGURADO\n"
            f"  Token: {token[:8]}...{token[-4:]}\n"
            f"  Allowed IDs: {allowed}\n\n"
            f"  /telegram start — Launch bot\n"
            f"  Run standalone: python core/telegram_bot.py"
        ))
    def _cmd_network(self, args: str) -> CommandResult:
        """Network discovery. Uso: /network [scan|hosts|changes|stats]"""
        sub = args.strip().lower()
        try:
            from core.network_discovery import get_network_discovery
            nd = get_network_discovery()
        except Exception as e:
            return CommandResult(success=False, output=f"NetworkDiscovery error: {e}")

        if sub == "scan":
            hosts = nd.scan_network()
            lines = [f"Scan complete: {len(hosts)} hosts found\n"]
            for h in hosts:
                entry = f"  {h.ip:18s}"
                if h.mac:
                    entry += f" {h.mac:18s}"
                if h.hostname:
                    entry += f" {h.hostname}"
                if h.vendor:
                    entry += f" [{h.vendor}]"
                if h.is_new:
                    entry += " [NEW]"
                lines.append(entry)
            return CommandResult(success=True, output="\n".join(lines))

        elif sub == "hosts":
            hosts = nd.get_all_hosts()
            if not hosts:
                return CommandResult(success=True, output="No hosts in database. Run /network scan first.")
            lines = [f"Known hosts ({len(hosts)}):\n"]
            for h in hosts:
                age = time.time() - h.last_seen
                ago = f"{age/3600:.0f}h ago" if age > 3600 else f"{age/60:.0f}m ago"
                entry = f"  {h.ip:18s} {h.mac:18s} last_seen={ago}"
                if h.hostname:
                    entry += f" ({h.hostname})"
                if h.open_ports:
                    entry += f" ports={h.open_ports}"
                lines.append(entry)
            return CommandResult(success=True, output="\n".join(lines))

        elif sub == "changes":
            changes = nd.get_recent_changes(15)
            if not changes:
                return CommandResult(success=True, output="No network changes recorded.")
            lines = ["Recent network changes:\n"]
            for c in changes:
                lines.append(f"  [{c['severity']}] {c['type']}: {c['ip']} — {c['detail'][:60]}")
            return CommandResult(success=True, output="\n".join(lines))

        # Default: stats
        s = nd.stats
        lines = [
            "Network Discovery",
            f"  Local IP:  {s.get('local_ip', 'N/A')}",
            f"  Gateway:   {s.get('gateway', 'N/A')}",
            f"  Networks:  {s.get('networks', [])}",
            f"  Hosts DB:  {s['total_hosts']}",
            f"  Changes:   {s['total_changes']}",
            f"  Scans:     {s['total_scans']}",
            f"  Running:   {s['running']}",
        ]
        if "last_scan" in s:
            ls = s["last_scan"]
            lines.append(f"  Last scan: {ls['type']} — {ls['hosts']} hosts in {ls['duration']} at {ls['when']}")
        lines.append(f"\n  /network scan     Scan network now")
        lines.append(f"  /network hosts    Show all known hosts")
        lines.append(f"  /network changes  Show recent changes")
        return CommandResult(success=True, output="\n".join(lines))

    def _cmd_alerts(self, args: str) -> CommandResult:
        """Unified alerts. Uso: /alerts [high|recent|stats|ack <id>]"""
        try:
            from core.alert_manager import get_alert_manager
            am = get_alert_manager()
        except Exception as e:
            return CommandResult(success=False, output=f"AlertManager error: {e}")

        sub = args.strip().lower()

        if sub.startswith("ack"):
            parts = sub.split()
            if len(parts) >= 2 and parts[1].isdigit():
                ok = am.acknowledge(int(parts[1]))
                return CommandResult(success=ok, output=f"Alert {parts[1]} {'acknowledged' if ok else 'not found'}")
            return CommandResult(success=True, output="Uso: /alerts ack <id>")

        if sub in ("high", "critical"):
            alerts = am.get_recent(20, severity=sub)
        elif sub == "stats":
            s = am.get_stats()
            return CommandResult(success=True, output=(
                f"Alert Stats\n"
                f"  Total: {s['total']} | Unacked: {s['unacknowledged']} | Last 24h: {s['last_24h']}\n"
                f"  By severity: {s['by_severity']}"
            ))
        else:
            alerts = am.get_recent(15)

        if not alerts:
            return CommandResult(success=True, output="No alerts.")

        severity_icons = {"info": ".", "low": ".", "medium": "!", "high": "!!", "critical": "!!!"}
        lines = [f"Alerts ({len(alerts)}):\n"]
        for a in alerts:
            icon = severity_icons.get(a['severity'], '?')
            ack = " [ACK]" if a['ack'] else ""
            ts = time.strftime("%H:%M", time.localtime(a['ts']))
            lines.append(f"  [{icon}] #{a['id']} {ts} [{a['source']}] {a['title']}{ack}")
            if a['detail']:
                lines.append(f"       {a['detail'][:80]}")
        lines.append(f"\n  /alerts stats  /alerts high  /alerts ack <id>")
        return CommandResult(success=True, output="\n".join(lines))

    def _cmd_prioritize(self, args: str) -> CommandResult:
        """Smart goal prioritization. Uso: /prioritize"""
        try:
            from core.autonomous import get_autonomous
            from core.smart_prioritizer import get_prioritizer
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

        core = get_autonomous()
        goals = core.get_active_goals(10)
        if not goals:
            return CommandResult(success=True, output="No active goals to prioritize.")

        sp = get_prioritizer()
        ranked = sp.prioritize(goals)

        lines = [f"Smart Priority Ranking ({len(ranked)} goals):\n"]
        for i, s in enumerate(ranked, 1):
            bar = "█" * int(s.total * 10) + "░" * (10 - int(s.total * 10))
            lines.append(f"  {i}. [{s.total:.3f}] {bar} {s.goal_title}")
            lines.append(f"     U={s.urgency} I={s.impact} E={s.effort} C={s.context} H={s.history} ({s.reason})")

        lines.append(f"\n  Stats: {sp.stats}")
        return CommandResult(success=True, output="\n".join(lines))

    def _cmd_services(self, args: str) -> CommandResult:
        """Systemd service monitor. Uso: /services [check|events|restart <name>]"""
        try:
            from core.service_monitor import get_service_monitor
            sm = get_service_monitor()
        except Exception as e:
            return CommandResult(success=False, output=f"ServiceMonitor error: {e}")

        sub = args.strip().lower()

        if sub.startswith("restart"):
            parts = sub.split()
            if len(parts) >= 2:
                name = parts[1]
                ok = sm.restart_service(name)
                return CommandResult(success=ok, output=f"Restart {name}: {'OK' if ok else 'FAILED'}")
            return CommandResult(success=True, output="Usage: /services restart <name>")

        if sub == "events":
            evts = sm.get_events(limit=15)
            if not evts:
                return CommandResult(success=True, output="No events.")
            lines = ["Service Events:\n"]
            for e in evts:
                ts = time.strftime("%H:%M", time.localtime(e["ts"]))
                lines.append(f"  {ts} [{e['service']}] {e['event']} {e['old']}->{e['new']}")
            return CommandResult(success=True, output="\n".join(lines))

        # Default: check all
        statuses = sm.check_all()
        lines = ["Service Status:\n"]
        for s in statuses:
            icon = "[OK]" if s.active else "[--]"
            mem = f"{s.memory_mb:.1f}MB" if s.memory_mb else "n/a"
            up = f"{s.uptime_s/3600:.1f}h" if s.uptime_s else "down"
            lines.append(f"  {icon} {s.name:<22} {s.state:<10} mem={mem:<10} up={up}")

        st = sm.stats
        lines.append(f"\n  Active: {st['active']}/{st['total']}  Mem: {st['memory_mb']:.1f}MB  Events24h: {st['events_24h']}")
        if st["failed_names"]:
            lines.append(f"  FAILED: {', '.join(st['failed_names'])}")
        lines.append(f"\n  /services events  /services restart <name>")
        return CommandResult(success=True, output="\n".join(lines))

    def _cmd_plugins(self, args: str) -> CommandResult:
        """Plugin system. Uso: /plugins [load|exec <name> <action>|create <name>]"""
        try:
            from core.plugin_system import get_plugin_manager
            pm = get_plugin_manager()
        except Exception as e:
            return CommandResult(success=False, output=f"PluginSystem error: {e}")

        sub = args.strip()

        if sub.startswith("load"):
            n = pm.load_all()
            return CommandResult(success=True, output=f"Loaded {n} plugins from {pm.plugins_dir}")

        if sub.startswith("create"):
            parts = sub.split()
            name = parts[1] if len(parts) >= 2 else "my_plugin"
            result = pm.create_template(name)
            return CommandResult(success=True, output=result)

        if sub.startswith("exec"):
            parts = sub.split()
            if len(parts) >= 2:
                pname = parts[1]
                action = parts[2] if len(parts) >= 3 else "execute"
                try:
                    r = pm.execute(pname, action)
                    return CommandResult(success=True, output=f"Result: {r}")
                except Exception as e:
                    return CommandResult(success=False, output=f"Error: {e}")
            return CommandResult(success=True, output="Usage: /plugins exec <name> [action]")

        # Default: list
        pm.load_all()
        plugins = pm.list_plugins()
        if not plugins:
            return CommandResult(success=True, output=f"No plugins found in {pm.plugins_dir}\n  /plugins create <name>")
        lines = [f"Plugins ({len(plugins)}):\n"]
        for p in plugins:
            status = "[OK]" if p["enabled"] else f"[ERR: {p.get('error', '')}]"
            lines.append(f"  {status} {p['name']} v{p.get('version','?')}: {p.get('description','')}")
            lines.append(f"       actions: {', '.join(p.get('actions', []))}")
        lines.append(f"\n  Dir: {pm.plugins_dir}")
        lines.append(f"  /plugins load  /plugins exec <name> <action>  /plugins create <name>")
        return CommandResult(success=True, output="\n".join(lines))

    def _cmd_docs(self, args: str) -> CommandResult:
        """Document learner. Uso: /docs [learn <path>|search <query>|stats]"""
        try:
            from core.doc_learner import get_doc_learner
            dl = get_doc_learner()
        except Exception as e:
            return CommandResult(success=False, output=f"DocLearner error: {e}")

        sub = args.strip()

        if sub.startswith("learn"):
            parts = sub.split(maxsplit=1)
            path = parts[1] if len(parts) >= 2 else "~/EIDOS/docs"
            import os
            path = os.path.expanduser(path)
            if os.path.isdir(path):
                results = dl.learn_directory(path)
                learned = [r for r in results if not r.get("skipped") and not r.get("error")]
                skipped = [r for r in results if r.get("skipped")]
                return CommandResult(success=True, output=f"Learned {len(learned)} new, {len(skipped)} skipped from {path}")
            elif os.path.isfile(path):
                r = dl.learn_file(path)
                return CommandResult(success=True, output=f"Result: {r}")
            return CommandResult(success=False, output=f"Path not found: {path}")

        if sub.startswith("search"):
            query = sub[6:].strip()
            if not query:
                return CommandResult(success=True, output="Usage: /docs search <query>")
            results = dl.search(query, limit=5)
            if not results:
                return CommandResult(success=True, output=f"No results for: {query}")
            lines = [f"Results for '{query}':\n"]
            for r in results:
                lines.append(f"  [{r['ext']}] {r['filename']} (chunk {r['chunk']})")
                lines.append(f"    {r['content'][:100]}...")
            return CommandResult(success=True, output="\n".join(lines))

        # Default: stats
        s = dl.stats
        lines = [f"Doc Learner Stats:\n"]
        lines.append(f"  Documents: {s['documents']}")
        lines.append(f"  Chunks: {s['chunks']}")
        lines.append(f"  Size: {s['total_mb']} MB")
        if s['by_extension']:
            lines.append(f"  By type: {s['by_extension']}")
        lines.append(f"  Supported: {', '.join(s['supported'])}")

        recent = dl.get_docs(5)
        if recent:
            lines.append(f"\n  Recent docs:")
            for d in recent:
                lines.append(f"    {d['filename']} ({d['ext']}, {d['chunks']} chunks)")
        lines.append(f"\n  /docs learn <path>  /docs search <query>")
        return CommandResult(success=True, output="\n".join(lines))

    def _cmd_schedule(self, args: str) -> CommandResult:
        """Task scheduler. Uso: /schedule [run <name>|add <name> <action> <mins>|remove <name>]"""
        try:
            from core.task_scheduler import get_scheduler, setup_default_tasks
            sched = get_scheduler()
            if not sched.list_tasks():
                setup_default_tasks()
        except Exception as e:
            return CommandResult(success=False, output=f"Scheduler error: {e}")

        sub = args.strip()

        if sub.startswith("run"):
            parts = sub.split()
            if len(parts) >= 2:
                result = sched.run_task(parts[1])
                return CommandResult(success=True, output=f"Run {parts[1]}: {result}")
            return CommandResult(success=True, output="Usage: /schedule run <task_name>")

        if sub.startswith("add"):
            parts = sub.split()
            if len(parts) >= 4:
                name, action, mins = parts[1], parts[2], float(parts[3])
                sched.add(name, action=action, interval_m=mins)
                return CommandResult(success=True, output=f"Added: {name} ({action} every {mins}m)")
            return CommandResult(success=True, output="Usage: /schedule add <name> <action> <interval_min>")

        if sub.startswith("remove"):
            parts = sub.split()
            if len(parts) >= 2:
                ok = sched.remove(parts[1])
                return CommandResult(success=ok, output=f"{'Removed' if ok else 'Not found'}: {parts[1]}")
            return CommandResult(success=True, output="Usage: /schedule remove <name>")

        # Default: list tasks
        tasks = sched.list_tasks()
        if not tasks:
            return CommandResult(success=True, output="No scheduled tasks.\n  /schedule add <name> <action> <mins>")
        lines = [f"Scheduled Tasks ({len(tasks)}):\n"]
        for t in tasks:
            status = "[ON]" if t["enabled"] else "[OFF]"
            err = f" ERR:{t['last_error'][:30]}" if t["last_error"] else ""
            lines.append(f"  {status} {t['name']:<20} {t['action']:<16} every {t['interval_m']}m  "
                         f"runs={t['run_count']}  next={t['next_in_m']}m{err}")
        st = sched.stats
        lines.append(f"\n  Actions: {', '.join(st['actions'])}")
        lines.append(f"  /schedule run <name>  /schedule add <n> <action> <mins>  /schedule remove <n>")
        return CommandResult(success=True, output="\n".join(lines))

    def _cmd_lang(self, args: str) -> CommandResult:
        """Change language. Uso: /lang [es|en|pt]"""
        try:
            from core.i18n import set_lang, get_lang, t, available_langs
        except Exception as e:
            return CommandResult(success=False, output=f"i18n error: {e}")

        lang = args.strip().lower()
        if lang in available_langs():
            set_lang(lang)
            return CommandResult(success=True, output=f"{t('welcome')} (lang={get_lang()})")
        else:
            current = get_lang()
            return CommandResult(success=True, output=(
                f"Current: {current}\n"
                f"Available: {', '.join(available_langs())}\n"
                f"Usage: /lang es|en|pt"
            ))


    # ══════════════════════════════════════════════════════════════════════════
    #  Session 10 — 11 New Commands (Claw Integration)
    # ══════════════════════════════════════════════════════════════════════════

    def _cmd_economy(self, args: str) -> CommandResult:
        """Token Economy. Uso: /economy [status|leaderboard|treasury]"""
        try:
            from core.token_economy import get_economy
            eco = get_economy()
        except Exception as e:
            return CommandResult(success=False, output=f"Token Economy no disponible: {e}")

        sub = args.strip().lower()
        if sub == "leaderboard":
            lb = eco.get_leaderboard(limit=10)
            lines = ["Token Economy — Leaderboard"]
            for i, a in enumerate(lb, 1):
                lines.append(f"  {i}. {a.get('agent_id', '?')} — {a.get('tokens', 0)} tokens")
            return CommandResult(success=True, output="\n".join(lines))
        elif sub == "treasury":
            t = eco.get_treasury()
            return CommandResult(success=True, output=f"Treasury: {t}")
        else:
            st = eco.get_leaderboard(limit=5)
            treasury = eco.get_treasury()
            lines = [f"Token Economy — Treasury: {treasury}", f"Top agents: {len(st)}"]
            for a in st[:5]:
                lines.append(f"  {a.get('agent_id', '?')}: {a.get('tokens', 0)} tokens")
            return CommandResult(success=True, output="\n".join(lines))

    def _cmd_governance(self, args: str) -> CommandResult:
        """Governance. Uso: /governance [status|proposals|constitution]"""
        try:
            from core.governance import GovernanceSystem
            gov = GovernanceSystem()
        except Exception as e:
            return CommandResult(success=False, output=f"Governance no disponible: {e}")

        sub = args.strip().lower()
        if sub == "constitution":
            c = gov.get_constitution()
            return CommandResult(success=True, output=f"Constitution (Tian Dao):\n{c}")
        elif sub == "proposals":
            props = gov.list_proposals()
            lines = [f"Proposals: {len(props)}"]
            for p in props[:10]:
                lines.append(f"  [{p.get('status', '?')}] {p.get('title', '?')}")
            return CommandResult(success=True, output="\n".join(lines))
        else:
            stats = gov.get_stats()
            return CommandResult(success=True, output=f"Governance Stats:\n{stats}")

    def _cmd_graph(self, args: str) -> CommandResult:
        """Knowledge Graph. Uso: /graph [stats|search <term>|neighbors <node>]"""
        try:
            from core.knowledge_graph import get_knowledge_graph
            kg = get_knowledge_graph()
        except Exception as e:
            return CommandResult(success=False, output=f"Knowledge Graph no disponible: {e}")

        parts = args.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else "stats"

        if sub == "stats":
            s = kg.stats()
            return CommandResult(success=True, output=f"Knowledge Graph:\n  Nodes: {s.get('nodes', 0)}\n  Edges: {s.get('edges', 0)}\n  Types: {s.get('node_types', {})}")
        elif sub == "search" and len(parts) > 1:
            results = kg.search(parts[1])
            lines = [f"Search '{parts[1]}': {len(results)} results"]
            for r in results[:10]:
                lines.append(f"  - {r.get('name', '?')} ({r.get('type', '?')})")
            return CommandResult(success=True, output="\n".join(lines))
        elif sub == "neighbors" and len(parts) > 1:
            n = kg.get_neighbors(parts[1], depth=1)
            return CommandResult(success=True, output=f"Neighbors of '{parts[1]}':\n{n}")
        else:
            return CommandResult(success=True, output="/graph stats | search <term> | neighbors <node>")

    def _cmd_graphify(self, args: str) -> CommandResult:
        """Graphify Skill. Uso: /graphify [install|build|query|path|explain|stats|update]"""
        try:
            from core.skills.graphify_skill import (
                get_graphify_skill,
                cmd_graphify_install,
                cmd_graphify_build,
                cmd_graphify_query,
                cmd_graphify_path,
                cmd_graphify_explain,
                cmd_graphify_stats,
                cmd_graphify_update,
            )
            skill = get_graphify_skill()
        except Exception as e:
            return CommandResult(success=False, output=f"Graphify no disponible: {e}")

        parts = args.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else "stats"

        if sub == "install":
            output = cmd_graphify_install()
            return CommandResult(success=True, output=output)
        elif sub == "build":
            path = parts[1] if len(parts) > 1 else "."
            output = cmd_graphify_build(path)
            return CommandResult(success=True, output=output)
        elif sub == "query" and len(parts) > 1:
            output = cmd_graphify_query(parts[1])
            return CommandResult(success=True, output=output)
        elif sub == "path" and len(parts) > 1:
            nodes = parts[1].split()
            if len(nodes) >= 2:
                output = cmd_graphify_path(nodes[0], nodes[1])
                return CommandResult(success=True, output=output)
            return CommandResult(success=False, output="Uso: /graphify path <node_a> <node_b>")
        elif sub == "explain" and len(parts) > 1:
            output = cmd_graphify_explain(parts[1])
            return CommandResult(success=True, output=output)
        elif sub == "stats":
            output = cmd_graphify_stats()
            return CommandResult(success=True, output=output)
        elif sub == "update":
            path = parts[1] if len(parts) > 1 else "."
            output = cmd_graphify_update(path)
            return CommandResult(success=True, output=output)
        else:
            help_text = '''Graphify — Knowledge Graph Multi-Lenguaje (100% local)

Uso:
  /graphify install              Verificar instalación
  /graphify build [path]         Construir grafo (default: .)
  /graphify query "pregunta"     Consultar grafo en lenguaje natural
  /graphify path A B             Camino más corto entre nodos
  /graphify explain "node"       Explicar nodo
  /graphify stats                Estadísticas del grafo
  /graphify update [path]        Actualizar archivos cambiados

Privacidad:
  • Código procesado localmente vía Tree-sitter AST (sin LLM)
  • Sin telemetría, sin nube, todo en tu PC
  • 25+ lenguajes soportados

Ejemplo:
  /graphify build /home/ser/EIDOS
  /graphify query "qué es el KnowledgeGraph"
  /graphify path "kernel" "memory_vec"'''
            return CommandResult(success=True, output=help_text)

    def _cmd_healing(self, args: str) -> CommandResult:
        """Self-Healing. Uso: /healing [stats|diagnose <error>]"""
        try:
            from core.self_healing import get_self_healer
            healer = get_self_healer()
        except Exception as e:
            return CommandResult(success=False, output=f"Self-Healer no disponible: {e}")

        sub = args.strip().lower()
        if sub == "stats" or not sub:
            s = healer.stats()
            return CommandResult(success=True, output=f"Self-Healer Stats:\n{s}")
        elif sub.startswith("diagnose "):
            error = sub[9:]
            d = healer.diagnose(error)
            return CommandResult(success=True, output=f"Diagnosis:\n{d}")
        else:
            return CommandResult(success=True, output="/healing stats | diagnose <error_message>")

    def _cmd_router(self, args: str) -> CommandResult:
        """Smart Router. Uso: /router [stats|route <prompt>]"""
        try:
            from core.smart_router import SmartRouter
            router = SmartRouter()
        except Exception as e:
            return CommandResult(success=False, output=f"Smart Router no disponible: {e}")

        parts = args.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else "stats"

        if sub == "stats" or not args.strip():
            s = router.get_stats()
            return CommandResult(success=True, output=f"Smart Router Stats:\n{s}")
        elif sub == "route" and len(parts) > 1:
            r = router.route(parts[1])
            return CommandResult(success=True, output=f"Route result:\n  Model: {r.get('model', '?')}\n  Profile: {r.get('profile', '?')}\n  Score: {r.get('score', '?')}")
        else:
            return CommandResult(success=True, output="/router stats | route <prompt>")

    def _cmd_mcp(self, args: str) -> CommandResult:
        """MCP Protocol. Uso: /mcp [status|tools|start|stop]"""
        try:
            from core.mcp_protocol import get_mcp_server
            mcp = get_mcp_server()
        except Exception as e:
            return CommandResult(success=False, output=f"MCP Server no disponible: {e}")

        sub = args.strip().lower()
        if sub == "tools":
            tools = mcp.list_tools()
            lines = [f"MCP Tools: {len(tools)}"]
            for t in tools:
                lines.append(f"  - {t.get('name', '?')}: {t.get('description', '')[:60]}")
            return CommandResult(success=True, output="\n".join(lines))
        elif sub == "start":
            mcp.serve(background=True)
            return CommandResult(success=True, output="MCP Server started on port 3000")
        elif sub == "stop":
            mcp.stop()
            return CommandResult(success=True, output="MCP Server stopped")
        else:
            tools = mcp.list_tools()
            return CommandResult(success=True, output=f"MCP Protocol — Tools: {len(tools)}\n/mcp tools | start | stop")

    def _cmd_decay(self, args: str) -> CommandResult:
        """Memory Decay. Uso: /decay [health|strongest|weakest|cleanup]"""
        try:
            from core.memory_decay import get_decaying_memory
            dm = get_decaying_memory()
        except Exception as e:
            return CommandResult(success=False, output=f"Memory Decay no disponible: {e}")

        sub = args.strip().lower()
        if sub == "health":
            h = dm.memory_health()
            return CommandResult(success=True, output=f"Memory Health:\n{h}")
        elif sub == "strongest":
            s = dm.get_strongest(limit=10)
            lines = ["Strongest Memories:"]
            for m in s:
                lines.append(f"  [{m.get('strength', 0):.2f}] {m.get('key', '?')}: {str(m.get('content', ''))[:60]}")
            return CommandResult(success=True, output="\n".join(lines))
        elif sub == "weakest":
            w = dm.get_weakest(limit=10)
            lines = ["Weakest Memories (decay candidates):"]
            for m in w:
                lines.append(f"  [{m.get('strength', 0):.2f}] {m.get('key', '?')}: {str(m.get('content', ''))[:60]}")
            return CommandResult(success=True, output="\n".join(lines))
        elif sub == "cleanup":
            dm.cleanup()
            return CommandResult(success=True, output="Memory cleanup completed — weak memories archived")
        else:
            h = dm.memory_health()
            return CommandResult(success=True, output=f"Memory Decay:\n{h}\n\n/decay health | strongest | weakest | cleanup")

    def _cmd_compression(self, args: str) -> CommandResult:
        """7-Layer Compression. Uso: /compression [stats|test <text>]"""
        try:
            from core.compression_7layer import SevenLayerCompressor
            comp = SevenLayerCompressor()
        except Exception as e:
            return CommandResult(success=False, output=f"Compressor no disponible: {e}")

        parts = args.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else "stats"

        if sub == "stats" or not args.strip():
            s = comp.get_stats_summary()
            return CommandResult(success=True, output=f"7-Layer Compression Stats:\n{s}")
        elif sub == "test" and len(parts) > 1:
            original = parts[1]
            compressed = comp.compress(original)
            ratio = len(compressed) / len(original) * 100 if original else 100
            return CommandResult(success=True, output=f"Original: {len(original)} chars\nCompressed: {len(compressed)} chars\nRatio: {ratio:.1f}%\n\nResult: {compressed[:200]}")
        else:
            return CommandResult(success=True, output="/compression stats | test <text_to_compress>")

    def _cmd_dyntools(self, args: str) -> CommandResult:
        """Dynamic Tools. Uso: /dyntools [list|stats|cleanup]"""
        try:
            from core.dynamic_tools import DynamicToolBuilder
            dt = DynamicToolBuilder()
        except Exception as e:
            return CommandResult(success=False, output=f"Dynamic Tools no disponible: {e}")

        sub = args.strip().lower()
        if sub == "list":
            tools = dt.list_tools()
            lines = [f"Dynamic Tools: {len(tools)}"]
            for t in tools:
                lines.append(f"  - {t.get('name', '?')}: {t.get('description', '')[:60]}")
            return CommandResult(success=True, output="\n".join(lines))
        elif sub == "cleanup":
            dt.cleanup()
            return CommandResult(success=True, output="Dynamic tools cleaned up")
        else:
            s = dt.get_stats()
            return CommandResult(success=True, output=f"Dynamic Tool Builder:\n{s}\n\n/dyntools list | stats | cleanup")

    def _cmd_madmax(self, args: str) -> CommandResult:
        """RL MadMax. Uso: /madmax [stats|skills|tick]"""
        try:
            from core.rl_madmax import MadMaxLearner
            mm = MadMaxLearner()
        except Exception as e:
            return CommandResult(success=False, output=f"MadMax no disponible: {e}")

        sub = args.strip().lower()
        if sub == "skills":
            skills = mm.list_skills()
            lines = [f"MadMax Skills: {len(skills)}"]
            for s in skills[:15]:
                lines.append(f"  - {s.get('name', '?')}: weight={s.get('weight', 0):.3f}")
            return CommandResult(success=True, output="\n".join(lines))
        elif sub == "tick":
            r = mm.tick()
            return CommandResult(success=True, output=f"MadMax tick result: {r}")
        else:
            s = mm.get_stats()
            return CommandResult(success=True, output=f"RL MadMax (Meta-Learning):\n{s}\n\n/madmax stats | skills | tick")

    def _cmd_oauth(self, args: str) -> CommandResult:
        """Google OAuth Proxy. Uso: /oauth [status|models|login]"""
        try:
            from core.google_oauth_proxy import GoogleOAuthProxy
            proxy = GoogleOAuthProxy()
        except Exception as e:
            return CommandResult(success=False, output=f"OAuth Proxy no disponible: {e}")

        sub = args.strip().lower()
        if sub == "models":
            m = proxy.get_available_models()
            lines = ["Available LLM Models (via Google OAuth):"]
            for model in m:
                lines.append(f"  - {model}")
            return CommandResult(success=True, output="\n".join(lines))
        elif sub == "login":
            r = proxy.login()
            return CommandResult(success=True, output=f"OAuth Login: {r}")
        else:
            s = proxy.get_stats()
            return CommandResult(success=True, output=f"Google OAuth Proxy:\n{s}\n\n/oauth status | models | login")


    def _cmd_rust(self, args: str) -> CommandResult:
        """Rust Bridge. Uso: /rust [status|stats|scan <dir>|watch <dir>]"""
        try:
            from core.rust_bridge import RUST_AVAILABLE, get_rust_knowledge_db, get_auto_learn_observer
        except Exception as e:
            return CommandResult(success=False, output=f"Rust bridge error: {e}")

        if not RUST_AVAILABLE:
            return CommandResult(success=False, output="Rust module not compiled. Run: cd rust-core && cargo build --release")

        sub = args.strip().split()
        cmd = sub[0] if sub else "status"

        if cmd == "status":
            kb = get_rust_knowledge_db()
            stats = kb.get_stats()
            lang_stats = kb.get_language_stats()
            lines = [
                "🦀 Rust Bridge — eidos_core",
                f"  Languages tracked: {stats.get('total_languages', 0)}",
                f"  Libraries tracked: {stats.get('total_libraries', 0)}",
            ]
            for lang, count in lang_stats.items():
                lines.append(f"    {lang}: {count} files observed")
            return CommandResult(success=True, output="\n".join(lines))

        elif cmd == "stats":
            kb = get_rust_knowledge_db()
            lib_stats = kb.get_library_stats()
            top = sorted(lib_stats.items(), key=lambda x: x[1].get("times_seen", 0), reverse=True)[:15]
            lines = ["🦀 Top Libraries:"]
            for name, data in top:
                lines.append(f"  {name} ({data.get('language','?')}): seen {data.get('times_seen',0)}x")
            return CommandResult(success=True, output="\n".join(lines))

        elif cmd == "scan" and len(sub) > 1:
            kb = get_rust_knowledge_db(verbose=False)
            result = kb.observe_directory(sub[1], max_files=200)
            lines = [
                f"🦀 Scan: {sub[1]}",
                f"  Files: {result.get('files_observed', 0)}",
                f"  Errors: {result.get('errors', 0)}",
                f"  Languages: {result.get('languages', {})}",
            ]
            return CommandResult(success=True, output="\n".join(lines))

        elif cmd == "watch" and len(sub) > 1:
            alo = get_auto_learn_observer(verbose=False)
            msg = alo.start(sub[1])
            return CommandResult(success=True, output=f"🦀 {msg}")

        else:
            return CommandResult(success=True, output="/rust status | stats | scan <dir> | watch <dir>")

    def _cmd_vlearn(self, args: str) -> CommandResult:
        """Vision-Learner Bridge. Uso: /vlearn [status|screen|watch <secs>|history|image <path>]"""
        try:
            from core.vision_learner_bridge import get_vision_learner_bridge
            bridge = get_vision_learner_bridge()
        except Exception as e:
            return CommandResult(success=False, output=f"VisionLearner no disponible: {e}")

        sub = args.strip().lower().split()
        cmd = sub[0] if sub else "status"

        if cmd == "status":
            s = bridge.get_status()
            mods = s["modules"]
            mod_str = ", ".join(f"{k}={'✓' if v else '✗'}" for k, v in mods.items())
            stats = s["stats"]
            lines = [
                "🔬 Vision-Learner Bridge",
                f"  Modules: {mod_str}",
                f"  Watching: {'🟢 Active' if s['watching'] else '⚪ Inactive'}",
                f"  Sessions: {s['sessions']}",
                f"  Frames analyzed: {stats.get('total_frames', 0)}",
                f"  Knowledge items: {stats.get('total_knowledge', 0)}",
                f"  Code snippets: {stats.get('total_code', 0)}",
                f"  Libraries found: {len(stats.get('libraries_found', []))}",
            ]
            if s.get("last_session"):
                ls = s["last_session"]
                lines.append(f"  Last session: {ls.get('status', '?')} ({ls.get('frames_analyzed', 0)} frames)")
            return CommandResult(success=True, output="\n".join(lines))

        elif cmd == "screen":
            vk = bridge.learn_from_screen()
            lines = [
                "📸 Screen Analysis:",
                f"  Text lines: {len(vk.text_content)}",
                f"  Code snippets: {len(vk.code_snippets)}",
                f"  Libraries: {', '.join(vk.libraries_found) if vk.libraries_found else 'none'}",
                f"  Categories: {', '.join(vk.semantic_categories[:3]) if vk.semantic_categories else 'none'}",
                f"  Technical score: {vk.technical_score:.2f}",
            ]
            return CommandResult(success=True, output="\n".join(lines))

        elif cmd == "watch":
            dur = float(sub[1]) if len(sub) > 1 else 300
            msg = bridge.start_watching(duration=dur)
            return CommandResult(success=True, output=f"👁️ {msg}")

        elif cmd == "stop":
            msg = bridge.stop_watching()
            return CommandResult(success=True, output=f"⏹️ {msg}")

        elif cmd == "history":
            limit = int(sub[1]) if len(sub) > 1 else 10
            entries = bridge.get_learning_history(limit=limit)
            if not entries:
                return CommandResult(success=True, output="No learning history yet.")
            lines = [f"📜 Vision Learning History (last {len(entries)}):"]
            for e in entries:
                ts = e.get("timestamp", "?")[:19]
                src = e.get("source", "?")
                score = e.get("technical_score", 0)
                code = len(e.get("code_snippets", []))
                libs = e.get("libraries_found", [])
                lines.append(f"  [{ts}] {src} score={score:.2f} code={code} libs={','.join(libs[:3])}")
            return CommandResult(success=True, output="\n".join(lines))

        elif cmd == "image" and len(sub) > 1:
            path = sub[1]
            vk = bridge.learn_from_image(path)
            lines = [
                f"🖼️ Image Analysis: {path}",
                f"  Text: {len(vk.text_content)} lines",
                f"  Code: {len(vk.code_snippets)} snippets",
                f"  Libs: {', '.join(vk.libraries_found) if vk.libraries_found else 'none'}",
                f"  Score: {vk.technical_score:.2f}",
            ]
            return CommandResult(success=True, output="\n".join(lines))

        else:
            return CommandResult(success=True,
                output="/vlearn status | screen | watch [secs] | stop | history [n] | image <path>")

    # ══════════════════════════════════════════════════════════════════════════
    # SESSION 12: Colony Engine + Extension Intelligence + IPC Bridge
    # ══════════════════════════════════════════════════════════════════════════

    def _cmd_colony(self, args: str) -> CommandResult:
        """Colony Query Engine. Uso: /colony [status|query <text>|agents|stats|history]"""
        try:
            from core.colony_query_engine import get_colony_engine
            engine = get_colony_engine()
        except Exception as e:
            return CommandResult(success=False, output=f"ColonyEngine no disponible: {e}")

        sub = args.strip().split(maxsplit=1)
        cmd = sub[0].lower() if sub else "status"
        rest = sub[1] if len(sub) > 1 else ""

        if cmd == "status":
            s = engine.get_status()
            lines = [
                "🏛️ Colony Query Engine",
                f"  Online: {s['online']}",
                f"  Agents: {s['agents']}",
                f"  Queries this session: {s['queries_this_session']}",
                "  Subsystems:",
            ]
            for name, state in s["subsystems"].items():
                icon = "🟢" if state == "online" else "🔴"
                lines.append(f"    {icon} {name}: {state}")
            return CommandResult(success=True, output="\n".join(lines))

        elif cmd == "query" and rest:
            result = engine.query(rest, requester="slash_command")
            lines = [
                f"🏛️ Colony Response ({result.model_used} via {result.agent_used}):",
                f"  Type: {result.query_type.value} | Tokens: {result.tokens_spent} | Time: {result.elapsed_s:.1f}s",
                "",
                result.response[:2000],
            ]
            return CommandResult(success=True, output="\n".join(lines))

        elif cmd == "agents":
            agents = engine.get_agents()
            lines = ["🏛️ Colony Agents:"]
            for a in agents:
                balance = f" balance={a.get('balance', '?')}" if 'balance' in a else ""
                lines.append(f"  {a['agent_id']}: {a['name']} — {a['specialties']}{balance}")
            return CommandResult(success=True, output="\n".join(lines))

        elif cmd == "stats":
            stats = engine.get_stats()
            lines = [
                "🏛️ Colony Stats:",
                f"  Total queries: {stats.get('db_total_queries', 0)}",
                f"  Success rate: {stats.get('db_success_rate', 0):.0%}",
                f"  Tokens spent: {stats.get('total_tokens_spent', 0)}",
                f"  By type: {stats.get('by_type', {})}",
                f"  By model: {stats.get('by_model', {})}",
            ]
            return CommandResult(success=True, output="\n".join(lines))

        elif cmd == "history":
            limit = 10
            try:
                limit = int(rest) if rest else 10
            except ValueError:
                pass
            history = engine.get_history(limit=limit)
            if not history:
                return CommandResult(success=True, output="No query history yet.")
            lines = [f"🏛️ Colony History (last {len(history)}):"]
            for h in history:
                ok = "✓" if h["success"] else "✗"
                lines.append(f"  [{ok}] {h['type']} → {h['model']} ({h['elapsed']:.1f}s) — {h['query'][:60]}")
            return CommandResult(success=True, output="\n".join(lines))

        else:
            return CommandResult(success=True,
                output="/colony status | query <text> | agents | stats | history [n]")

    def _cmd_extensions(self, args: str) -> CommandResult:
        """Extension Intelligence. Uso: /extensions [status|scan|install <id>|uninstall <id>|search <q>|auto]"""
        try:
            from core.extension_intelligence import get_extension_intelligence
            ei = get_extension_intelligence()
        except Exception as e:
            return CommandResult(success=False, output=f"ExtensionIntelligence no disponible: {e}")

        sub = args.strip().split(maxsplit=1)
        cmd = sub[0].lower() if sub else "status"
        rest = sub[1].strip() if len(sub) > 1 else ""

        if cmd == "status":
            s = ei.get_status()
            stats = ei.get_stats()
            lines = [
                "🧩 Extension Intelligence",
                f"  Binary: {s['code_binary']}",
                f"  VSEIDOS: {'✓' if s['is_vseidos'] else '✗'}",
                f"  Installed: {s['installed_count']}",
                f"  Total installs: {stats.get('total_installs', 0)}",
                f"  Total scans: {stats.get('total_scans', 0)}",
            ]
            if s.get("last_scan"):
                ls = s["last_scan"]
                lines.append(f"  Last scan: {ls.get('workspace', '?')} ({len(ls.get('languages', []))} langs, {ls.get('to_install', 0)} pending)")
            return CommandResult(success=True, output="\n".join(lines))

        elif cmd == "scan":
            workspace = rest if rest else None
            scan = ei.analyze_workspace(workspace)
            lines = [
                f"🧩 Scan: {scan.workspace}",
                f"  Languages: {', '.join(sorted(scan.detected_languages))}",
                f"  Recommended: {len(scan.recommended)}",
                f"  Already installed: {len(scan.already_installed)}",
                f"  To install: {len(scan.to_install)}",
                f"  Time: {scan.scan_time:.2f}s",
            ]
            if scan.to_install:
                lines.append("  Pending:")
                for ext in scan.to_install[:10]:
                    lines.append(f"    [{ext.priority}] {ext.ext_id} — {ext.reason}")
            return CommandResult(success=True, output="\n".join(lines))

        elif cmd == "install" and rest:
            success = ei.install(rest, reason="slash_command")
            icon = "✓" if success else "✗"
            return CommandResult(success=success, output=f"🧩 Install {rest}: {icon}")

        elif cmd == "uninstall" and rest:
            success = ei.uninstall(rest, reason="slash_command")
            icon = "✓" if success else "✗"
            return CommandResult(success=success, output=f"🧩 Uninstall {rest}: {icon}")

        elif cmd == "search" and rest:
            results = ei.search_extensions(rest)
            if not results:
                return CommandResult(success=True, output=f"No extensions found for '{rest}'")
            lines = [f"🧩 Search: '{rest}' ({len(results)} results):"]
            for r in results[:10]:
                installed = " [installed]" if r["installed"] else ""
                lines.append(f"  {r['ext_id']} ({r['language']}) — {r['reason']}{installed}")
            return CommandResult(success=True, output="\n".join(lines))

        elif cmd == "auto":
            dry = rest.lower() == "dry" if rest else False
            result = ei.auto_install(dry_run=dry)
            lines = [
                f"🧩 Auto-Install {'(DRY RUN)' if dry else ''}:",
                f"  Detected: {', '.join(result.get('detected', []))}",
                f"  Recommended: {result.get('recommended', 0)}",
                f"  Already installed: {result.get('already_installed', 0)}",
            ]
            if dry:
                for ext in result.get("would_install", []):
                    lines.append(f"    [{ext['priority']}] {ext['id']} — {ext['reason']}")
            else:
                lines.append(f"  Installed: {len(result.get('installed', []))}")
                lines.append(f"  Failed: {len(result.get('failed', []))}")
            return CommandResult(success=True, output="\n".join(lines))

        else:
            return CommandResult(success=True,
                output="/extensions status | scan [path] | install <id> | uninstall <id> | search <q> | auto [dry]")

    def _cmd_ipc(self, args: str) -> CommandResult:
        """IPC Bridge. Uso: /ipc [status|start|stop|send <msg>]"""
        try:
            from core.ipc_bridge import get_ipc_bridge
            bridge = get_ipc_bridge()
        except Exception as e:
            return CommandResult(success=False, output=f"IPC Bridge no disponible: {e}")

        sub = args.strip().split(maxsplit=1)
        cmd = sub[0].lower() if sub else "status"
        rest = sub[1] if len(sub) > 1 else ""

        if cmd == "status":
            s = bridge.get_status()
            lines = [
                "🔗 IPC Bridge (EIDOS CLI <-> VSEIDOS)",
                f"  Identity: {s['identity']}",
                f"  Server: {'🟢 running' if s['server_running'] else '⚪ stopped'}",
                f"  Connections: {s['connections']}",
                f"  Socket: {s['socket_path']} ({'exists' if s['socket_exists'] else 'no socket'})",
                f"  Messages processed: {s['messages_processed']}",
                f"  Pending replies: {s['pending_replies']}",
            ]
            return CommandResult(success=True, output="\n".join(lines))

        elif cmd == "start":
            ok = bridge.start_server()
            return CommandResult(success=ok,
                output=f"🔗 IPC Server: {'started' if ok else 'failed to start'}")

        elif cmd == "stop":
            bridge.stop_server()
            return CommandResult(success=True, output="🔗 IPC Server stopped")

        elif cmd == "send" and rest:
            bridge.send_event("user_message", {"text": rest})
            return CommandResult(success=True, output=f"🔗 Sent: {rest[:100]}")

        elif cmd == "messages":
            msgs = bridge.get_recent_messages(limit=10)
            if not msgs:
                return CommandResult(success=True, output="No IPC messages yet.")
            lines = ["🔗 Recent IPC messages:"]
            for m in msgs:
                lines.append(f"  [{m['type']}] from {m['source']} — {m['msg_id']}")
            return CommandResult(success=True, output="\n".join(lines))

        else:
            return CommandResult(success=True,
                output="/ipc status | start | stop | send <msg> | messages")

    def _cmd_wake(self, args: str) -> CommandResult:
        """Wake Word Listener. Uso: /wake [status|start|stop|lang <es|en>]"""
        try:
            from core.wake_word import get_wake_listener
            listener = get_wake_listener()
        except Exception as e:
            return CommandResult(success=False, output=f"WakeWord no disponible: {e}")

        sub = args.strip().split(maxsplit=1)
        cmd = sub[0].lower() if sub else "status"
        rest = sub[1].strip() if len(sub) > 1 else ""

        if cmd == "status":
            s = listener.get_status()
            lines = [
                "🎤 Wake Word Listener",
                f"  Running: {'🟢' if s['running'] else '⚪'} {s['mode']}",
                f"  Language: {s['language']}",
                f"  Vosk: {'✓' if s['vosk_available'] else '✗'}",
                f"  SoundDevice: {'✓' if s['sounddevice_available'] else '✗'}",
                f"  Model ES: {'✓' if s['model_es_exists'] else '✗'}",
                f"  Model EN: {'✓' if s['model_en_exists'] else '✗'}",
                f"  Available: {'✓' if listener.is_available() else '✗'}",
                f"  Wakes: {s['wake_count']} | Commands: {s['command_count']}",
                f"  Listen time: {s['total_listen_time_s']:.0f}s",
            ]
            if s.get("last_command"):
                lines.append(f"  Last: \"{s['last_command']['text']}\"")
            return CommandResult(success=True, output="\n".join(lines))

        elif cmd == "start":
            msg = listener.start()
            return CommandResult(success=True, output=f"🎤 {msg}")

        elif cmd == "stop":
            msg = listener.stop()
            return CommandResult(success=True, output=f"🎤 {msg}")

        elif cmd == "lang" and rest:
            msg = listener.set_language(rest)
            return CommandResult(success=True, output=f"🎤 {msg}")

        else:
            return CommandResult(success=True,
                output="/wake status | start | stop | lang <es|en>")

    def _cmd_v2code(self, args: str) -> CommandResult:
        """Visual-to-Code Generator (Kimi K2.5 inspired). Uso: /v2code <image.png> [html|react|python]"""
        try:
            from core.visual_code_generator import VisualCodeGenerator
            generator = VisualCodeGenerator()
        except Exception as e:
            return CommandResult(success=False, output=f"VisualCodeGenerator no disponible: {e}")

        if not args:
            lines = [
                "🎨 Visual-to-Code Generator (Kimi K2.5 inspired)",
                "",
                "Uso: /v2code <image.png> [framework]",
                "",
                "Frameworks disponibles:",
                "  html          - HTML/CSS vanilla",
                "  react         - React component",
                "  python_tkinter - Python Tkinter GUI",
                "  python_streamlit - Streamlit app",
                "  vue           - Vue 3 component",
                "  auto          - Selección automática (default)",
                "",
                "Ejemplo:",
                "  /v2code screenshot.png react",
            ]
            return CommandResult(success=True, output="\n".join(lines))

        parts = args.strip().split()
        image_path = parts[0]
        framework = parts[1] if len(parts) > 1 else "auto"

        # Verificar imagen existe
        from pathlib import Path
        if not Path(image_path).exists():
            # Buscar en directorios comunes
            alt_paths = [
                Path.home() / ".eidos" / "screenshots" / image_path,
                Path(image_path).expanduser(),
                Path.cwd() / image_path,
            ]
            found = False
            for alt in alt_paths:
                if alt.exists():
                    image_path = str(alt)
                    found = True
                    break
            if not found:
                return CommandResult(success=False, output=f"❌ Imagen no encontrada: {image_path}")

        # Generar código
        try:
            result = generator.generate_from_screenshot(image_path, framework)

            # Guardar
            filepath = generator.save_code(result)

            lines = [
                f"🎨 Visual-to-Code: {result.framework}",
                f"   Confianza: {result.confidence:.1%}",
                f"   Elementos detectados: {len(result.detected_elements)}",
                f"   Guardado en: {filepath}",
                "",
                f"Razonamiento: {result.reasoning}",
                "",
                "--- Preview (primeras 20 líneas) ---",
            ]
            preview_lines = result.code.split('\n')[:20]
            lines.extend(preview_lines)
            if len(result.code.split('\n')) > 20:
                lines.append("...")

            return CommandResult(success=True, output="\n".join(lines))

        except Exception as e:
            return CommandResult(success=False, output=f"❌ Error generando código: {e}")

    def _cmd_think(self, args: str) -> CommandResult:
        """Interleaved Thinking Mode (Kimi K2.5 style). Uso: /think <query> [--force]"""
        try:
            from core.thinking_mode import get_thinking_engine
            engine = get_thinking_engine()
        except Exception as e:
            return CommandResult(success=False, output=f"Thinking Engine no disponible: {e}")

        if not args:
            lines = [
                "🧠 Interleaved Thinking Mode (Kimi K2.5 inspired)",
                "",
                "Muestra el razonamiento paso a paso antes de la respuesta.",
                "",
                "Uso: /think <tu pregunta aquí>",
                "",
                "Ejemplos:",
                "  /think ¿Por qué el cielo es azul?",
                "  /think Explain quantum computing step by step",
                "  /think Cómo optimizaría una base de datos PostgreSQL",
                "",
                "Opciones:",
                "  --force    Forzar thinking aunque la query sea simple",
                "",
                "Tipos de pasos de thinking:",
                "  🔍 ANALYSIS    - Análisis del problema",
                "  📋 PLANNING    - Planificación del approach",
                "  ⚙️  EXECUTION   - Ejecución paso a paso",
                "  🤔 REFLECTION  - Reflexión sobre resultados",
                "  ✅ CONCLUSION  - Conclusión final",
            ]
            return CommandResult(success=True, output="\n".join(lines))

        # Parse args
        force = "--force" in args
        query = args.replace("--force", "").strip()

        if not query:
            return CommandResult(success=False, output="❌ Debes proporcionar una pregunta")

        # Verificar si amerita thinking
        should_think = engine.should_use_thinking(query)

        if not should_think and not force:
            lines = [
                f"💭 Query: {query}",
                "",
                "Esta query parece simple. No amerita thinking mode.",
                "Usa /think <query> --force para forzar thinking.",
                "",
                "O pregunta directamente sin /think para respuesta rápida.",
            ]
            return CommandResult(success=True, output="\n".join(lines))

        # Generar thinking
        lines = [
            f"🧠 THINKING MODE: {query}",
            "Analizando paso a paso...",
            "",
        ]

        try:
            result = engine.generate_thinking(query, force_thinking=force)

            # Mostrar pasos
            for step in result.thinking_steps:
                emoji = {
                    "analysis": "🔍",
                    "planning": "📋",
                    "execution": "⚙️",
                    "reflection": "🤔",
                    "conclusion": "✅"
                }.get(step.type, "💭")

                lines.append(f"{emoji} [{step.type.upper()}] (confianza: {step.confidence:.0%})")
                lines.append(f"   {step.content}")
                lines.append("")

            lines.append("─" * 50)
            lines.append("📤 RESPUESTA FINAL:")
            lines.append(result.final_response)
            lines.append("")
            lines.append(f"⏱️  Tiempo: {result.total_time:.1f}s | Modelo: {result.model_used}")

            return CommandResult(success=True, output="\n".join(lines))

        except Exception as e:
            return CommandResult(success=False, output=f"❌ Error en thinking: {e}")

    def _cmd_swarm(self, args: str) -> CommandResult:
        """Agent Swarm - spawn dinámico de agentes. Uso: /swarm <tarea> [--agents N]"""
        try:
            from core.agent_swarm import get_swarm_controller, SwarmTaskType
            controller = get_swarm_controller()
        except Exception as e:
            return CommandResult(success=False, output=f"Agent Swarm no disponible: {e}")

        if not args:
            lines = [
                "🐝 Agent Swarm Controller (Kimi K2.5 inspired)",
                "",
                "Spawn dinámico de agentes especializados para tareas complejas.",
                "",
                "Uso: /swarm <descripción de tarea> [--agents N]",
                "",
                "Ejemplos:",
                "  /swarm Research state of the art in LLMs",
                "  /swarm Code review of authentication module",
                "  /swarm Debug intermittent memory leak --agents 10",
                "",
                "Tipos de tareas soportadas:",
                "  • research      - Investigación multi-fuente",
                "  • code_review   - Revisión de código exhaustiva",
                "  • debugging     - Debugging multi-nivel",
                "  • architecture  - Diseño de sistemas",
                "  • analysis      - Análisis de datos",
                "  • creative      - Creación de contenido",
                "  • testing       - Testing exhaustivo",
                "  • documentation - Documentación completa",
                "",
                "Comandos adicionales:",
                "  /swarm status <task_id>  - Ver estado de swarm",
                "  /swarm stats             - Estadísticas del controller",
                "  /swarm active            - Swarms activos",
            ]
            return CommandResult(success=True, output="\n".join(lines))

        # Parsear args
        if args.startswith("status "):
            task_id = args[7:].strip()
            status = controller.get_swarm_status(task_id)
            if not status:
                return CommandResult(success=False, output=f"Task no encontrada: {task_id}")
            
            lines = [
                f"🐝 Swarm Status: {task_id}",
                f"   Descripción: {status['description'][:60]}...",
                f"   Tipo: {status['task_type']}",
                f"   Estado: {status['status']}",
                f"   Complejidad: {status['complexity']:.1%}",
                f"",
                f"   Agentes: {status['agents_total']} total",
                f"      ✅ Completados: {status['agents_completed']}",
                f"      ❌ Fallados: {status['agents_failed']}",
                f"      🔄 Trabajando: {status['agents_working']}",
                f"   Tiempo: {status['elapsed']:.1f}s",
            ]
            if status.get('result'):
                lines.append(f"   Resultado: {status['result'].get('summary', 'N/A')[:80]}")
            return CommandResult(success=True, output="\n".join(lines))

        if args == "stats":
            stats = controller.get_statistics()
            lines = [
                "🐝 Agent Swarm Statistics",
                f"   Max agents: {stats['max_agents_limit']}",
                f"   Active agents: {stats['active_agents']}",
                f"   Active tasks: {stats['active_tasks']}",
                f"   Completed tasks: {stats['completed_tasks']}",
                f"   Total spawned: {stats['total_spawned']}",
                f"   Total tasks: {stats['total_tasks_created']}",
            ]
            return CommandResult(success=True, output="\n".join(lines))

        if args == "active":
            swarms = controller.get_active_swarms()
            if not swarms:
                return CommandResult(success=True, output="No hay swarms activos")
            lines = [f"🐝 Active Swarms ({len(swarms)}):"]
            for s in swarms:
                lines.append(f"  {s['task_id']}: {s['description'][:50]}... [{s['status']}]")
            return CommandResult(success=True, output="\n".join(lines))

        # Spawn new swarm
        task_desc = args
        max_agents = None
        
        # Parse --agents flag
        if "--agents" in task_desc:
            parts = task_desc.split("--agents")
            task_desc = parts[0].strip()
            try:
                max_agents = int(parts[1].strip().split()[0])
            except Exception:
                pass  # error no crítico, continuar
        # Check if should use swarm
        use_swarm, task_type, complexity = controller.should_use_swarm(task_desc)

        if not use_swarm:
            lines = [
                f"💭 Tarea: {task_desc}",
                "",
                "Esta tarea parece simple (complejidad < 40%).",
                "No amerita Agent Swarm.",
                "",
                "Sugerencias:",
                "  • Usa /colony query <tarea> para tareas simples",
                "  • Usa /think <tarea> para razonamiento paso a paso",
            ]
            return CommandResult(success=True, output="\n".join(lines))

        # Spawn swarm
        lines = [
            f"🐝 Spawning Agent Swarm...",
            f"   Tarea: {task_desc[:60]}...",
            f"   Tipo detectado: {task_type.value}",
            f"   Complejidad: {complexity:.1%}",
        ]
        if max_agents:
            lines.append(f"   Max agents: {max_agents}")

        try:
            task = controller.spawn_swarm(task_desc, task_type, complexity, max_agents)
            
            lines.extend([
                "",
                f"✅ Swarm creado: {task.task_id}",
                f"   Sub-tareas: {len(task.subtasks)}",
                f"   Agentes: {task.parallel_agents}",
                "",
                "Ejecutando en background...",
                f"Ver estado con: /swarm status {task.task_id}",
            ])
            
            return CommandResult(success=True, output="\n".join(lines))
        except Exception as e:
            return CommandResult(success=False, output=f"❌ Error spawning swarm: {e}")

    def _cmd_comunidad(self, args: str) -> CommandResult:
        """
        Colony Community Chat - Interactúa con los agentes de la colonia.
        Uso: /comunidad [start|agentes|recompensa @agente X]
        """
        try:
            from core.colony_community import get_colony_community
            community = get_colony_community()
        except Exception as e:
            return CommandResult(success=False, output=f"Colony Community no disponible: {e}")
        
        if not args:
            # Mostrar ayuda y estado
            agents = community.get_participants()
            lines = [
                "🏛️  EIDOS COLONY COMMUNITY",
                "",
                "Tu mini-mundo de agentes que aprenden contigo.",
                "",
                "Agentes disponibles:",
            ]
            for a in agents:
                lines.append(f"  {a['emoji']} @{a['name'].lower()} - {', '.join(a['traits'])}")
                lines.append(f"     💰 Tokens ganados: {a['tokens_earned']:.1f}")
                if a.get('tokens_balance'):
                    lines.append(f"     🏦 Balance: {a['tokens_balance']:.1f}")
            
            lines.extend([
                "",
                "Comandos:",
                "  /comunidad start           - Iniciar sesión de chat",
                "  /comunidad agentes         - Ver todos los agentes con tokens",
                "  /comunidad recompensa @agente X [razón] - Dar X tokens a un agente",
                "  /comunidad stats [@agente] - Ver estadísticas de recompensas",
                "",
                "En el chat interactivo:",
                "  @nombre mensaje  - Whisper a un agente",
                "  !pregunta        - Preguntar a TODOS",
                "  #tema            - Convocar reunión",
            ])
            
            return CommandResult(success=True, output="\n".join(lines))
        
        parts = args.split()
        subcmd = parts[0].lower()
        
        if subcmd == "start":
            return CommandResult(
                success=True,
                output="🏛️  Iniciando Colony Community Chat...\n\nEscribe tus mensajes. Usa 'salir' para terminar.",
                action="start_colony_community"
            )
        
        elif subcmd == "agentes":
            agents = community.get_participants()
            lines = ["🤖 Agentes de la Comunidad:\n"]
            for a in agents:
                lines.append(f"  {a['emoji']} {a['name']} (@{a['name'].lower()})")
                lines.append(f"     Personalidad: {', '.join(a['traits'])}")
                lines.append(f"     🏆 Tokens ganados: {a['tokens_earned']:.1f}")
                if a.get('tokens_balance'):
                    lines.append(f"     💰 Balance actual: {a['tokens_balance']:.1f}")
                lines.append("")
            
            # Totales
            total_earned = sum(a['tokens_earned'] for a in agents)
            lines.append(f"📊 Total en la comunidad: {total_earned:.1f} tokens recompensados")
            
            return CommandResult(success=True, output="\n".join(lines))
        
        elif subcmd == "recompensa" and len(parts) >= 3:
            agent_name = parts[1].lstrip("@")
            try:
                amount = float(parts[2])
                reason = " ".join(parts[3:]) if len(parts) > 3 else ""
            except ValueError:
                return CommandResult(success=False, output="❌ Cantidad inválida. Uso: /comunidad recompensa @agente 10 [razón]")
            
            result = community.reward_agent(agent_name, amount, reason)
            
            if result["success"]:
                return CommandResult(
                    success=True,
                    output=(
                        f"🎁 ¡Recompensa enviada!\n\n"
                        f"   Agente: {result['agent_name']}\n"
                        f"   Tokens: +{result['amount']}\n"
                        f"   Razón: {result.get('reason') or 'Por su excelente contribución'}\n"
                        f"   {'✅ Sincronizado con TokenEconomy' if result.get('economy_sync') else '⚠️ Registrado localmente'}"
                    )
                )
            else:
                return CommandResult(success=False, output=f"❌ Error: {result.get('error')}")
        
        elif subcmd == "stats":
            agent_name = parts[1].lstrip("@") if len(parts) > 1 else None
            stats = community.get_agent_stats(agent_name)
            
            if agent_name:
                lines = [
                    f"📊 Estadísticas de {agent_name}",
                    f"",
                    f"Total ganado: {stats['total_earned']:.1f} tokens",
                    f"Recompensas recibidas: {stats['reward_count']}",
                ]
                if stats.get('history'):
                    lines.append(f"\nÚltimas recompensas:")
                    for h in stats['history'][:5]:
                        reason = h.get('reason') or 'Sin razón'
                        lines.append(f"  +{h['amount']:.1f}: {reason[:50]}")
            else:
                lines = [
                    "📊 Estadísticas de la Comunidad",
                    f"",
                    f"Total recompensado: {stats['total_rewards']:.1f} tokens",
                    f"Transacciones totales: {stats['total_transactions']}",
                    f"",
                    "Por agente:",
                ]
                for a in stats.get('agents', []):
                    lines.append(f"  {a['agent_name']}: {a['total']:.1f} tokens ({a['count']} recompensas)")
            
            return CommandResult(success=True, output="\n".join(lines))
        
        else:
            return CommandResult(
                success=False,
                output=f"❌ Subcomando desconocido: {subcmd}\n\nUsa /comunidad para ver opciones disponibles."
            )

    def _cmd_crawl(self, args: str) -> CommandResult:
        """
        /crawl <url> [max=N]
        Crawl profundo de una URL y todos sus sublinks del mismo dominio.
        Sintetiza con Ollama y guarda en ChromaDB.

        Ejemplos:
          /crawl https://docs.openclaw.dev
          /crawl https://docs.python.org max=20
        """
        if not args.strip():
            return CommandResult(success=False, output=(
                "❌ Uso: /crawl <url> [max=N]\n"
                "Ejemplo: /crawl https://docs.openclaw.dev\n"
                "         /crawl https://docs.openclaw.dev max=15"
            ))

        # Parsear url y max
        parts = args.strip().split()
        url = parts[0]
        max_pages = 30
        for p in parts[1:]:
            if p.startswith("max="):
                try:
                    max_pages = int(p.split("=")[1])
                except ValueError:
                    pass

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            from core.deep_crawler import get_deep_crawler
            crawler = get_deep_crawler()
            print(f"\n🕷️  Iniciando deep crawl de: {url}")
            print(f"   Máx páginas: {max_pages} | Incluye todos los sublinks\n")

            pages_visited = [0]
            def progress(visited, queued, current_url):
                pages_visited[0] = visited
                short = current_url[:65] + "…" if len(current_url) > 65 else current_url
                print(f"\r  [{visited}/{max_pages}] {short}      ", end="", flush=True)

            report = crawler.crawl(url, max_pages=max_pages, progress_cb=progress)
            print()

            lines = [
                f"✅ Deep crawl completado — {url}",
                f"",
                f"  Páginas visitadas:  {report.pages_visited}",
                f"  Páginas fallidas:   {report.pages_failed}",
                f"  Caracteres totales: {report.total_chars:,}",
                f"  ChromaDB guardados: {report.chroma_stored} nodos",
                f"  Tiempo total:       {report.duration_s}s",
            ]
            if report.key_topics:
                lines.append(f"\n  Temas encontrados: {', '.join(report.key_topics)}")
            lines.append(f"\n{'─'*55}\n📋 SÍNTESIS:\n")
            lines.append(report.synthesis)
            lines.append(f"\n{'─'*55}")
            lines.append(f"URLs visitadas: {', '.join(report.all_urls[:10])}")
            if len(report.all_urls) > 10:
                lines.append(f"  ... y {len(report.all_urls)-10} más")

            return CommandResult(success=True, output="\n".join(lines))

        except Exception as e:
            return CommandResult(success=False, output=f"❌ Error en crawl: {e}")

    def _cmd_devtools(self, args: str) -> CommandResult:
        """
        /devtools <subcomando>

        Subcomandos:
          attach          — adjunta DevTools al browser activo
          console         — muestra console logs capturados
          errors          — muestra solo errores JS/consola
          network [type]  — muestra log de red (type: xhr, fetch, script...)
          performance     — métricas de performance de la página actual
          storage         — localStorage, sessionStorage y cookies
          dom             — snapshot del DOM actual
          js <código>     — ejecuta JavaScript en la página
          clear           — limpia los logs acumulados
          summary         — resumen general del estado DevTools
        """
        from core.devtools_bridge import get_devtools
        dt = get_devtools()
        sub = args.strip().split(maxsplit=1)
        cmd = sub[0].lower() if sub else "summary"
        rest = sub[1] if len(sub) > 1 else ""

        if cmd == "attach":
            ok = dt.attach()
            return CommandResult(success=ok, output=
                "✅ DevTools adjuntadas al browser" if ok
                else "❌ No se pudo adjuntar (¿browser activo?)")

        elif cmd == "console":
            logs = dt.get_console_logs()
            if not logs:
                return CommandResult(success=True, output="(sin console logs capturados — usa /devtools attach primero)")
            lines = [f"📟 Console Logs ({len(logs)}):"]
            for m in logs[-30:]:
                icon = {"error":"❌","warn":"⚠️","info":"ℹ️","log":"▪"}.get(m.level, "▪")
                lines.append(f"  {icon} [{m.level}] {m.text[:120]}")
            return CommandResult(success=True, output="\n".join(lines))

        elif cmd == "errors":
            errs = dt.get_errors()
            if not errs:
                return CommandResult(success=True, output="✅ Sin errores JS detectados")
            return CommandResult(success=True, output=
                f"❌ Errores JS ({len(errs)}):\n" + "\n".join(f"  • {e[:100]}" for e in errs))

        elif cmd == "network":
            responses = dt.get_network_log(filter_type=rest or None)
            if not responses:
                return CommandResult(success=True, output="(sin requests capturados)")
            lines = [f"🌐 Network Log ({len(responses)} requests):"]
            for r in responses[-20:]:
                icon = "✅" if r.status < 400 else "❌"
                lines.append(f"  {icon} [{r.status}] {r.url[:80]}")
            return CommandResult(success=True, output="\n".join(lines))

        elif cmd == "performance":
            perf = dt.get_performance()
            if not perf:
                return CommandResult(success=False, output="❌ No se pudo obtener performance (adjunta DevTools primero)")
            return CommandResult(success=True, output=(
                f"⚡ Performance:\n"
                f"  DOM Content Loaded: {perf.dom_content_loaded_ms}ms\n"
                f"  Load completo:      {perf.load_ms}ms\n"
                f"  Heap JS usado:      {perf.heap_used_mb} MB\n"
                f"  Heap JS total:      {perf.heap_total_mb} MB"
            ))

        elif cmd == "storage":
            s = dt.get_storage()
            if not s:
                return CommandResult(success=False, output="❌ No se pudo obtener storage")
            lines = ["💾 Storage:"]
            ls = s.get("localStorage", {})
            lines.append(f"  localStorage ({len(ls)} keys): {list(ls.keys())[:10]}")
            ss = s.get("sessionStorage", {})
            lines.append(f"  sessionStorage ({len(ss)} keys): {list(ss.keys())[:10]}")
            ck = s.get("cookies", [])
            lines.append(f"  Cookies: {len(ck)}")
            return CommandResult(success=True, output="\n".join(lines))

        elif cmd == "dom":
            snap = dt.get_dom_snapshot()
            if not snap:
                return CommandResult(success=False, output="❌ No hay DOM snapshot disponible")
            import json
            return CommandResult(success=True, output=
                f"🌳 DOM snapshot (simplificado):\n{json.dumps(snap, indent=2, ensure_ascii=False)[:2000]}")

        elif cmd == "js" and rest:
            result = dt.execute_devtools_js(rest)
            return CommandResult(success=True, output=f"JS result: {result}")

        elif cmd == "clear":
            dt.clear_logs()
            return CommandResult(success=True, output="✅ Logs DevTools limpiados")

        else:
            return CommandResult(success=True, output=dt.summary())

    # ── Dominios de trabajo ───────────────────────────────────────────────────

    def _cmd_hack(self, args: str) -> "CommandResult":
        """Red team / offensive security con herramientas de Kali.
        Uso: /hack recon <target> | /hack sqli <url> | /hack brute <target> | /hack <tarea libre>"""
        if not args:
            return CommandResult(success=True, output=(
                "🔴 Red Team / Offensive Security\n\n"
                "  /hack recon <target>    — nmap + reconocimiento completo\n"
                "  /hack sqli <url>        — test SQL injection con sqlmap\n"
                "  /hack brute <target>    — hydra brute force\n"
                "  /hack fuzz <url>        — ffuf web fuzzing\n"
                "  /hack scan <target>     — nikto + gobuster\n"
                "  /hack <tarea libre>     — EIDOS ejecuta con ReAct\n\n"
                "Ejemplo: /hack recon 192.168.1.1"
            ))
        try:
            from core.react_engine import ReActEngine
            parts = args.split(None, 1)
            sub   = parts[0].lower()
            target = parts[1].strip() if len(parts) > 1 else ""

            if sub == "recon" and target:
                task = (f"Ejecuta reconocimiento completo de {target}. "
                        f"1) bash 'nmap -sV -sC -O {target} 2>/dev/null | head -40' "
                        f"2) analiza resultados 3) resume vulnerabilidades potenciales.")
            elif sub == "sqli" and target:
                task = (f"Analiza {target} para SQL injection. "
                        f"bash 'sqlmap -u {target} --batch --level=1 --risk=1 --dbs 2>/dev/null | tail -20'")
            elif sub == "fuzz" and target:
                task = (f"Fuzz {target} buscando directorios. "
                        f"bash 'ffuf -u {target}/FUZZ -w /usr/share/wordlists/dirb/common.txt -mc 200,301,302 2>/dev/null | head -20'")
            elif sub == "scan" and target:
                task = (f"Escaneo web completo de {target}. "
                        f"bash 'nikto -h {target} 2>/dev/null | tail -30'")
            else:
                task = f"Red team task: {args}. Usa bash con las herramientas de Kali disponibles."

            result = ReActEngine().run(task, max_steps=5)
            return CommandResult(success=True, output=result.answer)
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

    def _cmd_scan(self, args: str) -> "CommandResult":
        """Escaneo rápido de red o target. Uso: /scan <ip/dominio>"""
        if not args:
            return CommandResult(success=False, output="Uso: /scan <ip o dominio>")
        try:
            from core.tool_registry import ToolRegistry
            tr = ToolRegistry()
            # nmap rápido
            out = tr.call("bash", {"command": f"nmap -sV --open -T4 {args} 2>/dev/null | head -30"})
            return CommandResult(success=True, output=f"🔍 Scan de {args}:\n\n{out}")
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

    def _cmd_defend(self, args: str) -> "CommandResult":
        """Blue team / defensa. Uso: /defend status | /defend logs | /defend <tarea>"""
        if not args or args == "status":
            try:
                from core.tool_registry import ToolRegistry
                tr = ToolRegistry()
                fw  = tr.call("bash", {"command": "ufw status 2>/dev/null | head -10"})
                f2b = tr.call("bash", {"command": "fail2ban-client status 2>/dev/null | head -8"})
                ss  = tr.call("bash", {"command": "ss -tlnp 2>/dev/null | head -15"})
                return CommandResult(success=True, output=(
                    f"🛡️ Estado defensa:\n\nFirewall (ufw):\n{fw}\n\nFail2ban:\n{f2b}\n\nPuertos abiertos:\n{ss}"
                ))
            except Exception as e:
                return CommandResult(success=False, output=f"Error: {e}")
        try:
            from core.react_engine import ReActEngine
            result = ReActEngine().run(
                f"Blue team / defensa: {args}. Usa bash con herramientas de seguridad defensiva.",
                max_steps=5
            )
            return CommandResult(success=True, output=result.answer)
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

    def _cmd_seo(self, args: str) -> "CommandResult":
        """Análisis SEO. Uso: /seo <dominio> | /seo <pregunta SEO>"""
        if not args:
            return CommandResult(success=True, output=(
                "🔍 SEO con EIDOS\n\n"
                "  /seo <dominio>        — análisis básico (headers, velocidad, meta)\n"
                "  /seo keywords <nicho> — investiga keywords con volumen\n"
                "  /seo <pregunta>       — consulta SEO con LLM + web\n\n"
                "Ejemplo: /seo example.com"
            ))
        try:
            from core.react_engine import ReActEngine
            parts = args.split(None, 1)
            sub = parts[0].lower()

            if sub == "keywords" and len(parts) > 1:
                task = (f"Investiga keywords para el nicho: {parts[1]}. "
                        f"web_search 'keywords {parts[1]} volumen búsqueda' y "
                        f"web_search 'long tail keywords {parts[1]}'. Devuelve lista con potencial.")
            elif "." in parts[0] and not parts[0].startswith("http"):
                domain = args
                task = (f"Análisis SEO básico de {domain}. "
                        f"1) web_fetch 'https://{domain}' para ver title/meta/h1 "
                        f"2) web_search 'site:{domain}' para ver indexación "
                        f"3) Devuelve: title, meta description, H1, velocidad estimada, recomendaciones.")
            else:
                task = f"Consulta SEO: {args}. Usa search_brain + web_search para responder."

            result = ReActEngine().run(task, max_steps=6)
            return CommandResult(success=True, output=result.answer)
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

    def _cmd_workflow(self, args: str) -> "CommandResult":
        """Automatizaciones y workflows. Uso: /workflow <descripción> | /workflow n8n"""
        if not args or args == "n8n":
            try:
                from core.tool_registry import ToolRegistry
                tr = ToolRegistry()
                # Ver si n8n está corriendo
                n8n = tr.call("bash", {"command": "curl -s --max-time 3 http://localhost:5678/healthz 2>/dev/null || echo 'n8n no activo'"})
                if "n8n no activo" in n8n:
                    return CommandResult(success=True, output=(
                        "⚙️ n8n Workflows\n\n"
                        "n8n no está corriendo. Para iniciarlo:\n"
                        "  docker run -it --rm -p 5678:5678 n8nio/n8n\n"
                        "  → Abrir http://localhost:5678\n\n"
                        "O crear workflow con EIDOS:\n"
                        "  /workflow <descripción del workflow que quieres>"
                    ))
                return CommandResult(success=True, output=f"✅ n8n activo en :5678\n{n8n}")
            except Exception as e:
                return CommandResult(success=False, output=f"Error: {e}")
        try:
            from core.react_engine import ReActEngine
            task = (f"Crea o describe un workflow de automatización: {args}. "
                    f"Considera: n8n (webhooks, APIs), bash (scripts), Python (automatización). "
                    f"Si involucra EIDOS: usa los endpoints /api/query o /api/chat. "
                    f"Proporciona el código o configuración específica.")
            result = ReActEngine().run(task, max_steps=8)
            return CommandResult(success=True, output=result.answer)
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

    def _cmd_earn(self, args: str) -> "CommandResult":
        """Estrategias para ganar dinero con EIDOS. Uso: /earn | /earn <servicio>"""
        if not args:
            return CommandResult(success=True, output=(
                "💰 Monetización con EIDOS\n\n"
                "Servicios que puedes vender HOY:\n\n"
                "  1. /earn scan      — API escaneo seguridad ($5-20/scan)\n"
                "  2. /earn seo       — Auditoría SEO automatizada ($50-200)\n"
                "  3. /earn workflow  — Crear automatizaciones n8n ($100-500)\n"
                "  4. /earn intel     — Threat intelligence semanal ($10-30/mes)\n"
                "  5. /earn pentest   — Recon automatizado para empresas ($200-500)\n\n"
                "Para empezar ahora:\n"
                "  /earn setup        — configura la API pública con Cloudflare Tunnel\n"
                "  /earn fiverr       — guía para publicar en Fiverr"
            ))
        try:
            from core.react_engine import ReActEngine
            if args == "setup":
                task = (
                    "Prepara EIDOS para vender servicios de seguridad. "
                    "1) bash 'which cloudflared || echo instalar' para ver si cloudflared está "
                    "2) Describe cómo exponer localhost:8766 públicamente "
                    "3) Lista los endpoints de EIDOS API que tienen valor comercial "
                    "4) Sugiere precio y cómo publicarlo en Fiverr/Upwork"
                )
            elif args == "fiverr":
                task = (
                    "Crea una descripción para vender en Fiverr servicios de EIDOS. "
                    "web_search 'fiverr cybersecurity AI automation gig best selling' para ver competencia. "
                    "Genera: título, descripción, precio, FAQs para un gig de seguridad/automatización."
                )
            else:
                task = f"Estrategia para monetizar con EIDOS: {args}. Sé específico y accionable."
            result = ReActEngine().run(task, max_steps=6)
            return CommandResult(success=True, output=result.answer)
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

    def _cmd_vm(self, args: str) -> "CommandResult":
        """Gestión de máquinas virtuales. Uso: /vm list | /vm start <nombre> | /vm <tarea>"""
        if not args or args == "list":
            try:
                from core.tool_registry import ToolRegistry
                tr = ToolRegistry()
                vms = tr.call("bash", {"command": "virsh list --all 2>/dev/null | head -15 || echo 'virsh no disponible'"})
                docker_c = tr.call("bash", {"command": "docker ps 2>/dev/null | head -8"})
                return CommandResult(success=True, output=f"🖥️ VMs (KVM):\n{vms}\n\n🐳 Containers Docker:\n{docker_c}")
            except Exception as e:
                return CommandResult(success=False, output=f"Error: {e}")
        try:
            from core.react_engine import ReActEngine
            result = ReActEngine().run(
                f"VM task: {args}. Usa bash con virsh/docker/qemu según corresponda.",
                max_steps=5
            )
            return CommandResult(success=True, output=result.answer)
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

    def _cmd_improve(self, args: str) -> "CommandResult":
        """Auto-mejora de EIDOS: detecta áreas, propone, prueba en clon, guarda para SER."""
        eidos_dir = os.environ.get("EIDOS_DIR", "/home/ser/EIDOS")
        sys.path.insert(0, eidos_dir)
        try:
            from core.self_improver import SelfImprover
            si = SelfImprover()
            sub = args.strip().lower() if args else "status"

            if sub == "scan":
                areas = si.detect_improvement_areas()
                lines = [f"🔍 {len(areas)} áreas de mejora detectadas:\n"]
                for a in areas[:8]:
                    lines.append(f"  [{a.priority}] {a.name[:55]}")
                    if a.evidence:
                        lines.append(f"      → {a.evidence[:70]}")
                return CommandResult(success=True, output="\n".join(lines))

            elif sub == "run":
                out = [f"⚙️ Ciclo auto-mejora iniciado...\n"]
                results = si.autonomous_cycle(max_improvements=2)
                out.append(f"  Detectadas: {results['detected']} áreas")
                out.append(f"  Propuestas: {results['proposed']}")
                out.append(f"  Aprobadas:  {results['approved']} (listas para /improve apply)")
                for d in results.get("details", []):
                    icon = "✅" if d.get("passed") else "❌"
                    out.append(f"  {icon} {d['area'][:50]} → {d.get('status','?')[:50]}")
                return CommandResult(success=True, output="\n".join(out))

            elif sub == "list":
                approved = si.list_approved()
                if not approved:
                    return CommandResult(success=True, output="Sin mejoras pendientes de aplicar.")
                lines = [f"📋 {len(approved)} mejoras aprobadas:\n"]
                for m in approved:
                    lines.append(f"  [{m['id']}] {m['area'][:50]}")
                    lines.append(f"      {m['reasoning'][:60]}")
                    lines.append(f"      Sintaxis: {'✅' if m['syntax_ok'] else '❌'} | Archivo: {Path(m['file']).name}")
                lines.append(f"\nPara aplicar: /improve apply <id>")
                return CommandResult(success=True, output="\n".join(lines))

            elif sub.startswith("apply ") or sub.startswith("apply"):
                imp_id = sub.replace("apply", "").strip()
                if not imp_id:
                    return CommandResult(success=False, output="Uso: /improve apply <id>")
                result = si.apply_to_production(imp_id)
                if result["success"]:
                    return CommandResult(success=True, output=(
                        f"✅ Mejora [{imp_id}] aplicada al sistema real.\n"
                        f"   Archivo: {result['file']}\n"
                        f"   Backup: {result.get('backup','')}\n"
                        f"   Área: {result.get('area','')}"
                    ))
                return CommandResult(success=False, output=f"❌ Error: {result['error']}")

            else:
                return CommandResult(success=True, output=(
                    "🔧 EIDOS Auto-Mejora (clon en S@NDBOX_EIDOS)\n\n"
                    "  /improve scan     — detectar áreas de mejora\n"
                    "  /improve run      — ciclo completo (detectar→proponer→probar en clon)\n"
                    "  /improve list     — ver mejoras aprobadas pendientes\n"
                    "  /improve apply <id> — aplicar mejora al sistema real\n\n"
                    "El clon está en /home/ser/NO TOCAR/S@NDBOX_EIDOS/eidos_clon/\n"
                    "EIDOS propone, prueba en sandbox, SER decide si aplicar."
                ))
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

    def _cmd_compress(self, args: str) -> "CommandResult":
        """Comprime la memoria de EIDOS (elimina duplicados + síntesis LLM)."""
        import subprocess, sys as _sys
        from pathlib import Path
        try:
            eidos_dir = os.environ.get("EIDOS_DIR", "/home/ser/EIDOS")
            sys.path.insert(0, eidos_dir)
            from core.memory_compressor import MemoryCompressor
            mc = MemoryCompressor()
            dupes = mc.remove_duplicates()
            stats_before = mc.stats()
            out = [f"🗜️ Compresión de memoria EIDOS",
                   f"  Duplicados eliminados: {dupes}",
                   f"  Nodos antes: {stats_before['total']:,}"]
            if args == "full":
                results = mc.full_compression_cycle()
                summary = results.get("_summary", {})
                out.append(f"  Nodos después: {summary.get('nodes_after',0):,}")
                out.append(f"  Ahorro: {summary.get('ratio','?')}")
                for cat, data in results.items():
                    if cat != "_summary" and isinstance(data, dict):
                        out.append(f"  {cat}: {data['before']}→{data['before']-data.get('saved',0)}")
            else:
                out.append(f"  (Usa /compress full para compresión semántica completa)")
            return CommandResult(success=True, output="\n".join(out))
        except Exception as e:
            return CommandResult(success=False, output=f"Error en compress: {e}")

    def _cmd_setup(self, args: str) -> "CommandResult":
        """Configura perfil de usuario (nickname → personaje Colony)."""
        try:
            eidos_dir = os.environ.get("EIDOS_DIR", "/home/ser/EIDOS")
            sys.path.insert(0, eidos_dir)
            from core.user_profile_creator import UserProfileCreator
            creator = UserProfileCreator()
            if creator.profile_exists():
                profile = creator.load_existing()
                return CommandResult(success=True, output=(
                    f"Perfil existente: {profile.emoji} {profile.nickname}\n"
                    f"Colony ID: {profile.colony_id}\n"
                    f"Personalidad: {profile.personality[:100]}\n"
                    f"(Usa /setup reset para crear uno nuevo)"
                ))
            nickname = args.strip() if args else "Usuario"
            profile = creator.onboard(nickname=nickname, interactive=False)
            return CommandResult(success=True, output=(
                f"✅ Perfil creado: {profile.emoji} {profile.nickname}\n"
                f"Colony ID: {profile.colony_id}\n"
                f"Personalidad: {profile.personality[:120]}"
            ))
        except Exception as e:
            return CommandResult(success=False, output=f"Error en setup: {e}")

    # ── Sesión 49: comandos Claude Code → EIDOS ───────────────────────────────

    def _cmd_plan(self, args: str) -> "CommandResult":
        if not args:
            return CommandResult(success=False, output="Uso: /plan <descripción de la tarea>")
        try:
            from core.plan_mode import PlanMode
            plan = PlanMode().propose(args, auto_approve=True)
            return CommandResult(success=True, output=plan.display() if plan else "Plan cancelado.")
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

    def _cmd_research(self, args: str) -> "CommandResult":
        if not args:
            return CommandResult(success=False, output="Uso: /research <tema>")
        try:
            from core.react_engine import ReActEngine
            result = ReActEngine().run(
                f"Investiga en profundidad: {args}. "
                "Usa search_brain primero, luego web_search si falta información. "
                "Devuelve síntesis estructurada.",
                max_steps=8
            )
            output = result.answer + f"\n\n[{len(result.steps)} pasos · {result.total_s}s]"
            return CommandResult(success=True, output=output)
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

    def _cmd_code(self, args: str) -> "CommandResult":
        if not args:
            return CommandResult(success=False, output="Uso: /code <objetivo>")
        try:
            from core.react_engine import ReActEngine
            result = ReActEngine().run(
                f"Tarea de código: {args}. Lee archivos con read_file, "
                "escribe con write_file, verifica con bash.",
                max_steps=10
            )
            return CommandResult(success=True, output=result.answer)
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

    def _cmd_react(self, args: str) -> "CommandResult":
        if not args:
            return CommandResult(success=False, output="Uso: /react <tarea>")
        try:
            from core.react_engine import ReActEngine
            result = ReActEngine().run(args)
            steps  = "\n".join(str(s) for s in result.steps)
            output = f"{steps}\n\n**Respuesta:** {result.answer}"
            return CommandResult(success=True, output=output)
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

    def _cmd_tasks(self, args: str) -> "CommandResult":
        try:
            from core.task_list import TaskList
            tl    = TaskList()
            tasks = tl.list_pending(20)
            stats = tl.stats()
            bys   = stats.get("by_status", {})
            hdr   = (f"📋 Tareas EIDOS — {stats.get('total',0)} total"
                     f" | {bys.get('pending',0)} pendientes"
                     f" | {bys.get('in_progress',0)} en progreso\n")
            return CommandResult(success=True, output=hdr + tl.display(tasks))
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

    def _cmd_task(self, args: str) -> "CommandResult":
        if not args:
            return CommandResult(success=False, output="Uso: /task <descripción>")
        try:
            from core.task_list import TaskList
            tid = TaskList().create(args, agent_id="ser")
            return CommandResult(success=True, output=f"✅ Tarea [{tid}] creada: {args}")
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

    def _cmd_tools_list(self, args: str) -> "CommandResult":
        try:
            from core.tool_registry import ToolRegistry
            desc = ToolRegistry().describe()
            return CommandResult(success=True, output="🔧 Herramientas:\n\n" + desc)
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

    def _cmd_curiosity(self, args: str) -> "CommandResult":
        if not args:
            return CommandResult(success=False, output="Uso: /curiosity <tema>")
        try:
            from core.task_list import TaskList
            from pathlib import Path
            tid = TaskList().create(
                f"Investigar: {args}", priority="normal",
                tags="curiosity,learning", agent_id="eidos_libre"
            )
            interest = Path.home() / ".eidos" / "libre_interest.txt"
            interest.parent.mkdir(parents=True, exist_ok=True)
            with open(interest, "a") as f:
                f.write(f"{args}\n")
            return CommandResult(success=True, output=f"✅ [{tid}] Tema añadido: {args}")
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

    def _cmd_sync(self, args: str) -> "CommandResult":
        import subprocess
        from pathlib import Path
        try:
            eidos_dir = Path(os.environ.get("EIDOS_DIR", "/home/ser/EIDOS"))
            r = subprocess.run(
                ["bash", str(eidos_dir / "scripts" / "sync_knowledge.sh")],
                capture_output=True, text=True, timeout=300, cwd=str(eidos_dir)
            )
            out = (r.stdout + r.stderr)[-1200:].strip()
            return CommandResult(success=True, output=f"🔄 Sync:\n{out}")
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

    def _cmd_harvest(self, args: str) -> "CommandResult":
        import subprocess, sys as _sys
        from pathlib import Path
        sources   = args.split() if args else ["cve", "wikipedia"]
        eidos_dir = Path(os.environ.get("EIDOS_DIR", "/home/ser/EIDOS"))
        results   = []
        for src in sources:
            try:
                r = subprocess.run(
                    [_sys.executable, str(eidos_dir / "scripts" / "knowledge_harvester.py"),
                     "--source", src, "--limit", "50"],
                    capture_output=True, text=True, timeout=300, cwd=str(eidos_dir)
                )
                results.append(f"  {src}: {'✅' if r.returncode == 0 else '❌'}")
            except Exception as ex:
                results.append(f"  {src}: ❌ {ex}")
        return CommandResult(success=True, output="🌱 Harvest:\n" + "\n".join(results))

    def _cmd_screen(self, args: str) -> "CommandResult":
        try:
            from core.tool_registry import ToolRegistry
            tr  = ToolRegistry()
            cap = tr.call("screenshot", {})
            ocr = tr.call("get_screen_text", {})
            if args:
                ask = tr.call("ask_colony", {
                    "question": f"Pantalla:\n{ocr[:800]}\n\n{args}"
                })
                return CommandResult(success=True,
                                     output=f"📸 {cap}\n🔤 {ocr[:300]}\n💬 {ask}")
            return CommandResult(success=True,
                                 output=f"📸 {cap}\n🔤 Texto:\n{ocr[:800]}")
        except Exception as e:
            return CommandResult(success=False, output=f"Error: {e}")

    def _cmd_review(self, args: str) -> "CommandResult":
        """
        /review <target>  — EIDOS se cuestiona a sí mismo sobre algo que hizo o ve.
        /review history   — últimas revisiones
        target: URL, ruta de archivo, fragmento de código, resultado de acción
        """
        try:
            from core.self_reviewer import auto_review, format_review_history
            if not args or args.strip() in ("", "history", "log"):
                hist = format_review_history(10)
                return CommandResult(success=True, output=f"🔍 Historial de auto-revisiones:\n\n{hist}")
            result = auto_review(args.strip())
            icon = "✅" if result["verdict"] == "ok" else ("⚠️" if result["verdict"] == "issue" else "🔍")
            out = f"{icon} Veredicto: **{result['verdict'].upper()}**\n\n{result['detail']}"
            if "http_status" in result:
                out += f"\n\nHTTP: {result['http_status']}"
            if "raw_info" in result:
                out += f"\n\n```\n{result['raw_info'][:400]}\n```"
            return CommandResult(success=True, output=out)
        except Exception as e:
            return CommandResult(success=False, output=f"Error en self-review: {e}")

    def _cmd_binja(self, args: str) -> "CommandResult":
        """
        /binja <archivo>   — analiza binario con Binary Ninja MCP
        /binja status      — verifica si Binary Ninja MCP está activo
        /binja functions <archivo> — lista funciones
        Requiere: Binary Ninja instalado + eidos start
        """
        try:
            import requests
            BINJA_URL = "http://localhost:9009"
            sub = args.split(None, 1)
            action = sub[0].lower() if sub else "status"
            target = sub[1] if len(sub) > 1 else ""

            if action == "status":
                try:
                    r = requests.get(f"{BINJA_URL}/health", timeout=3)
                    if r.status_code == 200:
                        return CommandResult(success=True, output="✅ Binary Ninja MCP activo en :9009")
                    return CommandResult(success=False, output=f"⚠️ Binary Ninja MCP respondió {r.status_code}")
                except Exception:
                    return CommandResult(success=False, output=(
                        "❌ Binary Ninja MCP no disponible en :9009\n\n"
                        "Para activarlo:\n"
                        "1. Abre Binary Ninja\n"
                        "2. Instala plugin: Plugins > Manage Plugins > 'binary_ninja_mcp'\n"
                        "3. O manual: ~/EIDOS/tools/binary_ninja_mcp/\n"
                        "4. El servidor MCP arranca en :9009 automáticamente"
                    ))

            elif action in ("analyze", "open", "functions"):
                path = target or action
                from core.self_reviewer import review_binary
                result = review_binary(path)
                icon = "⚠️" if result["verdict"] == "issue" else "🔍"
                return CommandResult(success=True, output=f"{icon} Análisis binario: {path}\n\n{result['detail']}\n\n```\n{result.get('raw_info','')[:500]}\n```")

            else:
                # Asumir que es un path directo
                from core.self_reviewer import review_binary
                result = review_binary(args.strip())
                return CommandResult(success=True, output=f"🔍 {result['detail']}\n\n```\n{result.get('raw_info','')[:500]}\n```")

        except Exception as e:
            return CommandResult(success=False, output=f"Error binja: {e}")


# ── Singleton global ─────────────────────────────────────────────────────────

_handler: Optional[SlashCommandHandler] = None

def get_command_handler() -> SlashCommandHandler:
    """Obtiene la instancia singleton del command handler."""
    global _handler
    if _handler is None:
        _handler = SlashCommandHandler()
    return _handler


# ── CLI rápido ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    handler = SlashCommandHandler()

    if len(sys.argv) > 1:
        cmd = " ".join(sys.argv[1:])
        result = handler.handle(cmd)
        print(result.output)
        sys.exit(0 if result.success else 1)

    # Modo interactivo
    print("EIDOS Slash Commands - Modo Interactivo")
    print("Escribe /help para ver comandos disponibles")
    print("Escribe /exit para salir\n")

    while True:
        try:
            cmd = input("EIDOS> ").strip()

            if not cmd:
                continue

            result = handler.handle(cmd)
            print(result.output)

            if result.action == "exit":
                break

        except (KeyboardInterrupt, EOFError):
            print("\n👋 Saliendo...")
            break
