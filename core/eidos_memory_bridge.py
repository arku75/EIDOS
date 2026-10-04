"""
EIDOS Memory Bridge - Todo Aprendizaje a Memoria Persistente
=============================================================

Todo lo que EIDOS hace, descarga, instala o aprende se guarda en 
Episodic Memory para recordarlo entre sesiones.

Integraciones:
- VSEIDOS: Extensiones instaladas, comandos usados
- Kubernetes: Pods, servicios, configuraciones
- Docker: Containers, imágenes, redes
- Git: Commits, repos, cambios
- Sistema: Archivos creados, procesos, configuraciones

Uso:
    from core.eidos_memory_bridge import get_memory_bridge
    bridge = get_memory_bridge()
    
    # Registrar cualquier evento de aprendizaje
    bridge.record_learning("vscode_extension", "Instalada extensión Python", {
        "extension": "ms-python.python",
        "version": "2024.2.0"
    })
"""

import json
import logging
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.eidos_episodic_memory import get_episodic_memory, EpisodeType

log = logging.getLogger("eidos.memory_bridge")


class MemoryBridge:
    """
    Puente entre actividades del sistema y memoria episódica.
    Todo queda registrado para que EIDOS recuerde.
    """
    
    def __init__(self):
        self.memory = get_episodic_memory()
        self._setup_hooks()
        log.info("Memory Bridge initialized")
    
    def _setup_hooks(self):
        """Configura hooks para capturar actividades automáticamente."""
        # Por ahora manual, pero podría ser automático con file watchers
        pass
    
    # ═════════════════════════════════════════════════════════════════
    #  VSEIDOS / VSCODE INTEGRATION
    # ═════════════════════════════════════════════════════════════════
    
    def record_vscode_extension_installed(self, extension_id: str, 
                                          extension_name: str,
                                          version: str,
                                          enabled: bool = True):
        """Registra cuando se instala una extensión VSCode."""
        self.memory.record_episode(
            episode_type=EpisodeType.LEARNING,
            content=f"VSEIDOS: Instalada extensión '{extension_name}' ({extension_id}) v{version}",
            importance=6,
            tags=["vscode", "extension", extension_id, "vseidos"],
            data={
                "extension_id": extension_id,
                "name": extension_name,
                "version": version,
                "enabled": enabled,
                "type": "tool_acquisition"
            }
        )
        log.info(f"📝 Memoria: Extensión VSCode {extension_id} registrada")
    
    def record_vscode_command_used(self, command: str, 
                                   context: str = "",
                                   success: bool = True):
        """Registra uso de comandos VSCode."""
        self.memory.record_episode(
            episode_type=EpisodeType.ACTION,
            content=f"VSEIDOS: Comando '{command}' ejecutado ({'✓' if success else '✗'})",
            importance=4,
            tags=["vscode", "command", "vseidos"],
            data={
                "command": command,
                "context": context,
                "success": success
            }
        )
    
    def record_vseidos_session(self, files_opened: List[str],
                               duration_minutes: int,
                               actions_count: int):
        """Registra sesión de trabajo en VSEIDOS."""
        self.memory.record_episode(
            episode_type=EpisodeType.STATE,
            content=f"VSEIDOS: Sesión de {duration_minutes}min, {actions_count} acciones, {len(files_opened)} archivos",
            importance=5,
            tags=["vseidos", "session", "productivity"],
            data={
                "files": files_opened,
                "duration": duration_minutes,
                "actions": actions_count
            }
        )
    
    # ═════════════════════════════════════════════════════════════════
    #  KUBERNETES INTEGRATION
    # ═════════════════════════════════════════════════════════════════
    
    def record_kubectl_command(self, command: str, namespace: str,
                               resource: str, result: str,
                               success: bool = True):
        """Registra comandos kubectl ejecutados."""
        self.memory.record_episode(
            episode_type=EpisodeType.ACTION,
            content=f"K8s: {command} {resource} en {namespace}",
            importance=7 if not success else 5,
            tags=["kubernetes", "kubectl", resource, namespace],
            data={
                "command": command,
                "namespace": namespace,
                "resource": resource,
                "result": result,
                "success": success
            }
        )
    
    def record_k8s_resource_created(self, resource_type: str,
                                  name: str, namespace: str,
                                  yaml_content: str = ""):
        """Registra creación de recursos Kubernetes."""
        self.memory.record_episode(
            episode_type=EpisodeType.EVENT,
            content=f"K8s: Creado {resource_type}/{name} en {namespace}",
            importance=6,
            tags=["kubernetes", "created", resource_type, namespace],
            data={
                "type": resource_type,
                "name": name,
                "namespace": namespace,
                "yaml_hash": hash(yaml_content) & 0xFFFFFFFF  # Hash corto
            }
        )
        log.info(f"📝 Memoria: K8s {resource_type}/{name} registrado")
    
    def record_helm_install(self, chart: str, release_name: str,
                          namespace: str, version: str,
                          values_customized: Dict):
        """Registra instalaciones con Helm."""
        self.memory.record_episode(
            episode_type=EpisodeType.LEARNING,
            content=f"Helm: Instalado {chart} como '{release_name}' v{version}",
            importance=6,
            tags=["kubernetes", "helm", chart, namespace],
            data={
                "chart": chart,
                "release": release_name,
                "namespace": namespace,
                "version": version,
                "values": values_customized
            }
        )
    
    # ═════════════════════════════════════════════════════════════════
    #  DOCKER INTEGRATION
    # ═════════════════════════════════════════════════════════════════
    
    def record_docker_container_run(self, image: str, 
                                    container_name: str,
                                    ports: List[str],
                                    volumes: List[str],
                                    success: bool = True):
        """Regresa containers Docker iniciados."""
        self.memory.record_episode(
            episode_type=EpisodeType.ACTION,
            content=f"Docker: Container '{container_name}' desde imagen {image}",
            importance=5,
            tags=["docker", "container", image],
            data={
                "image": image,
                "name": container_name,
                "ports": ports,
                "volumes": volumes,
                "success": success
            }
        )
    
    def record_docker_image_build(self, image_name: str,
                                  dockerfile_path: str,
                                  build_time_seconds: int,
                                  size_mb: float):
        """Registra builds de imágenes Docker."""
        self.memory.record_episode(
            episode_type=EpisodeType.LEARNING,
            content=f"Docker: Imagen {image_name} construida ({size_mb:.1f}MB en {build_time_seconds}s)",
            importance=5,
            tags=["docker", "build", image_name],
            data={
                "image": image_name,
                "dockerfile": dockerfile_path,
                "build_time": build_time_seconds,
                "size_mb": size_mb
            }
        )
    
    # ═════════════════════════════════════════════════════════════════
    #  GIT INTEGRATION
    # ═════════════════════════════════════════════════════════════════
    
    def record_git_commit(self, repo_path: str, message: str,
                        files_changed: List[str],
                        commit_hash: str,
                        lines_added: int = 0,
                        lines_removed: int = 0):
        """Registra commits de git."""
        importance = 7 if "fix" in message.lower() or "bug" in message.lower() else 5
        
        self.memory.record_episode(
            episode_type=EpisodeType.ACTION,
            content=f"Git: Commit '{message[:50]}...' en {Path(repo_path).name}",
            importance=importance,
            tags=["git", "commit", Path(repo_path).name],
            data={
                "repo": repo_path,
                "message": message,
                "hash": commit_hash,
                "files": files_changed,
                "added": lines_added,
                "removed": lines_removed
            }
        )
        log.info(f"📝 Memoria: Git commit {commit_hash[:8]} registrado")
    
    def record_git_clone(self, repo_url: str, 
                        local_path: str,
                        branch: str = "main"):
        """Registra clones de repositorios."""
        self.memory.record_episode(
            episode_type=EpisodeType.EVENT,
            content=f"Git: Clonado {repo_url} a {local_path}",
            importance=5,
            tags=["git", "clone", Path(repo_url).stem],
            data={
                "url": repo_url,
                "local_path": local_path,
                "branch": branch
            }
        )
    
    # ═════════════════════════════════════════════════════════════════
    #  SISTEMA / ARCHIVOS
    # ═════════════════════════════════════════════════════════════════
    
    def record_file_created(self, file_path: str,
                          file_type: str,
                          content_preview: str = "",
                          purpose: str = ""):
        """Registra archivos creados."""
        self.memory.record_episode(
            episode_type=EpisodeType.ACTION,
            content=f"Sistema: Creado archivo {Path(file_path).name} ({file_type})",
            importance=4,
            tags=["file", "created", file_type],
            data={
                "path": file_path,
                "type": file_type,
                "purpose": purpose,
                "preview": content_preview[:200]
            }
        )
    
    def record_tool_installed(self, tool_name: str,
                             version: str,
                             install_method: str,
                             dependencies: List[str] = None):
        """Registra herramientas instaladas."""
        self.memory.record_episode(
            episode_type=EpisodeType.LEARNING,
            content=f"Sistema: Instalado {tool_name} v{version} vía {install_method}",
            importance=6,
            tags=["tool", "installed", tool_name],
            data={
                "tool": tool_name,
                "version": version,
                "method": install_method,
                "deps": dependencies or []
            }
        )
        log.info(f"📝 Memoria: Tool {tool_name} registrada")
    
    def record_command_learned(self, command: str,
                              context: str,
                              output_pattern: str,
                              use_case: str):
        """Registra comandos útiles aprendidos."""
        self.memory.record_episode(
            episode_type=EpisodeType.LEARNING,
            content=f"Aprendido: Comando '{command[:40]}...' para {use_case}",
            importance=7,
            tags=["command", "learned", context],
            data={
                "command": command,
                "context": context,
                "pattern": output_pattern,
                "use_case": use_case
            }
        )
    
    # ═════════════════════════════════════════════════════════════════
    #  APRENDIZAJE ESPECÍFICO
    # ═════════════════════════════════════════════════════════════════
    
    def record_skill_acquired(self, skill_name: str,
                            proficiency_level: float,  # 0-1
                            source: str,
                            examples: List[str] = None):
        """Registra adquisición de habilidades."""
        self.memory.record_episode(
            episode_type=EpisodeType.LEARNING,
            content=f"Skill adquirida: {skill_name} ({proficiency_level:.0%} proficiency)",
            importance=8,
            tags=["skill", "acquired", skill_name],
            data={
                "skill": skill_name,
                "level": proficiency_level,
                "source": source,
                "examples": examples or []
            }
        )
        log.info(f"📝 Memoria: Skill {skill_name} ({proficiency_level:.0%}) registrada")
    
    def record_error_and_solution(self, error_type: str,
                                error_message: str,
                                solution: str,
                                context: str):
        """Registra errores y sus soluciones para aprendizaje."""
        self.memory.record_episode(
            episode_type=EpisodeType.LEARNING,
            content=f"Solución aprendida: {error_type} → {solution[:60]}...",
            importance=9,  # Alta importancia - errores costosos
            tags=["error", "solution", error_type, context],
            data={
                "error_type": error_type,
                "error": error_message,
                "solution": solution,
                "context": context
            }
        )
    
    # ═════════════════════════════════════════════════════════════════
    #  AUTO-DISCOVERY
    # ═════════════════════════════════════════════════════════════════
    
    def auto_discover_and_record(self):
        """Descubre automáticamente configuración actual del sistema."""
        log.info("🔍 Auto-discovering system state...")
        
        # Detectar herramientas instaladas
        tools_to_check = [
            ("docker", "docker --version"),
            ("kubectl", "kubectl version --client"),
            ("helm", "helm version --short"),
            ("git", "git --version"),
            ("python", "python3 --version"),
            ("node", "node --version"),
            ("npm", "npm --version"),
        ]
        
        for tool, cmd in tools_to_check:
            try:
                result = subprocess.run(
                    cmd.split(),
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    version = result.stdout.strip() or result.stderr.strip()
                    self.record_tool_installed(tool, version, "system")
            except Exception:
                pass  # error no crítico, continuar
        # Detectar extensiones VSCode si existe
        vscode_ext_dir = Path.home() / ".vscode" / "extensions"
        if vscode_ext_dir.exists():
            for ext_dir in vscode_ext_dir.iterdir():
                if ext_dir.is_dir():
                    ext_id = ext_dir.name
                    self.record_vscode_extension_installed(
                        ext_id, ext_id, "unknown", enabled=True
                    )
        
        log.info("✅ Auto-discovery complete")


# Singleton
_memory_bridge: Optional[MemoryBridge] = None

def get_memory_bridge() -> MemoryBridge:
    global _memory_bridge
    if _memory_bridge is None:
        _memory_bridge = MemoryBridge()
    return _memory_bridge


if __name__ == "__main__":
    print("=" * 70)
    print("  EIDOS Memory Bridge - Test")
    print("=" * 70)
    
    bridge = get_memory_bridge()
    
    # Test 1: VSCode extension
    print("\n[Test 1] Registrando extensión VSCode...")
    bridge.record_vscode_extension_installed(
        "ms-python.python", "Python", "2024.2.0"
    )
    print("  ✅ Extensión registrada")
    
    # Test 2: K8s
    print("\n[Test 2] Registrando recurso Kubernetes...")
    bridge.record_k8s_resource_created(
        "Deployment", "eidos-api", "default", "yaml..."
    )
    print("  ✅ K8s registrado")
    
    # Test 3: Docker
    print("\n[Test 3] Registrando container Docker...")
    bridge.record_docker_container_run(
        "postgres:15", "eidos-db", ["5432:5432"], ["/data:/var/lib/postgresql"]
    )
    print("  ✅ Docker registrado")
    
    # Test 4: Git
    print("\n[Test 4] Registrando commit...")
    bridge.record_git_commit(
        str(Path(__file__).resolve().parents[1]), "Fix: RAM Guardian protection", 
        ["core/ram_guardian.py"], "abc1234"
    )
    print("  ✅ Git registrado")
    
    # Test 5: Auto-discovery
    print("\n[Test 5] Auto-discovering system...")
    bridge.auto_discover_and_record()
    print("  ✅ System scanned")
    
    print("\n✅ Memory Bridge test complete")
    print("   Everything EIDOS does is now remembered forever.")
