"""
core/eidos_local_tools.py — EIDOS detecta y USA herramientas locales del PC [S122]
=================================================================================
SER quiere que EIDOS, al investigar algo (p.ej. n8n), mire si YA lo tiene en el
PC, si es open source / GitHub, y que lo USE de verdad con lo aprendido.

Capacidades:
  - detect_tool(name): ¿está en PATH / Docker / apt / un puerto conocido?
  - is_opensource(name): mapa conocido + búsqueda GitHub.
  - n8n_status() / n8n_api(): salud, versión y uso vía API (si hay API key).

Las credenciales (API keys de tus tools) van SOLO en ~/.eidos/secrets.env.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request as _req
import urllib.parse as _up
import logging
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger("eidos.local_tools")

_UA = "Mozilla/5.0 (X11; Linux x86_64) EIDOS/1.0"

# Mapa de herramientas conocidas → (open source, repo github, puerto típico)
_KNOWN = {
    "n8n": (True, "n8n-io/n8n", 5678),
    "docker": (True, "moby/moby", None),
    "nmap": (True, "nmap/nmap", None),
    "metasploit": (True, "rapid7/metasploit-framework", None),
    "ollama": (True, "ollama/ollama", 11434),
    "grafana": (True, "grafana/grafana", 3000),
    "nginx": (True, "nginx/nginx", 80),
}


def _http(url: str, timeout: int = 6, headers: Optional[dict] = None,
          data: Optional[bytes] = None, method: Optional[str] = None) -> tuple:
    """GET/POST simple. Devuelve (status, body_text)."""
    try:
        h = {"User-Agent": _UA, "Accept": "application/json"}
        if headers:
            h.update(headers)
        req = _req.Request(url, data=data, headers=h, method=method)
        with _req.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="ignore")
    except Exception as e:  # noqa: BLE001
        code = getattr(e, "code", 0)
        return code, str(e)


def _docker_container(name_substr: str) -> Optional[Dict]:
    """Busca un contenedor docker cuyo nombre/imagen contenga name_substr."""
    try:
        r = subprocess.run(
            ["docker", "ps", "-a", "--format",
             "{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"],
            capture_output=True, text=True, timeout=8)
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and (name_substr in parts[0].lower()
                                    or name_substr in parts[1].lower()):
                return {"name": parts[0], "image": parts[1],
                        "status": parts[2] if len(parts) > 2 else "",
                        "ports": parts[3] if len(parts) > 3 else "",
                        "running": "up" in (parts[2].lower() if len(parts) > 2 else "")}
    except Exception as e:  # noqa: BLE001
        log.debug("docker ps fallo: %s", e)
    return None


def detect_tool(name: str) -> Dict:
    """¿Tiene SER esta herramienta? Mira PATH, Docker, apt y puerto conocido."""
    name = (name or "").strip().lower()
    info: Dict = {"tool": name, "present": False, "how": [], "details": {}}

    # 1. En PATH
    path = shutil.which(name)
    if path:
        info["present"] = True
        info["how"].append("PATH")
        info["details"]["path"] = path

    # 2. Docker
    cont = _docker_container(name)
    if cont:
        info["present"] = True
        info["how"].append("docker")
        info["details"]["docker"] = cont

    # 3. apt (paquete instalado)
    try:
        r = subprocess.run(["dpkg", "-l", name], capture_output=True, text=True, timeout=6)
        if r.returncode == 0 and "\nii " in r.stdout:
            info["present"] = True
            info["how"].append("apt")
    except Exception:
        pass

    # 4. Puerto conocido escuchando
    known = _KNOWN.get(name)
    if known and known[2]:
        port = known[2]
        st, _ = _http(f"http://127.0.0.1:{port}/", timeout=3)
        if st and st != 0:
            info["present"] = True
            info["how"].append(f"puerto :{port}")
            info["details"]["port"] = port

    # 5. Open source / github
    oss = is_opensource(name)
    info["opensource"] = oss.get("opensource")
    info["github"] = oss.get("github")
    return info


def is_opensource(name: str) -> Dict:
    """¿Es open source? Mapa conocido + búsqueda GitHub."""
    name = (name or "").strip().lower()
    if name in _KNOWN:
        oss, repo, _ = _KNOWN[name]
        return {"opensource": oss, "github": f"https://github.com/{repo}"}
    # Búsqueda GitHub (API pública, sin auth)
    try:
        st, body = _http(
            f"https://api.github.com/search/repositories?q={_up.quote(name)}&sort=stars&per_page=1",
            timeout=6, headers={"Accept": "application/vnd.github+json"})
        if st == 200:
            data = json.loads(body)
            items = data.get("items", [])
            if items:
                top = items[0]
                lic = (top.get("license") or {}).get("spdx_id")
                return {"opensource": bool(lic and lic != "NOASSERTION"),
                        "github": top.get("html_url"),
                        "stars": top.get("stargazers_count"),
                        "license": lic}
    except Exception as e:  # noqa: BLE001
        log.debug("github search fallo: %s", e)
    return {"opensource": None, "github": None}


# ─────────────────────────── n8n específico ──────────────────────────────────

def _n8n_key() -> str:
    """API key de n8n desde secrets.env (N8N_API_KEY). Vacío si no hay."""
    val = os.environ.get("N8N_API_KEY", "")
    if val:
        return val
    sec = Path.home() / ".eidos" / "secrets.env"
    if sec.exists():
        for line in sec.read_text(errors="ignore").splitlines():
            if line.strip().startswith("N8N_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def n8n_status(base: str = "http://127.0.0.1:5678") -> Dict:
    """Salud + versión de n8n local."""
    out = {"reachable": False, "healthy": False, "version": None, "auth": None,
           "api_key": bool(_n8n_key())}
    st, body = _http(f"{base}/healthz", timeout=4)
    out["reachable"] = st == 200
    out["healthy"] = (st == 200 and '"ok"' in body)
    st2, body2 = _http(f"{base}/rest/settings", timeout=5)
    if st2 == 200:
        try:
            d = json.loads(body2).get("data", {})
            out["version"] = d.get("versionCli") or d.get("n8nMetadata", {}).get("version")
            out["auth"] = d.get("userManagement", {}).get("authenticationMethod")
        except Exception:
            pass
    return out


def n8n_list_workflows(base: str = "http://127.0.0.1:5678") -> Dict:
    """Lista workflows vía API v1 (necesita N8N_API_KEY)."""
    key = _n8n_key()
    if not key:
        return {"ok": False, "error": "sin N8N_API_KEY en secrets.env",
                "hint": "crea una API key en n8n (Settings→API) y añádela a ~/.eidos/secrets.env"}
    st, body = _http(f"{base}/api/v1/workflows", timeout=8,
                     headers={"X-N8N-API-KEY": key})
    if st == 200:
        try:
            data = json.loads(body).get("data", [])
            return {"ok": True, "count": len(data),
                    "workflows": [{"id": w.get("id"), "name": w.get("name"),
                                   "active": w.get("active")} for w in data[:20]]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
    return {"ok": False, "error": f"HTTP {st}", "body": body[:200]}


def n8n_create_workflow(name: str, nodes: Optional[list] = None,
                        connections: Optional[dict] = None,
                        base: str = "http://127.0.0.1:5678") -> Dict:
    """Crea un workflow en n8n vía API v1 (EIDOS lo hace solo). [S122-I]

    Si no se dan nodes, crea uno mínimo (Manual Trigger → Set con un mensaje).
    Devuelve {ok, id, name, nodes, url}.
    """
    key = _n8n_key()
    if not key:
        return {"ok": False, "error": "sin N8N_API_KEY",
                "hint": "EIDOS puede registrarse con eidos_service_onboard.onboard_n8n()"}
    if nodes is None:
        nodes = [
            {"parameters": {}, "id": "trigger", "name": "Start",
             "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
             "position": [240, 300]},
            {"parameters": {"assignments": {"assignments": [
                {"id": "m1", "name": "mensaje",
                 "value": f"Workflow «{name}» creado por EIDOS", "type": "string"}]}},
             "id": "setnode", "name": "Set",
             "type": "n8n-nodes-base.set", "typeVersion": 3.4,
             "position": [460, 300]},
        ]
        connections = {"Start": {"main": [[{"node": "Set", "type": "main",
                                            "index": 0}]]}}
    if connections is None:
        connections = {}
    payload = json.dumps({
        "name": name, "nodes": nodes, "connections": connections,
        "settings": {"executionOrder": "v1"},
    }).encode()
    st, body = _http(f"{base}/api/v1/workflows", timeout=15,
                     headers={"X-N8N-API-KEY": key, "Content-Type": "application/json"},
                     data=payload, method="POST")
    if st in (200, 201):
        try:
            d = json.loads(body)
            d = d.get("data", d)
            wid = d.get("id")
            return {"ok": True, "id": wid, "name": d.get("name"),
                    "nodes": len(d.get("nodes", [])),
                    "url": f"{base}/workflow/{wid}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
    return {"ok": False, "error": f"HTTP {st}", "body": body[:200]}


def use_tool_report(name: str) -> str:
    """Genera un informe legible: ¿tengo X? ¿open source? ¿lo puedo usar?"""
    d = detect_tool(name)
    lines = []
    if d["present"]:
        lines.append(f"✅ Tienes «{name}» (vía {', '.join(d['how'])}).")
        dk = d["details"].get("docker")
        if dk:
            lines.append(f"   Docker: {dk['image']} — {dk['status']} — puertos {dk['ports']}")
    else:
        lines.append(f"❌ No encuentro «{name}» en tu PC (ni PATH, ni Docker, ni apt).")
    if d.get("opensource") is True:
        lines.append(f"   Open source: sí. GitHub: {d.get('github')}")
    elif d.get("opensource") is False:
        lines.append(f"   Open source: no / licencia restrictiva.")
    if name == "n8n":
        s = n8n_status()
        if s["reachable"]:
            lines.append(f"   n8n responde en :5678 (sano={s['healthy']}, "
                         f"versión={s['version']}, auth={s['auth']}).")
            wf = n8n_list_workflows()
            if wf.get("ok"):
                lines.append(f"   🔧 Puedo USARLO: {wf['count']} workflows. "
                             f"{', '.join(w['name'] for w in wf['workflows'][:5])}")
            else:
                lines.append(f"   ⚠ Para USARLO necesito tu API key: {wf.get('error')}. "
                             f"{wf.get('hint','')}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    logging.basicConfig(level=logging.INFO)
    name = sys.argv[1] if len(sys.argv) > 1 else "n8n"
    print(use_tool_report(name))
