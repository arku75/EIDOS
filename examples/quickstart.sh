#!/usr/bin/env bash
# Minimal "try the Bridge" quickstart. Assumes EIDOS is installed and running
# (see docs/INSTALL.md) and EIDOS_BRIDGE_KEY is set in ~/.eidos/secrets.env.
set -euo pipefail
: "${EIDOS_BRIDGE_KEY:?set EIDOS_BRIDGE_KEY (see docs/INSTALL.md)}"
BR=http://127.0.0.1:8003
echo "1) Health (no key needed):"; curl -s $BR/health; echo
echo "2) Ask EIDOS to research something (it will LEARN it):"
curl -s -X POST $BR/investiga -H "X-API-Key: $EIDOS_BRIDGE_KEY" \
  -H 'Content-Type: application/json' -d '{"topic":"what is nmap"}'; echo
echo "3) Ask its memory (should now know it):"
curl -s -X POST $BR/semantic -H "X-API-Key: $EIDOS_BRIDGE_KEY" \
  -H 'Content-Type: application/json' -d '{"query":"nmap"}'; echo
