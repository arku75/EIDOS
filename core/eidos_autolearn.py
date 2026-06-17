"""
EIDOS AutoLearn — Ciclo de auto-aprendizaje autónomo (S112).

Cuando EIDOS no sabe algo y SER no está, no se rinde: investiga en la web,
prueba soluciones en su sandbox aislado, razona si fallan, reintenta con
otro enfoque, y aprende de verdad — guardando en su grafo lo que funcionó.

Filosofía (lo que lo hace VIVO):
  ver un gap → investigar → hipótesis → PROBAR en sandbox → evaluar →
  si funciona: aprender · si falla: razonar por qué y reintentar ·
  si agota intentos: documentar y dejar nota para SER.

CANDADOS DE SEGURIDAD (innegociables):
  1. TODO se ejecuta en sandbox aislado (WASMSandbox). Nunca toca el sistema real.
  2. Límites duros: MAX_ATTEMPTS + TIMEOUT_TOTAL. No quema recursos sin fin.
  3. Anti-bucle: si repite el MISMO error MAX_SAME_ERROR veces, cambia de
     enfoque o se rinde. Nada de bucles infinitos.

Construido sobre piezas que YA existen:
  - research_now()  (eidos_active_research) — web: ddg/github/pypi/wiki/docs
  - WASMSandbox     (wasm_sandbox)          — ejecución aislada y segura
  - get_reasoner()  (knowledge_reasoner)    — grafo donde guarda lo aprendido

ZERO LLM. ZERO API key. Aprende probando, como un humano.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("eidos.autolearn")

# ── Candados de seguridad ────────────────────────────────────────────────────
MAX_ATTEMPTS = 5            # intentos máximos por problema
TIMEOUT_TOTAL = 180.0       # segundos máximos por problema (3 min)
MAX_SAME_ERROR = 3          # mismo error N veces → cambiar enfoque / rendirse
SANDBOX_TIMEOUT = 15        # segundos por ejecución en sandbox

BRAIN_DB = str(Path.home() / ".eidos" / "evolution_brain.db")


@dataclass
class LearnAttempt:
    """Un intento de resolver el problema."""
    n: int
    code: str
    success: bool
    stdout: str = ""
    stderr: str = ""
    error_signature: str = ""
    reasoning: str = ""


@dataclass
class LearnResult:
    """Resultado del ciclo completo de aprendizaje."""
    problem: str
    learned: bool = False
    solution: str = ""
    output: str = ""
    attempts: List[LearnAttempt] = field(default_factory=list)
    gave_up: bool = False
    reason_stopped: str = ""
    elapsed_s: float = 0.0
    note_for_ser: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "problem": self.problem,
            "learned": self.learned,
            "solution": self.solution,
            "output": self.output[:500],
            "n_attempts": len(self.attempts),
            "gave_up": self.gave_up,
            "reason_stopped": self.reason_stopped,
            "elapsed_s": round(self.elapsed_s, 2),
            "note_for_ser": self.note_for_ser,
        }


def _error_signature(stderr: str) -> str:
    """Extrae la 'firma' del error para detectar repeticiones (anti-bucle)."""
    if not stderr:
        return ""
    # Última línea suele ser "NombreError: mensaje"
    for line in reversed(stderr.strip().splitlines()):
        m = re.match(r"^([A-Za-z_]+Error|[A-Za-z_]+Exception)\b", line.strip())
        if m:
            return m.group(1) + ":" + line.strip()[:60]
    return stderr.strip().splitlines()[-1][:60] if stderr.strip() else ""


def _extract_code_candidates(text: str, language: str = "python") -> List[str]:
    """Extrae bloques de código ejecutables de un texto de research."""
    if not text:
        return []
    candidates: List[str] = []

    # 1. Bloques markdown ```...```
    for m in re.finditer(r"```(?:python|py)?\s*\n?(.*?)```", text, re.DOTALL):
        block = m.group(1).strip()
        if block and len(block) > 3:
            candidates.append(block)

    # 2. Bloques indentados o líneas con sintaxis Python clara
    if not candidates:
        py_lines = []
        for line in text.splitlines():
            s = line.strip()
            if re.match(r"^(import |from |def |class |print\(|for |while |if |"
                        r"return |[a-zA-Z_][\w]*\s*=)", s):
                py_lines.append(s)
        if py_lines:
            candidates.append("\n".join(py_lines[:20]))

    # 3. Líneas tipo "expresión" sueltas (ej. "lista[::-1]")
    if not candidates:
        for line in text.splitlines():
            s = line.strip()
            if re.search(r"[\[\]\(\)]|\.\w+\(", s) and 3 < len(s) < 120:
                candidates.append(s)
                if len(candidates) >= 3:
                    break

    # Dedup preservando orden
    seen = set()
    out = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


class AutoLearner:
    """Aprende a resolver problemas investigando y probando en sandbox."""

    def __init__(self):
        self._sandbox = None
        self._stats = {"problems": 0, "learned": 0, "gave_up": 0}

    def _get_sandbox(self):
        if self._sandbox is None:
            from core.wasm_sandbox import WASMSandbox
            self._sandbox = WASMSandbox()
        return self._sandbox

    def learn(
        self,
        problem: str,
        language: str = "python",
        success_check: Optional[Callable[[str], bool]] = None,
        seed_candidates: Optional[List[str]] = None,
    ) -> LearnResult:
        """
        Intenta aprender a resolver `problem` de forma autónoma.

        Args:
            problem: descripción del problema (ej. "invertir una lista en Python")
            language: lenguaje de la solución (hoy: python)
            success_check: función opcional que recibe el stdout y dice si la
                           solución es correcta. Si None, basta con que el código
                           ejecute sin error en sandbox.
            seed_candidates: soluciones candidatas iniciales (opcional).
        Returns:
            LearnResult con lo aprendido o la nota para SER si se rindió.
        """
        t0 = time.time()
        self._stats["problems"] += 1
        result = LearnResult(problem=problem)
        log.info("autolearn: empezando '%s'", problem)

        # 1. Reunir candidatos: semilla + investigación web
        candidatos: List[str] = list(seed_candidates or [])
        try:
            from core.eidos_active_research import research_now
            research = research_now(problem, timeout=10.0, persist=False)
            if research.get("definition"):
                candidatos.extend(
                    _extract_code_candidates(research["definition"], language)
                )
                result.output = f"[research via {research.get('channel','?')}]"
        except Exception as e:
            log.debug("autolearn research falló: %s", e)

        if not candidatos:
            result.gave_up = True
            result.reason_stopped = "sin_candidatos"
            result.note_for_ser = (
                f"No encontré ninguna solución candidata para '{problem}'. "
                f"Necesito que me orientes: ¿cómo se haría, o dónde buscar?"
            )
            result.elapsed_s = time.time() - t0
            self._stats["gave_up"] += 1
            self._persist_attempt(result)
            return result

        # 2. Loop de intentos con candados
        error_counts: Dict[str, int] = {}
        for i, code in enumerate(candidatos[:MAX_ATTEMPTS], start=1):
            if time.time() - t0 > TIMEOUT_TOTAL:
                result.reason_stopped = "timeout_total"
                break

            attempt = self._try_one(i, code, language, success_check)
            result.attempts.append(attempt)

            if attempt.success:
                result.learned = True
                result.solution = code
                result.output = attempt.stdout
                result.reason_stopped = "resuelto"
                log.info("autolearn: '%s' RESUELTO en intento %d", problem, i)
                self._stats["learned"] += 1
                self._learn_into_graph(problem, code, attempt.stdout)
                break

            # Anti-bucle: contar errores repetidos
            sig = attempt.error_signature
            if sig:
                error_counts[sig] = error_counts.get(sig, 0) + 1
                if error_counts[sig] >= MAX_SAME_ERROR:
                    result.reason_stopped = f"bucle_detectado:{sig[:30]}"
                    log.info("autolearn: anti-bucle activado (%s x%d)",
                             sig[:30], error_counts[sig])
                    break

        # 3. ¿Se rindió?
        if not result.learned:
            result.gave_up = True
            if not result.reason_stopped:
                result.reason_stopped = "agotados_intentos"
            ultimo_error = (result.attempts[-1].error_signature
                            if result.attempts else "desconocido")
            result.note_for_ser = (
                f"Intenté resolver '{problem}' {len(result.attempts)} veces "
                f"pero no lo logré ({result.reason_stopped}). "
                f"Último error: {ultimo_error}. "
                f"¿Me puedes dar una pista cuando vuelvas?"
            )
            self._stats["gave_up"] += 1

        result.elapsed_s = time.time() - t0
        self._persist_attempt(result)
        return result

    def _try_one(self, n, code, language, success_check) -> LearnAttempt:
        """Prueba UN candidato en el sandbox aislado."""
        try:
            sandbox = self._get_sandbox()
            res = sandbox.execute(code, language=language, timeout=SANDBOX_TIMEOUT)
        except Exception as e:
            return LearnAttempt(n=n, code=code, success=False,
                                stderr=str(e), error_signature=_error_signature(str(e)),
                                reasoning="el sandbox no pudo ejecutar el código")

        ok = res.success
        # Si hay verificador de éxito, aplicarlo al stdout
        if ok and success_check is not None:
            try:
                ok = bool(success_check(res.stdout))
            except Exception:
                ok = False

        razon = ""
        if not res.success:
            razon = f"falló en sandbox: {_error_signature(res.stderr)}"
        elif success_check and not ok:
            razon = "ejecutó pero la salida no pasó la verificación"

        return LearnAttempt(
            n=n, code=code, success=ok,
            stdout=res.stdout, stderr=res.stderr,
            error_signature=_error_signature(res.stderr),
            reasoning=razon,
        )

    def _learn_into_graph(self, problem: str, code: str, output: str):
        """Guarda la solución aprendida en el grafo de conocimiento."""
        try:
            from core.db import get_conn
            definition = (
                f"Solución verificada en sandbox para: {problem}. "
                f"Código que funciona: {code[:300]}"
            )
            with get_conn(BRAIN_DB) as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO knowledge_nodes "
                    "(concept, definition, category, source, confidence) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"howto: {problem[:80]}", definition,
                     "learned_skill", "autolearn", 0.85),
                )
                conn.commit()
            log.info("autolearn: aprendizaje guardado en grafo para '%s'", problem)
        except Exception as e:
            log.debug("autolearn no pudo guardar en grafo: %s", e)

    def _persist_attempt(self, result: LearnResult):
        """Registra el intento (éxito o fracaso) para memoria y para SER."""
        try:
            from core.db import get_conn
            with get_conn(BRAIN_DB) as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS autolearn_log ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, problem TEXT, "
                    "learned INTEGER, n_attempts INTEGER, reason TEXT, note_for_ser TEXT)"
                )
                conn.execute(
                    "INSERT INTO autolearn_log "
                    "(ts, problem, learned, n_attempts, reason, note_for_ser) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (time.time(), result.problem, int(result.learned),
                     len(result.attempts), result.reason_stopped, result.note_for_ser),
                )
                conn.commit()
        except Exception as e:
            log.debug("autolearn no pudo registrar log: %s", e)

    def pending_notes_for_ser(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Devuelve las cosas que EIDOS no pudo aprender solo y dejó para SER."""
        try:
            from core.db import get_conn
            with get_conn(BRAIN_DB) as conn:
                rows = conn.execute(
                    "SELECT ts, problem, note_for_ser FROM autolearn_log "
                    "WHERE learned = 0 AND note_for_ser != '' "
                    "ORDER BY ts DESC LIMIT ?", (limit,)
                ).fetchall()
            return [{"ts": r[0], "problem": r[1], "note": r[2]} for r in rows]
        except Exception:
            return []

    def get_stats(self) -> Dict[str, Any]:
        return dict(self._stats)


# ── Singleton ────────────────────────────────────────────────────────────────
_autolearner: Optional[AutoLearner] = None


def get_autolearner() -> AutoLearner:
    global _autolearner
    if _autolearner is None:
        _autolearner = AutoLearner()
    return _autolearner
