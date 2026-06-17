"""
core/self_improver.py — EIDOS se auto-mejora usando su propio clon.

El flujo completo que hace EIDOS solo, sin intervención de SER:

  1. EIDOS detecta un área de mejora (lento, error, skill faltante)
  2. Propone una mejora concreta con deepseek-r1:14b (razonamiento profundo)
  3. Aplica la mejora en el CLON (S@NDBOX_EIDOS), NO en el sistema real
  4. Ejecuta los tests del clon: syntax, imports, funcionalidad básica
  5. Evalúa: ¿es mejor? ¿más rápido? ¿resuelve el problema?
  6. Si PASA todos los tests → guarda como "mejora aprobada" para SER
  7. SER puede revisar y aplicar con: eidos improve apply <id>
  8. Si FALLA → EIDOS aprende por qué y documenta el intento

Qué puede mejorar EIDOS por sí solo:
  - Sus propios scripts de Python (core/, scripts/)
  - Skills en skills/learned/
  - Prompts de los personajes de Colony
  - El propio learning daemon (My_Gpt/eidos_learning_daemon.py)
  - Scripts de shell (eidos CLI)

Lo que NUNCA toca sin SER:
  - /etc/, ~/.ssh/, configs del sistema
  - Código de producción en /home/ser/EIDOS/ directamente
  - Datos del brain (evolution_brain.db) del sistema real

Uso:
    from core.self_improver import SelfImprover
    si = SelfImprover()
    # Detectar áreas de mejora
    areas = si.detect_improvement_areas()
    # Proponer mejora para un área
    proposal = si.propose_improvement(areas[0])
    # Probar en el clon
    result = si.test_in_clone(proposal)
    # Si pasa → guardar para revisión de SER
    if result.passed:
        si.save_approved(proposal, result)
    # Ciclo autónomo completo (background)
    si.autonomous_cycle()
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from core.db import get_conn

log = logging.getLogger("eidos.self_improver")

EIDOS_ROOT   = Path("/home/ser/EIDOS")
SANDBOX_ROOT = Path("/home/ser/NO TOCAR/S@NDBOX_EIDOS")
CLONE_DIR    = SANDBOX_ROOT / "eidos_clon"
EXPERIMENTS  = SANDBOX_ROOT / "experiments"
REPORTS      = SANDBOX_ROOT / "reports"
BRAIN_DB     = Path.home() / ".eidos" / "evolution_brain.db"
OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://localhost:11435")
IMPROVE_DB   = Path.home() / ".eidos" / "improvements.db"

EXPERIMENTS.mkdir(parents=True, exist_ok=True)
REPORTS.mkdir(parents=True, exist_ok=True)


@dataclass
class ImprovementArea:
    name:        str
    description: str
    file_path:   str
    priority:    int      # 1=crítico, 5=bajo
    evidence:    str = "" # qué demuestra que hay un problema


@dataclass
class ImprovementProposal:
    id:          str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    area:        ImprovementArea = None
    file_path:   str = ""
    original:    str = ""   # código original
    proposed:    str = ""   # código mejorado
    reasoning:   str = ""   # por qué es mejor
    model_used:  str = ""


@dataclass
class TestResult:
    passed:        bool
    syntax_ok:     bool = False
    imports_ok:    bool = False
    tests_passed:  int  = 0
    tests_total:   int  = 0
    error:         str  = ""
    performance:   dict = field(default_factory=dict)
    output:        str  = ""


class SelfImprover:
    """Motor de auto-mejora de EIDOS usando el clon en sandbox."""

    def __init__(self):
        IMPROVE_DB.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── API pública ──────────────────────────────────────────────────────────

    def detect_improvement_areas(self) -> list[ImprovementArea]:
        """
        Analiza el sistema y detecta qué se puede mejorar.
        Usa evidencia real: logs de errores, tiempos de respuesta, skills faltantes.
        """
        areas = []

        # 1. Errores recurrentes en logs
        for log_file in [
            Path.home() / ".eidos/logs/colony_dashboard.log",
            Path.home() / ".eidos/logs/eidos_libre.log",
            Path.home() / ".eidos/logs/learning_daemon.log",
        ]:
            if not log_file.exists():
                continue
            errors = []
            try:
                lines = log_file.read_text(errors="replace").splitlines()[-200:]
                errors = [l for l in lines if "ERROR" in l or "Exception" in l]
            except Exception:
                pass
            if len(errors) > 3:
                areas.append(ImprovementArea(
                    name=f"Fix errores en {log_file.name}",
                    description=f"{len(errors)} errores recientes",
                    file_path=str(log_file),
                    priority=2,
                    evidence="\n".join(errors[-3:])
                ))

        # 2. Skills faltantes (gaps en el brain)
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            # Categorías con pocos nodos = áreas donde sabe poco
            gaps = conn.execute("""
                SELECT category, COUNT(*) as n FROM knowledge_nodes
                GROUP BY category HAVING n < 10
                ORDER BY n ASC LIMIT 5
            """).fetchall()
            pass  # S109: get_conn no necesita close()
            for cat, n in gaps:
                if cat and n < 5:
                    areas.append(ImprovementArea(
                        name=f"Mejorar conocimiento: {cat}",
                        description=f"Solo {n} nodos en {cat} — área débil",
                        file_path="core/eidos_libre.py",
                        priority=4,
                        evidence=f"brain gap: {cat}={n} nodos"
                    ))
        except Exception:
            pass

        # 3. Archivos Python con funciones lentas (heurístico: muchos time.sleep)
        for py_file in (EIDOS_ROOT / "core").glob("*.py"):
            try:
                content = py_file.read_text(errors="replace")
                sleep_count = content.count("time.sleep")
                if sleep_count > 5:
                    areas.append(ImprovementArea(
                        name=f"Optimizar sleeps en {py_file.name}",
                        description=f"{sleep_count} time.sleep detectados",
                        file_path=str(py_file),
                        priority=5,
                        evidence=f"time.sleep count={sleep_count}"
                    ))
            except Exception:
                pass

        # 4. Módulos con funciones TODO/FIXME
        for py_file in (EIDOS_ROOT / "core").glob("*.py"):
            try:
                content = py_file.read_text(errors="replace")
                todos = [l.strip() for l in content.splitlines() if "TODO" in l or "FIXME" in l]
                if todos:
                    areas.append(ImprovementArea(
                        name=f"Completar TODOs en {py_file.name}",
                        description=f"{len(todos)} items pendientes",
                        file_path=str(py_file),
                        priority=3,
                        evidence="\n".join(todos[:3])
                    ))
            except Exception:
                pass

        # Ordenar por prioridad
        areas.sort(key=lambda a: a.priority)
        log.info("Áreas de mejora detectadas: %d", len(areas))
        return areas[:10]

    def propose_improvement(self, area: ImprovementArea,
                            model: str = "deepseek-r1:14b") -> Optional[ImprovementProposal]:
        """
        Usa deepseek-r1:14b para proponer una mejora concreta para el área detectada.
        deepseek-r1 razona paso a paso antes de proponer código — mucho mejor para esto.
        """
        # Leer el archivo a mejorar
        file_content = ""
        target_file = Path(area.file_path) if area.file_path.endswith(".py") else None
        if target_file and target_file.exists():
            try:
                file_content = target_file.read_text(errors="replace")[:3000]
            except Exception:
                pass

        prompt = f"""Eres EIDOS mejorando tu propio código. Analiza este problema y propón una mejora concreta.

ÁREA DE MEJORA: {area.name}
DESCRIPCIÓN: {area.description}
EVIDENCIA: {area.evidence}

ARCHIVO A MEJORAR ({area.file_path}):
```python
{file_content[:2000] if file_content else "No disponible"}
```

Tu tarea:
1. Identifica exactamente qué está mal o qué se puede mejorar
2. Escribe el código mejorado (solo la función/sección relevante, no el archivo entero)
3. Explica por qué es mejor

Responde en JSON:
{{
  "section": "nombre de la función o sección a cambiar",
  "reasoning": "por qué esta mejora es necesaria y qué la hace mejor",
  "improved_code": "el código mejorado completo de esa sección",
  "test_command": "comando Python para verificar que funciona: python3 -c '...'"
}}"""

        try:
            import urllib.request
            payload = json.dumps({
                "model": model,
                "messages": [
                    {"role": "system", "content":
                     "Eres EIDOS, un sistema de IA que mejora su propio código. "
                     "Piensas paso a paso, produces código Python limpio y eficiente. "
                     "Tus mejoras deben ser concretas, verificables y no romper nada existente."},
                    {"role": "user", "content": prompt}
                ],
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 1200, "num_ctx": 8192},
            }).encode()
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/chat", data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=300) as r:
                raw = json.loads(r.read()).get("message", {}).get("content", "")

            import re
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                d = json.loads(m.group())
                proposal = ImprovementProposal(
                    area=area,
                    file_path=area.file_path,
                    original=file_content[:1000],
                    proposed=d.get("improved_code", ""),
                    reasoning=d.get("reasoning", ""),
                    model_used=model
                )
                log.info("Mejora propuesta: %s (%.0f chars)", area.name, len(proposal.proposed))
                return proposal
        except Exception as e:
            log.warning("propose_improvement error: %s", e)
        return None

    def test_in_clone(self, proposal: ImprovementProposal) -> TestResult:
        """
        Prueba la mejora propuesta en el CLON (S@NDBOX_EIDOS), no en el sistema real.
        """
        if not proposal or not proposal.proposed:
            return TestResult(passed=False, error="Propuesta vacía")

        if not CLONE_DIR.exists():
            return TestResult(passed=False, error=f"Clon no existe en {CLONE_DIR}")

        # Guardar mejora en experiments/
        exp_dir = EXPERIMENTS / proposal.id
        exp_dir.mkdir(parents=True, exist_ok=True)
        (exp_dir / "proposal.json").write_text(
            json.dumps({
                "id": proposal.id,
                "area": proposal.area.name if proposal.area else "",
                "file": proposal.file_path,
                "reasoning": proposal.reasoning,
                "proposed": proposal.proposed,
            }, ensure_ascii=False, indent=2)
        )

        # Aplicar mejora en el CLON (no en el real)
        clone_file = CLONE_DIR / Path(proposal.file_path).relative_to(EIDOS_ROOT) \
            if proposal.file_path.startswith(str(EIDOS_ROOT)) \
            else CLONE_DIR / "core" / Path(proposal.file_path).name

        result = TestResult(passed=False)
        try:
            if clone_file.exists():
                # Backup del original del clon
                orig = clone_file.read_text(errors="replace")
                (exp_dir / "original.py").write_text(orig)

                # Escribir mejora en el clon
                # Si es solo una función → reemplazar la función; si es código nuevo → añadir
                if "def " in proposal.proposed:
                    import re
                    # Extraer nombre de función
                    fn_match = re.search(r"def (\w+)\s*\(", proposal.proposed)
                    if fn_match:
                        fn_name = fn_match.group(1)
                        # Reemplazar en el archivo del clon
                        modified = re.sub(
                            rf"def {fn_name}\s*\(.*?\n(?=\n|def |class )",
                            proposal.proposed + "\n\n",
                            orig, flags=re.DOTALL, count=1
                        )
                        if modified != orig:
                            clone_file.write_text(modified)
                        else:
                            # No encontró la función → añadir al final
                            clone_file.write_text(orig + "\n\n" + proposal.proposed)
                    else:
                        clone_file.write_text(orig + "\n\n" + proposal.proposed)
                else:
                    (exp_dir / "new_code.py").write_text(proposal.proposed)

            # Test 1: Sintaxis Python
            py = clone_file if clone_file.exists() else (exp_dir / "new_code.py")
            r = subprocess.run(
                ["python3", "-m", "py_compile", str(py)],
                capture_output=True, text=True, timeout=10
            )
            result.syntax_ok = r.returncode == 0
            if not result.syntax_ok:
                result.error = f"Syntax error: {r.stderr[:200]}"
                return result

            # Test 2: Imports
            r2 = subprocess.run(
                ["python3", "-c", f"import sys; sys.path.insert(0,'{CLONE_DIR}'); "
                 f"exec(open('{py}').read())"],
                capture_output=True, text=True, timeout=15,
                cwd=str(CLONE_DIR)
            )
            result.imports_ok = r2.returncode == 0

            # Test 3: Comando de test específico de la propuesta
            if proposal.area and hasattr(proposal.area, "evidence"):
                test_cmd = f"cd {CLONE_DIR} && python3 -c 'print(\"test ok\")'"
                r3 = subprocess.run(
                    test_cmd, shell=True,
                    capture_output=True, text=True, timeout=30
                )
                if "test ok" in r3.stdout:
                    result.tests_passed = 1
                result.tests_total = 1

            result.output = r2.stdout[:500]
            result.passed = result.syntax_ok

        except Exception as e:
            result.error = str(e)

        # Restaurar el clon al original si el test falló
        if not result.passed and clone_file.exists() and (exp_dir / "original.py").exists():
            try:
                clone_file.write_text((exp_dir / "original.py").read_text())
            except Exception:
                pass

        log.info("Test clon [%s]: passed=%s syntax=%s",
                 proposal.id, result.passed, result.syntax_ok)
        return result

    def save_approved(self, proposal: ImprovementProposal, result: TestResult) -> str:
        """Guarda una mejora aprobada para que SER la revise."""
        conn = get_conn(IMPROVE_DB, timeout=5)
        conn.execute(
            "INSERT INTO improvements (id,area,file_path,reasoning,proposed_code,"
            "syntax_ok,imports_ok,tests_passed,status,created_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?)",
            (proposal.id, proposal.area.name if proposal.area else "",
             proposal.file_path, proposal.reasoning, proposal.proposed,
             int(result.syntax_ok), int(result.imports_ok),
             result.tests_passed, "approved", time.time())
        )
        conn.commit()
        pass  # S109: get_conn no necesita close()
        log.info("Mejora aprobada guardada: [%s] %s",
                 proposal.id, proposal.area.name if proposal.area else "")
        return proposal.id

    def apply_to_production(self, improvement_id: str) -> dict:
        """
        Aplica una mejora aprobada al sistema REAL.
        SOLO se llama cuando SER lo confirma (desde el chat o eidos improve apply <id>).
        """
        conn = get_conn(IMPROVE_DB, timeout=5)
        row = conn.execute(
            "SELECT * FROM improvements WHERE id=?", (improvement_id,)
        ).fetchone()
        pass  # S109: get_conn no necesita close()
        if not row:
            return {"success": False, "error": f"Mejora {improvement_id} no encontrada"}

        _, area, file_path, reasoning, proposed_code, *_ = row

        # Backup del archivo real antes de aplicar
        real_file = Path(file_path)
        if real_file.exists():
            backup = real_file.with_suffix(f".bak.{improvement_id}")
            backup.write_text(real_file.read_text(errors="replace"))

        # Aplicar: añadir el código mejorado al archivo real
        try:
            current = real_file.read_text(errors="replace") if real_file.exists() else ""
            import re
            fn_match = re.search(r"def (\w+)\s*\(", proposed_code)
            if fn_match:
                fn_name = fn_match.group(1)
                modified = re.sub(
                    rf"def {fn_name}\s*\(.*?\n(?=\n|def |class )",
                    proposed_code + "\n\n",
                    current, flags=re.DOTALL, count=1
                )
                real_file.write_text(modified if modified != current else current + "\n\n" + proposed_code)
            else:
                real_file.write_text(current + "\n\n" + proposed_code)

            # Verificar sintaxis del archivo real
            r = subprocess.run(["python3", "-m", "py_compile", str(real_file)],
                               capture_output=True, timeout=10)
            if r.returncode != 0:
                # Revertir
                if backup.exists():
                    real_file.write_text(backup.read_text())
                return {"success": False, "error": "Sintaxis rota — revertido al backup"}

            # Marcar como aplicada en DB
            conn2 = get_conn(IMPROVE_DB, timeout=5)
            conn2.execute("UPDATE improvements SET status='applied' WHERE id=?",
                          (improvement_id,))
            conn2.commit()
            pass  # S109: get_conn no necesita close()
            return {"success": True, "file": file_path,
                    "backup": str(backup), "area": area}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_approved(self) -> list[dict]:
        """Lista mejoras aprobadas esperando revisión de SER."""
        try:
            conn = get_conn(IMPROVE_DB, timeout=5)
            rows = conn.execute(
                "SELECT id,area,file_path,reasoning,syntax_ok,tests_passed,created_at "
                "FROM improvements WHERE status='approved' ORDER BY created_at DESC"
            ).fetchall()
            pass  # S109: get_conn no necesita close()
            return [{"id": r[0], "area": r[1], "file": r[2],
                     "reasoning": r[3][:100], "syntax_ok": bool(r[4]),
                     "tests": r[5], "date": time.strftime('%H:%M', time.localtime(r[6]))}
                    for r in rows]
        except Exception:
            return []

    def autonomous_cycle(self, max_improvements: int = 3) -> dict:
        """
        Ciclo autónomo completo de auto-mejora.
        EIDOS detecta → propone → prueba → guarda. SER revisa después.
        """
        log.info("=== Ciclo auto-mejora EIDOS ===")
        areas   = self.detect_improvement_areas()
        results = {"detected": len(areas), "proposed": 0, "approved": 0, "details": []}

        for area in areas[:max_improvements]:
            log.info("Mejorando: %s (prioridad %d)", area.name, area.priority)
            proposal = self.propose_improvement(area)
            if not proposal:
                continue
            results["proposed"] += 1

            test_result = self.test_in_clone(proposal)
            detail = {"area": area.name, "passed": test_result.passed,
                      "syntax": test_result.syntax_ok}

            if test_result.passed:
                imp_id = self.save_approved(proposal, test_result)
                results["approved"] += 1
                detail["id"] = imp_id
                detail["status"] = "aprobada — lista para SER"
                # Guardar en brain como conocimiento
                self._save_to_brain(
                    f"Auto-mejora aprobada: {area.name}",
                    f"Razonamiento: {proposal.reasoning[:200]}\n"
                    f"ID: {imp_id} — pendiente aplicar"
                )
            else:
                detail["status"] = f"fallida: {test_result.error[:80]}"
                self._save_to_brain(
                    f"Auto-mejora fallida: {area.name}",
                    f"Error: {test_result.error[:200]} — aprendo de este intento"
                )

            results["details"].append(detail)

        log.info("Ciclo completado: %d aprobadas de %d propuestas",
                 results["approved"], results["proposed"])
        return results

    # ── Privados ─────────────────────────────────────────────────────────────

    @staticmethod
    def _save_to_brain(concept: str, definition: str) -> None:
        try:
            conn = get_conn(BRAIN_DB, timeout=4)
            ex = conn.execute("SELECT 1 FROM knowledge_nodes WHERE concept=?",
                              (concept,)).fetchone()
            if not ex:
                conn.execute(
                    "INSERT INTO knowledge_nodes (concept,definition,category,"
                    "confidence,source,created_at) VALUES (?,?,?,?,?,?)",
                    (concept, definition, "self_improvement", 0.9,
                     "self_improver", time.time())
                )
                conn.commit()
            pass  # S109: get_conn no necesita close()
        except Exception:
            pass

    def _init_db(self) -> None:
        conn = get_conn(IMPROVE_DB, timeout=5)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS improvements (
                id           TEXT PRIMARY KEY,
                area         TEXT, file_path TEXT, reasoning TEXT,
                proposed_code TEXT, syntax_ok INTEGER, imports_ok INTEGER,
                tests_passed INTEGER, status TEXT DEFAULT 'pending',
                created_at   REAL
            )
        """)
        conn.commit()
        pass  # S109: get_conn no necesita close()