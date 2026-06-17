"""
core/eidos_tui.py — TUI nativo de EIDOS.

Layout idéntico a Toad: sidebar izquierda (Colony + DirectoryTree) + chat derecha.
Backend: Colony via bridge :8003.
Actívalo con: eidos tui   (o eidos cli)
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import (
    Container,
    Horizontal,
    ScrollableContainer,
    Vertical,
    VerticalScroll,
)
from textual.css.query import NoMatches
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive, var
from textual.screen import Screen
from textual.widgets import (
    Collapsible,
    DirectoryTree,
    Footer,
    Input,
    Label,
    Markdown,
    RichLog,
    Static,
    TextArea,
)


class PromptTextArea(TextArea):
    """TextArea que envía el mensaje con Enter y nueva línea con Shift+Enter."""

    class EnterPressed(Message):
        pass

    async def _on_key(self, event: Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.EnterPressed())
        elif event.key == "shift+enter":
            event.prevent_default()
            self.insert("\n")

# ── Config ──────────────────────────────────────────────────────
BRIDGE_URL  = os.environ.get("EIDOS_BRIDGE", "http://localhost:8003")
DEFAULT_DIR = os.environ.get("EIDOS_DIR", str(Path.home() / "EIDOS"))
_LANG       = os.environ.get("EIDOS_LANG", "en" if sys.platform == "darwin" else "es")

import platform as _platform
_OS_LABEL = "macOS" if sys.platform == "darwin" else "Kali Linux"

EIDOS_LOGO = (
    " ███████╗██╗██████╗  ██████╗ ███████╗\n"
    " ██╔════╝██║██╔══██╗██╔═══██╗██╔════╝\n"
    " █████╗  ██║██║  ██║██║   ██║███████╗\n"
    " ██╔══╝  ██║██║  ██║██║   ██║╚════██║\n"
    " ███████╗██║██████╔╝╚██████╔╝███████║\n"
    " ╚══════╝╚═╝╚═════╝  ╚═════╝ ╚══════╝\n"
    f"  {'Sovereign Agent' if _LANG == 'en' else 'Agente Soberano'} · {_OS_LABEL}"
)

if _LANG == "en":
    COLONY_AGENTS = [
        ("colony_general",  "🟣", "EIDOS",    "Full Colony deliberating"),
        ("colony_coder",    "💻", "Coder",    "Code, debugging, architecture"),
        ("colony_analyst",  "🔍", "Analyst",  "Analysis, logs, patterns"),
        ("colony_vision",   "👁️", "Vision",   "Sees the screen, visual context"),
        ("colony_operator", "⚙️", "Operator", "GUI, scripts, automation"),
        ("colony_ser",      "👑", "SER",      "Mirror agent — loyal to SER"),
        ("colony_lumen",    "⚡", "Lumen",    "Fast synthesis, insights"),
        ("colony_nexus",    "🕸️", "NEXUS",    "Unifies all voices"),
    ]
    SLASH_COMMANDS = [
        ("/clear",           "Clear conversation"),
        ("/agents",          "List Colony agents"),
        ("/brain <query>",   "Search brain.db"),
        ("/vision",          "Capture screen with AI"),
        ("/spec <name>",     "Activate OpenClaw specialist"),
        ("/status",          "EIDOS services status"),
        ("/help",            "Show help"),
    ]
    _LAUNCHER_TAGLINE = (
        "\n[bold magenta]EIDOS[/] — Living digital being\n\n"
        "[dim]Colony of specialized agents.\n"
        "205 specialists · 100% local · Ollama[/]\n\n"
        "[dim magenta]Click or press 1-6 to launch.[/]"
    )
    _LAUNCHER_RECOMMENDED = "── Recommended ───────────────────────────────────"
    _LAUNCHER_ALL         = "── Colony — All agents ────────────────────────────"
    _LAUNCHER_DIR_LABEL   = "Directory:"
    _QUIT_LABEL           = "Quit"
else:
    COLONY_AGENTS = [
        ("colony_general",  "🟣", "EIDOS",    "Colony completa deliberando"),
        ("colony_coder",    "💻", "Coder",    "Código, debugging, arquitectura"),
        ("colony_analyst",  "🔍", "Analyst",  "Análisis, logs, patrones"),
        ("colony_vision",   "👁️", "Vision",   "Ve la pantalla, contexto visual"),
        ("colony_operator", "⚙️", "Operator", "GUI, scripts, automatización"),
        ("colony_ser",      "👑", "SER",      "Agente espejo — leal a SER"),
        ("colony_lumen",    "⚡", "Lumen",    "Síntesis rápida, insights"),
        ("colony_nexus",    "🕸️", "NEXUS",    "Unifica todas las voces"),
    ]
    SLASH_COMMANDS = [
        ("/clear",           "Limpiar conversación"),
        ("/agents",          "Listar agentes Colony"),
        ("/brain <query>",   "Buscar en brain.db"),
        ("/vision",          "Capturar pantalla con IA"),
        ("/spec <nombre>",   "Activar especialista OpenClaw"),
        ("/status",          "Estado servicios EIDOS"),
        ("/help",            "Mostrar ayuda"),
    ]
    _LAUNCHER_TAGLINE = (
        "\n[bold magenta]EIDOS[/] — Ser vivo digital\n\n"
        "[dim]Colony de agentes especializados.\n"
        "205 specialists · 100% local · Ollama[/]\n\n"
        "[dim magenta]Haz clic o usa 1-6 para lanzar.[/]"
    )
    _LAUNCHER_RECOMMENDED = "── Recomendados ──────────────────────────────────"
    _LAUNCHER_ALL         = "── Colony — Todos los agentes ────────────────────"
    _LAUNCHER_DIR_LABEL   = "Directorio:"
    _QUIT_LABEL           = "Salir"

# ── Mensajes internos ────────────────────────────────────────────
@dataclass
class UserPromptSubmitted(Message):
    text: str
    shell: bool = False

@dataclass
class AgentResponseChunk(Message):
    text: str
    agent: str = "EIDOS"

@dataclass
class AgentThinking(Message):
    text: str = "⚡ Colony deliberando..."

@dataclass
class AgentDone(Message):
    agents_used: list

@dataclass
class LaunchAgent(Message):
    agent_id: str
    name: str
    emoji: str


# ── CSS ──────────────────────────────────────────────────────────
EIDOS_CSS = """
/* ═══ Variables de color EIDOS ═══════════════════════════════════
   primary   = magenta  #c800ff
   secondary = cyan     #00ccff
   accent    = verde    #00ff88
   bg        = casi negro #050508
   bg2       = #07070f
   bg3       = #0a0a14
   muted     = #1a0a2e                                           */

Screen {
    background: #050508;
    color: #c8c8d4;
}

/* ══ LAUNCHER ════════════════════════════════════════════════════ */

LauncherScreen {
    background: #050508;
}

#launcher-container {
    height: 1fr;
    overflow: hidden auto;
    background: #050508;
    hatch: right #c800ff 4%;
}

#launcher-path-bar {
    height: 1;
    background: #07070f;
    border-bottom: tall #1a0a2e;
    padding: 0 2;
    color: #ffcc00;
}

#launcher-title-grid {
    height: 10;
    background: #07070f;
    border-bottom: tall #1a0a2e;
    layout: horizontal;
}

#launcher-logo {
    width: 44;
    padding: 1 2;
    color: #c800ff;
    text-style: bold;
    background: #07070f;
}

#launcher-tagline {
    padding: 1 2;
    width: 1fr;
    color: #555577;
    background: #07070f;
}

#launcher-sections {
    padding: 1 1;
}

.section-title {
    color: #440055;
    text-style: dim;
    height: 1;
    padding: 0 1;
    margin-top: 1;
}

#launcher-quick {
    layout: horizontal;
    height: auto;
    margin: 0 0 1 0;
}

LauncherCard {
    width: 1fr;
    height: 5;
    border: tall #1a0a2e;
    padding: 0 1;
    margin: 0 1 0 0;
    background: #07070f;
}

LauncherCard:hover {
    background: #120822;
    border: tall #c800ff 50%;
}

LauncherCard.-selected {
    background: #1a0a2e;
    border: tall #c800ff;
}

LauncherCard .card-key {
    color: #00ff88;
    text-style: bold;
    width: 2;
}

LauncherCard .card-emoji {
    width: 3;
}

LauncherCard .card-name {
    color: #c800ff;
    text-style: bold;
    width: 1fr;
}

LauncherCard .card-desc {
    color: #555577;
    text-style: dim;
}

AgentGridCard {
    width: 30;
    height: 6;
    border: tall #1a0a2e;
    padding: 0 1;
    margin: 0 1 1 0;
    background: #07070f;
}

AgentGridCard:hover {
    background: #120822;
    border: tall #c800ff 50%;
}

AgentGridCard.-selected {
    background: #1a0a2e;
    border: tall #c800ff;
}

AgentGridCard .card-name {
    color: #c8c8d4;
    text-style: bold;
}

AgentGridCard .card-desc {
    color: #666688;
    text-style: dim;
}

#agent-grid {
    layout: horizontal;
    height: auto;
}

#launcher-input-bar {
    height: 3;
    background: #07070f;
    border-top: tall #1a0a2e;
    layout: horizontal;
    padding: 0 2;
}

#launcher-dir-label {
    color: #555577;
    width: 12;
    content-align: left middle;
}

#launcher-dir-input {
    width: 1fr;
    background: #07070f;
    border: none;
    color: #ffcc00;
}

#launcher-dir-input:focus {
    border: none;
    background: #0f0f1e;
}

/* ══ MAIN SCREEN ═════════════════════════════════════════════════ */

MainScreen {
    background: #050508;
    layers: sidebar screen base;
}

EidosSidebar {
    width: 40;
    min-width: 30;
    max-width: 50%;
    dock: left;
    background: #07070f;
    border-right: tall #1a0a2e;
    height: 1fr;
}

EidosSidebar Collapsible {
    width: 1fr;
    height: 1fr;
    min-height: 3;
    background: transparent;
    border: none;
}

EidosSidebar Collapsible.-collapsed {
    height: auto;
}

EidosSidebar CollapsibleTitle {
    color: #c800ff;
    background: #0a0a14;
    padding: 0 1;
}

EidosSidebar DirectoryTree {
    height: 1fr;
    background: transparent;
    scrollbar-size-vertical: 1;
}

EidosSidebar DirectoryTree > .tree--guides {
    color: #2a0a3e;
}

EidosSidebar DirectoryTree .tree--cursor {
    background: #1a0a2e;
    color: #c800ff;
}

.colony-agent {
    height: 1;
    padding: 0 1;
    color: #888899;
}

.colony-agent:hover {
    background: #120822;
    color: #c800ff;
}

.colony-agent.-active {
    background: #1a0a2e;
    color: #c800ff;
    text-style: bold;
}

EidosConversation {
    height: 1fr;
    layout: vertical;
}

#conv-window {
    height: 1fr;
    scrollbar-size-vertical: 1;
    padding: 0 1 0 1;
    align: left bottom;
    layout: stream;
}

/* ── Bloques de contenido ── */

UserBlock {
    border-left: blank #00ccff;
    background: #00ccff 8%;
    padding: 1 1 1 0;
    margin: 1 1 1 0;
    min-height: 1;
}

AgentBlock {
    min-height: 1;
    padding: 0 0 0 0;
    layout: stream;
    overflow-x: auto;
}

ThinkingBlock {
    background: #c800ff 10%;
    color: #c800ff;
    min-height: 1;
    margin: 1 1 1 0;
    padding: 0 1;
    max-height: 5;
    overflow-y: auto;
    text-style: italic dim;
}

ShellBlock {
    border-left: blank #00ff88;
    background: #00ff88 5%;
    padding: 1 0;
    margin: 1 1 1 0;
}

/* ── Cursor de bloque (navegación) ── */
#cursor-container {
    height: auto;
    width: 1;
    color: $foreground 7%;
}

/* ── Prompt ── */
EidosPrompt {
    padding: 0 0 0 0;
    height: auto;
    dock: bottom;
}

#slash-overlay {
    display: none;
    overlay: screen;
    offset-y: -10;
    height: 10;
    width: 60;
    background: #0a0a14;
    border: tall #1a0a2e;
}

EidosPrompt.-show-slash #slash-overlay {
    display: block;
}

.slash-item {
    height: 1;
    padding: 0 1;
    color: #888899;
}

.slash-item.-selected {
    background: #1a0a2e;
    color: #c800ff;
}

#prompt-container {
    height: auto;
    margin: 0 0 1 0;
    border: tall #1a0a2e;
    background: #07070f;
}

#prompt-container:focus-within {
    border: tall #c800ff;
}

#prompt-symbol {
    padding: 0 1;
    color: #c800ff;
    text-opacity: 50%;
    width: 3;
    content-align: left middle;
}

#prompt-symbol.-shell {
    color: #00ff88;
}

#prompt-textarea {
    padding: 0 1 0 0;
    background: transparent;
    border: none;
    height: auto;
    max-height: 15;
}

#info-bar {
    height: 1;
    margin: 0 1;
    layout: horizontal;
}

#info-agent {
    padding: 0 1;
    color: #c8c8d4;
    background: #c800ff 15%;
    width: auto;
}

#info-path {
    margin: 0 1;
    width: 1fr;
    height: 1;
    color: #555577;
}

#info-status {
    color: #00ccff 50%;
    margin: 0 1;
    width: auto;
    text-align: right;
}

/* ── Throbber (busy indicator) ── */
#throbber {
    width: 100%;
    height: 1;
    color: #c800ff;
    background: #c800ff 10%;
    visibility: hidden;
}

#throbber.-busy {
    visibility: visible;
}
"""


# ── Widgets del launcher ──────────────────────────────────────────

class LauncherCard(Static, can_focus=True):
    """Card de agente en el launcher rápido (estilo Toad LauncherItem)."""

    class Launched(Message):
        def __init__(self, agent_id: str, name: str, emoji: str) -> None:
            super().__init__()
            self.agent_id = agent_id
            self.name = name
            self.emoji = emoji

    def __init__(self, key: str, agent_id: str, emoji: str, name: str, desc: str, **kw):
        super().__init__(**kw)
        self._key = key
        self._agent_id = agent_id
        self._emoji = emoji
        self._name = name
        self._desc = desc

    def compose(self) -> ComposeResult:
        yield Label(self._key, classes="card-key")
        yield Label(self._emoji, classes="card-emoji")
        yield Label(self._name, classes="card-name")
        yield Label(self._desc, classes="card-desc")

    def on_click(self) -> None:
        self.post_message(self.Launched(self._agent_id, self._name, self._emoji))

    def _on_key(self, event: Key) -> None:
        if event.key in ("enter", "space"):
            event.stop()
            self.post_message(self.Launched(self._agent_id, self._name, self._emoji))


class AgentGridCard(Static, can_focus=True):
    """Card en el grid de todos los agentes Colony."""

    class Launched(Message):
        def __init__(self, agent_id: str, name: str, emoji: str) -> None:
            super().__init__()
            self.agent_id = agent_id
            self.name = name
            self.emoji = emoji

    def __init__(self, agent_id: str, emoji: str, name: str, desc: str, **kw):
        super().__init__(**kw)
        self._agent_id = agent_id
        self._emoji = emoji
        self._name = name
        self._desc = desc

    def compose(self) -> ComposeResult:
        yield Label(f"{self._emoji} {self._name}", classes="card-name")
        yield Label(self._desc, classes="card-desc")

    def on_click(self) -> None:
        self.post_message(self.Launched(self._agent_id, self._name, self._emoji))

    def _on_key(self, event: Key) -> None:
        if event.key in ("enter", "space"):
            event.stop()
            self.post_message(self.Launched(self._agent_id, self._name, self._emoji))


# ── Launcher Screen ───────────────────────────────────────────────

class LauncherScreen(Screen):
    """Pantalla de inicio — seleccionar agente Colony (como el Store de Toad)."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("1", "quick_launch('1')", "EIDOS",    show=True),
        Binding("2", "quick_launch('2')", "Coder",    show=True),
        Binding("3", "quick_launch('3')", "Analyst",  show=True),
        Binding("4", "quick_launch('4')", "Vision",   show=False),
        Binding("5", "quick_launch('5')", "Operator", show=False),
        Binding("6", "quick_launch('6')", "Lumen",    show=False),
        Binding("tab,shift+tab", "focus_next", "Focus", show=False),
        Binding("ctrl+d",  "change_dir", "Dir"),
        Binding("escape",  "back",       "Back" if _LANG == "en" else "Volver"),
        Binding("ctrl+q",  "quit",       _QUIT_LABEL),
    ]

    _project_dir: reactive[Path] = reactive(Path(DEFAULT_DIR))

    def compose(self) -> ComposeResult:
        yield Static(str(self._project_dir), id="launcher-path-bar")
        yield Container(
            Container(
                Static(EIDOS_LOGO, id="launcher-logo"),
                Static(_LAUNCHER_TAGLINE, id="launcher-tagline", markup=True),
                id="launcher-title-grid",
            ),
            self._build_sections(),
            id="launcher-container",
        )
        yield Horizontal(
            Static(_LAUNCHER_DIR_LABEL, id="launcher-dir-label"),
            Input(value=str(self._project_dir), placeholder=str(DEFAULT_DIR), id="launcher-dir-input"),
            id="launcher-input-bar",
        )
        yield Footer()

    def on_mount(self) -> None:
        # Foco en la primera card, no en el input del directorio
        try:
            cards = self.query(LauncherCard)
            if cards:
                cards.first().focus()
        except Exception:
            pass

    def _build_sections(self) -> Container:
        quick_keys = ["1", "2", "3", "4", "5", "6"]
        quick_cards = [
            LauncherCard(quick_keys[i], aid, emoji, name, desc)
            for i, (aid, emoji, name, desc) in enumerate(COLONY_BASE[:6])
        ]
        all_cards = [
            AgentGridCard(aid, emoji, name, desc)
            for aid, emoji, name, desc in COLONY_BASE
        ]
        return Container(
            Static(_LAUNCHER_RECOMMENDED, classes="section-title"),
            Horizontal(*quick_cards, id="launcher-quick"),
            Static(_LAUNCHER_ALL, classes="section-title"),
            Horizontal(*all_cards, id="agent-grid"),
            id="launcher-sections",
        )

    def on_launcher_card_launched(self, event: LauncherCard.Launched) -> None:
        self.dismiss({"agent_id": event.agent_id, "name": event.name, "emoji": event.emoji})

    def on_agent_grid_card_launched(self, event: AgentGridCard.Launched) -> None:
        self.dismiss({"agent_id": event.agent_id, "name": event.name, "emoji": event.emoji})

    def action_quick_launch(self, key: str) -> None:
        idx = int(key) - 1
        if 0 <= idx < len(COLONY_BASE):
            aid, emoji, name, _ = COLONY_BASE[idx]
            self.dismiss({"agent_id": aid, "name": name, "emoji": emoji})

    def action_back(self) -> None:
        self.dismiss(None)

    def action_change_dir(self) -> None:
        try:
            self.query_one("#launcher-dir-input", Input).focus()
        except NoMatches:
            pass

    @on(Input.Submitted, "#launcher-dir-input")
    def on_dir_submitted(self, event: Input.Submitted) -> None:
        p = Path(event.value).expanduser().resolve()
        if p.is_dir():
            self._project_dir = p
            try:
                self.query_one("#launcher-path-bar", Static).update(str(p))
            except NoMatches:
                pass
            # Devolver el foco a las cards tras confirmar directorio
            try:
                cards = self.query(LauncherCard)
                if cards:
                    cards.first().focus()
            except Exception:
                pass
        else:
            self.notify(f"No existe: {event.value}", severity="error")

    def action_quit(self) -> None:
        self.app.exit()


# ── Widgets de conversación ───────────────────────────────────────

COLONY_BASE = COLONY_AGENTS  # alias


class UserBlock(Static):
    """Bloque de mensaje del usuario."""
    pass


class AgentBlock(Markdown):
    """Bloque de respuesta del agente (Markdown renderizado)."""
    pass


class ThinkingBlock(Static):
    """Bloque de 'pensando...' mientras Colony delibera."""
    pass


class ShellBlock(Static):
    """Bloque de resultado de comando shell."""
    pass


class EidosSidebar(Vertical):
    """Sidebar izquierda: Colony agents + DirectoryTree (como el SideBar de Toad)."""

    class AgentSelected(Message):
        def __init__(self, agent_id: str, name: str) -> None:
            super().__init__()
            self.agent_id = agent_id
            self.name = name

    def __init__(self, project_dir: str, **kw):
        super().__init__(**kw)
        self._project_dir = project_dir

    def compose(self) -> ComposeResult:
        with Collapsible(title="Colony", collapsed=False):
            for aid, emoji, name, desc in COLONY_BASE:
                yield Static(
                    f"{emoji} {name}",
                    classes="colony-agent",
                    id=f"agent-{aid}",
                )
        with Collapsible(title="Project", collapsed=False):
            yield DirectoryTree(self._project_dir)

    def on_static_click(self, event: Static.Clicked) -> None:
        w = event.widget
        if "colony-agent" in w.classes:
            for other in self.query(".colony-agent"):
                other.remove_class("-active")
            w.add_class("-active")
            wid = w.id or ""
            agent_id = wid.replace("agent-", "") if wid.startswith("agent-") else "colony_general"
            name = str(w.renderable).split(" ", 1)[-1] if " " in str(w.renderable) else "EIDOS"
            self.post_message(self.AgentSelected(agent_id, name))


class EidosPrompt(Vertical):
    """Input del usuario — mismo concepto que el Prompt de Toad."""

    class Submitted(Message):
        def __init__(self, text: str, shell: bool = False) -> None:
            super().__init__()
            self.text = text
            self.shell = shell

    shell_mode: reactive[bool] = reactive(False, toggle_class="-shell")
    _history: list[str]
    _history_idx: int

    def __init__(self, agent_name: str, project_dir: str, **kw):
        super().__init__(**kw)
        self._agent_name = agent_name
        self._project_dir = project_dir
        self._history = []
        self._history_idx = -1
        self._slash_items: list[Static] = []

    def compose(self) -> ComposeResult:
        # Overlay slash commands
        slash_widgets = []
        for cmd, desc in SLASH_COMMANDS:
            slash_widgets.append(
                Static(f"{cmd}  [dim]{desc}[/]", classes="slash-item", markup=True)
            )
        yield Vertical(*slash_widgets, id="slash-overlay")

        # Input principal
        with Horizontal(id="prompt-container"):
            yield Static("❯", id="prompt-symbol")
            yield TextArea(id="prompt-textarea", language=None)

        # Info bar
        yield Horizontal(
            Static(f"[bold]{self._agent_name}[/]", id="info-agent", markup=True),
            Static(self._project_dir, id="info-path"),
            Static("", id="info-status"),
            id="info-bar",
        )

    def on_mount(self) -> None:
        self.query_one("#prompt-textarea", TextArea).focus()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        text = event.text_area.text
        # Detectar modo shell
        self.shell_mode = text.startswith("!") or text.startswith("$")
        symbol = self.query_one("#prompt-symbol", Static)
        if self.shell_mode:
            symbol.update("$")
            symbol.add_class("-shell")
        else:
            symbol.update("❯")
            symbol.remove_class("-shell")

        # Mostrar/ocultar slash overlay
        show_slash = text.startswith("/") and len(text) > 0
        if show_slash:
            self.add_class("-show-slash")
        else:
            self.remove_class("-show-slash")

    def on_text_area_key(self, event) -> None:
        pass

    async def _submit(self) -> None:
        ta = self.query_one("#prompt-textarea", TextArea)
        text = ta.text.strip()
        if not text:
            return
        ta.clear()
        self.remove_class("-show-slash")
        if text:
            self._history.insert(0, text)
            self._history_idx = -1
        shell = text.startswith("!") or text.startswith("$")
        self.post_message(self.Submitted(text, shell=shell))

    def set_status(self, text: str) -> None:
        try:
            self.query_one("#info-status", Static).update(text)
        except NoMatches:
            pass

    def set_agent(self, name: str) -> None:
        self._agent_name = name
        try:
            self.query_one("#info-agent", Static).update(f"[bold]{name}[/]")
        except NoMatches:
            pass


class EidosConversation(Vertical):
    """Widget de conversación completo (análogo al Conversation de Toad)."""

    busy: reactive[bool] = reactive(False, toggle_class="-busy")
    _agent_id: str
    _agent_name: str

    def __init__(self, agent_id: str, agent_name: str, agent_emoji: str, project_dir: str, **kw):
        super().__init__(**kw)
        self._agent_id = agent_id
        self._agent_name = agent_name
        self._agent_emoji = agent_emoji
        self._project_dir = project_dir
        self._thinking_widget: ThinkingBlock | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="throbber")
        yield VerticalScroll(id="conv-window")
        yield EidosPrompt(self._agent_name, self._project_dir, id="eidos-prompt")

    def on_mount(self) -> None:
        self._add_welcome()

    def _add_welcome(self) -> None:
        window = self.query_one("#conv-window", VerticalScroll)
        if _LANG == "en":
            welcome_md = (
                f"## {self._agent_emoji} New session with **{self._agent_name}**\n\n"
                f"Connected to Colony via `{BRIDGE_URL}/talk`\n\n"
                f"Directory: `{self._project_dir}`\n\n"
                "---\n\n"
                "Type your message or use `/help` to see available commands.\n"
                "Use `!command` to run shell commands directly."
            )
        else:
            welcome_md = (
                f"## {self._agent_emoji} Nueva sesión con **{self._agent_name}**\n\n"
                f"Conectado a Colony via `{BRIDGE_URL}/talk`\n\n"
                f"Directorio: `{self._project_dir}`\n\n"
                "---\n\n"
                "Escribe tu mensaje o usa `/help` para ver comandos disponibles.\n"
                "Usa `!comando` para ejecutar shell directamente."
            )
        window.mount(AgentBlock(welcome_md))
        window.scroll_end(animate=False)

    @on(EidosPrompt.Submitted)
    def on_prompt_submitted(self, event: EidosPrompt.Submitted) -> None:
        text = event.text
        if not text:
            return

        # Slash commands locales
        if text.startswith("/"):
            self._handle_slash(text)
            return

        # Shell directo
        if event.shell:
            cmd = text.lstrip("!$").strip()
            self._run_shell(cmd)
            return

        # Enviar a Colony
        self._add_user_block(text)
        self._send_to_colony(text)

    def _add_user_block(self, text: str) -> None:
        window = self.query_one("#conv-window", VerticalScroll)
        window.mount(UserBlock(f"**SER ❯** {text}"))
        window.scroll_end(animate=False)

    def _add_thinking(self) -> ThinkingBlock:
        window = self.query_one("#conv-window", VerticalScroll)
        msg = "deliberating..." if _LANG == "en" else "deliberando..."
        w = ThinkingBlock(f"⚡ {self._agent_emoji} Colony {msg}")
        window.mount(w)
        window.scroll_end(animate=False)
        return w

    def _add_agent_block(self, text: str, agents: list | None = None) -> None:
        window = self.query_one("#conv-window", VerticalScroll)
        footer = f"\n\n---\n*{', '.join(agents)}*" if agents else ""
        window.mount(AgentBlock(text + footer))
        window.scroll_end(animate=False)

    def _add_shell_block(self, cmd: str, output: str) -> None:
        window = self.query_one("#conv-window", VerticalScroll)
        window.mount(ShellBlock(f"$ {cmd}\n{output[:2000]}"))
        window.scroll_end(animate=False)

    def _handle_slash(self, text: str) -> None:
        cmd = text.split()[0].lstrip("/")
        args = text.split()[1:] if len(text.split()) > 1 else []

        if cmd == "clear":
            window = self.query_one("#conv-window", VerticalScroll)
            for child in list(window.children):
                child.remove()
            self._add_welcome()

        elif cmd == "help":
            title = "## Available commands\n\n" if _LANG == "en" else "## Comandos disponibles\n\n"
            shell_hint = "\n- `!command` — Run shell command\n" if _LANG == "en" else "\n- `!comando` — Ejecutar comando shell\n"
            lines = title
            for c, d in SLASH_COMMANDS:
                lines += f"- `{c}` — {d}\n"
            lines += shell_hint
            window = self.query_one("#conv-window", VerticalScroll)
            window.mount(AgentBlock(lines))
            window.scroll_end(animate=False)

        elif cmd == "agents":
            title = "## Colony Agents\n\n" if _LANG == "en" else "## Agentes Colony\n\n"
            lines = title
            for aid, emoji, name, desc in COLONY_BASE:
                lines += f"- {emoji} **{name}** (`{aid}`) — {desc}\n"
            window = self.query_one("#conv-window", VerticalScroll)
            window.mount(AgentBlock(lines))
            window.scroll_end(animate=False)

        elif cmd == "status":
            self._send_to_colony("/status of all EIDOS services" if _LANG == "en" else "/status de todos los servicios de EIDOS")

        elif cmd == "vision":
            self._send_to_colony("[VISION: describe what you see on screen right now]" if _LANG == "en" else "[VISION: describe lo que ves en la pantalla ahora mismo]")

        elif cmd == "brain":
            query = " ".join(args) if args else ("system status" if _LANG == "en" else "estado del sistema")
            self._send_to_colony(f"[BRAIN: search in your memory about '{query}']" if _LANG == "en" else f"[BRAIN: busca en tu memoria sobre '{query}']")

        elif cmd == "spec":
            spec = " ".join(args) if args else ""
            self._send_to_colony(f"[SPEC: {spec}] I want to talk to specialist {spec}" if _LANG == "en" else f"[SPEC: {spec}] Quiero hablar con el especialista {spec}")

        else:
            msg = f"Unknown command: /{cmd}" if _LANG == "en" else f"Comando desconocido: /{cmd}"
            self.notify(msg, severity="warning")

    @work(exclusive=False)
    async def _send_to_colony(self, text: str) -> None:
        thinking = self._add_thinking()
        try:
            self.query_one("#eidos-prompt", EidosPrompt).set_status("deliberando...")
        except NoMatches:
            pass

        try:
            import requests as req_lib
            payload = {
                "message": text,
                "max_agents": 3,
                "preferred_agent": self._agent_id if not self._agent_id.startswith("oc_") else None,
            }
            result = await asyncio.to_thread(
                lambda: req_lib.post(f"{BRIDGE_URL}/talk", json=payload, timeout=600)
            )
            data = result.json()
            response = data.get("response", data.get("text", "Sin respuesta de Colony"))
            agents = data.get("agents_used", data.get("agents_consulted", []))
        except Exception as exc:
            if _LANG == "en":
                response = f"⚠️ **Error connecting to Colony**\n\n`{exc}`\n\nCheck: `eidos start`"
            else:
                response = f"⚠️ **Error conectando con Colony**\n\n`{exc}`\n\nVerifica: `eidos start`"
            agents = []

        try:
            thinking.remove()
        except Exception:
            pass

        self._add_agent_block(response, agents if agents else None)
        try:
            self.query_one("#eidos-prompt", EidosPrompt).set_status("")
        except NoMatches:
            pass

    @work(exclusive=False)
    async def _run_shell(self, cmd: str) -> None:
        self._add_user_block(f"**$ {cmd}**")
        try:
            result = await asyncio.to_thread(
                lambda: subprocess.run(
                    cmd, shell=True, capture_output=True, text=True,
                    cwd=self._project_dir, timeout=30,
                )
            )
            out = result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            out = "Timeout (30s)"
        except Exception as exc:
            out = f"Error: {exc}"

        self._add_shell_block(cmd, out or ("(no output)" if _LANG == "en" else "(sin salida)"))

    def set_agent(self, agent_id: str, name: str, emoji: str) -> None:
        self._agent_id = agent_id
        self._agent_name = name
        self._agent_emoji = emoji
        try:
            self.query_one("#eidos-prompt", EidosPrompt).set_agent(name)
        except NoMatches:
            pass


# ── Main Screen ───────────────────────────────────────────────────

class MainScreen(Screen):
    """Pantalla principal: sidebar + conversación (análogo al MainScreen de Toad)."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+b,f20", "toggle_sidebar", "Sidebar"),
        Binding("ctrl+h",     "home",            "Home"),
        Binding("escape",     "home",            "Home" if _LANG == "en" else "Inicio", priority=False),
        Binding("ctrl+q",     "quit",            "Quit" if _LANG == "en" else "Salir"),
    ]

    _sidebar_visible: reactive[bool] = reactive(True)

    def __init__(self, agent_id: str, name: str, emoji: str, project_dir: str, **kw):
        super().__init__(**kw)
        self._agent_id = agent_id
        self._agent_name = name
        self._agent_emoji = emoji
        self._project_dir = project_dir

    def compose(self) -> ComposeResult:
        yield EidosSidebar(self._project_dir, id="eidos-sidebar")
        yield EidosConversation(
            self._agent_id,
            self._agent_name,
            self._agent_emoji,
            self._project_dir,
            id="eidos-conv",
        )
        yield Footer()

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#eidos-sidebar", EidosSidebar)
        if sidebar.display:
            sidebar.display = False
        else:
            sidebar.display = True

    async def action_home(self) -> None:
        # Mostrar launcher como overlay; al seleccionar agente se actualiza el chat
        if any(isinstance(s, LauncherScreen) for s in self.app.screen_stack):
            return  # ya hay un launcher abierto
        result = await self.app.push_screen_wait(LauncherScreen())
        if result:
            try:
                conv = self.query_one("#eidos-conv", EidosConversation)
                conv.set_agent(result["agent_id"], result["name"], result["emoji"])
                label = "Active agent" if _LANG == "en" else "Agente activo"
                self.notify(f"{label}: {result['emoji']} {result['name']}", timeout=2)
            except NoMatches:
                pass

    def action_quit(self) -> None:
        self.app.exit()

    @on(EidosSidebar.AgentSelected)
    def on_agent_selected(self, event: EidosSidebar.AgentSelected) -> None:
        emoji = next((e for a, e, n, _ in COLONY_BASE if a == event.agent_id), "🟣")
        conv = self.query_one("#eidos-conv", EidosConversation)
        conv.set_agent(event.agent_id, event.name, emoji)
        label = "Active agent" if _LANG == "en" else "Agente activo"
        self.notify(f"{label}: {emoji} {event.name}", timeout=2)

    @on(DirectoryTree.FileSelected)
    def on_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        path = str(event.path)
        try:
            prompt = self.query_one("#eidos-prompt", EidosPrompt)
            ta = prompt.query_one("#prompt-textarea", TextArea)
            current = ta.text
            ta.replace(f"{current} @{path}", (0, 0), ta.document.end)
        except NoMatches:
            pass


# ── App principal ─────────────────────────────────────────────────

class EidosTUI(App):
    """EIDOS TUI — Ser vivo digital. Inspirado en Toad, código propio."""

    CSS = EIDOS_CSS
    TITLE = "EIDOS"
    SUB_TITLE = "Sovereign Agent · macOS" if _LANG == "en" else "Agente Soberano · Kali Linux"
    SCREENS = {}

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+q", "quit",          "Quit"   if _LANG == "en" else "Salir"),
        Binding("f1",     "help",          "Help"   if _LANG == "en" else "Ayuda"),
        Binding("f2",     "settings_info", "Info"),
        Binding("ctrl+l", "show_launcher", "Agents" if _LANG == "en" else "Agentes"),
    ]

    def on_mount(self) -> None:
        # Ir directamente al chat con EIDOS (colony_general) como agente por defecto
        aid, emoji, name, _ = COLONY_AGENTS[0]
        self.push_screen(MainScreen(aid, name, emoji, DEFAULT_DIR))

    async def action_show_launcher(self) -> None:
        """Ctrl+L — muestra el launcher para cambiar de agente."""
        if any(isinstance(s, LauncherScreen) for s in self.screen_stack):
            return
        result = await self.push_screen_wait(LauncherScreen())
        if result:
            try:
                screen = self.screen
                conv = screen.query_one("#eidos-conv", EidosConversation)
                conv.set_agent(result["agent_id"], result["name"], result["emoji"])
                label = "Active agent" if _LANG == "en" else "Agente activo"
                self.notify(f"{label}: {result['emoji']} {result['name']}", timeout=2)
            except Exception:
                pass

    def action_help(self) -> None:
        if _LANG == "en":
            msg = "1-6: launch agent  ·  ctrl+b: sidebar  ·  ctrl+h: home  ·  ctrl+l: agents  ·  ctrl+q: quit"
            title = "EIDOS TUI — Help"
        else:
            msg = "1-6: lanzar agente  ·  ctrl+b: sidebar  ·  ctrl+h: home  ·  ctrl+l: agentes  ·  ctrl+q: salir"
            title = "EIDOS TUI — Ayuda"
        self.notify(msg, title=title, severity="information", timeout=5)

    def action_settings_info(self) -> None:
        self.notify(
            f"Bridge: {BRIDGE_URL}  ·  Dir: {DEFAULT_DIR}  ·  Lang: {_LANG}",
            title="EIDOS — Config",
            severity="information",
            timeout=4,
        )


def launch_tui() -> None:
    """Punto de entrada del TUI de EIDOS."""
    app = EidosTUI()
    app.run()


if __name__ == "__main__":
    launch_tui()
