# Installing EIDOS

> EIDOS targets **Linux** (developed on Kali/Debian). It is an experimental,
> single‑developer research system — expect to get your hands dirty.

## 1. Requirements

| | Minimum | Recommended |
|:--|:--|:--|
| OS | Linux (Kali/Debian) | Linux (Kali/Debian) |
| Python | 3.8+ | 3.11+ |
| RAM | 8 GB | 16+ GB |
| CPU | 4 cores | 8+ cores |
| Disk | 50 GB | 200+ GB SSD |
| GPU | — | NVIDIA + CUDA (for VLM/ML; AMD iGPU does **not** accelerate) |

## 2. System packages

```bash
sudo apt update
sudo apt install -y xdotool wmctrl scrot tesseract-ocr espeak-ng python3-venv git curl
```

## 3. Clone

```bash
git clone https://github.com/arku75/EIDOS.git ~/EIDOS
cd ~/EIDOS
```

## 4. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# If requirements is heavy, the essentials are:
# pip install fastapi flask aiohttp requests playwright chromadb cryptography \
#             rapidocr-onnxruntime faster-whisper Pillow numpy openai
```

## 5. Secrets

```bash
mkdir -p ~/.eidos
cp .env.example ~/.eidos/secrets.env
chmod 600 ~/.eidos/secrets.env
nano ~/.eidos/secrets.env
```

Set at least one LLM provider key (`DEEPSEEK_API_KEY` and/or `GROQ_API_KEY`) and a
`EIDOS_BRIDGE_KEY` (any long random string). EIDOS can also run on **local Ollama
models** with no cloud key — see step 6.

> **Never** put keys anywhere else (not in code, not in `connection_data`, not in
> committed files). `~/.eidos/secrets.env` is the only home for secrets.

## 6. Ollama (optional, for offline/local brain)

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull lfm2.5-1.2b-instruct:q4_0     # fast fallback
ollama pull lfm2.5-thinking:1.2b          # reasoning
ollama pull nomic-embed-text              # embeddings (ChromaDB)
```

## 7. Start & verify

```bash
PYTHONPATH=~/EIDOS python3 eidos.py start     # or: systemctl --user start 'eidos-*'
curl http://127.0.0.1:8003/health             # → {"status":"ok",...}
systemctl --user list-units | grep -i eidos   # services running
```

Stop everything:

```bash
eidos stop                                    # or: systemctl --user stop 'eidos-*'
```

## 8. Troubleshooting

- **`database is locked`** → some module used `sqlite3.connect()` directly; always
  use `from core.db import get_conn`. See [KNOWN_ISSUES.md](KNOWN_ISSUES.md).
- **Bridge slow to start / high load** → the Bridge rebuilds the in‑memory graph on
  boot (~5 cores for a minute or two). Don't chain‑restart it. Check `loadavg`
  first.
- **ChromaDB errors** → use the bundled ChromaDB 1.4.4 CLI microservice (port
  8767); the Python client ≥1.5 has a thread‑safety regression.
- **No GPU acceleration** → expected on AMD iGPU; local LLM inference will be slow.
  Use cloud (DeepSeek/Groq) as primary, Ollama as fallback.

Next: [USAGE.md](USAGE.md) · [BRIDGE.md](BRIDGE.md) · [COLONY_CHARACTER.md](COLONY_CHARACTER.md)
