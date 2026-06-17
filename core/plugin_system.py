"""
EIDOS core/plugin_system.py — Third-Party Plugin System
========================================================
Sistema de plugins extensible que permite cargar módulos externos
desde un directorio de plugins. Cada plugin es un archivo .py con
una clase que hereda de EidosPlugin.

Directorio: ~/.eidos/plugins/

Uso:
    from core.plugin_system import get_plugin_manager
    pm = get_plugin_manager()
    pm.load_all()
    pm.execute("plugin_name", "action", args)

Plugin template:
    class MyPlugin(EidosPlugin):
        name = "my_plugin"
        version = "1.0"
        description = "Does X"

        def on_load(self): ...
        def on_unload(self): ...
        def execute(self, action, args): ...
"""
from __future__ import annotations

import importlib.util
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("eidos.plugins")

PLUGINS_DIR = os.path.expanduser("~/.eidos/plugins")


# ══════════════════════════════════════════════════════════════════════════════
#  PLUGIN BASE CLASS
# ══════════════════════════════════════════════════════════════════════════════

class EidosPlugin(ABC):
    """Base class for all EIDOS plugins."""
    name: str = "unnamed"
    version: str = "0.1"
    description: str = ""
    author: str = ""
    requires: list[str] = []  # Required Python packages

    def on_load(self) -> None:
        """Called when plugin is loaded. Override for initialization."""
        pass

    def on_unload(self) -> None:
        """Called when plugin is unloaded. Override for cleanup."""
        pass

    @abstractmethod
    def execute(self, action: str, args: dict[str, Any] | None = None) -> Any:
        """Execute a plugin action."""
        ...

    def get_actions(self) -> list[str]:
        """Return list of available actions."""
        return ["execute"]

    @property
    def info(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "actions": self.get_actions(),
        }


# ══════════════════════════════════════════════════════════════════════════════
#  PLUGIN METADATA
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PluginEntry:
    """Metadata for a loaded plugin."""
    name: str
    path: str
    instance: EidosPlugin
    loaded_at: float = field(default_factory=time.time)
    enabled: bool = True
    error: str = ""
    exec_count: int = 0


# ══════════════════════════════════════════════════════════════════════════════
#  PLUGIN MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class PluginManager:
    """Discovers, loads, and manages EIDOS plugins."""

    def __init__(self, plugins_dir: str = PLUGINS_DIR):
        self.plugins_dir = plugins_dir
        self._plugins: dict[str, PluginEntry] = {}
        self._hooks: dict[str, list[str]] = {}  # hook_name -> [plugin_names]
        os.makedirs(self.plugins_dir, exist_ok=True)

    def discover(self) -> list[str]:
        """Discover .py files in plugins directory."""
        found = []
        plugins_path = Path(self.plugins_dir)
        if not plugins_path.exists():
            return found
        for f in plugins_path.glob("*.py"):
            if f.name.startswith("_"):
                continue
            found.append(f.stem)
        return sorted(found)

    def load(self, name: str) -> bool:
        """Load a single plugin by name."""
        path = os.path.join(self.plugins_dir, f"{name}.py")
        if not os.path.isfile(path):
            log.error("Plugin file not found: %s", path)
            return False

        if name in self._plugins:
            self.unload(name)

        try:
            spec = importlib.util.spec_from_file_location(f"eidos_plugin_{name}", path)
            if spec is None or spec.loader is None:
                log.error("Cannot create module spec for: %s", path)
                return False

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find EidosPlugin subclass in module
            plugin_cls = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and issubclass(attr, EidosPlugin)
                        and attr is not EidosPlugin):
                    plugin_cls = attr
                    break

            if plugin_cls is None:
                log.error("No EidosPlugin subclass found in: %s", path)
                return False

            # Check requirements
            for req in plugin_cls.requires:
                try:
                    __import__(req)
                except ImportError:
                    log.error("Plugin %s requires package: %s", name, req)
                    self._plugins[name] = PluginEntry(
                        name=name, path=path, instance=None,  # type: ignore
                        enabled=False, error=f"Missing: {req}"
                    )
                    return False

            instance = plugin_cls()
            instance.on_load()

            self._plugins[name] = PluginEntry(
                name=name, path=path, instance=instance
            )
            log.info("Plugin loaded: %s v%s", instance.name, instance.version)
            return True

        except Exception as e:
            log.error("Failed to load plugin %s: %s", name, e)
            self._plugins[name] = PluginEntry(
                name=name, path=path, instance=None,  # type: ignore
                enabled=False, error=str(e)
            )
            return False

    def unload(self, name: str) -> bool:
        """Unload a plugin."""
        entry = self._plugins.get(name)
        if not entry:
            return False
        if entry.instance:
            try:
                entry.instance.on_unload()
            except Exception as e:
                log.warning("Plugin %s on_unload error: %s", name, e)
        del self._plugins[name]
        # Remove from hooks
        for hook_name in list(self._hooks):
            if name in self._hooks[hook_name]:
                self._hooks[hook_name].remove(name)
        log.info("Plugin unloaded: %s", name)
        return True

    def load_all(self) -> int:
        """Discover and load all plugins. Returns count loaded."""
        names = self.discover()
        loaded = 0
        for name in names:
            if self.load(name):
                loaded += 1
        log.info("Loaded %d/%d plugins", loaded, len(names))
        return loaded

    def execute(self, plugin_name: str, action: str = "execute",
                args: dict[str, Any] | None = None) -> Any:
        """Execute an action on a plugin."""
        entry = self._plugins.get(plugin_name)
        if not entry or not entry.instance or not entry.enabled:
            raise ValueError(f"Plugin not available: {plugin_name}")
        try:
            result = entry.instance.execute(action, args)
            entry.exec_count += 1
            return result
        except Exception as e:
            log.error("Plugin %s execute error: %s", plugin_name, e)
            raise

    def get_plugin(self, name: str) -> Optional[PluginEntry]:
        """Get plugin entry by name."""
        return self._plugins.get(name)

    def list_plugins(self) -> list[dict]:
        """List all loaded plugins with info."""
        result = []
        for name, entry in sorted(self._plugins.items()):
            info = entry.instance.info if entry.instance else {"name": name}
            info["enabled"] = entry.enabled
            info["error"] = entry.error
            info["exec_count"] = entry.exec_count
            info["loaded_at"] = entry.loaded_at
            result.append(info)
        return result

    def enable(self, name: str) -> bool:
        entry = self._plugins.get(name)
        if entry:
            entry.enabled = True
            return True
        return False

    def disable(self, name: str) -> bool:
        entry = self._plugins.get(name)
        if entry:
            entry.enabled = False
            return True
        return False

    # ── Hooks ──────────────────────────────────────────────────────────────

    def register_hook(self, hook_name: str, plugin_name: str) -> None:
        """Register a plugin to be called on a specific hook event."""
        if hook_name not in self._hooks:
            self._hooks[hook_name] = []
        if plugin_name not in self._hooks[hook_name]:
            self._hooks[hook_name].append(plugin_name)

    def trigger_hook(self, hook_name: str, **kwargs) -> list[Any]:
        """Trigger all plugins registered for a hook."""
        results = []
        for pname in self._hooks.get(hook_name, []):
            entry = self._plugins.get(pname)
            if entry and entry.instance and entry.enabled:
                try:
                    r = entry.instance.execute(hook_name, kwargs)
                    results.append(r)
                except Exception as e:
                    log.warning("Hook %s on %s: %s", hook_name, pname, e)
        return results

    @property
    def stats(self) -> dict:
        loaded = [n for n, e in self._plugins.items() if e.enabled and e.instance]
        failed = [n for n, e in self._plugins.items() if not e.enabled or not e.instance]
        return {
            "plugins_dir": self.plugins_dir,
            "discovered": len(self.discover()),
            "loaded": len(loaded),
            "failed": len(failed),
            "failed_names": failed,
            "total_executions": sum(e.exec_count for e in self._plugins.values()),
            "hooks": {k: len(v) for k, v in self._hooks.items()},
        }

    def create_template(self, name: str) -> str:
        """Create a plugin template file."""
        path = os.path.join(self.plugins_dir, f"{name}.py")
        if os.path.exists(path):
            return f"Plugin file already exists: {path}"

        template = f'''"""
EIDOS Plugin: {name}
"""
from core.plugin_system import EidosPlugin


class {name.title().replace("_", "")}Plugin(EidosPlugin):
    name = "{name}"
    version = "1.0"
    description = "Description of {name}"
    author = "SER"
    requires = []  # e.g., ["requests", "numpy"]

    def on_load(self):
        """Called when plugin loads."""
        pass

    def on_unload(self):
        """Called when plugin unloads."""
        pass

    def execute(self, action, args=None):
        """Main plugin action."""
        args = args or {{}}
        if action == "execute":
            return f"{{self.name}} executed with args: {{args}}"
        return f"Unknown action: {{action}}"

    def get_actions(self):
        return ["execute"]
'''
        with open(path, "w") as f:
            f.write(template)
        return f"Template created: {path}"


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_manager: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    global _manager
    if _manager is None:
        _manager = PluginManager()
    return _manager


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    pm = get_plugin_manager()
    print("Plugin System")
    print(f"  Dir: {pm.plugins_dir}")
    discovered = pm.discover()
    print(f"  Discovered: {discovered}")
    loaded = pm.load_all()
    print(f"  Loaded: {loaded}")
    for p in pm.list_plugins():
        print(f"    {p['name']} v{p.get('version', '?')}: {p.get('description', '')}")
    print(f"  Stats: {pm.stats}")

    # Create example if none exist
    if not discovered:
        result = pm.create_template("example_plugin")
        print(f"  {result}")
    print("OK")
