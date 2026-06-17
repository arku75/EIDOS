#!/usr/bin/env bash
set -euo pipefail

# Script: prepare_sanitized_repo.sh
# Genera una copia sanitizada del repo en release_tmp/ y empaqueta un tarball

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTDIR="$ROOT/release_tmp"
TARBALL="$ROOT/EIDOS_sanitized_RELEASE.tar.gz"

echo "Creando copia sanitizada en: $OUTDIR"
rm -rf "$OUTDIR"
mkdir -p "$OUTDIR"

# rsync con exclusiones
rsync -av --exclude='.eidos' \
  --exclude='*.db' \
  --exclude='*.sqlite*' \
  --exclude='__pycache__' \
  --exclude='.venv' \
  --exclude='.venv*' \
  --exclude='.env' \
  --exclude='*.pem' \
  --exclude='*.key' \
  --exclude='node_modules' \
  --exclude='vendor' \
  --exclude='external_repos' \
  --exclude='Backups' \
  --exclude='OpenClaw_Config' \
  --exclude='OpenClaw_Data' \
  --exclude='OpenClaw_Workspace' \
  --exclude='openclaw' \
  --exclude='openclaw_master_skills' \
  --exclude='core/graphify-out' \
  --exclude='*.tar.gz' \
  --exclude='*.zip' \
  --exclude='*.bak' \
  --exclude='*.json.bak*' \
  --exclude='release_tmp' \
  --exclude='.git' \
  "$ROOT/" "$OUTDIR/"

echo "Borrando ficheros sensibles conocidos (si existen)"
find "$OUTDIR" -type f \( -name 'api_keys.json' -o -name '*.env' -o -name '.credentials' \) -print -delete || true

echo "Buscar patrones comunes de secretos (no modificar automáticamente). Lista de candidatos en release_tmp/secret_candidates.txt"
grep -R --line-number -E "ghp_[A-Za-z0-9_]+|gho_[A-Za-z0-9_]+|GITHUB_TOKEN|API_KEY|apikey|password|passwd|secret|BEGIN RSA PRIVATE KEY|PRIVATE KEY" "$OUTDIR" > "$OUTDIR/secret_candidates.txt" 2>/dev/null || true

echo "Creando tarball: $TARBALL"
rm -f "$TARBALL"
tar -czf "$TARBALL" -C "$OUTDIR" .

echo "Hecho. Revisa $OUTDIR y $TARBALL antes de publicar. Revisa $OUTDIR/secret_candidates.txt para posibles secretos." 
