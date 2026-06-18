> ⚖️ **EIDOS © 2026 SER · Licencia [ESSL v1.0](../LICENSE) — propietaria, source-available.** Prohibida la replicación, el uso comercial y construir un producto competidor. Todo cambio o propuesta debe documentarse en `THIRD_PARTY_CHANGES.md` y comunicarse a SER. EIDOS **no** es open source.

# Preparar una release pública (sanitizada)

Este documento explica cómo generar una copia del repositorio lista para publicar en GitHub sin exponer secretos ni datos personales.

Pasos recomendados:

1. Revocar cualquier token o contraseña publicados. NO continúes si no lo has hecho.

2. Generar una copia sanitizada (script incluido): `scripts/prepare_sanitized_repo.sh`.
   - El script excluye carpetas de estado (`.eidos`, `__pycache__`, `node_modules`), bases de datos, y patrones comunes de secretos.
   - Crea un tarball en la carpeta superior llamado `EIDOS_sanitized_RELEASE.tar.gz` listo para subir a Releases.

3. Revisar manualmente la copia en `release_tmp/` antes de subir.

4. Subir código a GitHub usando SSH o `gh` (recomendado):

```bash
# crear repo remoto (si no existe)
gh auth login
gh repo create arku75/EIDOS --public --source=release_tmp --remote=origin --push
```

5. Para archivos grandes (modelos, imágenes, dumps), usa GitHub Releases o Git LFS en lugar del repo principal.

6. Publica una release en GitHub y añade los tarballs como assets.

Notas de seguridad
- Nunca incluyas `.eidos/api_keys.json`, `~/.eidos/*`, `*.db` o `*.pem` en el repo.
- Revisa `git log --stat` si tienes dudas: secretos pueden haber quedado en commits antiguos.
