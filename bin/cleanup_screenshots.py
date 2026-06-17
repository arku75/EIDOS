#!/usr/bin/env python3
"""cleanup_screenshots.py — Limpieza automática de screenshots para EIDOS [S94]

"Sin screenshots viejos no hay I/O congestion." — DeepSeek

Elimina capturas viejas de ~/.eidos/screenshots/ para evitar que el
directorio crezca sin control (llegó a 8.6GB, 13460 archivos).

Política (conservadora):
  - Mantener máximo 2000 screenshots (los más recientes)
  - Eliminar screenshots de más de 3 días
  - Si tras limpiar por edad quedan > 2000, eliminar los más viejos

Uso:
    python3 bin/cleanup_screenshots.py [--dry-run] [--max-files 2000] [--max-age-days 3]
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

log = logging.getLogger("eidos.cleanup_screenshots")

SS_DIR = Path.home() / ".eidos" / "screenshots"
LOG_PATH = Path.home() / ".eidos" / "logs" / "cleanup_screenshots.log"

DEFAULT_MAX_FILES = 2000
DEFAULT_MAX_AGE_DAYS = 3


def get_screenshots() -> list[tuple[Path, float, int]]:
    """Retorna lista de (path, mtime, size) ordenada por mtime descendente."""
    if not SS_DIR.exists():
        return []
    files = []
    for p in SS_DIR.iterdir():
        if p.is_file() and p.suffix in (".png", ".jpg", ".jpeg"):
            try:
                stat = p.stat()
                files.append((p, stat.st_mtime, stat.st_size))
            except OSError:
                pass
    files.sort(key=lambda x: x[1], reverse=True)  # más recientes primero
    return files


def cleanup(max_files: int = DEFAULT_MAX_FILES,
            max_age_days: int = DEFAULT_MAX_AGE_DAYS,
            dry_run: bool = False) -> dict:
    """Ejecuta limpieza. Retorna dict con métricas."""
    files = get_screenshots()
    if not files:
        log.info("Sin screenshots que limpiar")
        # [S122] incluir 'remaining' — sin ella, main() daba KeyError al imprimir
        # el resumen cuando el directorio estaba vacío → servicio en estado failed.
        return {"total": 0, "deleted": 0, "freed_bytes": 0, "remaining": 0}

    cutoff = time.time() - (max_age_days * 86400)
    to_delete: set[Path] = set()
    total_size = sum(f[2] for f in files)

    # Fase 1: eliminar por antigüedad
    for path, mtime, size in files:
        if mtime < cutoff:
            to_delete.add(path)

    # Fase 2: si aún quedan más de max_files, eliminar los más viejos
    remaining = [f for f in files if f[0] not in to_delete]
    if len(remaining) > max_files:
        overflow = remaining[max_files:]  # los más viejos
        for path, mtime, size in overflow:
            to_delete.add(path)

    freed = sum(
        os.path.getsize(p) for p in to_delete
        if p.exists()
    ) if not dry_run else sum(f[2] for f in files if f[0] in to_delete)

    if dry_run:
        log.info("DRY RUN — se eliminarían %d/%d archivos (%d MB)",
                 len(to_delete), len(files), freed // (1024 * 1024))
        for p in sorted(to_delete):
            log.debug("  [dry-run] %s", p.name)
    else:
        deleted = 0
        errors = 0
        for p in to_delete:
            try:
                p.unlink()
                deleted += 1
            except OSError as e:
                errors += 1
                log.debug("error eliminando %s: %s", p.name, e)
        log.info("Eliminados %d/%d archivos (%d MB liberados, %d errores)",
                 deleted, len(files), freed // (1024 * 1024), errors)

    return {
        "total": len(files),
        "deleted": len(to_delete),
        "freed_bytes": freed,
        "remaining": len(files) - len(to_delete),
    }


def main():
    p = argparse.ArgumentParser(
        description="Limpieza automática de screenshots EIDOS"
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Mostrar lo que se eliminaría sin hacer cambios")
    p.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES,
                   help=f"Máximo de screenshots a conservar (default: {DEFAULT_MAX_FILES})")
    p.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
                   help=f"Días tras los cuales se eliminan (default: {DEFAULT_MAX_AGE_DAYS})")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH),
            logging.StreamHandler(sys.stderr),
        ],
    )

    if not SS_DIR.exists():
        log.info("%s no existe, nada que limpiar", SS_DIR)
        return 0

    result = cleanup(
        max_files=args.max_files,
        max_age_days=args.max_age_days,
        dry_run=args.dry_run,
    )
    print(f"\n📊 Resumen: {result['total']} total → {result['remaining']} restantes "
          f"({result['freed_bytes'] // (1024*1024)} MB liberados)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
