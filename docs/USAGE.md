# Using EIDOS

Two ways to interact: the **CLI** (`eidos …`) and the **Bridge HTTP API** (port
8003). For lending EIDOS's powers to another AI, see [BRIDGE.md](BRIDGE.md).

## CLI

```bash
# Lifecycle
eidos start                          # launch services
eidos stop                           # stop services
eidos status                         # full system status

# Talk
eidos talk "What is Kubernetes?"     # query via Bridge
eidos ask  "Explain Docker layers"   # query with Colony discussion

# Study queue (autonomous learning)
eidos study add "Learn about systemd"
eidos study list
eidos study report

# Free / autonomous mode (works the queue alone)
EIDOS_MASTER_MODE=1 eidos libre

# Teaching EIDOS (master–student protocol)
python3 -m core.master_protocol pending          # what EIDOS wants to know
python3 -m core.master_protocol answer <id> "…"  # teach it (stored at confidence 0.95)

# Verification
eidos smoke                          # integration tests
eidos health                         # health check
eidos graph-stats                    # neural graph statistics

# Native viewer (tkinter: vision + chat + research)
PYTHONPATH=~/EIDOS python3 bin/eidos-viewer
```

## Bridge API (port 8003)

All endpoints except `/health` require `X-API-Key: $EIDOS_BRIDGE_KEY`.

```bash
KEY="your-EIDOS_BRIDGE_KEY"

# General reasoning
curl -s -X POST http://127.0.0.1:8003/talk \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"text":"How are you?"}'

# Research (reads sources, synthesizes, LEARNS)
curl -s -X POST http://127.0.0.1:8003/investiga \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"topic":"Linux cgroups v2"}'

# Recall from memory (semantic / vector)
curl -s -X POST http://127.0.0.1:8003/semantic \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"query":"what do I know about nmap?"}'

# Browse a page
curl -s -X POST http://127.0.0.1:8003/browse \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"url":"https://nmap.org/book/man.html"}'

# Queue a study topic
curl -s -X POST http://127.0.0.1:8003/study \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"topic":"PostgreSQL replication"}'

# Graph stats
curl -s http://127.0.0.1:8003/graph/stats -H "X-API-Key: $KEY"
```

See the full endpoint table in [BRIDGE.md](BRIDGE.md#2-the-powers-you-are-lending).

## Search ("busca …")

EIDOS wires **61 search engines/sources** (DuckDuckGo, Google, Wikipedia, arXiv,
GitHub, ExploitDB, man pages, PyPI, npm, HuggingFace, Stack Overflow, …). Through
the chat/action path it opens the engine **and reads/synthesizes the results**
(not just opening the page). Examples it understands:

```
busca nmap en duckduckgo
busca quantum computing en arxiv
busca CVE-2024-1234 en exploitdb
busca transformers en huggingface
busca <topic> en internet        # defaults to DuckDuckGo
```

## Web dashboards (port 8080)

| URL | View |
|:--|:--|
| `http://127.0.0.1:8080/screen` | Main dashboard |
| `http://127.0.0.1:8080/neural_dashboard` | Interactive neural graph |
| `http://127.0.0.1:8080/monitor` | System monitor |

## Safety

- Physical mouse/screen control (the Body / BOM) is **off by default**. Enable with
  `EIDOS_BOM=1` and only with the human owner present.
- Stop everything cleanly with `eidos stop`.
- Known limitations and bugs: [KNOWN_ISSUES.md](KNOWN_ISSUES.md).
