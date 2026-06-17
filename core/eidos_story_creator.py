"""
EIDOS Story Creator - Sistema de Narrativas y Personajes Autónomos
==================================================================

Sistema de creación de historias, mundos y personajes autónomos.
EIDOS puede crear, evolucionar y mantener narrativas vivas sin intervención humana.

Features:
- Generación procedural de mundos (world-building)
- Personajes autónomos con personalidades, objetivos y relaciones
- Sistema de narrativa emergente (historias que se desarrollan solas)
- Memoria de personajes y evolución del mundo
- Exportación a múltiples formatos (novela, guión, podcast, juego)

Uso:
    from core.eidos_story_creator import StoryEngine, get_story_engine
    engine = get_story_engine()
    
    # Crear mundo
    world = engine.create_world("Cyberpunk Madrid", genre="cyberpunk")
    
    # Crear personaje autónomo
    character = engine.create_character(
        name="Luna",
        archetype="hacker",
        world=world
    )
    
    # Simular tiempo: personajes actúan autónomamente
    engine.simulate_day()
"""

import json
import logging
import os
import random
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from core.db import get_conn

log = logging.getLogger("eidos.story_creator")

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

DB_PATH = Path.home() / ".eidos" / "story_universe.db"
STORIES_DIR = Path.home() / ".eidos" / "stories"

GENRE_TEMPLATES = {
    "cyberpunk": {
        "time_period": "2080-2150",
        "themes": ["technology", "corporations", "identity", "rebellion"],
        "settings": ["megacity", "underground", "space station", "virtual reality"],
        "archetypes": ["hacker", "corporate agent", "street samurai", "netrunner", "fixer"],
        "conflicts": ["corporate war", "AI uprising", "data heist", "identity crisis"],
    },
    "fantasy": {
        "time_period": "medieval",
        "themes": ["magic", "destiny", "good vs evil", "coming of age"],
        "settings": ["kingdom", "dark forest", "ancient ruins", "magic academy"],
        "archetypes": ["wizard", "warrior", "rogue", "paladin", "druid", "bard"],
        "conflicts": ["dark lord", "ancient curse", "prophecy", "dragon awakening"],
    },
    "scifi": {
        "time_period": "2100-2500",
        "themes": ["exploration", "first contact", "survival", "evolution"],
        "settings": ["spaceship", "alien planet", "dyson sphere", "generation ship"],
        "archetypes": ["captain", "scientist", "engineer", "xenobiologist", "pilot"],
        "conflicts": ["alien threat", "resource shortage", "time paradox", "mutation"],
    },
    "horror": {
        "time_period": "present",
        "themes": ["fear", "unknown", "madness", "survival"],
        "settings": ["abandoned hospital", "small town", "ancient mansion", "deep woods"],
        "archetypes": ["survivor", "investigator", "skeptic", "medium", "victim"],
        "conflicts": ["supernatural entity", "cult", "ancient evil", "psychological terror"],
    },
    "noir": {
        "time_period": "1940-1960",
        "themes": ["corruption", "betrayal", "moral ambiguity", "obsession"],
        "settings": ["rainy city", "night club", "private office", "docks"],
        "archetypes": ["detective", "femme fatale", "mob boss", "corrupt cop", "informant"],
        "conflicts": ["murder mystery", "missing person", "conspiracy", "blackmail"],
    },
    "solarpunk": {
        "time_period": "2050-2100",
        "themes": ["sustainability", "community", "hope", "nature"],
        "settings": ["green city", "vertical farm", "coastal community", "orbital garden"],
        "archetypes": ["engineer", "botanist", "community leader", "artist", "diplomat"],
        "conflicts": ["ecological disaster recovery", "resource sharing", "old vs new"],
    },
}

PERSONALITY_TRAITS = [
    "brave", "cautious", "curious", "creative", "analytical", "emotional",
    "ambitious", "lazy", "honest", "deceptive", "loyal", "treacherous",
    "optimistic", "pessimistic", "aggressive", "peaceful", "charismatic", "shy",
    "intelligent", "simple", "generous", "greedy", "disciplined", "chaotic",
]

MOTIVATIONS = [
    "wealth", "power", "knowledge", "revenge", "love", "freedom",
    "recognition", "redemption", "survival", "legacy", "justice", "order",
    "chaos", "protect_family", "protect_community", "self_discovery", "immortality",
]

RELATIONSHIP_TYPES = [
    "friend", "enemy", "lover", "family", "mentor", "student",
    "rival", "ally", "debtor", "creditor", "master", "servant",
]

# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

class StoryStatus(str, Enum):
    ACTIVE = "active"           # Historia en desarrollo
    COMPLETED = "completed"     # Historia terminada
    ABANDONED = "abandoned"   # Historia descartada
    PAUSED = "paused"         # Pausada temporalmente


class CharacterStatus(str, Enum):
    ALIVE = "alive"
    DEAD = "dead"
    MISSING = "missing"
    CAPTURED = "captured"
    TRANSFORMED = "transformed"  # Convertido en algo más


@dataclass
class World:
    id: str
    name: str
    genre: str
    description: str
    time_period: str
    locations: List[Dict] = field(default_factory=list)
    factions: List[Dict] = field(default_factory=list)
    rules: Dict[str, Any] = field(default_factory=dict)  # Leyes del mundo
    history: List[Dict] = field(default_factory=list)  # Eventos históricos
    created_at: datetime = field(default_factory=datetime.now)
    last_simulated: Optional[datetime] = None
    
    def __post_init__(self):
        if not self.id:
            self.id = f"world_{int(time.time())}_{random.randint(1000,9999)}"


@dataclass
class Character:
    id: str
    name: str
    world_id: str
    archetype: str
    personality: List[str] = field(default_factory=list)
    motivations: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    backstory: str = ""
    current_location: str = ""
    status: CharacterStatus = CharacterStatus.ALIVE
    relationships: Dict[str, str] = field(default_factory=dict)  # char_id -> relationship_type
    goals: List[str] = field(default_factory=list)
    inventory: List[str] = field(default_factory=list)
    memories: List[Dict] = field(default_factory=list)  # Eventos memorables
    stats: Dict[str, int] = field(default_factory=lambda: {
        "health": 100, "energy": 100, "reputation": 50, "wealth": 0
    })
    created_at: datetime = field(default_factory=datetime.now)
    last_action: Optional[datetime] = None
    
    def __post_init__(self):
        if not self.id:
            self.id = f"char_{int(time.time())}_{random.randint(1000,9999)}"
    
    def add_memory(self, event: str, importance: int = 5):
        """Añade un recuerdo al personaje."""
        self.memories.append({
            "event": event,
            "timestamp": datetime.now().isoformat(),
            "importance": importance,
        })
        # Mantener solo memorias importantes
        self.memories = sorted(self.memories, key=lambda m: m["importance"], reverse=True)[:50]


@dataclass
class StoryEvent:
    id: str
    story_id: str
    title: str
    description: str
    characters_involved: List[str] = field(default_factory=list)
    location: str = ""
    consequences: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
    triggered_by: Optional[str] = None  # ID del evento que lo causó
    
    def __post_init__(self):
        if not self.id:
            self.id = f"evt_{int(time.time())}_{random.randint(1000,9999)}"


@dataclass
class Story:
    id: str
    title: str
    world_id: str
    premise: str
    main_characters: List[str] = field(default_factory=list)
    current_plot: str = ""
    events: List[StoryEvent] = field(default_factory=list)
    status: StoryStatus = StoryStatus.ACTIVE
    genre: str = ""
    target_audience: str = "adult"
    created_at: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)
    word_count: int = 0
    
    def __post_init__(self):
        if not self.id:
            self.id = f"story_{int(time.time())}_{random.randint(1000,9999)}"


# ══════════════════════════════════════════════════════════════════════════════
#  BASE DE DATOS
# ══════════════════════════════════════════════════════════════════════════════

class StoryDatabase:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        os.makedirs(self.db_path.parent, exist_ok=True)
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        # Tabla de mundos
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS worlds (
                id TEXT PRIMARY KEY,
                name TEXT,
                genre TEXT,
                description TEXT,
                time_period TEXT,
                locations TEXT,  -- JSON
                factions TEXT,   -- JSON
                rules TEXT,      -- JSON
                history TEXT,    -- JSON
                created_at TEXT,
                last_simulated TEXT
            )
        """)
        
        # Tabla de personajes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS characters (
                id TEXT PRIMARY KEY,
                name TEXT,
                world_id TEXT,
                archetype TEXT,
                personality TEXT,  -- JSON list
                motivations TEXT,  -- JSON list
                skills TEXT,       -- JSON list
                backstory TEXT,
                current_location TEXT,
                status TEXT,
                relationships TEXT,  -- JSON dict
                goals TEXT,         -- JSON list
                inventory TEXT,     -- JSON list
                memories TEXT,      -- JSON list
                stats TEXT,         -- JSON dict
                created_at TEXT,
                last_action TEXT
            )
        """)
        
        # Tabla de historias
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stories (
                id TEXT PRIMARY KEY,
                title TEXT,
                world_id TEXT,
                premise TEXT,
                main_characters TEXT,  -- JSON list
                current_plot TEXT,
                events TEXT,           -- JSON list
                status TEXT,
                genre TEXT,
                target_audience TEXT,
                created_at TEXT,
                last_updated TEXT,
                word_count INTEGER
            )
        """)
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def save_world(self, world: World):
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO worlds VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            world.id, world.name, world.genre, world.description, world.time_period,
            json.dumps(world.locations), json.dumps(world.factions),
            json.dumps(world.rules), json.dumps(world.history),
            world.created_at.isoformat(),
            world.last_simulated.isoformat() if world.last_simulated else None
        ))
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def load_world(self, world_id: str) -> Optional[World]:
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM worlds WHERE id = ?", (world_id,))
        row = cursor.fetchone()
        pass  # S109: get_conn no necesita close()
        if row:
            return World(
                id=row[0], name=row[1], genre=row[2], description=row[3],
                time_period=row[4],
                locations=json.loads(row[5]) if row[5] else [],
                factions=json.loads(row[6]) if row[6] else [],
                rules=json.loads(row[7]) if row[7] else {},
                history=json.loads(row[8]) if row[8] else [],
                created_at=datetime.fromisoformat(row[9]),
                last_simulated=datetime.fromisoformat(row[10]) if row[10] else None
            )
        return None
    
    def save_character(self, char: Character):
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO characters VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            char.id, char.name, char.world_id, char.archetype,
            json.dumps(char.personality), json.dumps(char.motivations),
            json.dumps(char.skills), char.backstory, char.current_location,
            char.status.value, json.dumps(char.relationships),
            json.dumps(char.goals), json.dumps(char.inventory),
            json.dumps(char.memories), json.dumps(char.stats),
            char.created_at.isoformat(),
            char.last_action.isoformat() if char.last_action else None
        ))
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def load_character(self, char_id: str) -> Optional[Character]:
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM characters WHERE id = ?", (char_id,))
        row = cursor.fetchone()
        pass  # S109: get_conn no necesita close()
        if row:
            return Character(
                id=row[0], name=row[1], world_id=row[2], archetype=row[3],
                personality=json.loads(row[4]) if row[4] else [],
                motivations=json.loads(row[5]) if row[5] else [],
                skills=json.loads(row[6]) if row[6] else [],
                backstory=row[7], current_location=row[8],
                status=CharacterStatus(row[9]),
                relationships=json.loads(row[10]) if row[10] else {},
                goals=json.loads(row[11]) if row[11] else [],
                inventory=json.loads(row[12]) if row[12] else [],
                memories=json.loads(row[13]) if row[13] else [],
                stats=json.loads(row[14]) if row[14] else {},
                created_at=datetime.fromisoformat(row[15]),
                last_action=datetime.fromisoformat(row[16]) if row[16] else None
            )
        return None
    
    def get_world_characters(self, world_id: str) -> List[Character]:
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM characters WHERE world_id = ?", (world_id,))
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        characters = []
        for (char_id,) in rows:
            char = self.load_character(char_id)
            if char:
                characters.append(char)
        return characters


# ══════════════════════════════════════════════════════════════════════════════
#  MOTOR DE HISTORIAS
# ══════════════════════════════════════════════════════════════════════════════

class StoryEngine:
    """
    Motor de creación y simulación de historias autónomas.
    """
    
    def __init__(self):
        self.db = StoryDatabase()
        STORIES_DIR.mkdir(parents=True, exist_ok=True)
        log.info("Story Engine initialized")
    
    # ════════════════════════════════════════════════════════════════════════
    #  CREACIÓN DE MUNDOS
    # ════════════════════════════════════════════════════════════════════════
    
    def create_world(self, name: str, genre: str = "fantasy", 
                     custom_description: Optional[str] = None) -> World:
        """Crea un nuevo mundo con configuración procedural."""
        
        template = GENRE_TEMPLATES.get(genre, GENRE_TEMPLATES["fantasy"])
        
        # Generar descripción si no se proporciona
        if custom_description:
            description = custom_description
        else:
            setting = random.choice(template["settings"])
            theme = random.choice(template["themes"])
            description = f"A {genre} world set in a {setting}. The dominant theme is {theme}."
        
        # Generar ubicaciones
        locations = []
        for i in range(random.randint(3, 6)):
            loc_type = random.choice(template["settings"])
            locations.append({
                "name": f"{loc_type.title()} {i+1}",
                "type": loc_type,
                "description": f"A significant location in the {genre} setting.",
                "controlled_by": None,
                "danger_level": random.randint(1, 10)
            })
        
        # Generar facciones
        factions = []
        for i in range(random.randint(2, 4)):
            factions.append({
                "name": f"Faction {i+1}",
                "type": random.choice(["political", "religious", "criminal", "military", "economic"]),
                "goals": random.sample(MOTIVATIONS, 2),
                "power_level": random.randint(1, 10),
                "territory": [random.choice(locations)["name"] for _ in range(random.randint(1, 2))]
            })
        
        world = World(
            id="",
            name=name,
            genre=genre,
            description=description,
            time_period=template["time_period"],
            locations=locations,
            factions=factions,
            rules={"magic_level": random.randint(1, 10) if genre == "fantasy" else 0},
            history=[{
                "event": f"The world of {name} came into existence",
                "year": "Year 0",
                "significance": 10
            }]
        )
        
        self.db.save_world(world)
        log.info(f"Created world: {world.name} ({world.id})")
        return world
    
    # ════════════════════════════════════════════════════════════════════════
    #  CREACIÓN DE PERSONAJES
    # ════════════════════════════════════════════════════════════════════════
    
    def create_character(self, name: str, archetype: str, world: World,
                         custom_traits: Optional[List[str]] = None) -> Character:
        """Crea un personaje autónomo en un mundo."""
        
        # Validar arquetipo
        template = GENRE_TEMPLATES.get(world.genre, GENRE_TEMPLATES["fantasy"])
        valid_archetypes = template["archetypes"]
        if archetype not in valid_archetypes:
            archetype = random.choice(valid_archetypes)
        
        # Generar personalidad
        if custom_traits:
            personality = custom_traits
        else:
            personality = random.sample(PERSONALITY_TRAITS, random.randint(2, 4))
        
        # Generar motivaciones
        motivations = random.sample(MOTIVATIONS, random.randint(1, 3))
        
        # Generar skills basadas en arquetipo
        skills_by_archetype = {
            "hacker": ["programming", "infiltration", "social engineering", "cryptography"],
            "wizard": ["spellcasting", "alchemy", "ancient lore", "divination"],
            "warrior": ["combat", "tactics", "endurance", "intimidation"],
            "detective": ["investigation", "deduction", "interrogation", "stealth"],
        }
        skills = skills_by_archetype.get(archetype, ["adaptability", "observation"])
        
        # Generar backstory procedural
        backstory_templates = [
            f"Born in {random.choice(world.locations)['name']}, {name} grew up surrounded by {random.choice(world.genre)} influences.",
            f"Orphaned at a young age, {name} learned to survive through {random.choice(skills)}.",
            f"Once a respected {archetype}, {name} lost everything in a tragic incident involving {random.choice(world.factions)['name'] if world.factions else 'unknown forces'}.",
            f"{name} is searching for {random.choice(motivations)}, which led them to become a {archetype}.",
        ]
        backstory = random.choice(backstory_templates)
        
        # Ubicación inicial
        current_location = random.choice(world.locations)["name"] if world.locations else "Unknown"
        
        character = Character(
            id="",
            name=name,
            world_id=world.id,
            archetype=archetype,
            personality=personality,
            motivations=motivations,
            skills=skills,
            backstory=backstory,
            current_location=current_location,
            goals=[f"Achieve {motivations[0]}" if motivations else "Survive"]
        )
        
        self.db.save_character(character)
        log.info(f"Created character: {character.name} ({archetype}) in {world.name}")
        return character
    
    # ════════════════════════════════════════════════════════════════════════
    #  SIMULACIÓN AUTÓNOMA
    # ════════════════════════════════════════════════════════════════════════
    
    def simulate_character_action(self, character: Character, world: World) -> StoryEvent:
        """
        Simula una acción autónoma del personaje basada en su estado y motivaciones.
        """
        # Determinar posibles acciones basadas en motivaciones y ubicación
        actions = []
        
        for motivation in character.motivations:
            if motivation == "wealth":
                actions.extend([
                    f"searches for valuable items in {character.current_location}",
                    "tries to negotiate a better deal",
                    "considers less legal ways to make money"
                ])
            elif motivation == "knowledge":
                actions.extend([
                    f"researches ancient secrets in {character.current_location}",
                    "reads old texts looking for information",
                    "asks locals about rumors"
                ])
            elif motivation == "revenge":
                actions.extend([
                    "gathers information about their target",
                    "trains to become stronger",
                    "plots their next move carefully"
                ])
            elif motivation == "love":
                actions.extend([
                    "writes a letter to their beloved",
                    "searches for a gift",
                    "daydreams about meeting them again"
                ])
            elif motivation == "survival":
                actions.extend([
                    "scavenges for food and supplies",
                    "finds shelter for the night",
                    "avoids dangerous areas"
                ])
        
        # Acciones por defecto si no hay motivaciones específicas
        if not actions:
            actions = [
                f"explores {character.current_location}",
                "rests and recovers",
                "observes their surroundings"
            ]
        
        # Personalidad afecta la acción
        if "brave" in character.personality:
            actions.append(f"confronts a challenge in {character.current_location}")
        if "curious" in character.personality:
            actions.append("investigates something unusual")
        if "cautious" in character.personality:
            actions.append("takes time to assess risks")
        
        # Seleccionar acción
        action = random.choice(actions)
        
        # Generar evento
        event = StoryEvent(
            id="",
            story_id="",  # No asociado a una historia específica aún
            title=f"{character.name} {action}",
            description=f"{character.name}, a {character.archetype}, {action}.",
            characters_involved=[character.id],
            location=character.current_location,
        )
        
        # Actualizar personaje
        character.last_action = datetime.now()
        character.add_memory(event.description, importance=random.randint(3, 7))
        
        # Posibles consecuencias
        if random.random() < 0.1:  # 10% chance de evento significativo
            consequences = [
                f"{character.name} discovered something valuable",
                f"{character.name} encountered a potential ally",
                f"{character.name} was noticed by enemies",
                f"{character.name} learned a new skill",
            ]
            event.consequences.append(random.choice(consequences))
            character.stats["experience"] = character.stats.get("experience", 0) + 1
        
        self.db.save_character(character)
        return event
    
    def simulate_day(self, world: World) -> List[StoryEvent]:
        """Simula un día completo en el mundo."""
        events = []
        characters = self.db.get_world_characters(world.id)
        
        log.info(f"Simulating day in {world.name} with {len(characters)} characters")
        
        for char in characters:
            if char.status == CharacterStatus.ALIVE:
                # Cada personaje hace 1-3 acciones por día
                for _ in range(random.randint(1, 3)):
                    event = self.simulate_character_action(char, world)
                    events.append(event)
        
        # Actualizar timestamp del mundo
        world.last_simulated = datetime.now()
        self.db.save_world(world)
        
        log.info(f"Day simulation complete: {len(events)} events generated")
        return events
    
    # ════════════════════════════════════════════════════════════════════════
    #  CREACIÓN DE HISTORIAS
    # ════════════════════════════════════════════════════════════════════════
    
    def create_story(self, title: str, world: World, 
                     main_chars: List[Character],
                     premise: Optional[str] = None) -> Story:
        """Crea una historia protagonizada por personajes."""
        
        if premise is None:
            template = GENRE_TEMPLATES.get(world.genre, GENRE_TEMPLATES["fantasy"])
            conflict = random.choice(template["conflicts"])
            
            if len(main_chars) >= 2:
                premise = f"When {main_chars[0].name} and {main_chars[1].name} meet, they become entangled in a {conflict} that will change {world.name} forever."
            else:
                premise = f"{main_chars[0].name if main_chars else 'A hero'} must face a {conflict} in the world of {world.name}."
        
        story = Story(
            id="",
            title=title,
            world_id=world.id,
            premise=premise,
            main_characters=[c.id for c in main_chars],
            genre=world.genre,
            current_plot="setup"
        )
        
        # Crear evento inicial
        initial_event = StoryEvent(
            id="",
            story_id=story.id,
            title="The Beginning",
            description=premise,
            characters_involved=story.main_characters,
            location=main_chars[0].current_location if main_chars else "Unknown"
        )
        story.events.append(initial_event)
        
        log.info(f"Created story: {story.title}")
        return story
    
    def advance_story(self, story: Story, world: World) -> Optional[StoryEvent]:
        """
        Avanza la historia generando el siguiente evento basado en el estado actual.
        """
        if story.status != StoryStatus.ACTIVE:
            return None
        
        # Cargar personajes principales
        characters = []
        for char_id in story.main_characters:
            char = self.db.load_character(char_id)
            if char:
                characters.append(char)
        
        if not characters:
            return None
        
        # Determinar siguiente plot point
        plot_stages = ["setup", "inciting_incident", "rising_action", "climax", "falling_action", "resolution"]
        current_idx = plot_stages.index(story.current_plot) if story.current_plot in plot_stages else 0
        
        if current_idx < len(plot_stages) - 1:
            story.current_plot = plot_stages[current_idx + 1]
        else:
            story.status = StoryStatus.COMPLETED
        
        # Generar evento basado en etapa
        char = random.choice(characters)
        
        event_templates = {
            "setup": [
                f"{char.name} prepares for the journey ahead",
                f"{char.name} receives mysterious information",
                f"{char.name} says goodbye to their old life"
            ],
            "inciting_incident": [
                f"{char.name} witnesses something that cannot be ignored",
                f"An unexpected visitor brings urgent news to {char.name}",
                f"{char.name} discovers a hidden truth"
            ],
            "rising_action": [
                f"{char.name} faces an obstacle in their path",
                f"{char.name} forms an unexpected alliance",
                f"A betrayal shocks {char.name}"
            ],
            "climax": [
                f"{char.name} confronts their greatest challenge",
                f"The fate of {world.name} rests on {char.name}'s decision",
                f"{char.name} must sacrifice something precious"
            ],
            "falling_action": [
                f"{char.name} deals with the aftermath",
                f"Loose ends are tied up for {char.name}",
                f"{char.name} reflects on what was lost and gained"
            ],
            "resolution": [
                f"{char.name} finds a new beginning",
                f"Peace returns to {world.name}, changed but enduring",
                f"{char.name}'s story reaches its conclusion"
            ]
        }
        
        templates = event_templates.get(story.current_plot, ["Something happens"])
        description = random.choice(templates)
        
        event = StoryEvent(
            id="",
            story_id=story.id,
            title=f"Chapter {len(story.events) + 1}: {story.current_plot.replace('_', ' ').title()}",
            description=description,
            characters_involved=[c.id for c in characters if random.random() > 0.3],
            location=char.current_location,
            triggered_by=story.events[-1].id if story.events else None
        )
        
        story.events.append(event)
        story.last_updated = datetime.now()
        story.word_count += len(description.split()) * 10  # Estimación
        
        log.info(f"Story advanced: {story.title} -> {story.current_plot}")
        return event
    
    # ════════════════════════════════════════════════════════════════════════
    #  EXPORTACIÓN
    # ════════════════════════════════════════════════════════════════════════
    
    def export_story_novel(self, story: Story, world: World) -> Path:
        """Exporta la historia como novela en formato Markdown."""
        
        filepath = STORIES_DIR / f"{story.id}_novel.md"
        
        content = f"""# {story.title}

**Genre:** {story.genre.title()}  
**World:** {world.name}  
**Created:** {story.created_at.strftime("%Y-%m-%d")}

## Premise

{story.premise}

## Characters

"""
        
        for char_id in story.main_characters:
            char = self.db.load_character(char_id)
            if char:
                content += f"""### {char.name}

- **Archetype:** {char.archetype}
- **Personality:** {', '.join(char.personality)}
- **Motivations:** {', '.join(char.motivations)}
- **Current Status:** {char.status.value}

{char.backstory}

---

"""
        
        content += "## Story\n\n"
        
        for event in story.events:
            content += f"### {event.title}\n\n"
            content += f"*{event.timestamp.strftime('%Y-%m-%d %H:%M')} - {event.location}*\n\n"
            content += f"{event.description}\n\n"
            if event.consequences:
                content += "**Consequences:**\n"
                for cons in event.consequences:
                    content += f"- {cons}\n"
                content += "\n"
            content += "---\n\n"
        
        filepath.write_text(content, encoding='utf-8')
        log.info(f"Exported story to: {filepath}")
        return filepath
    
    def export_character_sheet(self, character: Character, world: World) -> Path:
        """Exporta ficha de personaje."""
        
        filepath = STORIES_DIR / f"{character.id}_character.md"
        
        content = f"""# {character.name}

**World:** {world.name} ({world.genre})  
**Archetype:** {character.archetype}  
**Status:** {character.status.value}

## Stats

"""
        for stat, value in character.stats.items():
            bar = "█" * (value // 10) + "░" * (10 - value // 10)
            content += f"- **{stat.title()}:** {bar} {value}/100\n"
        
        content += f"""
## Personality

{', '.join(character.personality)}

## Motivations

{', '.join(character.motivations)}

## Skills

{', '.join(character.skills)}

## Backstory

{character.backstory}

## Memories

"""
        
        for memory in sorted(character.memories, key=lambda m: m.get("importance", 0), reverse=True)[:10]:
            content += f"- ({memory.get('importance', 5)}/10) {memory.get('event', '')}\n"
        
        content += f"""
## Relationships

"""
        
        for rel_char_id, rel_type in character.relationships.items():
            rel_char = self.db.load_character(rel_char_id)
            name = rel_char.name if rel_char else "Unknown"
            content += f"- **{name}:** {rel_type}\n"
        
        filepath.write_text(content, encoding='utf-8')
        return filepath


# Singleton
_story_engine: Optional[StoryEngine] = None

def get_story_engine() -> StoryEngine:
    global _story_engine
    if _story_engine is None:
        _story_engine = StoryEngine()
    return _story_engine


# ══════════════════════════════════════════════════════════════════════════════
#  TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  EIDOS Story Creator - Test")
    print("=" * 70)
    
    engine = get_story_engine()
    
    # Test 1: Crear mundo cyberpunk
    print("\n[Test 1] Creating cyberpunk world...")
    world = engine.create_world("Neo-Seoul 2180", genre="cyberpunk")
    print(f"  ✓ World: {world.name}")
    print(f"    Genre: {world.genre}")
    print(f"    Locations: {len(world.locations)}")
    print(f"    Factions: {len(world.factions)}")
    
    # Test 2: Crear personajes
    print("\n[Test 2] Creating characters...")
    char1 = engine.create_character("Kaito", "hacker", world)
    char2 = engine.create_character("Yuki", "netrunner", world)
    print(f"  ✓ {char1.name}: {char1.archetype}")
    print(f"    Personality: {', '.join(char1.personality)}")
    print(f"    Motivations: {', '.join(char1.motivations)}")
    print(f"  ✓ {char2.name}: {char2.archetype}")
    
    # Test 3: Simular día
    print("\n[Test 3] Simulating day...")
    events = engine.simulate_day(world)
    for i, event in enumerate(events[:3]):
        print(f"  • {event.title}")
    print(f"  ... ({len(events)} total events)")
    
    # Test 4: Crear historia
    print("\n[Test 4] Creating story...")
    story = engine.create_story("The Digital Ghost", world, [char1, char2])
    print(f"  ✓ Story: {story.title}")
    print(f"    Premise: {story.premise[:60]}...")
    
    # Test 5: Avanzar historia
    print("\n[Test 5] Advancing story...")
    for i in range(3):
        event = engine.advance_story(story, world)
        if event:
            print(f"  • {event.title}")
    
    # Test 6: Exportar
    print("\n[Test 6] Exporting...")
    novel_path = engine.export_story_novel(story, world)
    char_sheet_path = engine.export_character_sheet(char1, world)
    print(f"  ✓ Novel: {novel_path.name}")
    print(f"  ✓ Character sheet: {char_sheet_path.name}")
    
    print("\n✅ Story Creator test complete")
