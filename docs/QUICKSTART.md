# Quickstart — Ejecutar EIDOS localmente (modo seguro)

Este documento cubre los pasos mínimos para arrancar EIDOS en un entorno local de desarrollo.

Requisitos
- Python 3.11+
- Crear un entorno virtual: `python -m venv .venv` y `source .venv/bin/activate`
- Instalar dependencias: `pip install -r requirements.txt` (si no existe, instalar manualmente las librerías listadas en `setup.py`)

Configuración
1. Copia ejemplo de env y edita: `cp .env.example .env` (si existe). Rellena `TELEGRAM_BOT_TOKEN` y `EIDOS_TELEGRAM_ALLOWED`.
2. Nunca guardes tokens en commits.

Arrancar localmente (modo minimal):
```bash
source .venv/bin/activate
python3 eidos_main.py --mode=lite
```

Comandos útiles
- `./eidos status` — estado de servicios
- `./eidos start` — iniciar todos los servicios
- `./eidos stop` — detener
