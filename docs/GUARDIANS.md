# Guardians — The 6 autonomous systems that keep EIDOS alive

> Guardians are **not** optional daemons. They are the autonomic nervous system
> of EIDOS — each one watches a different dimension and acts without human
> intervention. If a service crashes, a Guardian restarts it. If RAM runs out,
> a Guardian frees it. If a change breaks the system, a Guardian rolls it back.

---

## 1. RAM Guardian (`core/ram_guardian.py`)

**Protects against OOM (Out of Memory).**

- Monitors RAM every **30 seconds**.
- Kills **non-critical** processes before the Linux OOM killer activates.
- Prioritizes: children processes first, then cache, then idle workers.
- Logs every action with the freed amount and the process name.
- **Never kills**: the Bridge, Colony, brain-lite, ChromaDB, or systemd.

```bash
# Status
systemctl --user status eidos-ram-guardian 2>/dev/null
```

---

## 2. Git Guardian (`core/git_guardian.py`)

**Atomic version control for EIDOS's code.**

- Watches file changes in `core/`, `bin/`, `web-panel/`.
- On change: **commit → test → merge or rollback**.
- **NEVER pushes automatically** (constitution rule).
- Stores snapshots in `~/.eidos/git-backup/EIDOS.git` (local).
- Runs a daily snapshot timer (`eidos-git-snapshot.timer` at 04:30).

> If a change breaks the smoke tests, the Git Guardian rolls it back
> atomically. This is how EIDOS can self-modify without self-destructing.

---

## 3. Sentinel — Anomaly Detection

**Detects suspicious patterns in system behavior.**

- Monitors: CPU spikes, disk I/O storms, unusual network activity, process
  count anomalies.
- Flags patterns that match known failure modes (load cascades, DB connection
  storms, memory leaks).
- Reports to SER's inbox and to the Healer for action.
- Integrated with `eidos_supervisor.py` for cross-reference.

---

## 4. Phoenix (`core/phoenix.py`)

**Auto-resurrection system.**

- Guardian #1 in the hierarchy.
- Detects crashed services and **restarts them**.
- On boot: verifies all 12 services are running; starts any that aren't.
- If a service crashes repeatedly (3+ times in 5 minutes), Phoenix escalates
  to SER (inbox notification) instead of infinite-restart-looping.

---

## 5. Mirror (`core/mirror.py`)

**Independent validator for EIDOS's self-improvements.**

- Guardian #2 in the hierarchy.
- When EIDOS proposes a code change to itself, the Mirror **independently
  validates** it before the Git Guardian commits.
- Checks: does the change respect the constitution? Does it pass syntax and
  smoke? Is it within operational limits (3 files, 50 lines per edit)?
- If validation fails, the change is discarded and logged.

> Mirror is the reason EIDOS can self-improve safely: every change is
> peer-reviewed by an independent guardian before it touches the codebase.

---

## 6. Watchdog (`core/watchdog.py`)

**Service supervision — the pulse check.**

- Runs every **60 seconds**.
- Checks: Colony, Trinity, web-panel, Bridge, Telegram bot.
- If a service is down: attempts restart, logs the event, notifies SER.
- Writes health status to the brain DB so Colony knows its own state.
- Service: `eidos-watchdog.service`.

---

## 7. Healer (`core/eidos_healer.py`)

**System Health Monitor & Auto-Repair Daemon.**

- Checks **every 60s**: HTTP endpoints (:8003, :8080, :8001), systemd services,
  disk usage, RAM, CPU load.
- If an endpoint is down → restarts the responsible service.
- If disk is >90% → triggers log rotation and cache cleanup.
- If load is > nº CPU cores → throttles non-critical loops.
- Logs all health events to `~/.eidos/healing.db`.
- Service: `eidos-healer.service`.

```
┌──────────────────────────────────────────────────────┐
│                   THE 6 GUARDIANS                     │
├──────────┬───────────────────────────────────────────┤
│ RAM      │ OOM prevention, process triage             │
│ Git      │ Atomic commits, rollback, local snapshots  │
│ Sentinel │ Anomaly detection, pattern matching        │
│ Phoenix  │ Service resurrection, crash recovery       │
│ Mirror   │ Code change validation (constitution check)│
│ Watchdog │ Pulse check every 60s, restart + notify    │
│ Healer   │ Full-system health, endpoint/disk/load     │
└──────────┴───────────────────────────────────────────┘
```

---

## 8. How to check them

```bash
# Are the guardian services running?
systemctl --user list-units | grep -E 'guardian|watchdog|healer|phoenix'

# Healer logs
tail -f ~/.eidos/healer.log

# Git Guardian snapshots
ls ~/.eidos/git-backup/

# Watchdog health status (in the brain DB)
python3 -c "
from core.db import get_conn
from pathlib import Path
c = get_conn(str(Path.home()/'.eidos'/'evolution_brain.db'))
for r in c.execute('SELECT * FROM service_health ORDER BY checked_at DESC LIMIT 5'):
    print(r)
"
```

---

## Security note

Guardians **cannot** escalate EIDOS's freedom level or modify the constitution.
They operate within the same operational limits as the rest of EIDOS: 3 file
modifications per cycle, 50 lines per edit, 5 new files per hour. The
constitution is hash-verified; any Guardian that attempts to modify it is
blocked by the Mirror Guardian's validation.
