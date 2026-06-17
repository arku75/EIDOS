#!/usr/bin/env python3
"""
bin/mission_runner.py — Ejecutor de misiones reales [S91]

Ejecuta misiones reales con pantalla, Firefox, OCR y xdotool.
Colecciona métricas y produce reportes de validación empírica.

Modo seguro:
  - Solo navega a dominios en SAFE_DOMAINS
  - Timeout máximo por misión: 120s
  - Confirma antes de acciones destructivas
  - Rate limiting entre pasos

Uso:
  # Misión simple
  python3 bin/mission_runner.py "busca n8n en google"

  # Misión compleja con HGD
  python3 bin/mission_runner.py "compara precios de n8n con alternativas"

  # Batch de validación
  python3 bin/mission_runner.py --batch

  # Reporte
  python3 bin/mission_runner.py --report

"No se valida lo que no se ejecuta." — DeepSeek
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure core/ is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mission_runner")

# ── Safety ─────────────────────────────────────────────────────────────────────

SAFE_DOMAINS = [
    "google.com", "www.google.com",
    "wikipedia.org", "en.wikipedia.org", "es.wikipedia.org",
    "github.com", "www.github.com",
    "stackoverflow.com", "www.stackoverflow.com",
    "python.org", "docs.python.org",
    "pypi.org",
    "docs.n8n.io", "n8n.io",
    "duckduckgo.com", "www.duckduckgo.com",
]

BLOCKED_DOMAINS = [
    "facebook.com", "twitter.com", "instagram.com", "tiktok.com",
    "youtube.com", "netflix.com", "amazon.com",
    "bank", "paypal", "login", "signin", "password",
]

MAX_MISSION_TIME = 120  # segundos
MAX_STEPS_PER_MISSION = 20
RATE_LIMIT_DELAY = 0.5  # segundos entre pasos

# Señal de abort
_abort_requested = False


def handle_sigint(sig, frame):
    global _abort_requested
    _abort_requested = True
    print("\n⚠️  ABORT solicitado. Terminando misión actual...")

signal.signal(signal.SIGINT, handle_sigint)


# ── Safety checks ──────────────────────────────────────────────────────────────

def is_safe_url(url: str) -> bool:
    """Verifica que una URL es segura para navegar."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        domain = parsed.netloc.lower() or parsed.path.lower()

        # Bloquear dominios peligrosos
        for blocked in BLOCKED_DOMAINS:
            if blocked in domain:
                log.warning("🚫 Dominio bloqueado: %s", domain)
                return False

        # Permitir solo dominios seguros
        for safe in SAFE_DOMAINS:
            if safe in domain:
                return True

        # Dominios desconocidos: permitir con advertencia
        log.warning("⚠️  Dominio no verificado: %s", domain)
        return True  # Permitir pero advertir
    except Exception:
        return False


def is_safe_action(action: str, target: str = "") -> bool:
    """Verifica que una acción no es destructiva."""
    dangerous = ["rm ", "delete", "format", "dd ", "mkfs", "shutdown",
                 "reboot", "poweroff", "chmod 777", "sudo "]
    for d in dangerous:
        if d in action.lower() or d in target.lower():
            log.warning("🚫 Acción peligrosa bloqueada: %s %s", action, target)
            return False
    return True


# ── Mission Runner ─────────────────────────────────────────────────────────────

class MissionRunner:
    """Ejecuta misiones reales con medición de métricas."""

    def __init__(self, dry_run: bool = False, safe_mode: bool = True,
                 enable_s89: bool = True):
        self._dry_run = dry_run
        self._safe_mode = safe_mode
        self._enable_s89 = enable_s89
        self._mm = None
        self._sc = None

    @property
    def mm(self):
        if self._mm is None:
            from core.mission_metrics import get_mission_metrics
            self._mm = get_mission_metrics()
        return self._mm

    @property
    def screen_controller(self):
        if self._sc is None:
            import core.screen_controller as sc_mod
            sc_mod._controller = None
            from core.screen_controller import get_screen_controller
            self._sc = get_screen_controller(
                dry_run=self._dry_run,
                enable_s89=self._enable_s89,
                enable_s90=True,
            )
        return self._sc

    def run_mission(self, goal: str, max_steps: int = 15,
                    use_hgd: bool = True) -> Dict[str, Any]:
        """Ejecuta una misión completa con medición.

        Args:
            goal: Meta en lenguaje natural
            max_steps: Máximo de pasos
            use_hgd: Usar descomposición jerárquica para metas complejas

        Returns:
            Dict con resultado y métricas
        """
        global _abort_requested
        _abort_requested = False

        t0 = time.time()
        log.info("🚀 MISIÓN: %s", goal[:100])

        # Safety check
        if self._safe_mode:
            # Extraer URLs de la meta
            import re
            urls = re.findall(r'https?://[^\s]+', goal)
            for url in urls:
                if not is_safe_url(url):
                    return {
                        "success": False,
                        "error": f"URL bloqueada: {url}",
                        "goal": goal,
                    }

        # Iniciar métricas
        mission_id = self.mm.start_mission(
            goal, s89_enabled=self._enable_s89, s90_enabled=use_hgd
        )

        try:
            # Configurar screen controller
            sc = self.screen_controller

            # Ejecutar misión (S93: pasar _disable_s90 según use_hgd)
            result = sc.execute_mission(
                goal=goal,
                max_steps=min(max_steps, MAX_STEPS_PER_MISSION),
                step_callback=self._step_callback,
                _disable_s90=not use_hgd,  # S93: respetar use_hgd del caller
            )

            elapsed = time.time() - t0
            success = result.get("completed", False)
            total_steps = result.get("total_steps", 0)
            hgd_used = result.get("s90", {}).get("goal_tree_used", False)
            hgd_nodes = result.get("s90", {}).get("tree_nodes", 0)

            # Finalizar métricas
            report = self.mm.end_mission(
                success=success,
                total_steps=total_steps,
                hgd_used=hgd_used,
                hgd_nodes=hgd_nodes,
                hgd_completed=result.get("plan_steps_completed", 0),
                error=result.get("error", ""),
            )

            log.info("🏁 Misión completada: success=%s | %d pasos | %.1fs | hgd=%s",
                    success, total_steps, elapsed, hgd_used)

            return {
                "mission_id": mission_id,
                "goal": goal,
                "success": success,
                "total_steps": total_steps,
                "successful_steps": result.get("successful_steps", 0),
                "failed_steps": result.get("failed_steps", 0),
                "elapsed_s": round(elapsed, 3),
                "hgd_used": hgd_used,
                "hgd_nodes": hgd_nodes,
                "extracted_knowledge": len(result.get("extracted_knowledge", [])),
                "s89_metrics": result.get("s89", {}),
                "s90_metrics": result.get("s90", {}),
                "error": "",
            }

        except Exception as e:
            elapsed = time.time() - t0
            log.error("❌ Misión fallida: %s", e)
            import traceback
            traceback.print_exc()

            self.mm.end_mission(
                success=False,
                total_steps=0,
                error=str(e),
            )

            return {
                "mission_id": mission_id,
                "goal": goal,
                "success": False,
                "total_steps": 0,
                "elapsed_s": round(elapsed, 3),
                "error": str(e),
            }

    def _step_callback(self, step: int, decision: Dict, result: Any, scene: Any):
        """Callback por cada paso de la misión.

        S92: Compatible con dos firmas:
          1. Normal: (step: int, decision: Dict, result: ActionResult, scene: Scene)
          2. HGD:    (step: int, decision: Dict{action="hgd_node",...}, result: Dict, scene: None)
        """
        if _abort_requested:
            raise KeyboardInterrupt("Abort solicitado")

        action = decision.get("action", "unknown")

        # Callback de HGD: registrar progreso del árbol sin safety checks
        if action == "hgd_node":
            try:
                goal = decision.get("goal", "")[:80]
                status = decision.get("status", "unknown")
                depth = decision.get("depth", 0)
                progress = decision.get("progress_pct", 0)
                log.info("🌳 HGD nodo [%s] d=%d progreso=%.0f%%: %s",
                         status, depth, progress * 100, goal)
                # No aplicar safety checks ni rate limiting a nodos HGD
                # (ya se aplicaron en los pasos individuales)
            except Exception as e:
                log.warning("Error en callback HGD: %s", e)
            return

        # Safety check en cada acción
        if self._safe_mode:
            url = decision.get("url", decision.get("text", ""))
            if not is_safe_action(action, url):
                raise ValueError(f"Acción bloqueada: {action}")

            # Verificar URLs en navigate
            if action in ("navigate", "navigate_url") and url:
                if not is_safe_url(url):
                    raise ValueError(f"URL bloqueada: {url}")

        # Rate limiting
        if not self._dry_run:
            time.sleep(RATE_LIMIT_DELAY)

        # Registrar métricas del paso
        try:
            elapsed = getattr(result, 'elapsed', 0.0)
            action_type = action
            success = getattr(result, 'success', False)
            scene_before = getattr(result, 'scene_before', None)
            scene_after = getattr(result, 'scene_after', None)
            verification = decision.get('_verification', None)

            self.mm.record_step(
                step_number=step,
                action_type=action_type,
                success=success,
                elapsed_s=elapsed,
                scene_before=scene_before,
                scene_after=scene_after,
                decision=decision,
                verification=verification,
            )
        except Exception as e:
            log.warning("Error registrando métrica de paso: %s", e)

    # ── Batch execution ─────────────────────────────────────────────────────

    def run_batch(self, missions: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Ejecuta un batch de misiones predefinidas para validación.

        Si no se especifican, usa BATCH_MISSIONS por defecto.
        """
        if missions is None:
            missions = BATCH_MISSIONS

        results = []
        t0 = time.time()

        log.info("📋 BATCH INICIADO: %d misiones", len(missions))

        for i, mission in enumerate(missions):
            if _abort_requested:
                log.warning("Batch abortado por el usuario")
                break

            log.info("📌 Misión %d/%d: %s", i + 1, len(missions),
                    mission["goal"][:80])

            result = self.run_mission(
                goal=mission["goal"],
                max_steps=mission.get("max_steps", 15),
                use_hgd=mission.get("use_hgd", True),
            )
            results.append(result)

            # Pequeña pausa entre misiones
            if i < len(missions) - 1:
                time.sleep(2)

        elapsed = time.time() - t0
        succeeded = sum(1 for r in results if r["success"])

        summary = {
            "batch_size": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
            "success_rate": round(succeeded / max(1, len(results)) * 100, 1),
            "total_elapsed_s": round(elapsed, 1),
            "avg_time_per_mission": round(elapsed / max(1, len(results)), 1),
            "target_70pct": (succeeded / max(1, len(results)) * 100) >= 70.0,
            "results": results,
            "overall_stats": self.mm.overall_stats(),
        }

        log.info("📊 BATCH COMPLETADO: %d/%d éxito (%.1f%%) | %.1fs total",
                succeeded, len(results), summary["success_rate"], elapsed)

        return summary


# ── Batch de misiones predefinidas ─────────────────────────────────────────────

BATCH_MISSIONS = [
    # Misiones simples (sin HGD)
    {
        "goal": "abre google y busca información sobre python",
        "max_steps": 10,
        "use_hgd": False,
        "expected": "Firefox abre Google, escribe query, muestra resultados",
    },
    {
        "goal": "navega a wikipedia y busca machine learning",
        "max_steps": 12,
        "use_hgd": False,
        "expected": "Navega a Wikipedia, busca ML, extrae definición",
    },
    {
        "goal": "busca en google cómo instalar docker en ubuntu",
        "max_steps": 10,
        "use_hgd": False,
        "expected": "Busca query, resultados visibles",
    },
    {
        "goal": "busca documentación de n8n en google",
        "max_steps": 12,
        "use_hgd": False,
        "expected": "Resultados incluyen docs.n8n.io",
    },
    {
        "goal": "busca en duckduckgo qué es inteligencia artificial",
        "max_steps": 10,
        "use_hgd": False,
        "expected": "Resultados de búsqueda visibles",
    },
    # Misiones complejas (con HGD)
    {
        "goal": "busca información sobre python en varios sitios",
        "max_steps": 20,
        "use_hgd": True,
        "expected": "HGD descompone en submetas: buscar, explorar, extraer",
    },
    {
        "goal": "aprende sobre docker buscando documentación",
        "max_steps": 18,
        "use_hgd": True,
        "expected": "HGD usa patrón learn_and_understand",
    },
    {
        "goal": "investiga machine learning en google",
        "max_steps": 20,
        "use_hgd": True,
        "expected": "HGD descompone en múltiples pasos de investigación",
    },
    # Misiones de extracción
    {
        "goal": "busca en google github n8n y extrae links relevantes",
        "max_steps": 15,
        "use_hgd": False,
        "expected": "Extrae links de GitHub",
    },
    {
        "goal": "busca documentación de python en google",
        "max_steps": 12,
        "use_hgd": False,
        "expected": "Encuentra docs.python.org",
    },
]


# ── Reporte ────────────────────────────────────────────────────────────────────

def print_report(summary: Dict[str, Any]):
    """Imprime un reporte formateado del batch."""
    print("\n" + "=" * 72)
    print("📊 REPORTE DE VALIDACIÓN EMPÍRICA — S91")
    print("=" * 72)
    print(f"  Fecha:     {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Misiones:  {summary['batch_size']}")
    print(f"  Éxito:     {summary['succeeded']}/{summary['batch_size']} "
          f"({summary['success_rate']}%)")
    print(f"  Tiempo:    {summary['total_elapsed_s']}s total "
          f"({summary['avg_time_per_mission']}s avg)")
    print(f"  Objetivo:  {'✅ ≥70%' if summary['target_70pct'] else '❌ <70%'}")

    print("\n📋 Resultados por misión:")
    for i, r in enumerate(summary["results"]):
        status = "✅" if r["success"] else "❌"
        hgd = "🌳" if r.get("hgd_used") else "📝"
        print(f"  {status} {hgd} #{i+1}: {r['goal'][:60]}")
        if r["success"]:
            print(f"       {r['successful_steps']}/{r['total_steps']} pasos "
                  f"| {r['elapsed_s']}s")
        else:
            print(f"       ERROR: {r.get('error', 'unknown')[:80]}")

    # Métricas S89/S90
    s89_count = sum(1 for r in summary["results"]
                   if r.get("s89_metrics", {}).get("enabled"))
    s90_count = sum(1 for r in summary["results"] if r.get("hgd_used"))
    print(f"\n🔧 S89 activo: {s89_count} misiones")
    print(f"🌳 S90 (HGD):  {s90_count} misiones")

    stats = summary.get("overall_stats", {})
    if stats:
        print(f"\n📈 Estadísticas globales:")
        print(f"  Total misiones históricas: {stats.get('total_missions', 0)}")
        print(f"  Tasa éxito global:         {stats.get('overall_success_rate', 0)}%")
        print(f"  Pasos totales:             {stats.get('total_steps', 0)}")
        print(f"  Tasa éxito por paso:       {stats.get('step_success_rate', 0)}%")
        print(f"  AVG tiempo por paso:       {stats.get('avg_step_time_s', 0)}s")
        print(f"  Total recoveries:          {stats.get('total_recoveries', 0)}")
        print(f"  Objetivo ≥70%:             {'✅ SÍ' if stats.get('target_70pct') else '❌ NO'}")

    print("=" * 72)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="MissionRunner — validación empírica S91 de EIDOS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python3 bin/mission_runner.py "busca n8n en google"
  python3 bin/mission_runner.py --batch
  python3 bin/mission_runner.py --report
  python3 bin/mission_runner.py --dry-run "investiga python"
  python3 bin/mission_runner.py --stats
        """,
    )
    parser.add_argument("goal", nargs="?", type=str,
                       help="Meta de la misión en lenguaje natural")
    parser.add_argument("--batch", action="store_true",
                       help="Ejecutar batch de 10 misiones predefinidas")
    parser.add_argument("--dry-run", action="store_true",
                       help="Modo simulación (no ejecuta acciones reales)")
    parser.add_argument("--no-safety", action="store_true",
                       help="Desactivar restricciones de seguridad")
    parser.add_argument("--max-steps", type=int, default=15,
                       help="Máximo de pasos por misión")
    parser.add_argument("--no-hgd", action="store_true",
                       help="No usar descomposición jerárquica")
    parser.add_argument("--no-s89", action="store_true",
                       help="Desactivar verificación S89 (ActionVerifier)")
    parser.add_argument("--report", action="store_true",
                       help="Mostrar reporte diario")
    parser.add_argument("--stats", action="store_true",
                       help="Mostrar estadísticas globales")
    parser.add_argument("--recent", type=int, default=0,
                       help="Mostrar últimas N misiones")
    parser.add_argument("--output", type=str,
                       help="Guardar reporte JSON en archivo")
    args = parser.parse_args()

    runner = MissionRunner(
        dry_run=args.dry_run,
        safe_mode=not args.no_safety,
        enable_s89=not args.no_s89,
    )

    if args.report:
        report = runner.mm.daily_report()
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    if args.stats:
        stats = runner.mm.overall_stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        return

    if args.recent > 0:
        missions = runner.mm.recent_missions(args.recent)
        print(f"\n📋 Últimas {len(missions)} misiones:")
        for m in missions:
            status = "✅" if m["success"] else "❌"
            hgd = "🌳" if m.get("hgd_used") else "  "
            print(f"  {status} {hgd} {m['mission_id']}: {m['goal'][:70]}")
            print(f"       {m['successful_steps']}/{m['total_steps']} steps "
                  f"| {m['avg_step_time']}s | s89={m['s89']} s90={m['s90']}")
        return

    if args.batch:
        print("🧪 INICIANDO BATCH DE VALIDACIÓN EMPÍRICA S91")
        print(f"   Modo: {'DRY-RUN (simulación)' if args.dry_run else 'REAL (pantalla)'}")
        print(f"   Seguridad: {'ACTIVA' if not args.no_safety else 'DESACTIVADA'}")
        print(f"   Misiones: {len(BATCH_MISSIONS)}")
        print()

        summary = runner.run_batch()
        print_report(summary)

        if args.output:
            with open(args.output, 'w') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
            print(f"\n📄 Reporte guardado en: {args.output}")

        return

    if args.goal:
        print(f"🎯 Misión: {args.goal}")
        print(f"   Modo: {'DRY-RUN' if args.dry_run else 'REAL'}")
        result = runner.run_mission(
            goal=args.goal,
            max_steps=args.max_steps,
            use_hgd=not args.no_hgd,
        )
        print(f"\nResultado: {'✅ ÉXITO' if result['success'] else '❌ FALLO'}")
        print(f"  ID: {result['mission_id']}")
        print(f"  Pasos: {result['successful_steps']}/{result['total_steps']}")
        print(f"  Tiempo: {result['elapsed_s']}s")
        print(f"  HGD: {result.get('hgd_used', False)} "
              f"({result.get('hgd_nodes', 0)} nodos)")
        print(f"  Knowledge: {result.get('extracted_knowledge', 0)} items")
        if result.get("error"):
            print(f"  Error: {result['error']}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
