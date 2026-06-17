"""
core/research_orchestrator.py — Orquestador de investigación multi-canal (S76 Fase 3)

Coordina búsquedas a través de múltiples canales con caché inteligente (TTL
por canal) y fusión de resultados. Sin LLM: cada canal es determinista.

Canales:
  LOCAL     → knowledge_nodes en SQLite (grafo neuronal)
  CODE      → nodos graphify (estructura de código real)
  WEB       → ddg (DuckDuckGo) + wikipedia
  MAN       → man pages del sistema
  APT       → paquetes disponibles vía apt-cache
  WORDNET   → diccionario WordNet EN+ES inyectado

Caché:
  - TTL por canal (LOCAL=60s, CODE=300s, WEB=600s, MAN=3600s, APT=300s, WORDNET=120s)
  - Clave: hash MD5 de (canal, query)
  - Almacenado en ~/.eidos/research_cache.json

API:
    orch = ResearchOrch()
    results = orch.research("nginx configuration reverse proxy")
    # → Dict con resultados de todos los canales aplicables
    # orch.research("nginx", channels=["local", "web", "man"])
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from core.db import get_conn
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger("eidos.research_orch")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
CACHE_FILE = Path.home() / ".eidos" / "research_cache.json"

# ── TTL por canal (segundos) ─────────────────────────────────────────────────
CHANNEL_TTL = {
    "local": 60,       # Grafo neuronal (cambia rápido)
    "code": 300,       # Estructura de código (cambia medio)
    "web": 600,        # Web (10 min, respeta rate limits)
    "man": 3600,       # Man pages (casi estáticas)
    "apt": 300,        # Paquetes (5 min)
    "wordnet": 120,    # Diccionario (casi estático, 2 min)
    "ddg": 600,        # DuckDuckGo (10 min)
    "wikipedia": 900,  # Wikipedia (15 min)
    "wiki_es": 900,    # Wikipedia ES
}

# Prioridad de canales (orden de consulta)
CHANNEL_PRIORITY = [
    "local", "code", "wordnet", "man",
    "apt", "wikipedia", "ddg", "web"
]


@dataclass
class ResearchResult:
    """Resultado de un canal de investigación."""
    channel: str
    query: str
    items: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    duration_ms: float = 0
    cached: bool = False
    hits: int = 0

    @property
    def success(self) -> bool:
        return self.error is None

    def to_text(self) -> str:
        if self.error:
            return f"[{self.channel}] ERROR: {self.error}"
        if not self.items:
            return f"[{self.channel}] sin resultados"
        lines = [f"[{self.channel}] {len(self.items)} resultados:"]
        for item in self.items[:10]:
            title = item.get("concept") or item.get("title") or item.get("name", "?")
            desc = item.get("definition") or item.get("snippet") or item.get("description", "")
            lines.append(f"  • {title}")
            if desc and len(desc) < 200:
                lines.append(f"    {desc}")
        return "\n".join(lines)


class ResearchOrch:
    """Orquestador de investigación multi-canal.

    Uso:
        orch = ResearchOrch()
        results = orch.research("cómo configurar nginx reverse proxy")
        for r in results:
            print(r.to_text())
    """

    def __init__(self, cache_ttl_overrides: Optional[Dict[str, int]] = None):
        self.ttl = {**CHANNEL_TTL, **(cache_ttl_overrides or {})}
        self._cache: Dict[str, Dict] = self._load_cache()
        self._db_path = str(BRAIN_DB)
        log.info("ResearchOrch: %d canales, %d entradas en caché",
                 len(CHANNEL_TTL), len(self._cache))

    # ── API principal ─────────────────────────────────────────────────────────

    def research(self, query: str, *,
                 channels: Optional[List[str]] = None,
                 force: bool = False,
                 max_results_per_channel: int = 20) -> List[ResearchResult]:
        """Investiga una query en todos los canales aplicables.

        Args:
            query: Consulta en lenguaje natural.
            channels: Canales a usar (None = todos).
            force: Ignorar caché.
            max_results_per_channel: Límite de resultados por canal.

        Returns:
            Lista de ResearchResult, uno por canal (ordenados por prioridad).
        """
        if channels is None:
            channels = list(CHANNEL_PRIORITY)

        results: List[ResearchResult] = []
        t0 = time.time()

        for channel in channels:
            result = self._query_channel(channel, query, force, max_results_per_channel)
            results.append(result)

        total_ms = (time.time() - t0) * 1000
        total_hits = sum(r.hits for r in results)
        log.info("research(%s): %d hits en %d canales (%.0fms)",
                 query[:60], total_hits, len(results), total_ms)

        # Ordenar por prioridad
        priority_map = {ch: i for i, ch in enumerate(CHANNEL_PRIORITY)}
        results.sort(key=lambda r: priority_map.get(r.channel, 99))

        return results

    def research_best(self, query: str, **kwargs) -> Optional[str]:
        """Investiga y retorna el mejor resultado como texto unificado."""
        results = self.research(query, **kwargs)
        parts = []
        for r in results:
            if r.success and r.hits > 0:
                parts.append(r.to_text())
        return "\n\n".join(parts) if parts else None

    # ── Canales ───────────────────────────────────────────────────────────────

    def _query_channel(self, channel: str, query: str,
                       force: bool, max_results: int) -> ResearchResult:
        """Consulta un canal específico con caché."""
        t0 = time.time()

        # Verificar caché
        if not force:
            cache_key = self._cache_key(channel, query)
            cached = self._cache_get(cache_key)
            if cached is not None:
                items = cached[:max_results]
                return ResearchResult(
                    channel=channel, query=query,
                    items=items, cached=True,
                    hits=len(items),
                    duration_ms=(time.time() - t0) * 1000
                )

        # Ejecutar canal
        try:
            handler = getattr(self, f"_ch_{channel}", None)
            if handler is None:
                return ResearchResult(
                    channel=channel, query=query,
                    error=f"Canal '{channel}' no implementado"
                )

            items = handler(query, max_results)
            duration_ms = (time.time() - t0) * 1000

            # Guardar en caché
            cache_key = self._cache_key(channel, query)
            self._cache_set(cache_key, items, self.ttl.get(channel, 300))

            return ResearchResult(
                channel=channel, query=query,
                items=items, hits=len(items),
                duration_ms=duration_ms
            )

        except Exception as e:
            log.debug("_ch_%s error: %s", channel, e)
            return ResearchResult(
                channel=channel, query=query,
                error=str(e)[:200],
                duration_ms=(time.time() - t0) * 1000
            )

    # ── Implementaciones de canales ───────────────────────────────────────────

    def _ch_local(self, query: str, max_results: int) -> List[Dict]:
        """Busca en el grafo neuronal (knowledge_nodes)."""
        try:
            conn = get_conn(self._db_path, timeout=5)
            words = [w for w in re.findall(r"\w{3,}", query.lower())
                    if w not in ("que", "los", "las", "del", "con", "para",
                                 "por", "una", "como", "qué", "cómo", "the")]
            if not words:
                return []

            # Construir query SQL con LIKE para cada palabra
            conditions = " OR ".join(
                [f"(concept LIKE '%{w}%' OR definition LIKE '%{w}%')" for w in words[:8]]
            )
            sql = (
                f"SELECT concept, definition, category, confidence, source "
                f"FROM knowledge_nodes "
                f"WHERE {conditions} "
                f"ORDER BY confidence DESC LIMIT {max_results}"
            )

            rows = conn.execute(sql).fetchall()

            return [
                {"concept": r[0], "definition": r[1] or "",
                 "category": r[2], "confidence": r[3], "source": r[4]}
                for r in rows
            ]
        except Exception as e:
            log.debug("_ch_local: %s", e)
            return []

    def _ch_code(self, query: str, max_results: int) -> List[Dict]:
        """Busca en nodos de estructura de código (source='graphify')."""
        try:
            conn = get_conn(self._db_path, timeout=5)
            words = [w for w in re.findall(r"\w{2,}", query.lower())[:6]]
            if not words:
                return []

            conditions = " OR ".join(
                [f"concept LIKE '%{w}%'" for w in words]
            )
            sql = (
                f"SELECT concept, definition, category, confidence "
                f"FROM knowledge_nodes "
                f"WHERE source='graphify' AND ({conditions}) "
                f"LIMIT {max_results}"
            )
            rows = conn.execute(sql).fetchall()

            return [
                {"concept": r[0], "definition": r[1] or "",
                 "category": r[2], "confidence": r[3]}
                for r in rows
            ]
        except Exception as e:
            log.debug("_ch_code: %s", e)
            return []

    def _ch_wordnet(self, query: str, max_results: int) -> List[Dict]:
        """Busca en nodos WordNet (wn_*)."""
        try:
            conn = get_conn(self._db_path, timeout=5)
            words = [w for w in re.findall(r"\w{2,}", query.lower())[:6]]
            if not words:
                return []

            conditions = " OR ".join(
                [f"concept LIKE '%{w}%'" for w in words]
            )
            sql = (
                f"SELECT concept, definition, category, confidence "
                f"FROM knowledge_nodes "
                f"WHERE (id LIKE 'wn_%') AND ({conditions}) "
                f"ORDER BY confidence DESC LIMIT {max_results}"
            )
            rows = conn.execute(sql).fetchall()

            return [
                {"concept": r[0], "definition": r[1] or "",
                 "category": r[2], "confidence": r[3]}
                for r in rows
            ]
        except Exception as e:
            log.debug("_ch_wordnet: %s", e)
            return []

    def _ch_man(self, query: str, max_results: int) -> List[Dict]:
        """Busca en man pages."""
        try:
            # Extraer posible nombre de comando
            cmd_match = re.search(r"\b([a-z][a-z0-9_-]{1,30})\b", query.lower())
            if not cmd_match:
                return []
            cmd = cmd_match.group(0)

            # Intentar man page
            r = subprocess.run(
                ["man", "-P", "cat", cmd],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode != 0 or not r.stdout.strip():
                return []

            # Extraer NAME y DESCRIPTION
            text = r.stdout[:5000]
            name_match = re.search(r"NAME\s*\n\s+(.+?)\n", text, re.I)
            desc_match = re.search(r"DESCRIPTION\s*\n(.+?)(?:\n\S|\Z)", text, re.I | re.S)

            items = []
            name = name_match.group(1).strip() if name_match else cmd
            desc = desc_match.group(1).strip()[:500] if desc_match else text[:500]

            items.append({
                "concept": f"man:{cmd}",
                "definition": desc,
                "category": "man_page",
                "source": "man",
                "confidence": 0.95
            })
            return items[:max_results]

        except Exception as e:
            log.debug("_ch_man: %s", e)
            return []

    def _ch_apt(self, query: str, max_results: int) -> List[Dict]:
        """Busca paquetes en apt-cache."""
        try:
            pkg_match = re.search(r"\b([a-z][a-z0-9._-]{1,40})\b", query.lower())
            if not pkg_match:
                return []
            pkg = pkg_match.group(0)

            r = subprocess.run(
                ["apt-cache", "search", pkg],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode != 0:
                return []

            items = []
            for line in r.stdout.strip().split("\n")[:max_results]:
                parts = line.split(" - ", 1)
                name = parts[0].strip()
                desc = parts[1].strip() if len(parts) > 1 else ""
                items.append({
                    "concept": f"pkg:{name}",
                    "definition": desc,
                    "category": "apt_package",
                    "source": "apt",
                    "confidence": 0.8
                })
            return items

        except Exception as e:
            log.debug("_ch_apt: %s", e)
            return []

    def _ch_ddg(self, query: str, max_results: int) -> List[Dict]:
        """Busca en DuckDuckGo (sin API key, via HTML scrape)."""
        try:
            import urllib.request
            import urllib.parse
            from html.parser import HTMLParser

            q = urllib.parse.quote(query[:100])
            url = f"https://html.duckduckgo.com/html/?q={q}"

            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            # Parse mínimo de resultados
            items = []
            # Buscar snippets en HTML de DDG
            for m in re.finditer(
                r'<a[^>]*class="result__a"[^>]*>(.*?)</a>.*?'
                r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
                html, re.DOTALL | re.I
            ):
                title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                snippet = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                if title:
                    items.append({
                        "title": title,
                        "snippet": snippet,
                        "source": "ddg",
                        "confidence": 0.6
                    })
                if len(items) >= max_results:
                    break

            return items

        except Exception as e:
            log.debug("_ch_ddg: %s", e)
            return []

    def _ch_wikipedia(self, query: str, max_results: int) -> List[Dict]:
        """Busca en Wikipedia (EN)."""
        return self._wikipedia_search(query, max_results, "en")

    def _ch_wiki_es(self, query: str, max_results: int) -> List[Dict]:
        """Busca en Wikipedia (ES)."""
        return self._wikipedia_search(query, max_results, "es")

    def _wikipedia_search(self, query: str, max_results: int,
                          lang: str = "en") -> List[Dict]:
        """Busca en Wikipedia vía API REST."""
        try:
            import urllib.request
            import urllib.parse

            q = urllib.parse.quote(query[:100])
            url = (
                f"https://{lang}.wikipedia.org/w/api.php"
                f"?action=opensearch&search={q}&limit={max_results}"
                f"&namespace=0&format=json"
            )

            req = urllib.request.Request(url, headers={
                "User-Agent": "EIDOS/1.0 (research; contact@eidos.local)"
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            # Formato: [query, [titles...], [descriptions...], [urls...]]
            items = []
            titles = data[1] if len(data) > 1 else []
            descs = data[2] if len(data) > 2 else []
            urls = data[3] if len(data) > 3 else []

            for i in range(min(len(titles), max_results)):
                items.append({
                    "title": titles[i],
                    "description": descs[i] if i < len(descs) else "",
                    "url": urls[i] if i < len(urls) else "",
                    "source": f"wikipedia_{lang}",
                    "confidence": 0.85
                })

            return items

        except Exception as e:
            log.debug("_wikipedia_search(%s): %s", lang, e)
            return []

    def _ch_web(self, query: str, max_results: int) -> List[Dict]:
        """Canal 'web' compuesto: DDG + Wikipedia ES."""
        items = []
        items.extend(self._ch_ddg(query, max_results // 2 or 1))
        items.extend(self._ch_wiki_es(query, max_results // 2 or 1))
        return items

    # ── Caché ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _cache_key(channel: str, query: str) -> str:
        raw = f"{channel}:{query.lower().strip()}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    def _cache_get(self, key: str) -> Optional[List[Dict]]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        if time.time() - entry.get("ts", 0) > entry.get("ttl", 300):
            del self._cache[key]
            return None
        return entry.get("items", [])

    def _cache_set(self, key: str, items: List[Dict], ttl: int):
        self._cache[key] = {"ts": time.time(), "ttl": ttl, "items": items}
        # Persistir cada 50 escrituras
        if len(self._cache) % 50 == 0:
            self._save_cache()

    def _load_cache(self) -> Dict:
        try:
            if CACHE_FILE.exists():
                with open(CACHE_FILE, "r") as f:
                    data = json.load(f)
                # Limpiar entradas expiradas
                now = time.time()
                return {
                    k: v for k, v in data.items()
                    if now - v.get("ts", 0) < v.get("ttl", 300)
                }
        except Exception:
            pass
        return {}

    def _save_cache(self):
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, "w") as f:
                json.dump(self._cache, f, ensure_ascii=False)
        except Exception as e:
            log.debug("_save_cache: %s", e)

    def clear_cache(self):
        """Limpia toda la caché."""
        self._cache.clear()
        try:
            CACHE_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def cache_stats(self) -> Dict[str, Any]:
        """Estadísticas de caché."""
        entries = len(self._cache)
        channels = {}
        for k, v in self._cache.items():
            # Inferir canal del prefijo (no podemos, usamos conteo simple)
            pass
        return {"entries": entries, "file": str(CACHE_FILE)}


# ── Singleton ──────────────────────────────────────────────────────────────────

_research_orch: Optional[ResearchOrch] = None


def get_research_orch() -> ResearchOrch:
    global _research_orch
    if _research_orch is None:
        _research_orch = ResearchOrch()
    return _research_orch


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(description="EIDOS Research Orchestrator")
    p.add_argument("query", nargs="+", help="Consulta a investigar")
    p.add_argument("--channels", type=str, default="local,code,wordnet",
                   help="Canales separados por coma")
    p.add_argument("--force", action="store_true", help="Ignorar caché")
    args = p.parse_args()

    query = " ".join(args.query)
    channels = [c.strip() for c in args.channels.split(",")]

    orch = ResearchOrch()
    results = orch.research(query, channels=channels, force=args.force)

    for r in results:
        print(r.to_text())
        print()

    print(f"Caché: {orch.cache_stats()['entries']} entradas")
