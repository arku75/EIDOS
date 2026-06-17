# Clones — How EIDOS replicates to other machines

> A **clone** is a copy of EIDOS running on another PC. It has its own
> cryptographic identity (Ed25519 keypair), registers against the **Hub** on
> SER's Kali, and communicates through a **Portal** session loop with strict
> allow-lists. The clone's private key **never** leaves its machine.

---

## 1. The three pieces

| Piece | Where | What it does |
|:------|:------|:-------------|
| **Hub** (`core/eidos_hub.py`) | SER's Kali (`~/.eidos/hub/`) | Registry of clones, enrollment tokens, key management |
| **Clone Agent** (`core/eidos_clone_agent.py`) | The clone PC (`~/.eidos/clone/`) | Generates keypair, builds signed enrollment requests |
| **Portal** (`core/eidos_portal.py`) | SER's Kali | Session loop that sends commands to a clone and verifies responses |

## 2. How a clone is born (step by step)

```
SER's Kali (Hub)                          Clone PC
───────────────                          ─────────
1. python3 core/eidos_hub.py init
   → generates hub keypair
   (~/.eidos/hub/hub_ed25519.*)

2. python3 core/eidos_hub.py issue-token
   → prints single-use token
   (valid 15 minutes)

                                         3. SER pastes the token into the
                                            clone installer / setup script

                                         4. python3 core/eidos_clone_agent.py init
                                            → clone generates ITS OWN keypair
                                            (~/.eidos/clone/clone_ed25519.*)

                                         5. clone builds signed registration:
                                            payload = {pub_key, friendly_name,
                                                       os_info, token}
                                            signed with clone's private key

                                         6. clone sends payload → Hub

7. Hub verifies:
   - token is valid (not expired, single-use)
   - signature matches clone's public key
   → enrolls clone in ~/.eidos/hub/registry.db
   → returns {clone_id, hub_pub_key} signed

                                         8. clone saves identity.json
                                            {clone_id, hub_pub_key, metadata}
```

After enrollment, **every message** in either direction is signed: the clone
signs with its private key; the Hub signs with its private key. Each side
verifies the other's signature using the stored public key.

### CLI for the Hub

```bash
python3 core/eidos_hub.py init           # first time: generate hub keypair
python3 core/eidos_hub.py status         # show hub status
python3 core/eidos_hub.py issue-token    # create single-use enrollment token
python3 core/eidos_hub.py list-clones    # all registered clones
python3 core/eidos_hub.py revoke <id>    # revoke a clone
```

---

## 3. The Portal — talking to a clone

Once enrolled, SER communicates with a clone through the **Portal**
(`core/eidos_portal.py`):

```bash
python3 -m core.eidos_portal <clone_id_or_friendly_name>
```

This starts an interactive **session loop**: you type commands, the Portal
builds a signed request, sends it to the clone, the clone executes it, signs
the response, and the Portal verifies the signature.

### What a clone CAN do (allowlist)

| Command | What it does |
|:--------|:-------------|
| `list_dir <path>` | List files in a directory |
| `system_info` | OS, hostname, Python version, CPU/RAM |
| `ping` | Latency check |
| `get_file <path>` (read-only) | Read a file from the clone |

### What a clone CANNOT do

| Blocked | Reason |
|:--------|:-------|
| `run_shell` | **Constitution prohibition** — no remote shell |
| `write_file` | No remote file write |
| `get_cookies` | No reading browser cookies |
| `get_file` with write | Read-only only |

> The Portal **never expands the SHELL_ALLOWLIST**. The constitution forbids it.
> A clone cannot escalate its own permissions — even if compromised, the Hub
> controls what the clone can do and verifies every response signature.

---

## 4. Installing a clone on Windows

`installers/install_clone_windows.ps1`:
- Creates `~/.eidos-clone/` on the Windows machine
- Sets up Python venv + core modules
- Runs `clone_runner.py init` to generate the keypair
- You paste the enrollment token from the Hub
- The clone registers against the Hub

For Linux/Mac clones, the same flow applies (the client code is portable).

---

## 5. Security properties

- **No private key travels.** Clone private key never leaves its machine. Hub
  private key never leaves SER's Kali.
- **Single-use enrollment tokens.** Valid 15 minutes. Used once.
- **Every message signed.** Both directions. Ed25519.
- **Strict command allowlist.** No shell, no file write, no cookie theft.
- **No auto-reconnect.** A clone never initiates a connection on its own (Fase
  2 governs this). The Hub decides when to talk.

---

## 6. Quick test (same machine)

When both Hub and clone are on the same machine (development/testing):

```python
from core.eidos_clone_agent import init_clone, build_request
from core.eidos_hub import get_hub

hub = get_hub()
hub.init()                        # generate hub keypair
token = hub.issue_token()         # single-use token

init_clone()                      # generate clone keypair
req, sig = build_request(
    clone_id="test",
    kind="ping",
    args={},
    hub_pub_key=hub.pub_key,
    enrollment_token=token,
)
# ... register and verify
```
