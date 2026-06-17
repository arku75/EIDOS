"""
core/colony_web_learner.py — Aprendizaje desde APIs web gratuitas  [Fase 9]

Colony aprende del mundo exterior sin coste usando APIs 100% gratuitas y sin clave:

  Wikipedia       → conceptos, tecnologías, vocabulario, historia
  DuckDuckGo IA   → respuestas instantáneas a cualquier pregunta
  Dictionary API  → vocabulario inglés completo (definiciones, sinónimos)
  MyMemory        → traducción español ↔ inglés (1000 palabras/día gratis)
  HackerNews      → noticias tech, discusiones de programadores
  GitHub Search   → ejemplos de código real (60 req/h sin clave)
  Open Library    → libros y textos de conocimiento
  Quotable        → frases de sabiduría y filosofía

Uso:
    from core.colony_web_learner import get_web_learner
    learner = get_web_learner()
    result = learner.wikipedia("Docker containerization")
    result = learner.dictionary("autonomous")
    result = learner.translate("aprender", src="es", tgt="en")
    result = learner.hackernews(limit=3)
    result = learner.github_code("colony python async")
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from typing import Optional

log = logging.getLogger("eidos.web_learner")

REQUEST_TIMEOUT = 8   # segundos por petición HTTP
USER_AGENT = "EIDOS-Colony/1.0 (educational AI; contact: eidos-colony)"


def _fetch(url: str, headers: Optional[dict] = None) -> Optional[dict]:
    """GET JSON de una URL. Devuelve dict o None."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            **(headers or {})
        })
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except Exception as e:
        log.debug("fetch %s: %s", url[:60], e)
        return None


def _fetch_text(url: str, headers: Optional[dict] = None) -> Optional[str]:
    """GET texto plano de una URL."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            **(headers or {})
        })
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        log.debug("fetch_text %s: %s", url[:60], e)
        return None


class WebLearner:
    """APIs gratuitas para que Colony aprenda del mundo exterior."""

    # ── Wikipedia ────────────────────────────────────────────────────────────

    def wikipedia(self, query: str, lang: str = "es") -> Optional[str]:
        """
        Busca en Wikipedia y devuelve el extracto del artículo más relevante.
        lang: 'es' para español, 'en' para inglés.
        """
        q = urllib.parse.quote(query)
        # Primero busca el título exacto
        search_url = (
            f"https://{lang}.wikipedia.org/w/api.php"
            f"?action=query&list=search&srsearch={q}&srlimit=1&format=json"
        )
        data = _fetch(search_url)
        if not data:
            return None
        results = data.get("query", {}).get("search", [])
        if not results:
            return None

        title = results[0]["title"]
        title_q = urllib.parse.quote(title)
        # Obtener extracto del artículo
        extract_url = (
            f"https://{lang}.wikipedia.org/w/api.php"
            f"?action=query&prop=extracts&exintro&explaintext"
            f"&titles={title_q}&format=json"
        )
        data2 = _fetch(extract_url)
        if not data2:
            return None
        pages = data2.get("query", {}).get("pages", {})
        page = next(iter(pages.values()), {})
        extract = page.get("extract", "")
        if not extract:
            return None

        # Recortar a 600 chars y devolver con título
        extract = extract.strip()[:600]
        return f"[Wikipedia:{title}]\n{extract}"

    def wikipedia_en(self, query: str) -> Optional[str]:
        """Wikipedia en inglés."""
        return self.wikipedia(query, lang="en")

    # ── DuckDuckGo Instant Answer ─────────────────────────────────────────────

    def duckduckgo(self, query: str) -> Optional[str]:
        """
        DuckDuckGo Instant Answer API — respuestas rápidas a preguntas directas.
        Funciona mejor con preguntas del tipo "what is X", "how does Y work".
        """
        q = urllib.parse.quote(query)
        url = f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1"
        data = _fetch(url)
        if not data:
            return None

        abstract = data.get("AbstractText", "").strip()
        answer   = data.get("Answer", "").strip()
        definition = data.get("Definition", "").strip()
        topic   = data.get("Heading", query)

        text = abstract or answer or definition
        if not text:
            # Intentar RelatedTopics
            topics = data.get("RelatedTopics", [])
            texts = [t.get("Text","") for t in topics[:3] if isinstance(t,dict) and t.get("Text")]
            text = " | ".join(texts[:2])

        if not text:
            return None
        return f"[DuckDuckGo:{topic}]\n{text[:500]}"

    # ── Dictionary API (inglés) ───────────────────────────────────────────────

    def dictionary(self, word: str) -> Optional[str]:
        """
        Definición, sinónimos y ejemplo de uso de una palabra en inglés.
        API: dictionaryapi.dev — gratuita, sin clave.
        """
        w = urllib.parse.quote(word.lower().strip())
        url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{w}"
        data = _fetch(url)
        if not data or not isinstance(data, list):
            return None

        entry = data[0]
        word_out = entry.get("word", word)
        meanings = entry.get("meanings", [])
        if not meanings:
            return None

        lines = [f"[Dictionary:{word_out}]"]
        for meaning in meanings[:2]:
            pos = meaning.get("partOfSpeech", "")
            defs = meaning.get("definitions", [])
            if defs:
                d = defs[0]
                definition = d.get("definition", "")
                example    = d.get("example", "")
                lines.append(f"  [{pos}] {definition}")
                if example:
                    lines.append(f"  Ejemplo: \"{example}\"")
            synonyms = meaning.get("synonyms", [])[:4]
            if synonyms:
                lines.append(f"  Sinónimos: {', '.join(synonyms)}")

        return "\n".join(lines)[:600]

    # ── MyMemory Traducción ───────────────────────────────────────────────────

    def translate(self, text: str, src: str = "es", tgt: str = "en") -> Optional[str]:
        """
        Traduce texto usando MyMemory API (gratuita, sin clave, 1000 words/día).
        src/tgt: 'es', 'en', 'fr', 'de', 'pt', etc.
        """
        t = urllib.parse.quote(text[:300])
        langpair = urllib.parse.quote(f"{src}|{tgt}")
        url = f"https://api.mymemory.translated.net/get?q={t}&langpair={langpair}"
        data = _fetch(url)
        if not data:
            return None
        resp_data = data.get("responseData", {})
        translated = resp_data.get("translatedText", "")
        if not translated:
            return None
        return f"[Traducción {src}→{tgt}] {text[:80]} → {translated[:200]}"

    # ── HackerNews ───────────────────────────────────────────────────────────

    def hackernews(self, limit: int = 5, story_type: str = "top") -> Optional[str]:
        """
        Últimas noticias tech de HackerNews. story_type: top, new, best.
        """
        url = f"https://hacker-news.firebaseio.com/v0/{story_type}stories.json"
        ids = _fetch(url)
        if not ids:
            return None

        stories = []
        for sid in ids[:limit]:
            item_url = f"https://hacker-news.firebaseio.com/v0/item/{sid}.json"
            item = _fetch(item_url)
            if item and item.get("title"):
                score = item.get("score", 0)
                title = item.get("title", "")
                url_s = item.get("url", "")
                stories.append(f"  [{score}⬆] {title}")

        if not stories:
            return None
        return f"[HackerNews:{story_type}]\n" + "\n".join(stories)

    # ── GitHub Code Search ────────────────────────────────────────────────────

    def github_code(self, query: str, lang: str = "python", limit: int = 3) -> Optional[str]:
        """
        Busca ejemplos de código en GitHub (60 req/h sin autenticación).
        """
        q = urllib.parse.quote(f"{query} language:{lang}")
        url = f"https://api.github.com/search/repositories?q={q}&sort=stars&per_page={limit}"
        data = _fetch(url, headers={"Accept": "application/vnd.github.v3+json"})
        if not data:
            return None
        items = data.get("items", [])
        if not items:
            return None

        lines = [f"[GitHub:{query}]"]
        for item in items[:limit]:
            name  = item.get("full_name", "?")
            desc  = item.get("description", "")[:80]
            stars = item.get("stargazers_count", 0)
            lines.append(f"  ⭐{stars} {name}: {desc}")

        return "\n".join(lines)[:600]

    # ── Open Library ─────────────────────────────────────────────────────────

    def open_library(self, query: str, limit: int = 3) -> Optional[str]:
        """
        Busca libros sobre un tema en Open Library (openlibrary.org).
        """
        q = urllib.parse.quote(query)
        url = f"https://openlibrary.org/search.json?q={q}&limit={limit}&fields=title,author_name,first_publish_year,subject"
        data = _fetch(url)
        if not data:
            return None
        docs = data.get("docs", [])
        if not docs:
            return None

        lines = [f"[OpenLibrary:{query}]"]
        for doc in docs[:limit]:
            title   = doc.get("title", "?")
            authors = ", ".join(doc.get("author_name", ["?"])[:2])
            year    = doc.get("first_publish_year", "?")
            lines.append(f"  📚 {title} — {authors} ({year})")

        return "\n".join(lines)[:500]

    # ── Quotable (frases de sabiduría) ────────────────────────────────────────

    def zenquotes(self) -> Optional[str]:
        """
        Frase de sabiduría vía ZenQuotes API (gratuita, sin clave).
        """
        url = "https://zenquotes.io/api/random"
        data = _fetch(url)
        if not data or not isinstance(data, list):
            return None
        item = data[0]
        quote  = item.get("q", "")
        author = item.get("a", "Anónimo")
        if not quote:
            return None
        return f"[Sabiduría] \"{quote}\" — {author}"

    # ── Wikidata (conocimiento estructurado) ──────────────────────────────────

    def wikidata(self, query: str) -> Optional[str]:
        """
        Búsqueda en Wikidata para obtener hechos estructurados sobre un concepto.
        """
        q = urllib.parse.quote(query)
        url = (
            f"https://www.wikidata.org/w/api.php"
            f"?action=wbsearchentities&search={q}&language=es&format=json&limit=1"
        )
        data = _fetch(url)
        if not data:
            return None
        results = data.get("search", [])
        if not results:
            return None
        item = results[0]
        label = item.get("label", query)
        desc  = item.get("description", "sin descripción")
        return f"[Wikidata:{label}] {desc}"


# ── Métodos de conveniencia por dominio ──────────────────────────────────────

class DomainLearner(WebLearner):
    """Extensión con métodos específicos por dominio de personaje."""

    DOCKER_TOPICS = [
        "Docker containerization", "Docker Compose", "Docker networking",
        "Docker volumes", "Dockerfile best practices", "Docker vs virtual machine",
        "Container orchestration", "Docker registry", "Docker security",
    ]
    LINUX_TOPICS = [
        "Linux systemd", "Linux cgroups", "Linux namespaces",
        "Linux file system", "Linux networking", "Linux process management",
        "Shell scripting", "Linux security", "Linux kernel",
    ]
    AI_TOPICS = [
        "Large language model", "Embeddings machine learning",
        "Reinforcement learning", "Neural network", "Natural language processing",
        "Knowledge graph", "Autonomous agent AI", "Vector database",
    ]
    VOCAB_EN = [
        "autonomous", "inference", "embedding", "orchestration", "containerization",
        "deliberation", "synthesis", "cognition", "heuristic", "abstraction",
        "polymorphism", "recursion", "concurrency", "idempotent", "deterministic",
    ]
    VOCAB_ES_TECH = [
        "aprendizaje automático", "inteligencia artificial", "contenedor",
        "orquestación", "inferencia", "red neuronal", "base de datos vectorial",
    ]

    def learn_docker(self) -> Optional[str]:
        """Colony aprende sobre Docker — cuándo usarlo, por qué, cómo."""
        import random
        topic = random.choice(self.DOCKER_TOPICS)
        result = self.wikipedia(topic, lang="en")
        if not result:
            result = self.duckduckgo(f"what is {topic}")
        return result

    def learn_linux(self) -> Optional[str]:
        """Colony aprende sobre Linux — conceptos del sistema operativo."""
        import random
        topic = random.choice(self.LINUX_TOPICS)
        return self.wikipedia(topic, lang="en")

    def learn_ai_concept(self) -> Optional[str]:
        """Colony aprende conceptos de IA y ML."""
        import random
        topic = random.choice(self.AI_TOPICS)
        result = self.wikipedia(topic, lang="es")
        if not result:
            result = self.wikipedia(topic, lang="en")
        return result

    def learn_vocabulary(self) -> Optional[str]:
        """Colony aprende vocabulario técnico en inglés con traducción."""
        import random
        word = random.choice(self.VOCAB_EN)
        en_def = self.dictionary(word)
        es_trans = self.translate(word, src="en", tgt="es")
        parts = []
        if en_def:
            parts.append(en_def)
        if es_trans:
            parts.append(es_trans)
        return "\n".join(parts) if parts else None

    def learn_tech_news(self) -> Optional[str]:
        """Colony se informa de las últimas noticias tech."""
        return self.hackernews(limit=5, story_type="top")

    def learn_code_patterns(self, topic: str = "async python") -> Optional[str]:
        """Colony busca patrones de código en GitHub."""
        return self.github_code(topic, lang="python", limit=3)

    def learn_philosophy(self) -> Optional[str]:
        """Colony reflexiona con frases de sabiduría."""
        result = self.zenquotes()
        if not result:
            result = self.wikipedia("filosofía del conocimiento")
        return result

    def learn_about(self, concept: str) -> Optional[str]:
        """Aprende sobre cualquier concepto (Wikipedia ES → EN → DDG)."""
        result = self.wikipedia(concept, lang="es")
        if not result:
            result = self.wikipedia(concept, lang="en")
        if not result:
            result = self.duckduckgo(concept)
        return result


# ── Singleton ─────────────────────────────────────────────────────────────────

_instance: Optional[DomainLearner] = None
_lock = threading.Lock()


def get_web_learner() -> DomainLearner:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = DomainLearner()
    return _instance
