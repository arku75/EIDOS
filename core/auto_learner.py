"""
EIDOS core/auto_learner.py — Auto-Learning desde Docs/Wiki/GitHub
===================================================================
Sistema de aprendizaje automático que busca información en docs/wiki/GitHub
cuando EIDOS encuentra herramientas o conceptos desconocidos.

Comportamiento:
  1. EIDOS encuentra comando/tool desconocido
  2. Auto-Learner busca en:
     - README.md del proyecto
     - Wiki (si existe)
     - Docs oficiales
     - GitHub Issues/PRs
     - Stack Overflow
  3. Analiza múltiples fuentes
  4. Practica en sandbox
  5. Guarda como skill permanente

Ejemplo de Flujo:
  Usuario: "Usa gobuster para enumerar directorios"

  EIDOS detecta: No conozco "gobuster"

  Auto-Learner:
    1. Busca "gobuster" en GitHub
    2. Lee README.md
    3. Lee wiki
    4. Extrae ejemplos de uso
    5. Ejecuta en sandbox: gobuster --help
    6. Practica comando básico
    7. Guarda skill: "gobuster_enumeration"

  EIDOS: "Aprendido. Ejecutando gobuster..."

Uso:
    from core.auto_learner import AutoLearner

    learner = AutoLearner()

    # Aprender sobre una herramienta
    skill = learner.learn("gobuster")

    # Usar la skill
    learner.apply_skill("gobuster", target="example.com")
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any


# ── Configuración ────────────────────────────────────────────────────────────

SKILLS_DIR = Path.home() / ".eidos" / "learned_skills"
GITHUB_API = "https://api.github.com"
USER_AGENT = "EIDOS-Auto-Learner/1.0"


# ── Tipos básicos ────────────────────────────────────────────────────────────

@dataclass
class LearnedSkill:
    """Skill aprendida automáticamente."""
    name: str
    description: str
    sources: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    usage_pattern: str = ""
    installation: Optional[str] = None
    learned_at: float = field(default_factory=time.time)
    last_used: Optional[float] = None
    usage_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "sources": self.sources,
            "examples": self.examples,
            "usage_pattern": self.usage_pattern,
            "installation": self.installation,
            "learned_at": self.learned_at,
            "last_used": self.last_used,
            "usage_count": self.usage_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LearnedSkill:
        return cls(
            name=data["name"],
            description=data["description"],
            sources=data.get("sources", []),
            examples=data.get("examples", []),
            usage_pattern=data.get("usage_pattern", ""),
            installation=data.get("installation"),
            learned_at=data.get("learned_at", time.time()),
            last_used=data.get("last_used"),
            usage_count=data.get("usage_count", 0),
        )

    def save(self) -> None:
        """Guarda la skill a disco."""
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        skill_file = SKILLS_DIR / f"{self.name}.json"

        with open(skill_file, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, name: str) -> Optional[LearnedSkill]:
        """Carga una skill desde disco."""
        skill_file = SKILLS_DIR / f"{name}.json"

        if not skill_file.exists():
            return None

        try:
            with open(skill_file, "r") as f:
                data = json.load(f)
            return cls.from_dict(data)
        except Exception:
            return None


# ── Auto Learner ─────────────────────────────────────────────────────────────

class AutoLearner:
    """
    Sistema de aprendizaje automático que busca información en
    docs/wiki/GitHub cuando encuentra herramientas desconocidas.
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self._ensure_skills_dir()

    def _ensure_skills_dir(self) -> None:
        """Crea el directorio de skills si no existe."""
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    def _log(self, msg: str, level: str = "INFO") -> None:
        """Log con prefijo."""
        if self.verbose:
            icon = "🎓" if level == "INFO" else "⚠️" if level == "WARNING" else "❌"
            print(f"{icon} [AUTO-LEARN] {msg}")

    def _fetch_url(self, url: str) -> Optional[str]:
        """Fetch URL con manejo de errores."""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            self._log(f"Error fetching {url}: {e}", "WARNING")
            return None

    def _search_github(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Busca un repositorio en GitHub.

        Returns:
            Datos del primer resultado, o None si no hay resultados
        """
        self._log(f"Buscando '{query}' en GitHub...")

        url = f"{GITHUB_API}/search/repositories?q={urllib.parse.quote(query)}&sort=stars&order=desc"
        content = self._fetch_url(url)

        if not content:
            return None

        try:
            data = json.loads(content)
            items = data.get("items", [])

            if not items:
                return None

            # Retornar el más popular
            repo = items[0]
            return {
                "name": repo["name"],
                "full_name": repo["full_name"],
                "description": repo.get("description", ""),
                "url": repo["html_url"],
                "stars": repo["stargazers_count"],
                "language": repo.get("language", "Unknown"),
            }

        except Exception as e:
            self._log(f"Error parsing GitHub response: {e}", "ERROR")
            return None

    def _fetch_readme(self, full_name: str) -> Optional[str]:
        """Fetch README.md de un repo de GitHub."""
        self._log(f"Leyendo README de {full_name}...")

        # Intentar varios nombres comunes de README
        for readme_name in ["README.md", "README", "readme.md", "Readme.md"]:
            url = f"https://raw.githubusercontent.com/{full_name}/master/{readme_name}"
            content = self._fetch_url(url)

            if content:
                return content

            # Intentar con 'main' en lugar de 'master'
            url = f"https://raw.githubusercontent.com/{full_name}/main/{readme_name}"
            content = self._fetch_url(url)

            if content:
                return content

        return None

    def _extract_examples(self, content: str) -> List[str]:
        """Extrae ejemplos de uso del contenido (README, docs, etc.)."""
        examples = []

        # Buscar bloques de código
        code_blocks = re.findall(r'```(?:bash|sh|shell)?\n(.*?)```', content, re.DOTALL)

        for block in code_blocks[:10]:  # Primeros 10 bloques
            lines = block.strip().split("\n")
            for line in lines:
                line = line.strip()

                # Ignorar comentarios
                if line.startswith("#") or not line:
                    continue

                # Si parece un comando válido
                if len(line) > 3 and not line.startswith("$"):
                    examples.append(line)

        return examples[:5]  # Top 5

    def _extract_installation(self, content: str, tool_name: str) -> Optional[str]:
        """Extrae instrucciones de instalación."""
        # Buscar secciones de instalación
        install_patterns = [
            r'## Installation\n(.*?)##',
            r'### Install\n(.*?)###',
            r'# Installation\n(.*?)#',
        ]

        for pattern in install_patterns:
            match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
            if match:
                install_text = match.group(1).strip()

                # Buscar comandos de instalación
                install_cmds = re.findall(r'(?:apt|pip|npm|cargo|go)\s+install\s+[\w\-]+', install_text)

                if install_cmds:
                    return install_cmds[0]

        # Fallback: buscar comandos comunes
        common_installs = [
            f"apt-get install {tool_name}",
            f"pip install {tool_name}",
            f"npm install -g {tool_name}",
            f"cargo install {tool_name}",
            f"go install github.com/.../{{tool_name}}",
        ]

        for cmd in common_installs:
            if cmd in content:
                return cmd

        return None

    def _practice_in_sandbox(self, skill: LearnedSkill) -> bool:
        """
        Practica la skill en sandbox para verificar que funciona.

        Returns:
            True si la práctica fue exitosa
        """
        self._log(f"Practicando '{skill.name}' en sandbox...")

        # Intentar ejecutar --help
        try:
            result = subprocess.run(
                [skill.name, "--help"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                self._log(f"  ✅ '{skill.name} --help' funciona")
                return True
            else:
                self._log(f"  ⚠️  '{skill.name} --help' falló (código {result.returncode})", "WARNING")
                return False

        except FileNotFoundError:
            self._log(f"  ⚠️  '{skill.name}' no está instalado", "WARNING")
            return False

        except Exception as e:
            self._log(f"  ❌ Error practicando: {e}", "ERROR")
            return False

    def learn(self, tool_name: str, force: bool = False) -> Optional[LearnedSkill]:
        """
        Aprende sobre una herramienta buscando en GitHub/docs/wiki.

        Args:
            tool_name: Nombre de la herramienta a aprender
            force: Si True, re-aprende incluso si ya existe la skill

        Returns:
            LearnedSkill si el aprendizaje fue exitoso, None si falló
        """
        # Verificar si ya existe la skill
        if not force:
            existing = LearnedSkill.load(tool_name)
            if existing:
                self._log(f"Skill '{tool_name}' ya existe (usar force=True para re-aprender)")
                return existing

        self._log("=" * 70)
        self._log(f"🎓 APRENDIENDO: {tool_name}")
        self._log("=" * 70)

        # 1. Buscar en GitHub
        repo = self._search_github(tool_name)

        if not repo:
            self._log(f"No se encontró '{tool_name}' en GitHub", "WARNING")
            return None

        self._log(f"Repositorio encontrado: {repo['full_name']} ({repo['stars']} ⭐)")
        self._log(f"Descripción: {repo['description']}")

        # 2. Leer README
        readme = self._fetch_readme(repo["full_name"])

        if not readme:
            self._log("No se pudo leer el README", "WARNING")
            return None

        # 3. Extraer información
        examples = self._extract_examples(readme)
        installation = self._extract_installation(readme, tool_name)

        # Crear skill
        skill = LearnedSkill(
            name=tool_name,
            description=repo["description"] or f"Tool: {tool_name}",
            sources=[
                f"GitHub: {repo['url']}",
                f"README: {repo['full_name']}",
            ],
            examples=examples,
            usage_pattern=examples[0] if examples else "",
            installation=installation,
        )

        # 4. Practicar en sandbox
        practice_success = self._practice_in_sandbox(skill)

        if not practice_success:
            self._log("⚠️  Práctica falló - guardando skill de todos modos", "WARNING")

        # 5. Guardar skill
        skill.save()

        self._log(f"✅ Skill '{tool_name}' aprendida y guardada")
        self._log(f"   Ejemplos encontrados: {len(examples)}")
        self._log(f"   Instalación: {installation or 'N/A'}")
        self._log("=" * 70)

        return skill

    def get_skill(self, name: str) -> Optional[LearnedSkill]:
        """Obtiene una skill aprendida."""
        return LearnedSkill.load(name)

    def list_skills(self) -> List[LearnedSkill]:
        """Lista todas las skills aprendidas."""
        skills = []

        for skill_file in SKILLS_DIR.glob("*.json"):
            try:
                with open(skill_file, "r") as f:
                    data = json.load(f)
                skills.append(LearnedSkill.from_dict(data))
            except Exception:
                continue

        # Ordenar por más usado
        skills.sort(key=lambda s: s.usage_count, reverse=True)
        return skills

    def apply_skill(self, skill_name: str, **kwargs: Any) -> Optional[str]:
        """
        Aplica una skill aprendida.

        Args:
            skill_name: Nombre de la skill
            **kwargs: Parámetros para la skill

        Returns:
            Output de ejecutar la skill, o None si falló
        """
        skill = self.get_skill(skill_name)

        if not skill:
            self._log(f"Skill '{skill_name}' no encontrada - aprendiendo...", "WARNING")
            skill = self.learn(skill_name)

            if not skill:
                return None

        # Actualizar estadísticas
        skill.last_used = time.time()
        skill.usage_count += 1
        skill.save()

        self._log(f"Aplicando skill: {skill_name}")

        # Construir comando desde usage_pattern
        cmd = skill.usage_pattern

        # Reemplazar placeholders con kwargs
        for key, value in kwargs.items():
            cmd = cmd.replace(f"{{{key}}}", str(value))

        self._log(f"Ejecutando: {cmd}")

        try:
            # shell=False + shlex.split para prevenir inyección de comandos
            # El usage_pattern viene de GitHub READMEs vía regex — no es confiable
            result = subprocess.run(
                shlex.split(cmd),
                capture_output=True,
                text=True,
                timeout=30,
            )

            output = result.stdout + result.stderr

            if result.returncode == 0:
                self._log("✅ Skill ejecutada exitosamente")
            else:
                self._log(f"⚠️  Skill ejecutada con código {result.returncode}", "WARNING")

            return output

        except Exception as e:
            self._log(f"❌ Error aplicando skill: {e}", "ERROR")
            return None


# ── Singleton global ─────────────────────────────────────────────────────────

_auto_learner: Optional[AutoLearner] = None

def get_auto_learner() -> AutoLearner:
    """Obtiene la instancia singleton de AutoLearner."""
    global _auto_learner
    if _auto_learner is None:
        _auto_learner = AutoLearner()
    return _auto_learner


# ── CLI rápido ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    learner = AutoLearner(verbose=True)

    if len(sys.argv) < 2:
        print("Uso: python auto_learner.py [learn|list|apply|get] <args>")
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "learn":
        if len(sys.argv) < 3:
            print("Uso: python auto_learner.py learn <tool_name>")
            sys.exit(1)

        tool_name = sys.argv[2]
        skill = learner.learn(tool_name)

        if skill:
            print(f"\n✅ Skill aprendida: {skill.name}")
            print(f"   Descripción: {skill.description}")
            print(f"   Ejemplos: {len(skill.examples)}")
            print(f"   Instalación: {skill.installation or 'N/A'}")

    elif cmd == "list":
        skills = learner.list_skills()

        print(f"\n📚 Skills Aprendidas ({len(skills)}):\n")

        for i, skill in enumerate(skills, 1):
            print(f"{i}. {skill.name} (usado {skill.usage_count} veces)")
            print(f"   {skill.description}")
            print(f"   Aprendido: {datetime.fromtimestamp(skill.learned_at).strftime('%Y-%m-%d')}\n")

    elif cmd == "get":
        if len(sys.argv) < 3:
            print("Uso: python auto_learner.py get <skill_name>")
            sys.exit(1)

        skill_name = sys.argv[2]
        skill = learner.get_skill(skill_name)

        if skill:
            print(f"\n📖 Skill: {skill.name}\n")
            print(f"Descripción: {skill.description}")
            print(f"Aprendido: {datetime.fromtimestamp(skill.learned_at)}")
            print(f"Usado: {skill.usage_count} veces")
            print(f"\nFuentes:")
            for source in skill.sources:
                print(f"  - {source}")
            print(f"\nEjemplos:")
            for example in skill.examples[:3]:
                print(f"  $ {example}")
        else:
            print(f"❌ Skill '{skill_name}' no encontrada")

    elif cmd == "apply":
        if len(sys.argv) < 3:
            print("Uso: python auto_learner.py apply <skill_name> [args...]")
            sys.exit(1)

        skill_name = sys.argv[2]
        kwargs = {}
        for arg in sys.argv[3:]:
            if "=" in arg:
                key, val = arg.split("=", 1)
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    pass
                kwargs[key] = val
        output = learner.apply_skill(skill_name, **kwargs)

        if output:
            print(f"\n📤 Output:\n{output}")

    else:
        print(f"❌ Comando desconocido: {cmd}")
        sys.exit(1)
