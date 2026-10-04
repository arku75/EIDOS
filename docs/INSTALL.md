# Installing EIDOS

> EIDOS is experimental source-available software under [ESSL v1.0](../LICENSE).
>
> The public repository is being made reproducible on clean Linux. Do not assume that
> SER's private databases, services, browser profile or machine configuration are part
> of a fresh clone.

## 1. Supported development target

Current public validation uses **Ubuntu Linux + Python 3.11** in GitHub Actions.

The main development machine is Linux/Kali, but hardware/desktop behavior is tested separately from clean CI.

## 2. Clone

```bash
git clone https://github.com/arku75/EIDOS.git
cd EIDOS
```

## 3. Create an isolated Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

## 4. Dependency resolution

Before installing the full environment, verify that the current branch resolves:

```bash
python -m pip install --dry-run --ignore-installed -r requirements.txt
```

The CI runs this on a clean Ubuntu runner.

If that check fails, treat it as a packaging bug. Do **not** work around it by installing arbitrary system packages into the global Python environment.

## 5. Install

The continuous-learning daemon installer is intentionally isolated from the system
Python. It creates a project virtual environment and does not automatically install
or start a systemd unit. The tracked unit is a portable template: create a host-local
copy and replace `EIDOS_ROOT` / `EIDOS_VENV` before installing it.


For the dependency-light public gateway/CLI package, use:

```bash
python -m pip install .
eidos-gateway --help
eidos-config --help
```

CI proves those installed entrypoints from outside the source checkout.

The broader research/runtime environment is a separate layer:

```bash
python -m pip install -r requirements.txt
```

CI currently proves dependency resolution for that broader file; individual
hardware/model/desktop capabilities still require their own runtime tests.
Some capabilities also depend on system packages or external runtimes. Install
only the layer needed for the capability you intend to test.

Common desktop/vision development packages include:

```bash
sudo apt update
sudo apt install -y python3-venv git curl xdotool wmctrl scrot tesseract-ocr
```

On Wayland, installing `xdotool` does not make X11 injection equivalent to native Wayland input.

## 6. Secrets

Never put real credentials in the repository.

```bash
mkdir -p ~/.eidos
cp .env.example ~/.eidos/secrets.env
chmod 600 ~/.eidos/secrets.env
```

Fill only the providers/integrations you actually use.

A credential that was ever committed to Git history must be considered exposed and rotated.

## 7. Validate the public architecture first

These tests do not require a live EIDOS installation:

```bash
PYTHONPATH=. python -m unittest -v \
  tests.test_fly_lab \
  tests.test_runtime_hub \
  tests.test_autonomy_benchmark \
  tests.test_self_edit_lab
```

Run the standalone experiments:

```bash
PYTHONPATH=. python core/fly_lab.py
PYTHONPATH=. python core/autonomy_benchmark.py
PYTHONPATH=. python bin/eidos_agents_terminal.py
```

## 8. Full runtime

The full private/live runtime may require:

- runtime databases under `~/.eidos`;
- configured model backends;
- service definitions;
- optional vector store;
- desktop/browser permissions;
- machine-specific devices.

Do not present a clean clone as equivalent to the live private EIDOS instance until a hardware-in-the-loop installation test proves it.

## 9. ChromaDB warning

The first-party Chroma topology now targets port 8767 and one canonical server implementation, but runtime/API compatibility is still open under GitHub issue #8. See [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

Do not use a live `~/.eidos/chroma` directory as an integration-test fixture.

## 10. Desktop validation

Clean CI tests only controlled capabilities such as virtual X11 and local headless-browser behavior.

Real desktop validation must test the actual target session:

```bash
echo "$XDG_SESSION_TYPE"
```

For KDE/Wayland, verify the chosen input backend by observed before/after effects. Do not infer success from an API return value alone.

## 11. Safe development layout on the real machine

Recommended:

```text
/home/ser/EIDOS
└── live/private installation

/home/ser/EIDOS-SANEADO
└── isolated worktree for the sanitation branch
```

Do not switch the live worktree to an experimental branch while its services are running.

## 12. Next documents

- [USAGE.md](USAGE.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [AUTONOMY_LAB.md](AUTONOMY_LAB.md)
- [KNOWN_ISSUES.md](KNOWN_ISSUES.md)
- [SECURITY.md](../SECURITY.md)
