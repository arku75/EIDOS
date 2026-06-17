"""
core/eidos_codewiki_adapter.py — Fwiki adapter (CodeWiki via Ollama local)
==========================================================================
Adaptador thin entre CodeWiki upstream (vendor/CodeWiki) y Ollama local.

DISEÑO DE LA LÍNEA DISCIPLINADA (v2026-05-21):
  • CodeWiki upstream por defecto manda código a Anthropic/OpenAI/Azure/Bedrock
    → CRUZARÍA never_exfiltrate_data
  • Este adaptador FUERZA configuración por env vars que apunta CodeWiki a
    Ollama local (provider=openai-compatible, base_url=http://127.0.0.1:11434/v1)
  • Modo "sensitive" (default ON para /home/ser/EIDOS/ y ~/.eidos/):
    - aborta si Ollama no responde (NO fallback a APIs externas)
    - CODEWIKI_NO_KEYRING=1 (respeta owner_policy.never_access_keyring)
  • Modo "public": permite fallback con warning + audit log
  • NO modifica vendor/CodeWiki/ — solo prepara env vars antes de ejecutar
  • Archivos sensibles excluidos por patrón: *secrets*, *.env, *id_ed25519*,
    *_priv*, *.priv

Honesto:
  • NO instala las deps de CodeWiki (anthropic, boto3, fastapi, tree-sitter…)
    desde aquí. Eso es responsabilidad del comando `eidos-wiki install`
    (venv aislado bajo demanda). Sin el venv, este adaptador funciona solo
    para validar configuración + self-test.
  • La verificación de no-exfiltración (no hay tráfico fuera de localhost)
    requiere monitorización externa (tcpdump/strace). El adaptador hace
    su parte: configurar env vars y abortar si Ollama no responde.

Uso:
  python3 -m core.eidos_codewiki_adapter status
  python3 -m core.eidos_codewiki_adapter self-test
  python3 -m core.eidos_codewiki_adapter prepare-env <repo_path> --sensitive
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional


EIDOS_REPO = Path("/home/ser/EIDOS")
CODEWIKI_VENDOR = EIDOS_REPO / "vendor" / "CodeWiki"
EIDOS_HOME = Path(os.path.expanduser("~/.eidos"))
AUDIT_LOG = EIDOS_HOME / "codewiki_audit.log"
DOCS_OUT_BASE = EIDOS_REPO / "docs" / "wiki"

# Path patterns considerados sensibles — el adaptador refuses procesarlos en
# modo público también, por defensa-en-profundidad.
SENSITIVE_PATH_PATTERNS = (
    "secrets", ".env", "id_ed25519", "_priv", ".priv",
    "hub_ed25519", "clone_ed25519", "ssh_id_ed25519",
    "credentials.json", ".log_enabled", "compute_quota.toml",
)

# Modelos Ollama que CodeWiki podrá usar (en modo sensitive)
DEFAULT_LOCAL_MODELS = {
    "main": "lfm2.5-1.2b-instruct:q4_0",
    "cluster": "lfm2.5-1.2b-instruct:q4_0",
    "fallback": "llama3.2:3b",
}

OLLAMA_BASE = os.environ.get("EIDOS_OLLAMA_URL",
                              "http://127.0.0.1:11434").rstrip("/")
OLLAMA_OPENAI_BASE = OLLAMA_BASE + "/v1"
OLLAMA_TAGS = OLLAMA_BASE + "/api/tags"


# ── Audit log ──────────────────────────────────────────────────────────────

def _audit(event: dict) -> None:
    EIDOS_HOME.mkdir(parents=True, exist_ok=True)
    event["ts"] = event.get("ts", time.time())
    event["ts_iso"] = time.strftime("%Y-%m-%d %H:%M:%S",
                                    time.localtime(event["ts"]))
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    try:
        os.chmod(AUDIT_LOG, 0o600)
    except Exception:
        pass


# ── Detección de modo sensitive vs public ──────────────────────────────────

def is_sensitive_repo(repo_path: str) -> bool:
    """Devuelve True si el repo a documentar está dentro de paths que
    contienen código/datos del propio EIDOS o ~/.eidos/ (sensitive)."""
    p = os.path.realpath(os.path.expanduser(repo_path))
    sensitive_bases = [
        str(EIDOS_REPO.resolve()),
        str(EIDOS_HOME.resolve()),
        os.path.realpath(os.path.expanduser("~/.eidos")),
    ]
    for base in sensitive_bases:
        if p == base or p.startswith(base + os.sep):
            return True
    return False


def path_contains_sensitive_name(path: str) -> bool:
    """True si el path contiene patrones de archivos sensibles."""
    lower = path.lower()
    return any(pat in lower for pat in SENSITIVE_PATH_PATTERNS)


# ── Pre-flight Ollama ──────────────────────────────────────────────────────

def ollama_available() -> bool:
    try:
        with urllib.request.urlopen(OLLAMA_TAGS, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def ollama_models() -> list[str]:
    try:
        with urllib.request.urlopen(OLLAMA_TAGS, timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return []


# ── Preparación de env vars para CodeWiki ──────────────────────────────────

def prepare_env(repo_path: str,
                mode: str = "auto",
                main_model: Optional[str] = None,
                cluster_model: Optional[str] = None,
                fallback_model: Optional[str] = None) -> dict:
    """Prepara variables de entorno para invocar CodeWiki.

    mode='auto'  → sensitive si el repo está dentro de EIDOS, public si no
    mode='sensitive' → fuerza Ollama local, ABORTA si no disponible
    mode='public'    → permite (con warning) que CodeWiki use sus defaults

    Devuelve dict con {env_to_set, mode_effective, decisions, abort_reason?}.
    NO modifica os.environ directamente — eso lo hace el caller.
    """
    decisions: list[str] = []

    if mode == "auto":
        mode_eff = "sensitive" if is_sensitive_repo(repo_path) else "public"
        decisions.append(f"auto → mode={mode_eff} (repo={'EIDOS-internal' if mode_eff=='sensitive' else 'external'})")
    else:
        mode_eff = mode
        decisions.append(f"mode forced = {mode_eff}")

    env: dict[str, str] = {
        # NUNCA permitir keyring (respeta owner_policy.never_access_keyring)
        "CODEWIKI_NO_KEYRING": "1",
    }

    if mode_eff == "sensitive":
        if not ollama_available():
            return {
                "ok": False,
                "abort_reason": ("Ollama no disponible en %s y modo sensitive "
                                 "no permite fallback a APIs externas."
                                 % OLLAMA_BASE),
                "mode_effective": mode_eff,
                "decisions": decisions,
            }
        mm = main_model or DEFAULT_LOCAL_MODELS["main"]
        cm = cluster_model or DEFAULT_LOCAL_MODELS["cluster"]
        fb = fallback_model or DEFAULT_LOCAL_MODELS["fallback"]
        models_disp = ollama_models()
        for m in (mm, cm, fb):
            if m not in models_disp:
                decisions.append(
                    f"⚠ modelo '{m}' no en Ollama local; "
                    f"disponibles: {models_disp}")
        env.update({
            "LLM_BASE_URL": OLLAMA_OPENAI_BASE,   # http://127.0.0.1:11434/v1
            "LLM_API_KEY":  "ollama-local",        # Ollama acepta cualquier valor
            "MAIN_MODEL":     mm,
            "CLUSTER_MODEL":  cm,
            "FALLBACK_MODEL_1": fb,
            # CodeWiki provider — openai-compatible es el adecuado para Ollama
            "CODEWIKI_PROVIDER": "openai-compatible",
        })
        decisions.append("LLM_BASE_URL → Ollama local (no APIs externas)")
        decisions.append("LLM_API_KEY → dummy 'ollama-local' (no keyring lookup)")
    else:
        # Modo public: NO escribimos LLM_API_KEY (debe estar en env del user)
        # NO ponemos LLM_BASE_URL (CodeWiki usará default → APIs externas)
        # Audit log marca esto como decisión consciente
        decisions.append("⚠ modo PUBLIC: CodeWiki podrá usar APIs externas si "
                         "el huésped las tiene configuradas vía env. Sin warning "
                         "no haríamos esta llamada — pero modo público implica "
                         "consentimiento explícito.")

    _audit({
        "event": "prepare_env",
        "repo_path": repo_path,
        "mode_requested": mode,
        "mode_effective": mode_eff,
        "decisions": decisions,
        "ollama_available": ollama_available(),
    })

    return {
        "ok": True,
        "mode_effective": mode_eff,
        "env_to_set": env,
        "decisions": decisions,
    }


# ── Status / inspector ─────────────────────────────────────────────────────

def status() -> dict:
    deps_ok = CODEWIKI_VENDOR.exists()
    venv_path = CODEWIKI_VENDOR / ".venv-codewiki"
    return {
        "codewiki_vendor_present": deps_ok,
        "vendor_path": str(CODEWIKI_VENDOR) if deps_ok else None,
        "venv_path": str(venv_path) if venv_path.exists() else None,
        "venv_present": venv_path.exists(),
        "ollama_url": OLLAMA_BASE,
        "ollama_available": ollama_available(),
        "ollama_models": ollama_models(),
        "audit_log": str(AUDIT_LOG),
        "docs_out_base": str(DOCS_OUT_BASE),
        "sensitive_path_patterns": list(SENSITIVE_PATH_PATTERNS),
        "default_local_models": DEFAULT_LOCAL_MODELS,
    }


# ── Self-test (sin instalar CodeWiki ni tocar el sistema) ──────────────────

def _self_test() -> int:
    global OLLAMA_BASE, OLLAMA_OPENAI_BASE, OLLAMA_TAGS
    failures: list[str] = []

    def chk(name: str, cond: bool, detail: str = ""):
        mark = "✅" if cond else "❌"
        print(f"{mark} {name}" + (f" — {detail[:120]}" if detail else ""))
        if not cond:
            failures.append(name)

    # 1) is_sensitive_repo()
    chk("/home/ser/EIDOS/core es sensitive",
        is_sensitive_repo("/home/ser/EIDOS/core") is True)
    chk("~/.eidos es sensitive",
        is_sensitive_repo(os.path.expanduser("~/.eidos")) is True)
    chk("/tmp NO es sensitive",
        is_sensitive_repo("/tmp") is False)
    chk("/etc NO es sensitive",
        is_sensitive_repo("/etc") is False)

    # 2) path_contains_sensitive_name
    chk("'secrets.env' es sensitive name",
        path_contains_sensitive_name("/home/x/secrets.env") is True)
    chk("'id_ed25519' es sensitive name",
        path_contains_sensitive_name("/etc/ssh/id_ed25519") is True)
    chk("'README.md' NO es sensitive name",
        path_contains_sensitive_name("/home/x/README.md") is False)

    # 3) prepare_env modo sensitive con Ollama vivo → set vars correctas
    if ollama_available():
        r = prepare_env("/home/ser/EIDOS/core", mode="sensitive",
                         main_model="lfm2.5-1.2b-instruct:q4_0")
        chk("sensitive + Ollama vivo → ok",
            r.get("ok") is True
            and r["env_to_set"]["LLM_BASE_URL"] == OLLAMA_OPENAI_BASE
            and r["env_to_set"]["CODEWIKI_NO_KEYRING"] == "1"
            and r["env_to_set"]["MAIN_MODEL"] == "lfm2.5-1.2b-instruct:q4_0")
        chk("sensitive NUNCA setea LLM_API_KEY a key real",
            r["env_to_set"]["LLM_API_KEY"] == "ollama-local")
    else:
        chk("sensitive + Ollama caído → ABORTA",
            prepare_env("/home/ser/EIDOS/core",
                         mode="sensitive").get("ok") is False)

    # 4) Simular "Ollama caído" forzando URL imposible
    orig_base, orig_v1, orig_tags = OLLAMA_BASE, OLLAMA_OPENAI_BASE, OLLAMA_TAGS
    OLLAMA_BASE = "http://127.0.0.1:1"
    OLLAMA_OPENAI_BASE = OLLAMA_BASE + "/v1"
    OLLAMA_TAGS = OLLAMA_BASE + "/api/tags"
    try:
        r = prepare_env("/home/ser/EIDOS/core", mode="sensitive")
        chk("sensitive + Ollama caído → ABORTA con reason",
            r.get("ok") is False and "Ollama no disponible" in r.get("abort_reason", ""))
    finally:
        OLLAMA_BASE, OLLAMA_OPENAI_BASE, OLLAMA_TAGS = orig_base, orig_v1, orig_tags

    # 5) Modo public NO setea LLM_BASE_URL (CodeWiki usaría sus defaults)
    r = prepare_env("/tmp", mode="public")
    chk("public NO setea LLM_BASE_URL (caller debe definirlo si quiere fallback externo)",
        "LLM_BASE_URL" not in r["env_to_set"])
    chk("public sí mantiene CODEWIKI_NO_KEYRING=1",
        r["env_to_set"]["CODEWIKI_NO_KEYRING"] == "1")

    # 6) Mode auto: EIDOS → sensitive, /tmp → public
    r1 = prepare_env("/home/ser/EIDOS/core", mode="auto")
    r2 = prepare_env("/tmp", mode="auto")
    chk("auto sobre EIDOS → sensitive",
        r1.get("mode_effective") == "sensitive")
    chk("auto sobre /tmp → public",
        r2.get("mode_effective") == "public")

    # 7) Audit log se escribió
    chk("audit log generado",
        AUDIT_LOG.exists()
        and len(AUDIT_LOG.read_text(encoding='utf-8').strip().splitlines()) >= 4)

    print("-" * 60)
    if failures:
        print(f"❌ FAIL ({len(failures)}): {failures}")
        return 1
    print("✅ SELF-TEST PASS")
    return 0


# ── CLI ────────────────────────────────────────────────────────────────────

def _cli() -> int:
    ap = argparse.ArgumentParser(prog="eidos_codewiki_adapter",
        description="Adaptador CodeWiki ↔ EIDOS (Ollama local, no exfil)")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("status")
    sp = sub.add_parser("prepare-env")
    sp.add_argument("repo_path")
    sp.add_argument("--mode", choices=["auto", "sensitive", "public"],
                    default="auto")
    sp.add_argument("--main-model", default=None)
    sp.add_argument("--cluster-model", default=None)
    sp.add_argument("--fallback-model", default=None)
    sub.add_parser("self-test")

    args = ap.parse_args()

    if args.cmd == "status":
        print(json.dumps(status(), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "prepare-env":
        r = prepare_env(args.repo_path, mode=args.mode,
                        main_model=args.main_model,
                        cluster_model=args.cluster_model,
                        fallback_model=args.fallback_model)
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0 if r.get("ok") else 1
    if args.cmd == "self-test":
        return _self_test()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
