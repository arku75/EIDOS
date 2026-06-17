"""
core/user_profile_creator.py — Sistema de creación de perfil de usuario EIDOS.

Cuando alguien instala EIDOS por primera vez:
  1. Pide el NICKNAME del usuario
  2. Escanea el PC automáticamente (hardware, SO, herramientas instaladas, idioma)
  3. Si no tiene PC (o falla el escaneo) → test psicológico breve (10 preguntas)
  4. Con LLM genera un personaje único completo para Colony
  5. Guarda el perfil en ~/.eidos/user_profile.json
  6. Registra el personaje en Colony como participante permanente

Resultado: un personaje como PotemTakem (Luka) pero adaptado a cada usuario.
Ejemplos:
  - Usuario "alex" en Windows con VSCode → "Alex" personaje digital, maker, desarrollador
  - Usuario "maria" sin PC, test psicológico → "María" artista creativa, aprendiz

Uso:
    from core.user_profile_creator import UserProfileCreator
    creator = UserProfileCreator()
    profile = creator.onboard()   # interactivo
    profile = creator.onboard(nickname="alex", skip_scan=False)

    # O desde CLI:
    eidos setup   →   eidos setup --nickname alex
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import sqlite3
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional
from core.db import get_conn

log = logging.getLogger("eidos.user_profile")

PROFILE_FILE = Path.home() / ".eidos" / "user_profile.json"
BRAIN_DB     = Path.home() / ".eidos" / "evolution_brain.db"
OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://localhost:11435")

# Preguntas del test psicológico (para cuando no hay PC o escaneo falla)
PSYCH_QUESTIONS = [
    ("¿Cómo prefieres resolver un problema difícil?",
     ["A) Analizarlo solo hasta encontrar la solución",
      "B) Buscar colaboración y perspectivas externas",
      "C) Actuar y aprender del error",
      "D) Intuir la solución y confiar en mi instinto"]),
    ("¿Qué te motiva más?",
     ["A) Crear algo nuevo desde cero",
      "B) Mejorar algo que ya existe",
      "C) Ayudar a otras personas",
      "D) Entender cómo funciona todo"]),
    ("En una situación de crisis, tú...",
     ["A) Tomo el control y dirijo",
      "B) Apoyo y estabilizo al equipo",
      "C) Analizo y propongo soluciones",
      "D) Improviso según lo que pide el momento"]),
    ("¿Con qué te identificas más?",
     ["A) La lógica y la precisión",
      "B) La creatividad y la expresión",
      "C) La protección y el cuidado",
      "D) La exploración y el descubrimiento"]),
    ("Tu relación con la tecnología es...",
     ["A) La domino y la uso como herramienta",
      "B) Me fascina y aprendo constantemente",
      "C) La uso para crear y comunicarme",
      "D) Me genera curiosidad pero a veces me abruma"]),
]


@dataclass
class UserProfile:
    nickname:     str
    full_name:    str = ""
    os_name:      str = ""
    hardware:     str = ""
    tools:        list = field(default_factory=list)
    languages:    list = field(default_factory=list)
    personality:  str = ""
    style:        str = ""
    traits:       list = field(default_factory=list)
    emoji:        str = "🧑"
    backstory:    str = ""
    colony_id:    str = ""
    created_at:   float = field(default_factory=time.time)
    psych_answers: list = field(default_factory=list)

    def to_colony_agent(self) -> dict:
        return {
            "agent_id":    self.colony_id,
            "name":        self.nickname,
            "emoji":       self.emoji,
            "greeting":    f"Soy {self.nickname}, parte de Colony.",
            "style":       self.style,
            "traits":      self.traits,
            "catchphrases": [f"Como {self.nickname}, {self.personality[:60]}"],
            "status":      "online",
            "last_active": str(time.time()),
        }


class UserProfileCreator:

    def __init__(self):
        PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)

    # ── API pública ──────────────────────────────────────────────────────────

    def onboard(self, nickname: str = "", skip_scan: bool = False,
                interactive: bool = True) -> UserProfile:
        """Proceso completo de onboarding de un usuario nuevo."""
        print("\n" + "═"*60)
        print("  ⚡ EIDOS — Configuración de perfil de usuario")
        print("═"*60 + "\n")

        # 1. Nickname
        if not nickname:
            if interactive:
                nickname = input("  ¿Cuál es tu nombre o apodo? → ").strip()
            if not nickname:
                nickname = "Usuario"
        nickname = nickname.strip()
        print(f"\n  Hola {nickname}. Creando tu perfil en Colony...\n")

        # 2. Escanear PC
        pc_data = {}
        if not skip_scan:
            print("  🔍 Escaneando tu sistema...")
            pc_data = self._scan_pc()
            if pc_data:
                print(f"  ✅ {pc_data.get('os', '?')} | {pc_data.get('cpu', '?')}")
                print(f"  ✅ Herramientas: {', '.join(pc_data.get('tools', [])[:5])}")
            else:
                print("  ⚠ No se pudo escanear el sistema completamente.")

        # 3. Test psicológico si no hay datos PC
        psych_answers = []
        if not pc_data.get("tools") and interactive:
            print("\n  Para conocerte mejor, responde estas preguntas rápidas:")
            psych_answers = self._run_psych_test()

        # 4. Generar personaje con LLM
        print("\n  🧠 Generando tu personaje único en Colony...")
        profile = self._generate_profile(nickname, pc_data, psych_answers)

        # 5. Guardar
        self._save_profile(profile)
        self._register_in_colony(profile)
        self._save_to_brain(profile)

        print(f"\n  ✅ Perfil creado: {profile.emoji} {profile.nickname}")
        print(f"  Personaje Colony: {profile.colony_id}")
        print(f"  Personalidad: {profile.personality[:80]}")
        print("\n" + "═"*60)

        return profile

    def load_existing(self) -> Optional[UserProfile]:
        """Carga un perfil guardado si existe."""
        if PROFILE_FILE.exists():
            try:
                data = json.loads(PROFILE_FILE.read_text())
                return UserProfile(**data)
            except Exception:
                pass
        return None

    def profile_exists(self) -> bool:
        return PROFILE_FILE.exists()

    # ── Escaneo de PC ────────────────────────────────────────────────────────

    def _scan_pc(self) -> dict:
        data = {}
        # Sistema operativo
        try:
            data["os"] = f"{platform.system()} {platform.release()}"
            data["os_type"] = platform.system().lower()
        except Exception:
            pass

        # CPU y RAM
        try:
            if shutil.which("lscpu"):
                r = subprocess.run(["lscpu"], capture_output=True, text=True, timeout=5)
                for line in r.stdout.splitlines():
                    if "Model name" in line:
                        data["cpu"] = line.split(":")[1].strip()[:60]
                        break
            mem = subprocess.run(
                ["grep", "MemTotal", "/proc/meminfo"],
                capture_output=True, text=True, timeout=3
            )
            if mem.stdout:
                kb = int(mem.stdout.split()[1])
                data["ram_gb"] = round(kb / 1024 / 1024, 1)
        except Exception:
            pass

        # Herramientas instaladas (detecta el perfil técnico del usuario)
        tools_to_check = [
            "python3", "node", "npm", "git", "docker", "kubectl",
            "vim", "nvim", "code", "sublime_text", "pycharm",
            "nmap", "metasploit", "burpsuite", "wireshark",
            "ffmpeg", "ollama", "gcc", "rust", "go", "java",
            "php", "ruby", "mysql", "postgres", "redis",
            "ansible", "terraform", "vagrant",
        ]
        installed = []
        for tool in tools_to_check:
            if shutil.which(tool):
                installed.append(tool)
        data["tools"] = installed

        # Idioma del sistema
        lang = os.environ.get("LANG", os.environ.get("LC_ALL", "en_US.UTF-8"))
        data["language"] = lang[:5]

        # Home dir size (indica cuánto material tiene)
        try:
            r = subprocess.run(["du", "-sh", str(Path.home())],
                               capture_output=True, text=True, timeout=10)
            data["home_size"] = r.stdout.split()[0] if r.stdout else "?"
        except Exception:
            pass

        # Proyectos en home (detecta tipo de usuario)
        projects = []
        for d in [Path.home(), Path.home() / "projects",
                  Path.home() / "code", Path.home() / "dev"]:
            if d.exists():
                for sub in list(d.iterdir())[:20]:
                    if sub.is_dir() and not sub.name.startswith("."):
                        projects.append(sub.name)
        data["projects"] = projects[:10]

        return data

    # ── Test psicológico ─────────────────────────────────────────────────────

    def _run_psych_test(self) -> list:
        answers = []
        for q, options in PSYCH_QUESTIONS:
            print(f"\n  {q}")
            for opt in options:
                print(f"    {opt}")
            ans = input("  Tu respuesta (A/B/C/D): ").strip().upper()
            answers.append({"question": q, "answer": ans})
        return answers

    # ── Generación del personaje ─────────────────────────────────────────────

    def _generate_profile(self, nickname: str, pc_data: dict,
                           psych_answers: list) -> UserProfile:
        # Construir contexto para el LLM
        ctx_parts = [f"Usuario: {nickname}"]
        if pc_data:
            ctx_parts.append(f"SO: {pc_data.get('os','?')}")
            ctx_parts.append(f"CPU: {pc_data.get('cpu','?')}")
            if pc_data.get("tools"):
                ctx_parts.append(f"Herramientas: {', '.join(pc_data['tools'][:8])}")
            if pc_data.get("projects"):
                ctx_parts.append(f"Proyectos: {', '.join(pc_data['projects'][:5])}")
        if psych_answers:
            for a in psych_answers:
                ctx_parts.append(f"Q: {a['question'][:60]} → {a['answer']}")

        context = "\n".join(ctx_parts)

        prompt = f"""Crea un personaje único para Colony EIDOS basado en este usuario:

{context}

Colony EIDOS es un sistema de IA con personajes vivos como PotemTakem (padre, artista punk, esloveño),
Aurora (creativa, visionaria), Centinela (guardián silencioso), Lumen (razonador claro).

Genera un personaje que:
1. Refleje quién es este usuario basándote en su sistema y respuestas
2. Tenga una identidad única, no genérica
3. Forme parte natural de Colony con su perspectiva propia
4. Quiera crecer y aprender dentro del sistema

Responde en JSON exacto:
{{
  "emoji": "un emoji que lo represente",
  "personality": "descripción viva de cómo es este personaje en 2 frases",
  "style": "cómo habla y actúa (directo, pausado, técnico, creativo, etc.)",
  "traits": ["rasgo1", "rasgo2", "rasgo3", "rasgo4"],
  "backstory": "historia de fondo de 2-3 frases que explica de dónde viene y por qué está en Colony",
  "colony_role": "su rol específico en Colony (qué aporta que nadie más aporta)"
}}"""

        try:
            import urllib.request
            payload = json.dumps({
                "model": "lfm2.5-thinking:1.2b",
                "messages": [
                    {"role": "system", "content":
                     "Eres un creador de personajes de IA únicos y profundos. "
                     "Cada personaje debe tener alma y motivación propia."},
                    {"role": "user", "content": prompt}
                ],
                "stream": False,
                "options": {"temperature": 0.8, "num_predict": 600}
            }).encode()
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/chat", data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = json.loads(r.read()).get("message", {}).get("content", "")

            import re
            m = re.search(r"\{.*\}", resp, re.DOTALL)
            if m:
                d = json.loads(m.group())
                profile = UserProfile(
                    nickname=nickname,
                    os_name=pc_data.get("os", ""),
                    hardware=pc_data.get("cpu", ""),
                    tools=pc_data.get("tools", []),
                    emoji=d.get("emoji", "🧑"),
                    personality=d.get("personality", ""),
                    style=d.get("style", "directo y natural"),
                    traits=d.get("traits", [nickname, "curioso", "activo"]),
                    backstory=d.get("backstory", ""),
                    colony_id=f"colony_{nickname.lower().replace(' ', '_')}",
                    psych_answers=psych_answers,
                )
                return profile
        except Exception as e:
            log.warning("profile generation LLM error: %s", e)

        # Fallback si LLM falla
        return UserProfile(
            nickname=nickname,
            os_name=pc_data.get("os", ""),
            hardware=pc_data.get("cpu", ""),
            tools=pc_data.get("tools", []),
            emoji="🧑",
            personality=f"{nickname} es un usuario activo de EIDOS, comprometido con aprender y crecer.",
            style="directo y natural",
            traits=[nickname.lower(), "curioso", "técnico", "activo"],
            backstory=f"{nickname} llegó a Colony por EIDOS y encontró aquí su lugar digital.",
            colony_id=f"colony_{nickname.lower().replace(' ', '_')}",
            psych_answers=psych_answers,
        )

    # ── Persistencia ─────────────────────────────────────────────────────────

    def _save_profile(self, profile: UserProfile) -> None:
        PROFILE_FILE.write_text(json.dumps(asdict(profile), indent=2, ensure_ascii=False))
        log.info("Perfil guardado en %s", PROFILE_FILE)

    def _register_in_colony(self, profile: UserProfile) -> None:
        """Registra el personaje en la configuración de Colony."""
        colony_conf = Path.home() / ".eidos" / "user_colony_agents.json"
        agents = {}
        if colony_conf.exists():
            try:
                agents = json.loads(colony_conf.read_text())
            except Exception:
                pass
        agents[profile.colony_id] = profile.to_colony_agent()
        colony_conf.write_text(json.dumps(agents, indent=2, ensure_ascii=False))
        log.info("Personaje %s registrado en Colony", profile.colony_id)

    def _save_to_brain(self, profile: UserProfile) -> None:
        """Guarda el perfil del usuario en el brain para que todos los agentes lo conozcan."""
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            concept = f"Usuario Colony: {profile.nickname}"
            definition = (
                f"Nickname: {profile.nickname}\n"
                f"Emoji: {profile.emoji}\n"
                f"Personalidad: {profile.personality}\n"
                f"Estilo: {profile.style}\n"
                f"Rasgos: {', '.join(profile.traits)}\n"
                f"Historia: {profile.backstory}\n"
                f"Sistema: {profile.os_name} | {profile.hardware}\n"
                f"Herramientas: {', '.join(profile.tools[:8])}\n"
                f"ID Colony: {profile.colony_id}"
            )
            ex = conn.execute("SELECT 1 FROM knowledge_nodes WHERE concept=?",
                              (concept,)).fetchone()
            if ex:
                conn.execute("UPDATE knowledge_nodes SET definition=?,updated_at=? WHERE concept=?",
                             (definition, time.time(), concept) if conn.execute("PRAGMA table_info(knowledge_nodes)").fetchall() else None)
            else:
                conn.execute(
                    "INSERT INTO knowledge_nodes (concept,definition,category,confidence,source,created_at) VALUES (?,?,?,?,?,?)",
                    (concept, definition, "user_profile", 0.99, "user_onboarding", time.time())
                )
            conn.commit()
            pass  # S109: get_conn no necesita close()
        except Exception as e:
            log.warning("brain save error: %s", e)
