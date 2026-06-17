#!/usr/bin/env python3
"""
EIDOS VSCode Extensions Manager
================================
Sistema para instalar, gestionar y recomendar extensiones de VSCode automáticamente.

Funcionalidades:
- Instalar extensiones desde marketplace
- Detectar extensiones necesarias según proyecto
- Recomendar extensiones basadas en archivos
- Sincronizar extensiones entre VSEIDOS y VSCode normal
"""

import os
import sys
import json
import subprocess
import time
from pathlib import Path
from typing import List, Dict, Optional, Set
from dataclasses import dataclass, asdict
from datetime import datetime

# Add EIDOS root to path
EIDOS_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(EIDOS_ROOT))

from core.eidos_config import get_config


@dataclass
class VSCodeExtension:
    """Representa una extensión de VSCode"""
    id: str  # publisher.extension-name
    name: str
    description: str
    category: str
    install_priority: int  # 1=critical, 2=recommended, 3=optional
    installed: bool = False
    installed_at: Optional[float] = None


class VSCodeExtensionsManager:
    """
    Gestor de extensiones de VSCode para EIDOS.

    Puede instalar extensiones automáticamente y recomendar según el proyecto.
    """

    def __init__(self):
        self.config = get_config()

        # Directorio de datos de extensiones
        self.extensions_dir = Path.home() / ".eidos" / "vscode_extensions"
        self.extensions_dir.mkdir(parents=True, exist_ok=True)

        # Registro de extensiones
        self.registry_file = self.extensions_dir / "extensions_registry.json"

        # Extensiones esenciales para EIDOS
        self.essential_extensions = self._get_essential_extensions()

        # Extensiones por lenguaje
        self.language_extensions = self._get_language_extensions()

        print("[VSCODE] 🔌 VSCode Extensions Manager inicializado")

    def _get_essential_extensions(self) -> List[VSCodeExtension]:
        """Extensiones esenciales para trabajar con EIDOS"""
        return [
            VSCodeExtension(
                id="ms-python.python",
                name="Python",
                description="Python language support",
                category="language",
                install_priority=1
            ),
            VSCodeExtension(
                id="ms-python.vscode-pylance",
                name="Pylance",
                description="Python IntelliSense",
                category="language",
                install_priority=1
            ),
            VSCodeExtension(
                id="rust-lang.rust-analyzer",
                name="Rust Analyzer",
                description="Rust language support",
                category="language",
                install_priority=1
            ),
            VSCodeExtension(
                id="golang.go",
                name="Go",
                description="Go language support",
                category="language",
                install_priority=1
            ),
            VSCodeExtension(
                id="ms-vscode.cpptools",
                name="C/C++",
                description="C/C++ IntelliSense",
                category="language",
                install_priority=1
            ),
            VSCodeExtension(
                id="ziglang.vscode-zig",
                name="Zig Language",
                description="Zig language support",
                category="language",
                install_priority=1
            ),
            VSCodeExtension(
                id="eamodio.gitlens",
                name="GitLens",
                description="Git supercharged",
                category="git",
                install_priority=2
            ),
            VSCodeExtension(
                id="esbenp.prettier-vscode",
                name="Prettier",
                description="Code formatter",
                category="formatter",
                install_priority=2
            ),
            VSCodeExtension(
                id="ms-vscode-remote.remote-ssh",
                name="Remote SSH",
                description="SSH remote development",
                category="remote",
                install_priority=2
            ),
            VSCodeExtension(
                id="visualstudioexptteam.vscodeintellicode",
                name="IntelliCode",
                description="AI-assisted development",
                category="ai",
                install_priority=2
            ),
        ]

    def _get_language_extensions(self) -> Dict[str, List[str]]:
        """Mapeo de extensiones de archivo a extensiones recomendadas"""
        return {
            ".py": ["ms-python.python", "ms-python.vscode-pylance"],
            ".rs": ["rust-lang.rust-analyzer"],
            ".go": ["golang.go"],
            ".cpp": ["ms-vscode.cpptools"],
            ".c": ["ms-vscode.cpptools"],
            ".h": ["ms-vscode.cpptools"],
            ".zig": ["ziglang.vscode-zig"],
            ".js": ["dbaeumer.vscode-eslint"],
            ".ts": ["dbaeumer.vscode-eslint"],
            ".jsx": ["dbaeumer.vscode-eslint"],
            ".tsx": ["dbaeumer.vscode-eslint"],
            ".html": ["ecmel.vscode-html-css"],
            ".css": ["ecmel.vscode-html-css"],
            ".json": ["zainchen.json"],
            ".md": ["yzhang.markdown-all-in-one"],
            ".yaml": ["redhat.vscode-yaml"],
            ".yml": ["redhat.vscode-yaml"],
            ".toml": ["tamasfe.even-better-toml"],
            ".docker": ["ms-azuretools.vscode-docker"],
            "Dockerfile": ["ms-azuretools.vscode-docker"],
        }

    def install_extension(self, extension_id: str, vscode_command: str = "code") -> bool:
        """
        Instala una extensión de VSCode.

        Args:
            extension_id: ID de la extensión (publisher.extension-name)
            vscode_command: Comando de VSCode ('code' o 'vseidos')

        Returns:
            True si se instaló correctamente
        """
        print(f"[VSCODE] 📦 Instalando extensión: {extension_id}")

        try:
            result = subprocess.run(
                [vscode_command, "--install-extension", extension_id],
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.returncode == 0:
                print(f"[VSCODE] ✅ Instalada: {extension_id}")
                self._mark_as_installed(extension_id)
                return True
            else:
                print(f"[VSCODE] ❌ Error instalando {extension_id}: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            print(f"[VSCODE] ⏱️  Timeout instalando {extension_id}")
            return False
        except FileNotFoundError:
            print(f"[VSCODE] ❌ Comando '{vscode_command}' no encontrado")
            return False
        except Exception as e:
            print(f"[VSCODE] ❌ Error: {e}")
            return False

    def install_essential_extensions(self, vscode_command: str = "code") -> Dict[str, bool]:
        """
        Instala todas las extensiones esenciales para EIDOS.

        Returns:
            Dict con resultados {extension_id: success}
        """
        print(f"\n[VSCODE] 🚀 Instalando extensiones esenciales...")

        results = {}

        # Ordenar por prioridad
        sorted_extensions = sorted(self.essential_extensions, key=lambda x: x.install_priority)

        for ext in sorted_extensions:
            if not ext.installed:
                success = self.install_extension(ext.id, vscode_command)
                results[ext.id] = success
                time.sleep(2)  # Esperar entre instalaciones
            else:
                print(f"[VSCODE] ⏭️  Ya instalada: {ext.id}")
                results[ext.id] = True

        # Resumen
        installed = sum(1 for v in results.values() if v)
        total = len(results)
        print(f"\n[VSCODE] 📊 Instaladas {installed}/{total} extensiones")

        return results

    def detect_project_extensions(self, project_path: Path) -> List[str]:
        """
        Detecta qué extensiones se necesitan según los archivos del proyecto.

        Args:
            project_path: Ruta al proyecto

        Returns:
            Lista de IDs de extensiones recomendadas
        """
        if not project_path.exists():
            return []

        print(f"[VSCODE] 🔍 Detectando extensiones para: {project_path}")

        # Encontrar todos los archivos
        file_extensions = set()
        for file in project_path.rglob("*"):
            if file.is_file():
                ext = file.suffix
                if ext:
                    file_extensions.add(ext)
                # Archivos especiales sin extensión
                if file.name in ["Dockerfile", "Makefile", "Cargo.toml", "go.mod"]:
                    file_extensions.add(file.name)

        # Mapear a extensiones recomendadas
        recommended = set()
        for ext in file_extensions:
            if ext in self.language_extensions:
                recommended.update(self.language_extensions[ext])

        print(f"[VSCODE] 💡 Extensiones recomendadas: {len(recommended)}")
        for ext_id in sorted(recommended):
            print(f"   • {ext_id}")

        return list(recommended)

    def install_for_project(self, project_path: Path, vscode_command: str = "code") -> Dict[str, bool]:
        """
        Instala extensiones necesarias para un proyecto específico.

        Args:
            project_path: Ruta al proyecto
            vscode_command: Comando de VSCode

        Returns:
            Dict con resultados
        """
        recommended = self.detect_project_extensions(project_path)

        if not recommended:
            print("[VSCODE] ℹ️  No se detectaron extensiones adicionales necesarias")
            return {}

        print(f"\n[VSCODE] 📦 Instalando extensiones para el proyecto...")

        results = {}
        for ext_id in recommended:
            success = self.install_extension(ext_id, vscode_command)
            results[ext_id] = success
            time.sleep(2)

        return results

    def list_installed_extensions(self, vscode_command: str = "code") -> List[str]:
        """
        Lista extensiones actualmente instaladas.

        Returns:
            Lista de IDs de extensiones instaladas
        """
        try:
            result = subprocess.run(
                [vscode_command, "--list-extensions"],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                extensions = result.stdout.strip().split('\n')
                return [ext for ext in extensions if ext]
            else:
                return []

        except Exception as e:
            print(f"[VSCODE] ❌ Error listando extensiones: {e}")
            return []

    def sync_extensions(self, source: str = "code", target: str = "vseidos") -> Dict[str, bool]:
        """
        Sincroniza extensiones de un VSCode a otro.

        Args:
            source: VSCode fuente ('code' o 'vseidos')
            target: VSCode destino ('code' o 'vseidos')

        Returns:
            Dict con resultados
        """
        print(f"[VSCODE] 🔄 Sincronizando extensiones: {source} → {target}")

        # Listar extensiones del source
        source_extensions = self.list_installed_extensions(source)

        if not source_extensions:
            print(f"[VSCODE] ⚠️  No se encontraron extensiones en {source}")
            return {}

        print(f"[VSCODE] 📋 Encontradas {len(source_extensions)} extensiones en {source}")

        # Instalar en target
        results = {}
        for ext_id in source_extensions:
            success = self.install_extension(ext_id, target)
            results[ext_id] = success
            time.sleep(1)

        installed = sum(1 for v in results.values() if v)
        print(f"[VSCODE] ✅ Sincronizadas {installed}/{len(source_extensions)} extensiones")

        return results

    def _mark_as_installed(self, extension_id: str):
        """Marca una extensión como instalada en el registro"""
        registry = self._load_registry()

        registry[extension_id] = {
            "installed": True,
            "installed_at": time.time()
        }

        self._save_registry(registry)

    def _load_registry(self) -> Dict:
        """Carga el registro de extensiones"""
        if self.registry_file.exists():
            try:
                with open(self.registry_file, 'r') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_registry(self, registry: Dict):
        """Guarda el registro de extensiones"""
        with open(self.registry_file, 'w') as f:
            json.dump(registry, f, indent=2)

    def get_statistics(self) -> Dict:
        """Obtiene estadísticas de extensiones"""
        installed = self.list_installed_extensions()
        registry = self._load_registry()

        return {
            "total_installed": len(installed),
            "essential_count": len(self.essential_extensions),
            "registry_count": len(registry),
            "installed_extensions": installed[:10],  # Primeras 10
        }


def main():
    """Función principal para testing"""
    print("🔌 EIDOS VSCode Extensions Manager\n")

    manager = VSCodeExtensionsManager()

    # Mostrar extensiones esenciales
    print("📦 Extensiones esenciales para EIDOS:")
    for ext in manager.essential_extensions:
        priority = "🔴 CRÍTICA" if ext.install_priority == 1 else "🟡 RECOMENDADA" if ext.install_priority == 2 else "🟢 OPCIONAL"
        print(f"  {priority} {ext.name} ({ext.id})")
        print(f"     {ext.description}")

    print(f"\n📊 Total: {len(manager.essential_extensions)} extensiones esenciales")

    # Estadísticas
    stats = manager.get_statistics()
    print(f"\n📈 Estadísticas:")
    print(f"  Instaladas actualmente: {stats['total_installed']}")
    print(f"  En registro: {stats['registry_count']}")


if __name__ == "__main__":
    main()
