"""
EIDOS core/skill_registry.py — Skill Registry (Fase 4 del Roadmap)
===================================================================
Según el Oráculo 2:
  1. Skill execution registry        ✅ Catálogo de skills con SKILL.md
  2. Skill sandbox tester            ✅ Test unitario antes de registrar
  3. Self-Skill Creator controlado   ✅ El agente puede escribir sus propios skills
  
  "Regla: Skill creado → test unitario automático → aprobación antes de ejecución.
   Nunca ejecución directa sin validación."
   
  PicoClaw Sipeed: cada skill tiene un SKILL.md que el agente lee antes de usarla.
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path

EIDOS_DIR    = os.path.expanduser("~/EIDOS")
SKILLS_DIR   = os.path.join(EIDOS_DIR, "skills", "plugins")
REGISTRY_DB  = os.path.expanduser("~/.eidos/skill_registry.json")


# ══════════════════════════════════════════════════════════════════════════════
#  SKILL DESCRIPTOR — Metadatos de cada skill
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SkillDescriptor:
    """Metadatos de un skill: qué hace, cómo usarla, qué acciones tiene."""
    name:        str
    path:        str
    description: str      = ""
    actions:     list[str] = field(default_factory=list)
    examples:    str       = ""
    validated:   bool      = False
    created_by:  str       = "human"   # "human" | "eidos" (self-generated)
    last_test:   float     = 0.0
    test_pass:   bool      = False
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "description": self.description,
            "actions": self.actions,
            "examples": self.examples,
            "validated": self.validated,
            "created_by": self.created_by,
            "last_test": self.last_test,
            "test_pass": self.test_pass,
        }
    
    @classmethod
    def from_dict(cls, d: dict) -> "SkillDescriptor":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
    
    def to_system_prompt_entry(self) -> str:
        """Formato conciso para inyectar en el system prompt del agente."""
        actions_str = ", ".join(self.actions) if self.actions else "run(action, **kwargs)"
        return (
            f"• {self.name}: {self.description}\n"
            f"  Acciones: {actions_str}\n"
            f"  Uso: call_skill(skill='{self.name}', action='<accion>', kwargs='{{...}}')"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  SKILL REGISTRY — El catálogo maestro
# ══════════════════════════════════════════════════════════════════════════════

class SkillRegistry:
    """
    Registro central de todos los skills de EIDOS.
    
    Funciones:
    - discover(): Escanea skills/ y los registra automáticamente
    - get(name): Obtiene el descriptor de un skill
    - call(name, action, **kwargs): Ejecuta un skill de forma segura
    - for_context(task): Devuelve solo los skills relevantes para una tarea
    - create_skill(): Self-Skill Creator controlado
    """
    
    def __init__(self) -> None:
        self._skills: dict[str, SkillDescriptor] = {}
        self._load_registry()
        self.discover()   # Auto-descubrir al inicializar
    
    def _load_registry(self) -> None:
        """Carga los metadatos guardados del registry."""
        if os.path.exists(REGISTRY_DB):
            try:
                with open(REGISTRY_DB) as f:
                    data = json.load(f)
                    for name, d in data.items():
                        self._skills[name] = SkillDescriptor.from_dict(d)
            except Exception:
                pass  # error no crítico, continuar
    def _save_registry(self) -> None:
        """Persiste el registry a disco."""
        try:
            os.makedirs(os.path.dirname(REGISTRY_DB), exist_ok=True)
            with open(REGISTRY_DB, "w") as f:
                json.dump({k: v.to_dict() for k, v in self._skills.items()}, f, indent=2)
        except Exception as e:
            print(f"\033[93m[REGISTRY] Error guardando: {e}\033[0m")
    
    def discover(self) -> int:
        """
        Escanea skills/plugins/ y registra los nuevos automáticamente.
        Lee SKILL.md si existe para obtener la descripción.
        Returns: número de skills nuevos descubiertos.
        """
        if not os.path.isdir(SKILLS_DIR):
            return 0
        
        new_count = 0
        for fname in os.listdir(SKILLS_DIR):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            
            skill_name = fname[:-3]  # Sin .py
            if skill_name in self._skills:
                continue  # Ya registrado
            
            skill_path = os.path.join(SKILLS_DIR, fname)
            
            # Leer SKILL.md si existe (PicoClaw Sipeed pattern)
            description = ""
            actions = []
            examples = ""
            
            skill_md = os.path.join(SKILLS_DIR, skill_name, "SKILL.md")
            skill_md_flat = os.path.join(SKILLS_DIR, skill_name + ".md")
            md_dirs = [skill_md, skill_md_flat,
                       os.path.join(EIDOS_DIR, "skills", skill_name + ".md")]
            
            for md_path in md_dirs:
                if os.path.exists(md_path):
                    try:
                        content = open(md_path).read()[:600]  # pyre-ignore[arg-type]
                        description = content[:200]  # pyre-ignore[arg-type]
                        examples = content
                        break
                    except Exception:
                        pass  # error no crítico, continuar
            # Extraer descripción del docstring del módulo si no hay SKILL.md
            if not description:
                try:
                    src = open(skill_path).read()[:500]  # pyre-ignore[arg-type]
                    # Buscar el primer string de documentación
                    import re
                    m = re.search(r'"""(.+?)"""', src, re.DOTALL)
                    if m:
                        description = m.group(1).strip()[:150]  # pyre-ignore[arg-type]
                except Exception:
                    pass  # error no crítico, continuar
            # Extraer acciones disponibles (funciones run() y similares)
            try:
                src = open(skill_path).read()
                import re
                # Buscar funciones def run o def <action>
                fns = re.findall(r'^def (\w+)\s*\(', src, re.MULTILINE)
                actions = [f for f in fns if not f.startswith("_")][:10]  # pyre-ignore[arg-type]
            except Exception:
                actions = ["run"]
            
            self._skills[skill_name] = SkillDescriptor(
                name=skill_name,
                path=skill_path,
                description=description or f"Skill de EIDOS: {skill_name}",
                actions=actions,
                examples=examples,
                validated=False,
                created_by="human",
            )
            new_count += 1
        
        if new_count > 0:
            print(f"\033[94m[REGISTRY]\033[0m Descubiertos {new_count} skills nuevos")
            self._save_registry()
        
        return new_count
    
    def get(self, name: str) -> SkillDescriptor | None:
        """Obtiene el descriptor de un skill por nombre."""
        return self._skills.get(name)
    
    def list_all(self) -> list[str]:
        """Lista todos los skills registrados."""
        return sorted(self._skills.keys())
    
    def call(self, skill_name: str, action: str = "run",
             **kwargs: Any) -> str:
        """
        Ejecuta un skill de forma segura (con validación y error handling).
        
        Args:
            skill_name: Nombre del skill (sin .py)
            action: Función a llamar dentro del skill
            **kwargs: Argumentos para la función
        
        Returns:
            Resultado como string.
        """
        descriptor = self._skills.get(skill_name)
        if descriptor is None:
            return f"[REGISTRY ERROR] Skill desconocido: '{skill_name}'. Disponibles: {self.list_all()[:10]}"  # pyre-ignore[arg-type]
        
        try:
            # Importar el módulo del skill
            spec = importlib.util.spec_from_file_location(
                skill_name, descriptor.path
            )
            module = importlib.util.module_from_spec(spec)
            sys.path.insert(0, EIDOS_DIR)
            spec.loader.exec_module(module)
            
            # Obtener y ejecutar la función
            fn = getattr(module, action, None) or getattr(module, "run", None)
            if fn is None:
                return f"[REGISTRY ERROR] '{skill_name}' no tiene función '{action}' ni 'run'"
            
            result = fn(**kwargs) if kwargs else fn()
            return str(result)[:3000]  # pyre-ignore[arg-type]
        
        except Exception as e:
            return f"[REGISTRY ERROR] Error ejecutando {skill_name}.{action}: {e}"
    
    def for_context(self, task: str, max_skills: int = 8) -> list[SkillDescriptor]:
        """
        Devuelve los skills más relevantes para una tarea.
        OpenClaw pattern: inyectar solo los skills relevantes en el system prompt.
        
        Algoritmo simple: coincidencia de keywords en nombre/descripción.
        """
        task_lower = task.lower()
        scored: list[tuple[int, SkillDescriptor]] = []
        
        for desc in self._skills.values():
            score = 0
            combined = (desc.name + " " + desc.description + " " + " ".join(desc.actions)).lower()
            
            for word in task_lower.split():
                if len(word) > 3 and word in combined:
                    score += 1
            
            if score > 0:
                scored.append((score, desc))
        
        scored.sort(key=lambda x: -x[0])  # pyre-ignore[arg-type]
        return [d for _, d in scored[:max_skills]]
    
    def build_system_prompt_section(self, task: str) -> str:
        """
        Construye la sección de skills para el system prompt del agente.
        Solo incluye los skills relevantes para la tarea actual.
        """
        relevant = self.for_context(task)
        if not relevant:
            # Si no hay match, mostrar los 5 más generales
            relevant = list(self._skills.values())[:5]  # pyre-ignore[arg-type]
        
        lines = ["## Skills disponibles para esta tarea:"]
        for desc in relevant:
            lines.append(desc.to_system_prompt_entry())
        
        return "\n".join(lines)
    
    # ── SELF-SKILL CREATOR ─────────────────────────────────────────────────
    
    def create_skill(self, skill_name: str, description: str,
                     code: str, auto_approve: bool = False) -> str:
        """
        Self-Skill Creator controlado (PicoClaw Sipeed pattern).
        
        Flujo: Código recibido → Sandbox test → Validación → Registro
        NUNCA ejecuta directamente sin test previo.
        
        Args:
            skill_name: Nombre del nuevo skill (sin .py)
            description: Qué hace el skill
            code: Código Python del skill
            auto_approve: Si False (default), requiere verificación manual
        
        Returns:
            Mensaje de éxito o error.
        """
        # 1. Sanitizar nombre
        skill_name = "".join(c for c in skill_name if c.isalnum() or c == "_").lower()
        if not skill_name:
            return "[SKILL CREATOR] Nombre inválido"
        
        if skill_name in self._skills and self._skills[skill_name].created_by == "human":
            return f"[SKILL CREATOR] ERROR: '{skill_name}' es un skill del sistema y no puede ser sobreescrito."
        
        # 2. Verificar que el código contiene función run()
        if "def run(" not in code and "def run (" not in code:
            return (
                "[SKILL CREATOR] El skill DEBE contener una función 'def run(action, **kwargs) -> str:' "
                "que actúe como dispatcher de acciones."
            )
        
        # 3. Test en sandbox (ejecutar en proceso aislado con timeout)
        test_result = self._sandbox_test(skill_name, code)
        if not test_result["pass"]:
            return (
                f"[SKILL CREATOR] RECHAZADO: El skill falló el test de sandbox.\n"
                f"Error: {test_result['error']}\n"
                f"No se registrará hasta pasar el test."
            )
        
        # 4. Si auto_approve=False, guardar en staging (no en skills/ todavía)
        staging_dir = os.path.join(EIDOS_DIR, "skills", "staging")
        os.makedirs(staging_dir, exist_ok=True)
        staging_path = os.path.join(staging_dir, f"{skill_name}.py")
        
        with open(staging_path, "w") as f:
            f.write(f'"""\nSelf-generated skill: {skill_name}\n{description}\n"""\n\n')
            f.write(code)
        
        if not auto_approve:
            return (
                f"[SKILL CREATOR] ✅ Skill '{skill_name}' creado y en staging: {staging_path}\n"
                f"Para activarlo: mueve a skills/plugins/ y llama a skill_registry.discover()\n"
                f"Test sandbox: PASS ({test_result.get('elapsed', '?')}s)"
            )
        
        # 5. Auto-approve: mover directamente a skills/plugins/
        final_path = os.path.join(SKILLS_DIR, f"{skill_name}.py")
        import shutil
        shutil.move(staging_path, final_path)
        
        # Crear SKILL.md automáticamente
        skill_md_path = os.path.join(SKILLS_DIR, f"{skill_name}.md")
        with open(skill_md_path, "w") as f:
            f.write(f"# {skill_name}\n\n{description}\n\nGenerado por EIDOS Self-Skill Creator.\n")
        
        # Registrar
        self._skills[skill_name] = SkillDescriptor(
            name=skill_name,
            path=final_path,
            description=description,
            actions=["run"],
            validated=True,
            created_by="eidos",
            last_test=time.time(),
            test_pass=True,
        )
        self._save_registry()
        self.discover()
        
        return f"[SKILL CREATOR] ✅ Skill '{skill_name}' registrado y activo en skills/plugins/"
    
    def _sandbox_test(self, skill_name: str, code: str) -> dict:
        """Ejecuta el código en un subproceso aislado con timeout."""
        test_code = (
            f"{code}\n\n"
            f"try:\n"
            f"    result = run(action='help')\n"
            f"    print('TEST_PASS:' + str(result)[:100])\n"  # pyre-ignore[arg-type]
            f"except Exception as e:\n"
            f"    print('TEST_FAIL:' + str(e))\n"
        )
        
        start = time.time()
        try:
            venv_python = os.path.join(EIDOS_DIR, "venv_eidos", "bin", "python")
            if not os.path.exists(venv_python):
                venv_python = sys.executable
            
            r = subprocess.run(
                [venv_python, "-c", test_code],
                capture_output=True, text=True, timeout=10,
                env={**os.environ, "PYTHONPATH": EIDOS_DIR}
            )
            elapsed = round(time.time() - start, 1)
            
            output = r.stdout + r.stderr
            if "TEST_PASS:" in output:
                return {"pass": True, "elapsed": f"{elapsed}s", "output": output}
            else:
                return {"pass": False, "elapsed": f"{elapsed}s", "error": output[:300]}  # pyre-ignore[arg-type]
        
        except subprocess.TimeoutExpired:
            return {"pass": False, "error": "Timeout: el skill tardó más de 10s en el test"}
        except Exception as e:
            return {"pass": False, "error": str(e)}


# Singleton global
skill_registry = SkillRegistry()


# ── Test rápido ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Test Skill Registry ===")
    reg = SkillRegistry()
    
    all_skills = reg.list_all()
    print(f"Skills descubiertos: {len(all_skills)}")
    print(f"Primeros 10: {all_skills[:10]}")  # pyre-ignore[arg-type]
    
    # Test relevancia contextual
    relevant = reg.for_context("escanear la red con nmap", max_skills=5)
    print(f"\nRelevantes para 'escanear red': {[r.name for r in relevant]}")
    
    relevant2 = reg.for_context("tomar screenshot y analizar", max_skills=5)
    print(f"Relevantes para 'screenshot': {[r.name for r in relevant2]}")
    
    # Test system prompt entry
    if relevant:
        print(f"\nEjemplo entry system prompt:")
        print(relevant[0].to_system_prompt_entry())  # pyre-ignore[arg-type]
    
    print("\n✅ Skill Registry operativo")
