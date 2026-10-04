#!/usr/bin/env bash
set -euo pipefail

# EIDOS hardware validation helper.
# Read-only by default. Use --active only for explicitly permitted active checks.

MODE="dry"
if [[ "${1:-}" == "--active" ]]; then
  MODE="active"
fi

say() { printf '%s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

say "EIDOS hardware validation"
say "mode=$MODE"
say "timestamp=$(date -Is 2>/dev/null || date)"
say "host=$(hostname 2>/dev/null || true)"
say "user=$(id -un 2>/dev/null || true)"
say "pwd=$(pwd)"
say "session_type=${XDG_SESSION_TYPE:-unknown}"
say "display=${DISPLAY:-unset}"
say "wayland_display=${WAYLAND_DISPLAY:-unset}"

if have python3; then
  say "python=$(python3 --version 2>&1)"
fi

if have nvidia-smi; then
  say "gpu:"
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader || true
else
  say "gpu=nvidia-smi unavailable"
fi

if have free; then
  say "memory:"
  free -h || true
fi

if have df; then
  say "disk:"
  df -h / /home 2>/dev/null || true
fi

for cmd in ollama llama-cli llama-server xdotool wmctrl scrot tesseract chromium google-chrome playwright; do
  if have "$cmd"; then
    say "tool:$cmd=$(command -v "$cmd")"
  else
    say "tool:$cmd=missing"
  fi
done

say "paths:"
for path in   "/home/ser/EIDOS"   "/home/ser/EIDOS-SANEADO"   "$HOME/.eidos"; do
  if [[ -e "$path" ]]; then
    say "  present $path"
  else
    say "  missing $path"
  fi
done

say "ports:"
if have ss; then
  for port in 8001 8003 8004 8080 8766 8767 11434; do
    if ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$port$"; then
      say "  listening $port"
    else
      say "  closed $port"
    fi
  done
else
  say "  ss unavailable"
fi

say "services:"
if have systemctl; then
  systemctl --user --no-pager --plain --type=service --all 2>/dev/null | grep -i eidos || true
else
  say "  systemctl unavailable"
fi

say "databases:"
if [[ -d "$HOME/.eidos" ]]; then
  find "$HOME/.eidos" -maxdepth 2 -type f \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) -printf '  %p %s bytes\n' 2>/dev/null | sort || true
fi

if [[ "$MODE" != "active" ]]; then
  say "active_checks=skipped"
  say "Use --active only when SER is present and explicitly wants non-destructive active checks."
  exit 0
fi

say "active_checks=enabled"

# Active checks are intentionally non-destructive.
if have ollama; then
  say "ollama_models:"
  ollama list 2>/dev/null || true
fi

if have curl; then
  for url in     "http://127.0.0.1:11434/api/tags"     "http://127.0.0.1:8003/health"; do
    code="$(curl -sS -o /tmp/eidos-hw-check.$$ -w '%{http_code}' --max-time 3 "$url" 2>/dev/null || true)"
    say "http:$url=$code"
    rm -f /tmp/eidos-hw-check.$$
  done
fi

say "No mouse, keyboard, browser-profile, DB write, service restart, model download or network bind was performed."
