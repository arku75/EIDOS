"""
core/night_cycle.py — EIDOS ciclo nocturno autónomo.

Se ejecuta a las 3am via cron. EIDOS trabaja mientras SER duerme:
  1. Analiza el clon (bandit + vulture) → detecta problemas
  2. Compara clon vs real → propone sincronización
  3. Limpia el brain (deduplicación de nodos basura)
  4. Busca libros/temas nuevos en Open Library
  5. Reporta a SER via Telegram con el resumen del trabajo nocturno

REGLA: NUNCA modifica el sistema real. Solo el clon y el brain.
"""
import subprocess, time, sqlite3, uuid, logging, re, os
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from core.db import get_conn
from core.paths import EIDOS_HOME, REPO_ROOT, SANDBOX_ROOT, USER_HOME

log = logging.getLogger("night_cycle")

BRAIN_DB     = EIDOS_HOME / "evolution_brain.db"
CLONE_DIR    = SANDBOX_ROOT / "eidos_clon"
REAL_DIR     = REPO_ROOT
REPORTS_DIR  = SANDBOX_ROOT / "reports"
TELEGRAM_CHAT_ID = "7060736317"

# S63: herramientas de análisis externas (lectura-solo)
ANALIZADOR_DIR = Path(os.environ.get("EIDOS_ANALYZER_DIR", str(USER_HOME / "MIS PROGRAMAS" / "ANALIZADOR"))).expanduser()
JSCPD_BIN      = ANALIZADOR_DIR / "jscpd" / "jscpd.sh"
TOKEI_BIN      = ANALIZADOR_DIR / "tokei" / "tokei"


def _save_to_brain(concept: str, definition: str, category: str, confidence: float = 0.8):
    try:
        conn = get_conn(BRAIN_DB)
        conn.execute(
            "INSERT OR REPLACE INTO knowledge_nodes (id,concept,definition,category,confidence,"
            "source,last_used,agent_id,character) VALUES (?,?,?,?,?,'night_cycle',?,  'eidos','EIDOS')",
            (str(uuid.uuid4()), concept, definition, category, confidence, time.time())
        )
        conn.commit()
        pass  # S109: get_conn no necesita close()
    except Exception as e:
        log.error(f"brain save: {e}")


def analyze_clone() -> Dict[str, Any]:
    """Analiza el clon con bandit (seguridad) y detecta módulos desincronizados."""
    results = {"bandit": {}, "sync_diff": [], "timestamp": datetime.now().isoformat()}
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Bandit en el clon
    try:
        r = subprocess.run(
            ["bandit", "-r", str(CLONE_DIR / "core"), "-f", "json", "-q", "--exit-zero"],
            capture_output=True, text=True, timeout=120
        )
        if r.stdout.strip():
            import json
            data = json.loads(r.stdout)
            metrics = data.get("metrics", {}).get("_totals", {})
            results["bandit"] = {
                "high":   metrics.get("SEVERITY.HIGH", 0),
                "medium": metrics.get("SEVERITY.MEDIUM", 0),
                "low":    metrics.get("SEVERITY.LOW", 0),
            }
            log.info(f"Bandit: H={results['bandit']['high']} M={results['bandit']['medium']} L={results['bandit']['low']}")
    except Exception as e:
        log.warning(f"bandit falló: {e}")

    # 2. Diferencias real vs clon
    try:
        real_mods  = set(p.name for p in (REAL_DIR / "core").glob("*.py"))
        clone_mods = set(p.name for p in (CLONE_DIR / "core").glob("*.py"))
        only_in_real  = real_mods - clone_mods
        only_in_clone = clone_mods - real_mods
        results["sync_diff"] = {
            "real_has_not_clone": sorted(only_in_real),
            "clone_has_not_real": sorted(only_in_clone),
        }
        if only_in_real:
            log.info(f"Módulos solo en REAL: {only_in_real}")
    except Exception as e:
        log.warning(f"sync_diff: {e}")

    return results


def analyze_tokei(target: Path = None) -> Dict[str, Any]:
    """tokei: conteo LOC por lenguaje. Lectura-solo. Output a REPORTS_DIR."""
    target = target or (REAL_DIR / "core")
    out: Dict[str, Any] = {"languages": {}, "total_loc": 0}
    bin_path = TOKEI_BIN if TOKEI_BIN.exists() else Path("/usr/bin/tokei")
    if not bin_path.exists():
        out["error"] = "tokei no instalado"
        return out
    try:
        r = subprocess.run(
            [str(bin_path), "--output", "json", str(target)],
            capture_output=True, text=True, timeout=60
        )
        if r.returncode == 0 and r.stdout.strip():
            import json as _json
            data = _json.loads(r.stdout)
            tot = data.get("Total", {})
            out["total_loc"] = tot.get("code", 0)
            for lang, stats in data.items():
                if lang == "Total" or not isinstance(stats, dict):
                    continue
                code = stats.get("code", 0)
                if code > 0:
                    out["languages"][lang] = code
            (REPORTS_DIR / "tokei_last.json").write_text(r.stdout)
        else:
            out["error"] = (r.stderr or "tokei sin salida")[:200]
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


def analyze_jscpd(target: Path = None) -> Dict[str, Any]:
    """jscpd: detección de duplicación. Lectura-solo. Output a REPORTS_DIR."""
    target = target or (REAL_DIR / "core")
    out: Dict[str, Any] = {"clones": 0, "duplicated_lines": 0, "files_with_clones": 0}
    if not JSCPD_BIN.exists():
        out["error"] = "jscpd.sh no encontrado"
        return out
    try:
        report_dir = REPORTS_DIR / "jscpd"
        report_dir.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            ["bash", str(JSCPD_BIN), str(target),
             "--reporters", "json", "--silent",
             "--output", str(report_dir)],
            capture_output=True, text=True, timeout=180,
            cwd=str(ANALIZADOR_DIR / "jscpd"),
        )
        json_report = report_dir / "jscpd-report.json"
        if json_report.exists():
            import json as _json
            data = _json.loads(json_report.read_text())
            stats = data.get("statistics", {}).get("total", {})
            out["clones"] = stats.get("clones", 0)
            out["duplicated_lines"] = stats.get("duplicatedLines", 0)
            out["files_with_clones"] = stats.get("clonedFiles", 0)
        elif r.returncode != 0:
            out["error"] = (r.stderr or r.stdout or "jscpd falló")[:200]
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


def clean_brain() -> Dict[str, int]:
    """
    Limpia el brain: elimina nodos duplicados y basura.
    Nodos basura: concepto muy corto (<5 chars), definición vacía, o patrones genéricos.
    """
    removed = 0
    deduped = 0
    try:
        conn = get_conn(BRAIN_DB, timeout=10)

        # Eliminar nodos sin definición útil
        r = conn.execute(
            "DELETE FROM knowledge_nodes WHERE length(definition) < 10 OR definition IS NULL"
        )
        removed += r.rowcount

        # Eliminar nodos con concept muy corto
        r = conn.execute(
            "DELETE FROM knowledge_nodes WHERE length(concept) < 5"
        )
        removed += r.rowcount

        # Eliminar nodos duplicados por concept (mantener el más reciente)
        r = conn.execute("""
            DELETE FROM knowledge_nodes WHERE id NOT IN (
                SELECT id FROM knowledge_nodes
                GROUP BY concept
                HAVING id = MAX(id)
            )
        """)
        deduped += r.rowcount

        # Eliminar nodos de inventario genérico (basura conocida)
        r = conn.execute(
            "DELETE FROM knowledge_nodes WHERE concept LIKE 'libre:colony_operator:%' "
            "AND length(concept) > 80"
        )
        removed += r.rowcount

        conn.commit()
        pass  # S109: get_conn no necesita close()
        log.info(f"Brain limpiado: {removed} eliminados, {deduped} deduplicados")
    except Exception as e:
        log.error(f"clean_brain: {e}")

    return {"removed": removed, "deduped": deduped}


def discover_new_books(topics: List[str] = None) -> List[Dict]:
    """Busca libros nuevos sobre los temas prioritarios de EIDOS."""
    if not topics:
        topics = ["python security 2024", "ai agents 2024", "rust systems 2024", "sql injection testing"]
    all_books = []
    try:
        from core.firefox_session import search_openlibrary
        for topic in topics[:3]:
            books = search_openlibrary(topic, min_year=2022, limit=3)
            all_books.extend(books)
            time.sleep(1)
    except Exception as e:
        log.warning(f"discover_books: {e}")
    return all_books


def sync_new_modules_to_clone() -> List[str]:
    """Sincroniza módulos del real que no tiene el clon."""
    synced = []
    try:
        real_mods  = set(p.name for p in (REAL_DIR / "core").glob("*.py"))
        clone_mods = set(p.name for p in (CLONE_DIR / "core").glob("*.py"))
        missing    = real_mods - clone_mods
        for mod in missing:
            src = REAL_DIR / "core" / mod
            dst = CLONE_DIR / "core" / mod
            try:
                import shutil
                shutil.copy2(str(src), str(dst))
                synced.append(mod)
            except Exception as e:
                log.warning(f"sync {mod}: {e}")
    except Exception as e:
        log.error(f"sync_modules: {e}")
    return synced


def send_telegram_report(report: str) -> bool:
    """Envía el reporte nocturno a SER via Telegram."""
    try:
        token_file = EIDOS_HOME / "telegram_token.txt"
        if not token_file.exists():
            log.warning("No hay token de Telegram")
            return False
        token = token_file.read_text().strip()
        import urllib.request, urllib.parse, json
        msg = f"🌙 EIDOS nocturno ({datetime.now().strftime('%H:%M')}):\n\n{report[:3000]}"
        data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": msg}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
            return resp.get("ok", False)
    except Exception as e:
        log.error(f"telegram_report: {e}")
        return False


def run_night_cycle() -> Dict[str, Any]:
    """
    Ciclo nocturno completo. Retorna resumen de lo que hizo.
    EIDOS trabaja mientras SER duerme — todo dentro del sandbox.
    """
    t0 = time.time()
    log.info("=== EIDOS CICLO NOCTURNO INICIADO ===")

    # Emitir evento al monitor
    try:
        from core.realtime_monitor import emit
        emit("night_cycle", {"status": "started", "time": datetime.now().isoformat()}, "night_cycle")
    except Exception:
        pass

    report_lines = [f"EIDOS trabajó {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    results = {}

    # 1. Analizar el clon (bandit + tokei + jscpd, lectura-solo)
    log.info("Paso 1: Análisis del clon...")
    clone_analysis = analyze_clone()
    results["clone_analysis"] = clone_analysis
    b = clone_analysis.get("bandit", {})
    report_lines.append(f"🔍 Bandit clon: {b.get('high',0)} críticos, {b.get('medium',0)} medios, {b.get('low',0)} bajos")

    tokei = analyze_tokei(REAL_DIR / "core")
    results["tokei"] = tokei
    if not tokei.get("error"):
        top_lang = max(tokei["languages"].items(), key=lambda x: x[1], default=("?", 0))
        report_lines.append(f"📐 Tokei core/: {tokei['total_loc']} LOC ({top_lang[0]} {top_lang[1]})")

    jscpd = analyze_jscpd(REAL_DIR / "core")
    results["jscpd"] = jscpd
    if not jscpd.get("error"):
        report_lines.append(f"♻ jscpd core/: {jscpd['clones']} clones, {jscpd['duplicated_lines']} líneas duplicadas")

    # 2. Sincronizar módulos faltantes al clon
    log.info("Paso 2: Sincronizando módulos nuevos al clon...")
    synced = sync_new_modules_to_clone()
    results["synced_modules"] = synced
    if synced:
        report_lines.append(f"🔄 Sincronizados {len(synced)} módulos al clon: {', '.join(synced[:3])}")
    else:
        report_lines.append("✅ Clon al día — sin módulos pendientes")

    # 3. Limpiar brain
    log.info("Paso 3: Limpiando brain...")
    clean_result = clean_brain()
    results["brain_clean"] = clean_result
    total_cleaned = clean_result["removed"] + clean_result["deduped"]
    report_lines.append(f"🧠 Brain: {total_cleaned} nodos limpiados ({clean_result['deduped']} duplicados)")

    # 4. Buscar libros nuevos
    log.info("Paso 4: Descubriendo libros nuevos...")
    books = discover_new_books()
    results["new_books"] = books
    if books:
        report_lines.append(f"📚 {len(books)} libros nuevos encontrados")
        for b in books[:3]:
            _save_to_brain(
                f"book:night_discovery:{b.get('title','')[:30].replace(' ','_')}",
                f"Libro descubierto en ciclo nocturno: '{b.get('title','')}' de {b.get('author','?')} ({b.get('year','?')})",
                "books_discovered", 0.6
            )

    # 5. Guardar el trabajo en brain
    _save_to_brain(
        f"night_cycle:{datetime.now().strftime('%Y%m%d')}",
        f"Ciclo nocturno: {len(synced)} módulos sincronizados, {total_cleaned} nodos limpiados, {len(books)} libros encontrados",
        "night_work", 0.9
    )

    elapsed = round(time.time() - t0, 1)
    report_lines.append(f"\n⏱️ Trabajo completado en {elapsed}s")

    # 6. Reportar a SER
    full_report = "\n".join(report_lines)
    log.info(full_report)
    telegram_ok = send_telegram_report(full_report)

    # Emitir al monitor
    try:
        from core.realtime_monitor import emit
        emit("night_cycle", {"status": "completed", "elapsed": elapsed, "report": full_report}, "night_cycle")
    except Exception:
        pass

    return {
        "ok": True,
        "elapsed_s": elapsed,
        "report": full_report,
        "telegram_sent": telegram_ok,
        "details": results,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [night] %(message)s")
    result = run_night_cycle()
    print(result["report"])
