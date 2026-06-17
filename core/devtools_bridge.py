"""
EIDOS core/devtools_bridge.py — Browser DevTools Bridge
Acceso a DevTools del browser vía Playwright CDP (Chrome DevTools Protocol).

Capacidades:
- Captura de network requests/responses
- Console logs (errores, warnings, info)
- Performance metrics
- Coverage de JavaScript
- Interceptación y modificación de requests
- Ejecución de JS en contexto de página

Uso:
    from core.devtools_bridge import get_devtools
    dt = get_devtools()
    dt.attach()
    logs = dt.get_console_logs()
    network = dt.get_network_log()
    metrics = dt.get_performance()
    dt.detach()
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("eidos.devtools")

# ── Tipos ──────────────────────────────────────────────────────────────────────

@dataclass
class ConsoleMessage:
    level: str       # log, info, warn, error, debug
    text: str
    url: str
    line: int
    ts: float = field(default_factory=time.time)


@dataclass
class NetworkRequest:
    request_id: str
    url: str
    method: str
    headers: Dict[str, str]
    post_data: Optional[str]
    resource_type: str
    ts: float = field(default_factory=time.time)


@dataclass
class NetworkResponse:
    request_id: str
    url: str
    status: int
    headers: Dict[str, str]
    body: Optional[str]
    timing_ms: float
    ts: float = field(default_factory=time.time)


@dataclass
class PerformanceMetrics:
    layout_count: int
    recalc_style_count: int
    script_duration: float
    task_duration: float
    heap_used_mb: float
    heap_total_mb: float
    dom_content_loaded_ms: float
    load_ms: float


# ── DevTools Bridge ────────────────────────────────────────────────────────────

class DevToolsBridge:
    """
    Bridge entre EIDOS y las DevTools del browser activo.
    Usa Playwright si está disponible, con fallback a CDP directo.
    """

    def __init__(self):
        self._page = None
        self._console_log: List[ConsoleMessage] = []
        self._network_requests: Dict[str, NetworkRequest] = {}
        self._network_responses: List[NetworkResponse] = []
        self._attached = False
        self._js_errors: List[str] = []

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def attach(self, page=None) -> bool:
        """
        Adjunta las DevTools a la página activa del browser EIDOS.
        Si page=None intenta obtenerla de eidos_browser singleton.
        """
        try:
            if page is not None:
                self._page = page
            else:
                from core.eidos_browser import get_browser
                br = get_browser()
                if not br._started:
                    br.start()
                if hasattr(br, "page"):
                    self._page = br.page
                else:
                    log.warning("Browser no tiene .page (modo selenium — DevTools CDP no disponible)")
                    return False

            self._wire_events()
            self._attached = True
            log.info("DevTools adjuntadas a la página: %s", self._page.url)
            return True
        except Exception as e:
            log.error("Error adjuntando DevTools: %s", e)
            return False

    def detach(self) -> None:
        """Desconecta los listeners."""
        self._page = None
        self._attached = False

    def _wire_events(self) -> None:
        """Registra listeners de Playwright para capturar eventos DevTools."""
        if self._page is None:
            return

        # Console
        self._page.on("console", self._on_console)

        # Page errors (JS no capturado)
        self._page.on("pageerror", lambda err: self._js_errors.append(str(err)))

        # Network
        self._page.on("request",  self._on_request)
        self._page.on("response", self._on_response)

        log.debug("Listeners DevTools registrados")

    def _on_console(self, msg) -> None:
        try:
            loc = msg.location or {}
            self._console_log.append(ConsoleMessage(
                level=msg.type,
                text=msg.text,
                url=loc.get("url", ""),
                line=loc.get("lineNumber", 0),
            ))
        except Exception:
            pass

    def _on_request(self, request) -> None:
        try:
            self._network_requests[request.url] = NetworkRequest(
                request_id=request.url,
                url=request.url,
                method=request.method,
                headers=dict(request.headers),
                post_data=request.post_data,
                resource_type=request.resource_type,
            )
        except Exception:
            pass

    def _on_response(self, response) -> None:
        try:
            t0 = time.time()
            body = None
            try:
                if "json" in response.headers.get("content-type", ""):
                    body = response.text()[:2000]
            except Exception:
                pass
            timing = round((time.time() - t0) * 1000, 1)
            self._network_responses.append(NetworkResponse(
                request_id=response.url,
                url=response.url,
                status=response.status,
                headers=dict(response.headers),
                body=body,
                timing_ms=timing,
            ))
        except Exception:
            pass

    # ── Consultas ──────────────────────────────────────────────────────────────

    def get_console_logs(self, level: str = None) -> List[ConsoleMessage]:
        """Devuelve los console logs capturados. Filtra por level si se especifica."""
        if level:
            return [m for m in self._console_log if m.level == level]
        return list(self._console_log)

    def get_errors(self) -> List[str]:
        """Devuelve errores de consola + JS no capturados."""
        errs = [m.text for m in self._console_log if m.level == "error"]
        errs.extend(self._js_errors)
        return errs

    def get_network_log(self, filter_type: str = None) -> List[NetworkResponse]:
        """
        Devuelve el log de red capturado.
        filter_type: 'xhr', 'fetch', 'document', 'script', 'stylesheet', etc.
        """
        if filter_type:
            req_urls = {url for url, req in self._network_requests.items()
                       if req.resource_type == filter_type}
            return [r for r in self._network_responses if r.url in req_urls]
        return list(self._network_responses)

    def get_performance(self) -> Optional[PerformanceMetrics]:
        """Obtiene métricas de performance vía CDP."""
        if self._page is None:
            return None
        try:
            metrics_raw = self._page.evaluate("""() => {
                const nav = performance.getEntriesByType('navigation')[0] || {};
                const mem = performance.memory || {};
                return {
                    domContentLoaded: nav.domContentLoadedEventEnd - nav.startTime || 0,
                    load: nav.loadEventEnd - nav.startTime || 0,
                    heapUsed: mem.usedJSHeapSize || 0,
                    heapTotal: mem.totalJSHeapSize || 0,
                };
            }""")
            return PerformanceMetrics(
                layout_count=0,
                recalc_style_count=0,
                script_duration=0.0,
                task_duration=0.0,
                heap_used_mb=round(metrics_raw.get("heapUsed", 0) / 1e6, 2),
                heap_total_mb=round(metrics_raw.get("heapTotal", 0) / 1e6, 2),
                dom_content_loaded_ms=round(metrics_raw.get("domContentLoaded", 0), 1),
                load_ms=round(metrics_raw.get("load", 0), 1),
            )
        except Exception as e:
            log.warning("Error obteniendo performance: %s", e)
            return None

    def execute_devtools_js(self, js_code: str) -> Any:
        """Ejecuta JS en la página activa con acceso a DevTools APIs."""
        if self._page is None:
            return None
        try:
            return self._page.evaluate(js_code)
        except Exception as e:
            log.error("Error ejecutando JS: %s", e)
            return None

    def intercept_requests(self, url_pattern: str, handler: Callable) -> None:
        """
        Intercepta requests que coincidan con url_pattern.
        handler(route, request) — puede llamar route.continue_() o route.fulfill()
        """
        if self._page is None:
            return
        try:
            self._page.route(url_pattern, handler)
            log.info("Interceptando requests: %s", url_pattern)
        except Exception as e:
            log.error("Error configurando intercepción: %s", e)

    def get_dom_snapshot(self) -> Dict:
        """Obtiene un snapshot completo del DOM actual."""
        if self._page is None:
            return {}
        try:
            return self._page.evaluate("""() => {
                function nodeInfo(el, depth=0) {
                    if (depth > 5) return null;
                    return {
                        tag: el.tagName || '#text',
                        id: el.id || null,
                        classes: el.className || null,
                        text: el.tagName ? null : el.textContent?.slice(0, 100),
                        children: Array.from(el.children || [])
                            .slice(0, 20)
                            .map(c => nodeInfo(c, depth+1))
                            .filter(Boolean)
                    };
                }
                return nodeInfo(document.body);
            }""")
        except Exception as e:
            log.error("Error obteniendo DOM snapshot: %s", e)
            return {}

    def get_storage(self) -> Dict:
        """Obtiene localStorage, sessionStorage y cookies."""
        if self._page is None:
            return {}
        try:
            storage = self._page.evaluate("""() => ({
                localStorage: Object.fromEntries(Object.entries(localStorage)),
                sessionStorage: Object.fromEntries(Object.entries(sessionStorage)),
            })""")
            cookies = self._page.context.cookies()
            storage["cookies"] = [
                {"name": c["name"], "domain": c["domain"], "path": c["path"]}
                for c in cookies
            ]
            return storage
        except Exception as e:
            log.error("Error obteniendo storage: %s", e)
            return {}

    def clear_logs(self) -> None:
        """Limpia los logs acumulados."""
        self._console_log.clear()
        self._network_requests.clear()
        self._network_responses.clear()
        self._js_errors.clear()

    def summary(self) -> str:
        """Resumen textual del estado DevTools actual."""
        perf = self.get_performance()
        lines = [
            f"DevTools {'adjuntadas' if self._attached else 'desconectadas'}",
            f"Console logs: {len(self._console_log)} ({len(self.get_errors())} errores)",
            f"Network: {len(self._network_responses)} responses capturadas",
            f"JS errors: {len(self._js_errors)}",
        ]
        if perf:
            lines.append(
                f"Performance: DOM={perf.dom_content_loaded_ms}ms "
                f"Load={perf.load_ms}ms Heap={perf.heap_used_mb}MB"
            )
        return "\n".join(lines)


_instance: Optional[DevToolsBridge] = None


def get_devtools() -> DevToolsBridge:
    global _instance
    if _instance is None:
        _instance = DevToolsBridge()
    return _instance
