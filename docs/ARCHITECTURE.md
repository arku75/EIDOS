# Arquitectura (resumen para desarrolladores)

Este archivo resume los componentes clave y dónde intervenir para contribuir.

- `bridge_to_eidos.py` — API REST principal (puerto 8003). Punto de entrada para interfaces.
- `colony_community.py` — motor multi-agente. Añadir agentes aquí.
- `eidos_natural.py` — respuestas instantáneas, lógica sin llamar modelos.
- `brain_memory.py` — persistencia principal, cuidado: no subir DBs en el repo.
- `web-panel/` — interfaz web (server.py en 8080).

Puntos de extensión
- Agentes: `core/*` (ver `skill_registry.py` y `agent.py`).
- Integraciones: `trinity_connector.py`, `moltbook_connector.py`.
- Observabilidad: `eidos_monitor.py`, `eidos_health_check.py`.

Consejos de desarrollo
- Usa `./eidos start` y `./eidos stop` para controlar servicios.
- Los cambios en `core/` pueden requerir reinicio completo para recargar la colonia.
