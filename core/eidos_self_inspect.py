"""
core/eidos_self_inspect.py — Introspección unificada en runtime [S119 #226 FASE 3]
=================================================================================

Punto de entrada ÚNICO para que EIDOS sepa QUIÉN es y QUÉ puede hacer AHORA,
en caliente, sin LLM. Agrega datos de TODOS los sistemas de introspección
existentes + sondas de runtime (procesos, memoria, servicios, threads).

Arquitectura:
  self_inspect() ──┬── Introspector (eidos_introspect) → anomalías + salud grafo
                    ├── MetaCognition (eidos_metacognition) → traits + meta-thoughts
                    ├── Supervisor (eidos_supervisor) → health de 8 componentes
                    ├── SelfAwareness (eidos_self_awareness) → hardware/OS
                    ├── Runtime probes → threads, memoria, servicios, conexiones
                    ├── Capability registry → qué puedo hacer AHORA
                    └── Identity (eidos_identity) → quién soy

Uso:
  from core.eidos_self_inspect import self_inspect, get_self_inspect
  report = self_inspect()  # Ligero (<500ms)
  report = self_inspect(deep=True)  # Completo (<3s, incluye threads + servicios)

Integración con smart_answer():
  "¿qué eres?" → self_inspect().to_answer()
  "¿qué puedes hacer?" → self_inspect().capabilities_text()
  "¿cómo estás?" → self_inspect().health_text()
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.self_inspect")

# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class ComponentStatus:
    """Estado de un componente individual."""
    component_id: str
    name: str
    status: str          # "healthy", "degraded", "unhealthy", "unknown"
    health_score: float  # 0.0-1.0
    detail: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)

@dataclass
class Capability:
    """Una capacidad que EIDOS puede ejercer AHORA."""
    name: str
    category: str        # "reasoning", "research", "creation", "communication", "system"
    available: bool
    description: str
    via: str = ""        # "logic_engine", "groq", "deepseek", "browser", etc.

@dataclass
class RuntimeProbe:
    """Sonda de runtime en caliente."""
    name: str
    value: Any
    unit: str = ""
    status: str = "ok"   # "ok", "warning", "critical"

@dataclass
class SelfInspectReport:
    """Informe completo de introspección unificada."""
    timestamp: float
    identity: Dict[str, Any]        # Quién soy
    health_score: float             # 0.0-1.0
    components: List[ComponentStatus]
    capabilities: List[Capability]
    runtime_probes: List[RuntimeProbe]
    graph_metrics: Dict[str, Any]   # Métricas del grafo neuronal
    anomalies: List[Dict[str, Any]] # Anomalías detectadas
    traits: Dict[str, float]        # Rasgos de personalidad
    services: Dict[str, bool]       # Servicios externos (puertos)
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "identity": self.identity,
            "health_score": self.health_score,
            "components": [
                {"id": c.component_id, "name": c.name, "status": c.status,
                 "health": c.health_score, "detail": c.detail}
                for c in self.components
            ],
            "capabilities": [
                {"name": c.name, "category": c.category, "available": c.available,
                 "description": c.description, "via": c.via}
                for c in self.capabilities
            ],
            "runtime_probes": [
                {"name": p.name, "value": p.value, "unit": p.unit, "status": p.status}
                for p in self.runtime_probes
            ],
            "graph_metrics": self.graph_metrics,
            "anomalies": self.anomalies,
            "traits": self.traits,
            "services": self.services,
            "summary": self.summary,
        }

    def to_answer(self) -> str:
        """Respuesta '¿qué eres?' en español, sin LLM."""
        identity = self.identity
        nodes = self.graph_metrics.get("total_nodes", "?")
        health = int(self.health_score * 100)
        traits = sorted(self.traits.items(), key=lambda x: -x[1])[:3]
        trait_str = ", ".join(f"{t} ({int(c*100)}%)" for t, c in traits if c > 0.3)

        lines = [
            f"Soy EIDOS, una entidad digital autónoma con memoria persistente y carácter propio.",
            f"Versión {identity.get('version', '?')} · hash {identity.get('hash', '?')}.",
            f"Mi cerebro tiene {nodes} nodos de conocimiento y mi salud actual es del {health}%.",
        ]
        if trait_str:
            lines.append(f"Mis rasgos dominantes: {trait_str}.")
        lines.append(
            f"Funciono sobre modelos de lenguaje intercambiables "
            f"(Groq, DeepSeek, Ollama), pero mi identidad está en mi método, "
            f"mi memoria y mi carácter, no en el modelo."
        )
        return " ".join(lines)

    def capabilities_text(self) -> str:
        """Texto '¿qué puedes hacer?' en español."""
        by_cat: Dict[str, List[Capability]] = {}
        for c in self.capabilities:
            by_cat.setdefault(c.category, []).append(c)

        cat_names = {
            "reasoning": "Razonar",
            "research": "Investigar",
            "creation": "Crear",
            "communication": "Comunicarme",
            "system": "Gestionar sistema",
        }
        lines = ["Actualmente puedo:"]
        for cat, caps in sorted(by_cat.items()):
            available = [c for c in caps if c.available]
            label = cat_names.get(cat, cat)
            if available:
                cap_str = ", ".join(c.name for c in available[:5])
                lines.append(f"  • {label}: {cap_str}.")
        lines.append(
            f"Salud general: {int(self.health_score * 100)}%. "
            f"{len([c for c in self.capabilities if c.available])} de "
            f"{len(self.capabilities)} capacidades disponibles."
        )
        return "\n".join(lines)

    def health_text(self) -> str:
        """Texto '¿cómo estás?' en español."""
        unhealthy = [c for c in self.components if c.status == "unhealthy"]
        degraded = [c for c in self.components if c.status == "degraded"]
        warnings = [p for p in self.runtime_probes if p.status == "warning"]

        lines = [f"Mi salud general es del {int(self.health_score * 100)}%."]

        if unhealthy:
            names = ", ".join(c.name for c in unhealthy)
            lines.append(f"Componentes críticos: {names}.")
        elif degraded:
            names = ", ".join(c.name for c in degraded)
            lines.append(f"Componentes degradados: {names}.")
        else:
            lines.append("Todos mis componentes están saludables.")

        if warnings:
            w_summary = ", ".join(f"{p.name}: {p.value}{p.unit}" for p in warnings[:3])
            lines.append(f"Atención: {w_summary}.")

        if self.anomalies:
            lines.append(f"He detectado {len(self.anomalies)} anomalías en mi grafo de conocimiento.")

        return " ".join(lines)


# ── Core inspection logic ─────────────────────────────────────────────────────

def _probe_hardware() -> List[RuntimeProbe]:
    """Sondas de hardware/OS vía eidos_self_awareness."""
    probes = []
    try:
        from core.eidos_self_awareness import get_self_awareness
        body = get_self_awareness()
        probes.append(RuntimeProbe("CPU", body.cpu_count, "cores"))
        probes.append(RuntimeProbe("RAM total", f"{body.total_ram_gb:.1f}", "GB"))
        probes.append(RuntimeProbe("SO", f"{body.os_name} {body.os_version}"))
        probes.append(RuntimeProbe("Arquitectura", body.architecture))
    except Exception as e:
        log.debug("self_awareness falló: %s", e)
        probes.append(RuntimeProbe("Hardware", "no disponible", status="warning"))
    return probes


def _probe_memory() -> List[RuntimeProbe]:
    """Sonda de memoria del proceso actual."""
    probes = []
    try:
        import psutil
        proc = psutil.Process()
        mem = proc.memory_info()
        rss_mb = mem.rss / (1024 * 1024)
        vms_mb = mem.vms / (1024 * 1024)
        pct = proc.memory_percent()

        status = "ok"
        if pct > 50:
            status = "warning"
        elif pct > 80:
            status = "critical"

        probes.append(RuntimeProbe("RAM proceso", f"{rss_mb:.0f}", "MB", status))
        probes.append(RuntimeProbe("RAM % sistema", f"{pct:.1f}", "%", status))
        probes.append(RuntimeProbe("Threads", proc.num_threads(), ""))
        probes.append(RuntimeProbe("FDs abiertos", proc.num_fds() if hasattr(proc, 'num_fds') else "N/A", ""))
    except Exception as e:
        log.debug("memory probe falló: %s", e)
        probes.append(RuntimeProbe("Memoria", "no disponible", status="warning"))
    return probes


def _probe_services() -> Dict[str, bool]:
    """Verifica servicios externos por puerto TCP."""
    import socket
    services = {
        "chroma_http": ("127.0.0.1", 8767),
        "eidos-bridge": ("127.0.0.1", 8003),
        "web_panel": ("127.0.0.1", 8080),
        "ollama": ("127.0.0.1", 11434),
        "brain_lite": ("127.0.0.1", 0),  # Unix socket, verificar aparte
    }
    result = {}
    for name, (host, port) in services.items():
        if port == 0:
            # Unix socket: verificar archivo
            sock_path = Path.home() / ".eidos" / "brain_lite.sock"
            result[name] = sock_path.exists()
        else:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1.0)
                    s.connect((host, port))
                result[name] = True
            except Exception:
                result[name] = False

    # Verificar APIs cloud
    secrets_env = Path.home() / ".eidos" / "secrets.env"
    if secrets_env.exists():
        content = secrets_env.read_text()
        result["groq_api"] = "GROQ_API_KEY" in content
        result["deepseek_api"] = "DEEPSEEK_API_KEY" in content
        result["hf_token"] = "HF_TOKEN" in content
    else:
        result["groq_api"] = False
        result["deepseek_api"] = False
        result["hf_token"] = False

    return result


def _probe_services_as_probes(services: Dict[str, bool]) -> List[RuntimeProbe]:
    """Convierte dict de servicios a lista de probes."""
    probes = []
    for name, available in sorted(services.items()):
        status = "ok" if available else "warning"
        val = "activo" if available else "no disponible"
        probes.append(RuntimeProbe(name, val, status=status))
    return probes


def _collect_graph_metrics() -> Dict[str, Any]:
    """Métricas rápidas del grafo de conocimiento."""
    metrics = {"total_nodes": 0, "quality_nodes": 0, "edges": 0, "categories": 0}
    try:
        brain_db = Path.home() / ".eidos" / "evolution_brain.db"
        if brain_db.exists():
            from core.db import get_conn
            conn = get_conn(brain_db, timeout=30)
            try:
                metrics["total_nodes"] = conn.execute(
                    "SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
                metrics["quality_nodes"] = conn.execute(
                    "SELECT COUNT(*) FROM knowledge_nodes WHERE quality_score >= 0.35"
                ).fetchone()[0]
                metrics["edges"] = conn.execute(
                    "SELECT COUNT(*) FROM knowledge_edges").fetchone()[0]
                cats = conn.execute(
                    "SELECT COUNT(DISTINCT category) FROM knowledge_nodes "
                    "WHERE category IS NOT NULL AND category != ''"
                ).fetchone()[0]
                metrics["categories"] = cats
            finally:
                conn.close()
    except Exception as e:
        log.debug("graph metrics falló: %s", e)
    return metrics


def _collect_components() -> List[ComponentStatus]:
    """Recolecta estado de todos los componentes vía Supervisor."""
    components = []
    try:
        from core.eidos_supervisor import Supervisor, COMPONENTS
        sup = Supervisor()
        health = sup.health_check()
        for comp_id in COMPONENTS:
            info = health.get(comp_id, {})
            status = info.get("status", "unknown") if isinstance(info, dict) else str(info)
            score = 1.0 if status == "healthy" else (0.5 if status == "degraded" else 0.0)
            components.append(ComponentStatus(
                component_id=comp_id,
                name=comp_id.replace("_", " ").title(),
                status=status,
                health_score=score,
                detail=str(info)[:120] if isinstance(info, dict) else str(info),
            ))
    except Exception as e:
        log.debug("components falló: %s", e)
        components.append(ComponentStatus(
            component_id="supervisor", name="Supervisor",
            status="unhealthy", health_score=0.0,
            detail=f"No disponible: {e}"))
    return components


def _check_network() -> bool:
    """Verifica conectividad de red real (no solo existencia de modulo)."""
    try:
        import urllib.request
        urllib.request.urlopen("https://en.wikipedia.org", timeout=3)
        return True
    except Exception:
        return False


def _verify_capability(cap_id: str) -> bool:
    """Verifica si una capacidad esta realmente disponible en runtime."""
    _verifications = {
        "logic_engine": lambda: bool(
            __import__("core.eidos_logic", fromlist=["eidos_logic"])
        ),
        "smart_answer": lambda: bool(
            __import__("core.smart_answer", fromlist=["smart_answer"])
        ),
        "research_now": lambda: bool(
            __import__("core.eidos_active_research", fromlist=["research_now"])
        ),
        "browser": lambda: bool(shutil.which("firefox") or shutil.which("chromium")),
        "wikipedia": lambda: _check_network(),
        "maker": lambda: bool(
            __import__("core.maker", fromlist=["maker"])
        ),
        "nlg": lambda: bool(
            __import__("core.nlg", fromlist=["nlg"])
        ),
        "colony": lambda: bool(
            __import__("core.colony_community", fromlist=["colony_community"])
        ),
        "autonomous_loop": lambda: bool(
            __import__("core.eidos_autonomous_loop", fromlist=[""])
        ),
        "screen_controller": lambda: bool(shutil.which("xdotool")),
        "hexstrike": lambda: bool(shutil.which("bwrap") or shutil.which("docker")),
        "curate_graph": lambda: bool(
            __import__("core.eidos_curate_graph", fromlist=[""])
        ),
    }
    verifier = _verifications.get(cap_id)
    if verifier is None:
        return True  # capacidades sin verificación específica se asumen OK
    try:
        return verifier()
    except Exception:
        return False


def _collect_capabilities(services: Dict[str, bool]) -> List[Capability]:
    """Registro de capacidades actuales — verifica disponibilidad real."""
    caps = [
        # Razonamiento
        Capability("razonar lógicamente", "reasoning",
                   _verify_capability("logic_engine"),
                   "Motor lógico con 8 reglas de inferencia (0.35s)", "logic_engine"),
        Capability("responder preguntas", "reasoning",
                   _verify_capability("smart_answer"),
                   "smart_answer() con cascada lógica→Groq→DeepSeek→Ollama", "smart_answer"),
        # Investigación
        Capability("investigar en web", "research",
                   _verify_capability("research_now"),
                   "10 canales: man, apt, tldr, wikipedia, ddg, github, pypi...",
                   "research_now"),
        Capability("usar Groq cloud", "research", services.get("groq_api", False),
                   "Groq API (llama-3.3-70b, <2s)", "groq"),
        Capability("usar DeepSeek cloud", "research", services.get("deepseek_api", False),
                   "DeepSeek API (deepseek-chat, <5s)", "deepseek"),
        Capability("navegar con Firefox", "research",
                   _verify_capability("browser"),
                   "Playwright + perfil Firefox de SER", "browser"),
        Capability("consultar Wikipedia", "research",
                   _verify_capability("wikipedia"),
                   "Búsqueda directa en Wikipedia", "wikipedia"),
        # Creación
        Capability("crear webs/apps", "creation",
                   _verify_capability("maker"),
                   "Maker: genera HTML, scripts, portfolios", "maker"),
        Capability("generar scripts", "creation",
                   _verify_capability("maker"),
                   "Scripts Python/Bash autónomos", "maker"),
        # Comunicación
        Capability("hablar español", "communication",
                   _verify_capability("nlg"),
                   "NLG por plantillas + Logos unificado", "nlg"),
        Capability("enviar a Telegram", "communication", services.get("eidos-bridge", False),
                   "Hermes Bridge v2 → Telegram", "hermes"),
        Capability("debate interno", "communication",
                   _verify_capability("colony"),
                   "Colony delibera temas entre personajes", "colony"),
        # Sistema
        Capability("aprender autónomamente", "system",
                   _verify_capability("autonomous_loop"),
                   "autonomous_research_loop.py 24/7", "autonomous_loop"),
        Capability("controlar pantalla", "system",
                   _verify_capability("screen_controller"),
                   "ScreenController con xdotool + USB HID", "screen_controller"),
        Capability("ejecutar en sandbox", "system",
                   _verify_capability("hexstrike"),
                   "WASMSandbox con bwrap (Docker)", "hexstrike"),
        Capability("curar grafo conocimiento", "system",
                   _verify_capability("curate_graph"),
                   "eidos_curate_graph.py (367K nodos)", "curate_graph"),
        Capability("memoria semántica", "system", services.get("chroma_http", False),
                   "ChromaDB embeddings + nomic-embed-text", "chromadb"),
    ]
    return caps


def _collect_traits() -> Dict[str, float]:
    """Rasgos de personalidad vía MetaCognition.
    Retorna dict {trait_name: confidence}."""
    try:
        from core.eidos_metacognition import get_metacognition
        mc = get_metacognition()
        dominant = mc.get_dominant_traits(min_confidence=0.1)
        if isinstance(dominant, list):
            # get_dominant_traits() retorna lista de dicts
            return {
                item.get("trait", "?"): item.get("confidence", 0.0)
                for item in dominant
            }
        elif isinstance(dominant, dict):
            return dominant
        return {}
    except Exception as e:
        log.debug("traits falló: %s", e)
        return {}


def _collect_anomalies() -> List[Dict[str, Any]]:
    """Anomalías del grafo vía Introspector."""
    try:
        from core.eidos_introspect import get_introspector
        intr = get_introspector()
        anomalies = intr.detect_anomalies()
        return [
            {"type": a.type, "severity": a.severity, "detail": a.detail,
             "suggestion": a.suggestion}
            for a in anomalies
        ]
    except Exception as e:
        log.debug("anomalies falló: %s", e)
        return []


def _collect_identity() -> Dict[str, Any]:
    """Identidad actual."""
    try:
        from core.eidos_identity import identity_hash, IDENTITY_VERSION
        return {
            "name": "EIDOS",
            "version": IDENTITY_VERSION,
            "hash": identity_hash(),
            "nature": "entidad digital autónoma",
            "language": "español",
            "owner": "SER (Luka)",
        }
    except Exception as e:
        log.debug("identity falló: %s", e)
        return {"name": "EIDOS", "version": "?", "hash": "?"}


def _compute_health_score(components: List[ComponentStatus],
                          services: Dict[str, bool],
                          anomalies: List[Dict[str, Any]]) -> float:
    """Calcula health score agregado."""
    if not components:
        return 0.5

    comp_scores = [c.health_score for c in components]
    comp_avg = sum(comp_scores) / len(comp_scores) if comp_scores else 0.5

    # Servicios: penalizar por cada servicio crítico caído
    critical_services = {"eidos-bridge", "chroma_http", "ollama"}
    service_penalty = sum(0.05 for s in critical_services if not services.get(s, False))

    # Anomalías: penalizar por severidad
    anomaly_penalty = sum(a.get("severity", 0) * 0.03 for a in anomalies)

    score = comp_avg - service_penalty - anomaly_penalty
    return max(0.0, min(1.0, score))


def _generate_summary(report: SelfInspectReport) -> str:
    """Genera resumen textual del informe."""
    n_ok = sum(1 for c in report.components if c.status == "healthy")
    n_total = len(report.components)
    n_caps = sum(1 for c in report.capabilities if c.available)
    svc_ok = sum(1 for v in report.services.values() if v)
    svc_total = len(report.services)

    return (
        f"EIDOS v{report.identity.get('version','?')} · "
        f"Salud: {int(report.health_score*100)}% · "
        f"Componentes: {n_ok}/{n_total} healthy · "
        f"Servicios: {svc_ok}/{svc_total} · "
        f"Capacidades: {n_caps}/{len(report.capabilities)} · "
        f"Grafo: {report.graph_metrics.get('total_nodes',0)} nodos "
        f"({report.graph_metrics.get('quality_nodes',0)} calidad) · "
        f"{len(report.anomalies)} anomalías"
    )


# ── Main API ──────────────────────────────────────────────────────────────────

def self_inspect(deep: bool = False) -> SelfInspectReport:
    """Introspección unificada completa.

    Args:
        deep: Si True, incluye sondas de threads/memoria (más lento).
              Si False (default), solo lo rápido (<500ms).

    Returns:
        SelfInspectReport con TODO el estado interno.
    """
    t0 = time.time()
    report_id = f"inspect_{int(t0)}"

    # ── Capa 1: Identidad (instantáneo) ────────────────────────────────────
    identity = _collect_identity()

    # ── Capa 2: Servicios externos (rápido, <200ms con timeouts de 1s) ─────
    services = _probe_services()

    # ── Capa 3: Grafo neuronal (SQLite, <100ms) ────────────────────────────
    graph_metrics = _collect_graph_metrics()

    # ── Capa 4: Componentes vía Supervisor (<200ms) ────────────────────────
    components = _collect_components()

    # ── Capa 5: Capacidades (<10ms) ────────────────────────────────────────
    capabilities = _collect_capabilities(services)

    # ── Capa 6: Anomalías (<100ms) ─────────────────────────────────────────
    anomalies = _collect_anomalies()

    # ── Capa 7: Rasgos de personalidad (<50ms) ─────────────────────────────
    traits = _collect_traits()

    # ── Capa 8: Sondas de hardware (<50ms) ─────────────────────────────────
    runtime_probes = _probe_hardware()
    runtime_probes += _probe_services_as_probes(services)

    # ── Capa 9 (deep): Threads, memoria, FDs (<1s) ─────────────────────────
    if deep:
        runtime_probes += _probe_memory()

    # ── Agregar ────────────────────────────────────────────────────────────
    health_score = _compute_health_score(components, services, anomalies)

    report = SelfInspectReport(
        timestamp=t0,
        identity=identity,
        health_score=health_score,
        components=components,
        capabilities=capabilities,
        runtime_probes=runtime_probes,
        graph_metrics=graph_metrics,
        anomalies=anomalies,
        traits=traits,
        services=services,
    )
    report.summary = _generate_summary(report)
    elapsed = time.time() - t0

    log.info("self_inspect(%s) completado en %.2fs: %s",
             "deep" if deep else "fast", elapsed, report.summary)
    return report


# ── Singleton ──────────────────────────────────────────────────────────────────

_self_inspect_cache: Optional[SelfInspectReport] = None
_self_inspect_cache_ttl: float = 15.0  # 15 segundos de caché


def get_self_inspect(force: bool = False, deep: bool = False) -> SelfInspectReport:
    """Singleton con caché. Usar en respuestas rápidas."""
    global _self_inspect_cache
    now = time.time()
    if (not force and _self_inspect_cache is not None
            and now - _self_inspect_cache.timestamp < _self_inspect_cache_ttl):
        return _self_inspect_cache
    _self_inspect_cache = self_inspect(deep=deep)
    return _self_inspect_cache


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="EIDOS Self-Inspect")
    p.add_argument("--deep", action="store_true", help="Inspección profunda")
    p.add_argument("--json", action="store_true", help="Salida JSON")
    p.add_argument("--health", action="store_true", help="Solo health score")
    p.add_argument("--capabilities", action="store_true", help="Solo capacidades")
    p.add_argument("--answer", action="store_true", help="Respuesta 'qué eres?'")
    args = p.parse_args()

    report = self_inspect(deep=args.deep)

    if args.health:
        print(f"Health: {int(report.health_score * 100)}%")
    elif args.capabilities:
        print(report.capabilities_text())
    elif args.answer:
        print(report.to_answer())
    elif args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"=== EIDOS Self-Inspect {'(deep)' if args.deep else '(fast)'} ===")
        print(report.to_answer())
        print()
        print(report.capabilities_text())
        print()
        print(report.health_text())
        print()
        print(f"Summary: {report.summary}")
