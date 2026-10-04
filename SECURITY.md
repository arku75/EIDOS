> ⚖️ **EIDOS © 2026 SER · Licencia [ESSL v1.0](LICENSE) — propietaria, source-available.** Prohibida la replicación, el uso comercial y construir un producto competidor. Todo cambio o propuesta debe documentarse en `THIRD_PARTY_CHANGES.md` y comunicarse a SER. EIDOS **no** es open source.

# Reportar vulnerabilidades

Si has encontrado una vulnerabilidad de seguridad en EIDOS, por favor contacta a SER por Telegram: https://t.me/ARKUu_12_8

Incluye:
- Descripción del problema
- Pasos para reproducir
- Severidad estimada

No publiques exploits en Issues públicas; usa el canal privado anterior para que podamos corregirlo.

## Secretos y credenciales

- Nunca incluyas claves, tokens, cookies, archivos `.env`, credenciales cloud o claves SSH en Issues, PRs, logs o commits.
- Si una credencial llega a Git, **revócala o rótala primero**. Eliminarla del archivo actual no la elimina del historial.
- Después de rotarla, limpia el historial cuando corresponda y vuelve a verificar con GitHub Secret Scanning.
- Usa `.env.example` únicamente con valores vacíos o ficticios; las credenciales reales deben permanecer fuera del repositorio.

## Dependencias

Las alertas de Dependabot deben revisarse antes de fusionar actualizaciones grandes. Se priorizan parches/minor de seguridad compatibles y se prueban los cambios que impliquen saltos de versión mayor.
