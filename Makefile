# ══════════════════════════════════════════════════════════════════════════════
#  EIDOS Makefile — Instalación Nativa en Kali Linux
#  Uso: make good    → instala todo, listo para usar
#       make install → instala EIDOS sin kali-linux-everything
#       make kali    → instala kali-linux-everything (6660 tools)
#       make skills  → auto-descubre tools instaladas y genera skills
#       make pack    → empaqueta EIDOS en eidos_portable.tar.gz
#       make unpack  → instala desde eidos_portable.tar.gz
#       make clean   → limpia archivos temporales y cache
#       make status  → muestra estado de la instalación
#       make update  → actualiza EIDOS a la última versión
# ══════════════════════════════════════════════════════════════════════════════

EIDOS_DIR    := /home/$(shell whoami)/EIDOS/My_Gpt
INSTALL_DIR  := /opt/eidos
BIN_DIR      := /usr/local/bin
SERVICE_DIR  := /etc/systemd/system
PACK_OUT     := eidos_portable.tar.gz
PYTHON       := python3
VENV         := $(EIDOS_DIR)/venv_eidos
PIP          := $(VENV)/bin/pip
EIDOS_PY     := $(VENV)/bin/python

.PHONY: all good install kali skills pack unpack clean status update help

# ─────────────────────────────────────────────────────────────────────────────
#  GOOD — El target principal: todo en un comando
#  make good → apt update + kali-everything + EIDOS instalado + skills listos
# ─────────────────────────────────────────────────────────────────────────────
good: _banner _check_root _apt_update kali install skills _register _done
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════════╗"
	@echo "║  ✅  EIDOS instalado y listo. Escribe:  eidos               ║"
	@echo "╚══════════════════════════════════════════════════════════════╝"

# ─────────────────────────────────────────────────────────────────────────────
#  INSTALL — Instala EIDOS (sin kali-everything)
# ─────────────────────────────────────────────────────────────────────────────
install: _banner _check_python _venv _pip_deps _ollama _create_bin _service
	@echo "✅  EIDOS instalado en $(BIN_DIR)/eidos"

# ─────────────────────────────────────────────────────────────────────────────
#  KALI — Instala kali-linux-everything (6660 herramientas)
# ─────────────────────────────────────────────────────────────────────────────
kali:
	@echo "⚔️  Instalando kali-linux-everything (~6660 herramientas)..."
	@echo "  ⚠️  Esto puede tardar 30-90 minutos dependiendo de la conexión."
	sudo apt update -qq
	sudo apt full-upgrade -y
	sudo DEBIAN_FRONTEND=noninteractive apt install -y \
		kali-linux-everything \
		|| sudo DEBIAN_FRONTEND=noninteractive apt install -y kali-linux-large
	@echo "✅  Kali Linux Everything instalado."

# ─────────────────────────────────────────────────────────────────────────────
#  SKILLS — Auto-descubre herramientas instaladas y genera skill stubs
# ─────────────────────────────────────────────────────────────────────────────
skills:
	@echo "🔍  Descubriendo herramientas del sistema..."
	$(EIDOS_PY) $(EIDOS_DIR)/kali_skills_auto.py --discover --generate
	@echo "✅  Skills actualizados."

# ─────────────────────────────────────────────────────────────────────────────
#  PACK — Empaquetar EIDOS en un archivo portable
# ─────────────────────────────────────────────────────────────────────────────
pack:
	@echo "📦  Empaquetando EIDOS en $(PACK_OUT)..."
	tar --exclude='$(EIDOS_DIR)/venv_eidos' \
		--exclude='$(EIDOS_DIR)/__pycache__' \
		--exclude='$(EIDOS_DIR)/.git' \
		--exclude='$(EIDOS_DIR)/*.pyc' \
		--exclude='$(EIDOS_DIR)/openclaw_master_skills' \
		-czf $(PACK_OUT) \
		-C $(shell dirname $(EIDOS_DIR)) \
		$(shell basename $(EIDOS_DIR)) \
		Makefile
	@ls -lh $(PACK_OUT)
	@echo "✅  Listo: $(PACK_OUT)"
	@echo "  Para instalar en otro PC: scp $(PACK_OUT) user@destino:~/ && ssh user@destino 'tar xzf $(PACK_OUT) && cd My_Gpt && make good'"

# ─────────────────────────────────────────────────────────────────────────────
#  UNPACK — Instalar desde archivo portable
# ─────────────────────────────────────────────────────────────────────────────
unpack:
	@if [ ! -f "$(PACK_OUT)" ]; then echo "❌  $(PACK_OUT) no encontrado"; exit 1; fi
	@echo "📦  Descomprimiendo $(PACK_OUT)..."
	tar xzf $(PACK_OUT)
	@echo "✅  Descomprimido. Ejecuta: make good"

# ─────────────────────────────────────────────────────────────────────────────
#  STATUS — Muestra el estado de la instalación
# ─────────────────────────────────────────────────────────────────────────────
status:
	@echo "══════════════════════════════════════════"
	@echo "  ESTADO DE EIDOS"
	@echo "══════════════════════════════════════════"
	@echo -n "  Python venv:      " && [ -f "$(EIDOS_PY)" ] && echo "✅" || echo "❌"
	@echo -n "  eidos binario:    " && [ -f "$(BIN_DIR)/eidos" ] && echo "✅" || echo "❌"
	@echo -n "  Ollama service:   " && systemctl is-active ollama 2>/dev/null | grep -q active && echo "✅" || echo "⚠️  (no como servicio)"
	@echo -n "  kali-everything:  " && dpkg -l kali-linux-everything 2>/dev/null | grep -q '^ii' && echo "✅" || echo "❌"
	@echo -n "  Skills en DB:     " && $(EIDOS_PY) -c "from kali_skills_auto import count_skills; print(f'✅ {count_skills()}')" 2>/dev/null || echo "❌"
	@echo -n "  hermes3 model:    " && curl -s http://localhost:11434/api/tags 2>/dev/null | grep -q hermes3 && echo "✅" || echo "❌"
	@echo -n "  GUI Observer:     " && $(EIDOS_PY) -c "from core.gui_observer import get_observer; print('✅')" 2>/dev/null || echo "❌"
	@echo "══════════════════════════════════════════"

# ─────────────────────────────────────────────────────────────────────────────
#  UPDATE — Actualiza EIDOS
# ─────────────────────────────────────────────────────────────────────────────
update:
	@echo "🔄  Actualizando EIDOS..."
	cd $(EIDOS_DIR) && git pull 2>/dev/null || echo "  (no git repo, actualización manual)"
	$(PIP) install -q -r $(EIDOS_DIR)/requirements.txt --upgrade
	$(MAKE) skills
	@echo "✅  EIDOS actualizado."

# ─────────────────────────────────────────────────────────────────────────────
#  CLEAN — Limpiar archivos temporales
# ─────────────────────────────────────────────────────────────────────────────
clean:
	find $(EIDOS_DIR) -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; true
	find $(EIDOS_DIR) -name '*.pyc' -delete 2>/dev/null; true
	find $(EIDOS_DIR) -name '*.pyo' -delete 2>/dev/null; true
	rm -f /tmp/eidos_*.png ~/.eidos/screenshots/*.png 2>/dev/null; true
	@echo "✅  Limpiado."

# ─────────────────────────────────────────────────────────────────────────────
#  HELP
# ─────────────────────────────────────────────────────────────────────────────
help:
	@echo "╔════════════════════════════════════════════════════════════╗"
	@echo "║  EIDOS — Comandos Make                                     ║"
	@echo "╠════════════════════════════════════════════════════════════╣"
	@echo "║  make good     → TODO: kali+install+skills (recomendado)  ║"
	@echo "║  make install  → Instala EIDOS (sin kali-everything)      ║"
	@echo "║  make kali     → Instala kali-linux-everything (6660)     ║"
	@echo "║  make skills   → Auto-descubre tools y genera skills      ║"
	@echo "║  make pack     → Crea eidos_portable.tar.gz               ║"
	@echo "║  make unpack   → Instala desde eidos_portable.tar.gz      ║"
	@echo "║  make status   → Estado de la instalación                 ║"
	@echo "║  make update   → Actualiza EIDOS                          ║"
	@echo "║  make clean    → Limpia temporales                        ║"
	@echo "╚════════════════════════════════════════════════════════════╝"

# ─────────────────────────────────────────────────────────────────────────────
#  Targets internos (prefijo _)
# ─────────────────────────────────────────────────────────────────────────────
_banner:
	@echo ""
	@echo "  ███████╗██╗██████╗  ██████╗ ███████╗"
	@echo "  ██╔════╝██║██╔══██╗██╔═══██╗██╔════╝"
	@echo "  █████╗  ██║██║  ██║██║   ██║███████╗"
	@echo "  ██╔══╝  ██║██║  ██║██║   ██║╚════██║"
	@echo "  ███████╗██║██████╔╝╚██████╔╝███████║"
	@echo "  Kali Linux Native Installer v2.0      "
	@echo ""

_check_root:
	@if [ "$$(id -u)" != "0" ] && ! sudo -n true 2>/dev/null; then \
		echo "⚠️  Se necesita sudo para instalar paquetes del sistema."; \
	fi

_check_python:
	@which python3 > /dev/null || (echo "❌  Python3 no encontrado. Instala: sudo apt install python3"; exit 1)
	@python3 -c "import sys; assert sys.version_info >= (3,10)" || (echo "❌  Python 3.10+ requerido"; exit 1)
	@echo "✅  Python3 OK"

_apt_update:
	sudo apt update -qq && sudo apt install -y \
		python3 python3-pip python3-venv python3-dev \
		xdotool xclip scrot tmux curl wget git \
		at-spi2-core python3-pyatspi libatk-bridge2.0 \
		build-essential libssl-dev libffi-dev \
		tesseract-ocr tesseract-ocr-spa tesseract-ocr-eng \
		wmctrl x11-utils 2>/dev/null || true
	@echo "✅  Dependencias del sistema instaladas."

_venv:
	@echo "🐍  Creando entorno virtual..."
	@[ -d "$(VENV)" ] || python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip wheel setuptools
	@echo "✅  Venv listo."

_pip_deps:
	@echo "📦  Instalando dependencias Python..."
	$(PIP) install -q \
		ollama httpx rich prompt_toolkit \
		mss Pillow playwright \
		requests aiofiles aiohttp \
		python-dotenv cryptography \
		pytesseract opencv-python \
		pyperclip pyautogui \
		edge-tts pygame \
		pydantic fastapi uvicorn \
		faiss-cpu sentence-transformers \
		anthropic openai \
		schedule watchdog \
		psutil 2>/dev/null || true
	$(EIDOS_PY) -m playwright install chromium 2>/dev/null || true
	@echo "✅  Dependencias Python instaladas."

_ollama:
	@echo "🦙  Verificando Ollama..."
	@if ! command -v ollama >/dev/null 2>&1; then \
		echo "  Instalando Ollama..."; \
		curl -fsSL https://ollama.ai/install.sh | sh; \
	else \
		echo "  Ollama ya instalado."; \
	fi
	@systemctl start ollama 2>/dev/null || ollama serve &>/dev/null &; sleep 2
	@ollama pull hermes3 2>/dev/null || true
	@ollama pull moondream2 2>/dev/null || true
	@ollama pull nomic-embed-text 2>/dev/null || true
	@echo "✅  Ollama + modelos listos."

_create_bin:
	@echo "🔗  Creando comandos globales..."
	@sudo tee $(BIN_DIR)/eidos > /dev/null << 'EOF'
#!/bin/bash
# EIDOS — Lanzador global
EIDOS_DIR="$(EIDOS_DIR)"
source "$$EIDOS_DIR/venv_eidos/bin/activate"
exec python3 "$$EIDOS_DIR/eidos_cli.py" "$$@"
EOF
	@sudo chmod +x $(BIN_DIR)/eidos
	@echo "  → 'eidos' disponible globalmente"

	@sudo tee $(BIN_DIR)/eidos-status > /dev/null << 'EOF'
#!/bin/bash
cd $(EIDOS_DIR) && make status
EOF
	@sudo chmod +x $(BIN_DIR)/eidos-status

	@sudo tee $(BIN_DIR)/eidos-skills > /dev/null << 'EOF'
#!/bin/bash
cd $(EIDOS_DIR) && make skills
EOF
	@sudo chmod +x $(BIN_DIR)/eidos-skills
	@echo "✅  Comandos globales: eidos, eidos-status, eidos-skills"

_service:
	@echo "⚙️  Configurando servicio EIDOS GUI Observer..."
	@sudo tee $(SERVICE_DIR)/eidos-observer.service > /dev/null << EOF
[Unit]
Description=EIDOS GUI Observer Daemon
After=network.target graphical.target

[Service]
Type=simple
User=$(shell whoami)
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/$(shell whoami)/.Xauthority
WorkingDirectory=$(EIDOS_DIR)
ExecStart=$(EIDOS_PY) $(EIDOS_DIR)/core/gui_observer.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical.target
EOF
	@sudo systemctl daemon-reload 2>/dev/null || true
	@echo "✅  Servicio eidos-observer configurado (actívalo con: sudo systemctl enable --now eidos-observer)"

_register:
	@echo "📝  Registrando EIDOS en el sistema..."
	@mkdir -p ~/.eidos/screenshots ~/.eidos/skills ~/.eidos/memory
	@echo "EIDOS_DIR=$(EIDOS_DIR)" > ~/.eidos/config
	@echo "INSTALL_DATE=$(shell date -Iseconds)" >> ~/.eidos/config
	@grep -q 'alias eidos=' ~/.bashrc 2>/dev/null || echo 'alias eidos="$(BIN_DIR)/eidos"' >> ~/.bashrc
	@grep -q 'alias eidos=' ~/.zshrc 2>/dev/null || echo 'alias eidos="$(BIN_DIR)/eidos"' >> ~/.zshrc
	@echo "✅  EIDOS registrado."

_done:
	@echo ""
	@echo "  Versión instalada: 2.0-kali"
	@echo "  Directorio:        $(EIDOS_DIR)"
	@echo "  Binario:           $(BIN_DIR)/eidos"
	@echo "  Lanzar:            eidos"
	@echo "  Estado:            make status"
	@echo ""
