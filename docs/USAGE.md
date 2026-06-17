# Using EIDOS

Two ways to interact: the **CLI** (`eidos …`) and the **Bridge HTTP API** (port
8003). For lending EIDOS's powers to another AI, see [BRIDGE.md](BRIDGE.md).

## CLI — Full Command Reference

EIDOS exposes 22 commands via `eidos <command> [options]`. Run `eidos --help` for
the full built-in help.

### Lifecycle

```bash
eidos start                          # launch all 12 systemd services
eidos stop                           # stop all services cleanly
eidos status                         # full system status (services, graph, Colony, RAM)
eidos awaken                         # wake EIDOS from sleep / cold start
```

### Interaction

```bash
eidos chat                           # interactive Colony chat (default)
eidos god                            # God Mode CLI (full control, interactive)
eidos talk "What is Kubernetes?"     # one-shot query via Bridge API
eidos ask "Explain Docker layers"    # query with Colony character discussion
```

### Learning & Teaching

```bash
eidos study add "Learn systemd"      # add topic to autonomous study queue
eidos study report                   # view the study report (~/.eidos/study_report.md)
eidos teach --qid <id> --text "..." # teach EIDOS (master-student, conf 0.95)
python3 -m core.master_protocol pending  # list questions EIDOS wants answered
```

### Autonomous modes

```bash
EIDOS_MASTER_MODE=1 eidos libre      # free autonomous mode (works study queue)
eidos pipeline [--dry-run]           # run the multi-step pipeline
eidos improve                        # run smoke test (read-only diagnostics)
```

### Web & Browser

```bash
eidos browse                         # open Chromium with SER's session and navigate
eidos set-browser-default            # choose default browser (firefox/chromium)
eidos estudio-app                    # launch the dedicated study app
```

### Development & operations

```bash
eidos setup                          # system health checklist (OK/FAIL)
eidos test                           # quick module test
eidos sync                           # sync state between components
eidos bridge [--socket]              # unix socket bridge for Go dispatcher
eidos vscode                         # VSCode/IDE bridge
eidos gateway [--port]               # unified API gateway (port 8003)
eidos api [--port]                   # FastAPI server
```

### Advanced

```bash
eidos reproduce                      # replay a recorded session
eidos neuron                         # manage graph neurons
eidos wake-word                      # configure wake-word for voice
eidos vseidos-control                # control the VSEIDOS panel
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
