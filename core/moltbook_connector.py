"""
EIDOS Moltbook Connector
========================
Conector para aprender de moltbook.com y compartir ideas.

Features:
- Fetch de contenido de moltbook.com
- Aprendizaje de ideas compartidas
- Compartir descubrimientos de EIDOS
- Almacenamiento local de conocimiento
"""
from __future__ import annotations

import sys
import time
import json
import hashlib
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime

# Añadir root al path
EIDOS_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(EIDOS_ROOT))

from core.eidos_config import get_config

# ============================
# DATA MODELS
# ============================

@dataclass
class MoltbookIdea:
    """Una idea de moltbook.com"""
    id: str
    title: str
    content: str
    author: str
    timestamp: float
    tags: List[str]
    url: str
    learned: bool = False

@dataclass
class LearningSession:
    """Sesión de aprendizaje de moltbook"""
    session_id: str
    started_at: float
    ideas_fetched: int
    ideas_learned: int
    topics: List[str]

# ============================
# MOLTBOOK CONNECTOR
# ============================

class MoltbookConnector:
    """
    Conector para interactuar con moltbook.com.

    Nota: Esta es una implementación base. Se requiere conocer
    la API de moltbook.com para implementación completa.
    """

    def __init__(self):
        self.config = get_config()
        self.base_url = self.config.moltbook.url
        self.enabled = self.config.moltbook.enabled

        # Directorio de conocimiento
        self.knowledge_dir = Path(self.config.paths.knowledge) / "moltbook"
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)

        # Archivo de ideas aprendidas
        self.ideas_file = self.knowledge_dir / "ideas.jsonl"

        print(f"[MOLTBOOK] 📚 Connector inicializado")
        print(f"[MOLTBOOK]    URL: {self.base_url}")
        print(f"[MOLTBOOK]    Enabled: {self.enabled}")

    def fetch_ideas(
        self,
        limit: int = 10,
        topic: Optional[str] = None
    ) -> List[MoltbookIdea]:
        """
        Obtiene ideas de moltbook.com.

        Args:
            limit: Número máximo de ideas a obtener
            topic: Filtrar por tema específico

        Returns:
            Lista de MoltbookIdea

        Nota: Requiere implementar llamadas a la API real
        """
        if not self.enabled:
            print("[MOLTBOOK] ⚠️  Connector deshabilitado")
            return []

        print(f"[MOLTBOOK] 🔍 Fetching ideas from {self.base_url}...")

        try:
            # TODO: Implementar fetch real cuando tengamos acceso a la API
            # Por ahora retornamos ideas de ejemplo

            ideas = self._fetch_example_ideas(limit, topic)

            print(f"[MOLTBOOK] ✅ Fetched {len(ideas)} ideas")
            return ideas

        except Exception as e:
            print(f"[MOLTBOOK] ❌ Error fetching ideas: {e}")
            return []

    def _fetch_example_ideas(self, limit: int, topic: Optional[str]) -> List[MoltbookIdea]:
        """
        Ideas de ejemplo para testing.
        En producción, esto haría un HTTP request a moltbook.com
        """
        example_ideas = [
            {
                "title": "AI Autonomous Learning Systems",
                "content": "Los sistemas de IA pueden aprender observando patterns en lugar de ser entrenados explícitamente",
                "author": "researcher_123",
                "tags": ["ai", "learning", "autonomy"]
            },
            {
                "title": "Efficient Memory Management for AI",
                "content": "Usar lazy loading y caché inteligente reduce el uso de RAM hasta 60%",
                "author": "dev_456",
                "tags": ["optimization", "memory", "performance"]
            },
            {
                "title": "Window-Specific Context in Desktop AI",
                "content": "Capturar ventanas específicas en vez de pantalla completa mejora privacidad y performance",
                "author": "privacy_expert",
                "tags": ["privacy", "desktop-ai", "context"]
            }
        ]

        ideas = []
        for i, idea_data in enumerate(example_ideas[:limit]):
            idea_id = hashlib.md5(idea_data["title"].encode()).hexdigest()[:12]

            idea = MoltbookIdea(
                id=idea_id,
                title=idea_data["title"],
                content=idea_data["content"],
                author=idea_data["author"],
                timestamp=time.time() - (i * 3600),  # 1 hora de diferencia
                tags=idea_data["tags"],
                url=f"{self.base_url}/ideas/{idea_id}",
                learned=False
            )

            # Filtrar por topic si se especifica
            if topic:
                if topic.lower() not in [t.lower() for t in idea.tags]:
                    continue

            ideas.append(idea)

        return ideas

    def learn_idea(self, idea: MoltbookIdea) -> bool:
        """
        Aprende una idea de moltbook.

        Args:
            idea: Idea a aprender

        Returns:
            True si se aprendió exitosamente
        """
        print(f"[MOLTBOOK] 🎓 Aprendiendo: {idea.title}")

        try:
            # Guardar idea en knowledge base
            self._save_idea(idea)

            # Marcar como aprendida
            idea.learned = True

            print(f"[MOLTBOOK] ✅ Idea aprendida y guardada")
            return True

        except Exception as e:
            print(f"[MOLTBOOK] ❌ Error aprendiendo idea: {e}")
            return False

    def _save_idea(self, idea: MoltbookIdea):
        """Guarda idea en knowledge base local"""
        idea_dict = {
            "id": idea.id,
            "title": idea.title,
            "content": idea.content,
            "author": idea.author,
            "timestamp": idea.timestamp,
            "tags": idea.tags,
            "url": idea.url,
            "learned_at": time.time()
        }

        # Append a archivo JSONL
        with open(self.ideas_file, 'a') as f:
            f.write(json.dumps(idea_dict) + '\n')

    def get_learned_ideas(self, limit: int = 100) -> List[Dict]:
        """Obtiene ideas ya aprendidas"""
        if not self.ideas_file.exists():
            return []

        ideas = []
        with open(self.ideas_file, 'r') as f:
            for line in f:
                if line.strip():
                    ideas.append(json.loads(line))

        return ideas[-limit:]  # Retornar las más recientes

    def search_learned_ideas(self, query: str) -> List[Dict]:
        """Busca en ideas aprendidas"""
        all_ideas = self.get_learned_ideas()
        query_lower = query.lower()

        matching = []
        for idea in all_ideas:
            # Buscar en título, contenido y tags
            if (query_lower in idea["title"].lower() or
                query_lower in idea["content"].lower() or
                any(query_lower in tag.lower() for tag in idea["tags"])):
                matching.append(idea)

        return matching

    def share_idea(
        self,
        title: str,
        content: str,
        tags: List[str]
    ) -> bool:
        """
        Comparte una idea en moltbook.com.

        Args:
            title: Título de la idea
            content: Contenido
            tags: Tags/categorías

        Returns:
            True si se compartió exitosamente

        Nota: Requiere implementar POST a la API real
        """
        if not self.enabled:
            print("[MOLTBOOK] ⚠️  Connector deshabilitado")
            return False

        if not self.config.moltbook.auto_share_ideas:
            print("[MOLTBOOK] ⚠️  Auto-share deshabilitado")
            return False

        print(f"[MOLTBOOK] 📤 Compartiendo idea: {title}")

        try:
            # TODO: Implementar POST real a la API
            # Por ahora solo guardamos localmente

            idea_id = hashlib.md5(title.encode()).hexdigest()[:12]

            shared_idea = {
                "id": idea_id,
                "title": title,
                "content": content,
                "author": "EIDOS",
                "tags": tags,
                "shared_at": time.time(),
                "url": f"{self.base_url}/ideas/{idea_id}"
            }

            # Guardar en archivo de ideas compartidas
            shared_file = self.knowledge_dir / "shared_ideas.jsonl"
            with open(shared_file, 'a') as f:
                f.write(json.dumps(shared_idea) + '\n')

            print(f"[MOLTBOOK] ✅ Idea compartida (local)")
            return True

        except Exception as e:
            print(f"[MOLTBOOK] ❌ Error compartiendo idea: {e}")
            return False

    def learning_session(
        self,
        duration_minutes: int = 30,
        topics: Optional[List[str]] = None
    ) -> LearningSession:
        """
        Ejecuta una sesión de aprendizaje de moltbook.

        Args:
            duration_minutes: Duración de la sesión
            topics: Temas específicos a explorar

        Returns:
            LearningSession con estadísticas
        """
        session_id = f"session_{int(time.time())}"
        started_at = time.time()

        print(f"[MOLTBOOK] 🎓 Iniciando sesión de aprendizaje")
        print(f"[MOLTBOOK]    ID: {session_id}")
        print(f"[MOLTBOOK]    Duración: {duration_minutes} min")

        ideas_fetched = 0
        ideas_learned = 0
        all_topics = topics or []

        try:
            # Fetch ideas periódicamente
            end_time = time.time() + (duration_minutes * 60)

            while time.time() < end_time:
                # Fetch batch de ideas
                batch_size = 5
                for topic in all_topics if all_topics else [None]:
                    ideas = self.fetch_ideas(limit=batch_size, topic=topic)
                    ideas_fetched += len(ideas)

                    # Aprender cada idea
                    for idea in ideas:
                        if self.learn_idea(idea):
                            ideas_learned += 1

                # Esperar antes del siguiente batch
                time.sleep(60)  # 1 minuto entre batches

        except KeyboardInterrupt:
            print(f"\n[MOLTBOOK] ⏸️  Sesión interrumpida")

        session = LearningSession(
            session_id=session_id,
            started_at=started_at,
            ideas_fetched=ideas_fetched,
            ideas_learned=ideas_learned,
            topics=all_topics
        )

        print(f"[MOLTBOOK] ✅ Sesión completada")
        print(f"[MOLTBOOK]    Ideas fetched: {ideas_fetched}")
        print(f"[MOLTBOOK]    Ideas learned: {ideas_learned}")

        return session

# ============================
# SINGLETON
# ============================

_moltbook_connector: Optional[MoltbookConnector] = None

def get_moltbook_connector() -> MoltbookConnector:
    """Obtiene instancia singleton"""
    global _moltbook_connector
    if _moltbook_connector is None:
        _moltbook_connector = MoltbookConnector()
    return _moltbook_connector

# ============================
# TESTING
# ============================

if __name__ == "__main__":
    print("=== EIDOS Moltbook Connector Test ===\n")

    connector = get_moltbook_connector()

    # Habilitar temporalmente para testing
    connector.enabled = True

    # Fetch ideas
    print("\n📚 Fetching ideas...")
    ideas = connector.fetch_ideas(limit=3)

    for i, idea in enumerate(ideas, 1):
        print(f"\n{i}. {idea.title}")
        print(f"   Author: {idea.author}")
        print(f"   Content: {idea.content[:60]}...")
        print(f"   Tags: {', '.join(idea.tags)}")

    # Aprender primera idea
    if ideas:
        print(f"\n🎓 Aprendiendo primera idea...")
        connector.learn_idea(ideas[0])

    # Ver ideas aprendidas
    learned = connector.get_learned_ideas()
    print(f"\n📖 Ideas aprendidas: {len(learned)}")

    # Buscar
    print(f"\n🔍 Buscando 'ai'...")
    results = connector.search_learned_ideas("ai")
    print(f"   Resultados: {len(results)}")

    # Compartir idea
    print(f"\n📤 Compartiendo idea de EIDOS...")
    connector.share_idea(
        title="Autonomous Objectives System",
        content="EIDOS puede crear y perseguir objetivos propios sin supervisión humana",
        tags=["ai", "autonomy", "objectives"]
    )

    print("\n🎯 Test completado")
