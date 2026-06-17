# Guía rápida de seguridad para publicar EIDOS

Antes de publicar o compartir código públicamente:

1. Revocar cualquier token/clave expuesta.
2. Eliminar archivos de estado o bases de datos (`.eidos`, `*.db`, `*.sqlite`).
3. Revisar historial Git para secretos (usar `git-filter-repo` si es necesario).
4. No subir modelos o datos personales; usar Releases o Git LFS si es necesario.

Gestión de secretos:
- Mantén credenciales en variables de entorno o servicios de secrets (GitHub Secrets para CI).
- Proporciona `.env.example` sin valores reales.

Contacto de seguridad: reportar a SER por Telegram: https://t.me/ARKUu_12_8
