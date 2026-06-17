"""
EIDOS Autonomous Web Crawler
=============================
Autonomous web exploration: CrawlQueue, ContentExtractor, LinkDiscoverer.
Integrates with IgnoranceAtlas (curiosity engine) and brain DB (knowledge nodes).
Uses urllib (stdlib) for fetching, with optional Firefox ESR via browser_manager.

Architecture:
  CrawlQueue     - Priority heap of URLs, robots.txt respect, rate limiting
  ContentExtractor - Clean text + Markdown from HTML (no external deps)
  LinkDiscoverer   - Extract/filter/score links from crawled pages
  EidosCrawler     - Orchestrator: fetch, extract, discover, persist

Usage:
  from core.eidos_crawler import get_crawler, crawl_url
  crawler = get_crawler()
  report = crawler.run(seed_urls=["https://example.com/docs"])
  # Or: wire into curiosity engine
  from core.eidos_crawler import crawl_from_curiosity
  result = crawl_from_curiosity("https://man7.org/linux/man-pages/man1/ls.1.html", parent_id=42)
"""

from __future__ import annotations

import hashlib
import heapq
import re
import sqlite3
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse
from core.db import get_conn as _get_conn_crawler

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_PAGES_PER_SESSION: int = 50
MAX_CRAWL_DEPTH: int = 3
REQUEST_DELAY: float = 1.0  # seconds between requests to same domain
REQUEST_TIMEOUT: int = 15  # seconds
USER_AGENT: str = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "EIDOS/1.0 Crawler (autonomous research)"
)
MAX_CONTENT_BYTES: int = 512 * 1024  # 512 KB max per page
ROBOTS_CACHE_TTL: float = 3600.0  # 1 hour
ATLAS_DB_PATH: Path = Path.home() / ".eidos" / "curiosity_atlas.db"
BRAIN_DB_PATH: Path = Path.home() / ".eidos" / "evolution_brain.db"
CRAWL_DIR: Path = Path.home() / ".eidos" / "crawl"

# Tags stripped before content extraction
_STRIP_TAGS_RE: re.Pattern = re.compile(
    r"<(script|style|nav|footer|header|aside|noscript|iframe|form|button|svg|canvas)"
    r"[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_ANY_TAG_RE: re.Pattern = re.compile(r"<[^>]+>")
_WHITESPACE_RE: re.Pattern = re.compile(r"\s+")
_LINK_HREF_RE: re.Pattern = re.compile(
    r'<a\s[^>]*href=["\']([^"\'#\s][^"\']*)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TITLE_RE: re.Pattern = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# URL patterns suggesting documentation / knowledge-rich content
_DOC_PATTERNS: List[str] = [
    r"/docs?/", r"/wiki/", r"/man(ual)?/", r"/guide/", r"/tutorial/",
    r"/reference/", r"/learn/", r"/doc/", r"/article/", r"/blog/",
    r"/readme", r"/howto", r"/faq", r"/man\d?/",
    r"\.md$", r"\.rst$", r"\.txt$",
]
_DOC_RE: re.Pattern = re.compile("|".join(_DOC_PATTERNS), re.IGNORECASE)

# File extensions that are NOT HTML documents
_SKIP_EXTENSIONS: Set[str] = {
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".mp3", ".mp4", ".avi", ".mov", ".webm", ".ogg",
    ".css", ".js", ".json", ".xml", ".rss", ".atom",
    ".woff", ".woff2", ".ttf", ".eot",
    ".exe", ".dmg", ".iso", ".img", ".bin",
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass

class PageContent:
    """Result of fetching and extracting a single page."""
    url: str
    title: str
    text: str          # clean plain text
    markdown: str      # Markdown conversion
    links: List[str]   # discovered URLs
    domain: str = ""
    depth: int = 0
    status: int = 0
    error: str = ""

    def __post_init__(self) -> None:
        if not self.domain:
            self.domain = urlparse(self.url).netloc


@dataclass
class CrawlSession:
    """Report produced at the end of a crawl session."""
    seed_urls: List[str]
    pages_crawled: int = 0
    pages_failed: int = 0
    pages_skipped: int = 0
    links_discovered: int = 0
    links_added_to_atlas: int = 0
    duration_s: float = 0.0
    results: List[PageContent] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CrawlQueue -- priority queue with politeness
# ---------------------------------------------------------------------------

class CrawlQueue:
    """Priority queue of URLs to visit.

    - Heap-ordered by (priority, depth, sequence, url)
    - Deduplicates via visited set
    - Respects robots.txt (cached per domain, 1h TTL)
    - Enforces per-domain request delay
    - Seeds from IgnoranceAtlas URL items
    """

    def __init__(
        self,
        max_pages: int = MAX_PAGES_PER_SESSION,
        max_depth: int = MAX_CRAWL_DEPTH,
    ) -> None:
        self._max_pages = max_pages
        self._max_depth = max_depth
        self._heap: List[Tuple[float, int, int, str]] = []
        self._seq: int = 0
        self._visited: Set[str] = set()
        self._domain_last: Dict[str, float] = {}
        self._robots_cache: Dict[str, Tuple[urllib.robotparser.RobotFileParser, float]] = {}
        self._locked_domains: Set[str] = set()

    # --- public API ---

    def seed_url(self, url: str, priority: float = 0.5, depth: int = 0) -> bool:
        """Add a single seed URL."""
        return self.push(url, priority, depth)

    def seed_urls(self, urls: List[str], priority: float = 0.5) -> int:
        """Add multiple seed URLs. Returns count added."""
        return sum(1 for u in urls if self.push(u, priority, 0))

    def seed_from_atlas(self, limit: int = 30) -> int:
        """Load pending 'url' items from IgnoranceAtlas and enqueue them."""
        count = 0
        for item in _query_atlas_urls(limit):
            url = item["payload"]
            priority = item.get("priority", 0.3)
            depth = item.get("chain_depth", 0)
            if self.push(url, priority, depth):
                count += 1
        return count

    def push(self, url: str, priority: float = 0.5, depth: int = 0) -> bool:
        """Push a URL onto the queue. Returns False if already visited or over depth."""
        url = _normalize_url(url)
        if not url:
            return False
        if url in self._visited:
            return False
        if depth > self._max_depth:
            return False
        if not self._url_allowed(url):
            return False
        # Don't enqueue duplicates already in the heap
        for _, _, _, existing in self._heap:
            if existing == url:
                return False
        self._seq += 1
        # heapq is min-heap: negate priority so higher priority pops first
        heapq.heappush(self._heap, (-priority, depth, self._seq, url))
        return True

    def pop(self) -> Optional[Tuple[str, int, float]]:
        """Pop the next (url, depth, priority) or None if queue empty."""
        while self._heap:
            neg_pri, depth, _seq, url = heapq.heappop(self._heap)
            if url in self._visited:
                continue
            domain = urlparse(url).netloc
            if domain in self._locked_domains:
                continue  # robots.txt denied entire domain
            self._visited.add(url)
            self._respect_delay(domain)
            return url, depth, -neg_pri
        return None

    def mark_locked_domain(self, domain: str) -> None:
        """Permanently exclude a domain (e.g. robots.txt disallows all)."""
        self._locked_domains.add(domain)

    @property
    def pending(self) -> int:
        return len(self._heap)

    @property
    def visited_count(self) -> int:
        return len(self._visited)

    # --- robots.txt ---

    def _url_allowed(self, url: str) -> bool:
        """Check robots.txt for a single URL."""
        parsed = urlparse(url)
        domain = parsed.netloc
        if domain in self._locked_domains:
            return False
        rp = self._get_robots(domain)
        if rp is None:
            return True  # no robots.txt = allow
        return rp.can_fetch(USER_AGENT, url)

    def _get_robots(self, domain: str) -> Optional[urllib.robotparser.RobotFileParser]:
        """Get cached RobotFileParser for domain, fetching if needed."""
        now = time.time()
        cached = self._robots_cache.get(domain)
        if cached and (now - cached[1]) < ROBOTS_CACHE_TTL:
            return cached[0]
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"http://{domain}/robots.txt")
        try:
            rp.read()
            self._robots_cache[domain] = (rp, now)
            return rp
        except Exception:
            self._robots_cache[domain] = (None, now)  # type: ignore[assignment]
            return None

    # --- rate limiting ---

    def _respect_delay(self, domain: str) -> None:
        """Sleep if needed to respect per-domain request delay."""
        now = time.time()
        last = self._domain_last.get(domain, 0)
        wait = REQUEST_DELAY - (now - last)
        if wait > 0:
            time.sleep(wait)
        self._domain_last[domain] = time.time()


# ---------------------------------------------------------------------------
# ContentExtractor -- HTML to clean text + Markdown
# ---------------------------------------------------------------------------

class ContentExtractor:
    """Extract clean text from HTML pages, convert to Markdown.

    - Strips nav/footer/aside/script/style boilerplate
    - Attempts to find main content area
    - Converts remaining HTML to basic Markdown
    - Zero external dependencies (stdlib regex only)
    """

    @staticmethod
    def extract(html: str, url: str = "") -> PageContent:
        """Extract title, clean text, and Markdown from raw HTML."""
        title = ContentExtractor._extract_title(html)
        # Stage 1: strip boilerplate blocks
        cleaned = _STRIP_TAGS_RE.sub("", html)
        # Stage 2: find main content (heuristic)
        body = ContentExtractor._find_main_content(cleaned)
        # Stage 3: plain text (all tags removed)
        text = _ANY_TAG_RE.sub(" ", body)
        text = _WHITESPACE_RE.sub(" ", text).strip()
        # Stage 4: Markdown conversion
        markdown = ContentExtractor._to_markdown(body, url)

        return PageContent(
            url=url,
            title=title,
            text=text,
            markdown=markdown,
            links=[],  # filled later by LinkDiscoverer
        )

    @staticmethod
    def _extract_title(html: str) -> str:
        m = _TITLE_RE.search(html)
        if m:
            return _WHITESPACE_RE.sub(" ", m.group(1)).strip()
        return ""

    @staticmethod
    def _find_main_content(html: str) -> str:
        """Heuristic: prefer <article>, <main>, or the largest <div>/<section>."""
        # Try <article> first
        for tag in ("article", "main"):
            m = re.search(
                rf"<{tag}[^>]*>(.*?)</{tag}>", html, re.DOTALL | re.IGNORECASE
            )
            if m and len(m.group(1)) > 200:
                return m.group(1)
        # Fallback: strip only the worst offenders, keep everything else
        return html

    @staticmethod
    def _to_markdown(html: str, base_url: str = "") -> str:
        """Convert stripped HTML fragment to basic Markdown."""
        md = html
        # Headings
        for i in range(6, 0, -1):
            md = re.sub(
                rf"<h{i}[^>]*>(.*?)</h{i}>",
                rf"\n\n{'#' * i} \1\n",
                md,
                flags=re.DOTALL | re.IGNORECASE,
            )
        # Paragraphs
        md = re.sub(r"<p[^>]*>", "\n\n", md, flags=re.IGNORECASE)
        md = re.sub(r"</p>", "", md, flags=re.IGNORECASE)
        md = re.sub(r"<br\s*/?>", "\n", md, flags=re.IGNORECASE)
        # Links: <a href="x">text</a> -> [text](x)
        md = re.sub(
            r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            lambda m: f"[{m.group(2).strip() or m.group(1)}]({urljoin(base_url, m.group(1))})",
            md,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Bold / italic
        for tag, md_char in [("strong", "**"), ("b", "**"), ("em", "*"), ("i", "*")]:
            md = re.sub(
                rf"<{tag}[^>]*>(.*?)</{tag}>",
                rf"{md_char}\1{md_char}",
                md,
                flags=re.DOTALL | re.IGNORECASE,
            )
        # Inline code
        md = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", md, flags=re.DOTALL | re.IGNORECASE)
        # Preformatted blocks
        md = re.sub(r"<pre[^>]*>(.*?)</pre>", r"\n```\n\1\n```\n", md, flags=re.DOTALL | re.IGNORECASE)
        # Unordered lists: <li> -> * (inside <ul>)
        md = re.sub(r"<li[^>]*>", "* ", md, flags=re.IGNORECASE)
        md = re.sub(r"</li>", "", md, flags=re.IGNORECASE)
        # Remove remaining tags
        md = _ANY_TAG_RE.sub("", md)
        # Collapse whitespace (preserve blank lines)
        md = re.sub(r"[ \t]+", " ", md)
        md = re.sub(r"\n{3,}", "\n\n", md)
        return md.strip()


# ---------------------------------------------------------------------------
# LinkDiscoverer -- extract and score links
# ---------------------------------------------------------------------------

class LinkDiscoverer:
    """Extract links from HTML, filter for relevance, compute priority.

    - Extracts all <a href> links
    - Filters: same domain, documentation patterns, skips binaries/media
    - Scores links: higher priority for doc/wiki/article patterns
    """

    @staticmethod
    def discover(html: str, base_url: str, current_depth: int) -> List[Tuple[str, float]]:
        """Extract, filter, and score links. Returns [(url, priority), ...]."""
        base_parsed = urlparse(base_url)
        base_domain = base_parsed.netloc
        raw = LinkDiscoverer._extract_all_links(html, base_url)
        scored: List[Tuple[str, float]] = []
        seen: Set[str] = set()
        for link in raw:
            url = _normalize_url(link)
            if not url:
                continue
            if url in seen:
                continue
            seen.add(url)
            parsed = urlparse(url)
            # Same-domain only (with subdomain tolerance)
            if not _same_domain(parsed.netloc, base_domain):
                continue
            # Skip binary / media
            if _is_skip_extension(parsed.path):
                continue
            priority = LinkDiscoverer._score_url(url, current_depth)
            scored.append((url, priority))
        return scored

    @staticmethod
    def _extract_all_links(html: str, base_url: str) -> List[str]:
        """Extract all absolute URLs from <a href> tags."""
        links: List[str] = []
        for m in _LINK_HREF_RE.finditer(html):
            href = m.group(1).strip()
            absolute = urljoin(base_url, href)
            links.append(absolute)
        return links

    @staticmethod
    def _score_url(url: str, depth: int) -> float:
        """Score a URL for crawl priority (0.0 - 1.0)."""
        parsed = urlparse(url)
        path = parsed.path.lower()

        # Shorter paths = more important
        depth_penalty = depth * 0.10
        path_segments = len([s for s in path.split("/") if s])
        segment_penalty = max(0, (path_segments - 2) * 0.05)

        score = 0.5  # base

        # Boost: documentation patterns
        if _DOC_RE.search(path):
            score += 0.25
        # Boost: short path (likely top-level page)
        if path_segments <= 2:
            score += 0.10
        # Boost: index / readme
        if path.endswith("/") or re.search(r"(index|readme|main)", path):
            score += 0.05
        # Penalize: query strings (dynamic pages)
        if parsed.query:
            score -= 0.10
        # Penalize: fragments
        if parsed.fragment:
            score -= 0.05

        score = max(0.05, min(0.95, score - depth_penalty - segment_penalty))
        return round(score, 3)


# ---------------------------------------------------------------------------
# EidosCrawler -- main orchestrator
# ---------------------------------------------------------------------------

class EidosCrawler:
    """Autonomous web crawler orchestrator.

    Coordinates CrawlQueue, ContentExtractor, and LinkDiscoverer.
    Fetches pages, extracts content, discovers new links, persists results.
    """

    def __init__(
        self,
        max_pages: int = MAX_PAGES_PER_SESSION,
        max_depth: int = MAX_CRAWL_DEPTH,
        use_browser: bool = False,
    ) -> None:
        self._max_pages = max_pages
        self._max_depth = max_depth
        self._use_browser = use_browser
        self._queue = CrawlQueue(max_pages=max_pages, max_depth=max_depth)

    # --- public API ---

    def run(
        self,
        seed_urls: Optional[List[str]] = None,
        seed_from_atlas: bool = True,
        on_page: Optional[Callable[[PageContent], None]] = None,
    ) -> CrawlSession:
        """Run a full crawl session.

        Args:
            seed_urls: Initial URLs to crawl.
            seed_from_atlas: Also seed from IgnoranceAtlas URL items.
            on_page: Optional callback(page) after each page is processed.

        Returns:
            CrawlSession with stats and results.
        """
        t0 = time.time()
        session = CrawlSession(seed_urls=list(seed_urls or []))

        # Seed phase
        if seed_urls:
            self._queue.seed_urls(seed_urls)
        if seed_from_atlas:
            n_atlas = self._queue.seed_from_atlas()
            session.links_added_to_atlas += n_atlas

        # Crawl loop
        while session.pages_crawled < self._max_pages:
            item = self._queue.pop()
            if item is None:
                break
            url, depth, priority = item

            page = self._fetch_and_extract(url)
            page.depth = depth

            if page.status == 200 and page.error == "":
                session.pages_crawled += 1
                # Discover links on success
                links = LinkDiscoverer.discover(
                    _page_html_cache.get(url, ""), url, depth
                )
                page.links = [u for u, _ in links]
                session.links_discovered += len(links)
                for link_url, link_priority in links:
                    if self._queue.push(link_url, link_priority, depth + 1):
                        session.links_added_to_atlas += 1
                # Persist
                self._persist(page)
            else:
                if page.status == 0 and "robots" in page.error.lower():
                    session.pages_skipped += 1
                else:
                    session.pages_failed += 1

            session.results.append(page)
            if on_page:
                on_page(page)

        session.duration_s = round(time.time() - t0, 2)
        return session

    def crawl_single(self, url: str, depth: int = 0) -> PageContent:
        """Fetch and extract a single page (no link discovery)."""
        page = self._fetch_and_extract(url)
        page.depth = depth
        if page.status == 200 and page.error == "":
            self._persist(page)
        return page

    # --- internal ---

    def _fetch_and_extract(self, url: str) -> PageContent:
        """Fetch URL and extract content."""
        html, status, error = self._fetch(url)
        # Cache HTML for link discovery after extraction
        _page_html_cache[url] = html or ""
        if error:
            return PageContent(url=url, title="", text="", markdown="",
                              status=status, error=error)
        page = ContentExtractor.extract(html or "", url)
        page.status = status
        return page

    def _fetch(self, url: str) -> Tuple[Optional[str], int, str]:
        """Fetch a URL, returning (html, status, error)."""
        parsed = urlparse(url)
        domain = parsed.netloc

        # Check robots.txt
        rp = self._queue._get_robots(domain)  # noqa: SLF001
        if rp is not None and not rp.can_fetch(USER_AGENT, url):
            return None, 0, f"Blocked by robots.txt for {domain}"

        if self._use_browser:
            return self._fetch_with_browser(url)

        return self._fetch_with_urllib(url)

    @staticmethod
    def _fetch_with_urllib(url: str) -> Tuple[Optional[str], int, str]:
        """Fetch using urllib (fast, no JS)."""
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if "html" not in content_type and "text/plain" not in content_type:
                    return None, resp.status, f"Non-HTML content-type: {content_type}"
                raw = resp.read(MAX_CONTENT_BYTES)
                # Decode
                html: Optional[str] = None
                for enc in ("utf-8", "latin-1", "cp1252", "iso-8859-1"):
                    try:
                        html = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                if html is None:
                    html = raw.decode("utf-8", errors="replace")
                return html, 200, ""
        except urllib.error.HTTPError as e:
            return None, e.code, f"HTTP {e.code}"
        except urllib.error.URLError as e:
            return None, 0, f"Fetch error: {e.reason}"
        except Exception as e:
            return None, 0, f"Fetch error: {e}"

    @staticmethod
    def _fetch_with_browser(url: str) -> Tuple[Optional[str], int, str]:
        """Fetch using Firefox ESR via browser_manager (handles JS pages)."""
        try:
            from core.browser_manager import navigate_with_session
            result = navigate_with_session(url, extract_text=True)
            if result.get("ok"):
                html = result.get("html", "") or ""
                text = result.get("text", "")
                if not html and text:
                    html = f"<html><body><pre>{text}</pre></body></html>"
                return html, 200, ""
            return None, 0, result.get("error", "Browser fetch failed")
        except ImportError:
            return None, 0, "browser_manager not available"
        except Exception as e:
            return None, 0, f"Browser error: {e}"

    def _persist(self, page: PageContent) -> None:
        """Save extracted content to filesystem and brain DB."""
        # 1. Save Markdown file via eidos_filesystem
        _save_to_filesystem(page)
        # 2. Save knowledge node to evolution_brain.db
        _save_to_brain_db(page)


# HTML cache (URL -> raw HTML) for link discovery after extraction
_page_html_cache: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _save_to_filesystem(page: PageContent) -> Optional[Path]:
    """Save page content as Markdown using eidos_filesystem."""
    domain = page.domain or urlparse(page.url).netloc
    safe_path = re.sub(r"[^a-zA-Z0-9._-]", "_", page.url.split("/")[-1] or "index")
    rel_path = f"{domain}/{safe_path}.md"
    file_path = CRAWL_DIR / rel_path

    header = (
        f"# {page.title or 'Untitled'}\n\n"
        f"**URL:** {page.url}\n"
        f"**Domain:** {domain}\n"
        f"**Crawled:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"---\n\n"
    )
    content = header + page.markdown

    try:
        from core.eidos_filesystem import write_file
        write_file(str(file_path), content)
        return file_path
    except ImportError:
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return file_path
        except Exception:
            return None


def _save_to_brain_db(page: PageContent) -> bool:
    """Insert page content into evolution_brain.db knowledge_nodes."""
    node_id = hashlib.md5(f"crawl:{page.url}".encode()).hexdigest()[:16]
    concept = page.title or page.url
    now = time.time()
    try:
        conn = _get_conn_crawler(str(BRAIN_DB_PATH))
        conn.execute(
            """INSERT OR REPLACE INTO knowledge_nodes
               (id, concept, definition, category, confidence, source,
                usage_count, last_used, created_at, verified, quality_score)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, 0, ?)""",
            (
                node_id, concept, page.markdown, "web_content", 0.65,
                page.url, now, now, 0.5,
            ),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def _query_atlas_urls(limit: int = 30) -> List[Dict[str, Any]]:
    """Query pending URL items from IgnoranceAtlas."""
    try:
        conn = _get_conn_crawler(str(ATLAS_DB_PATH))
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """SELECT * FROM ignorance_atlas
               WHERE item_type = 'url' AND status = 'pending'
               ORDER BY priority DESC, discovered_at ASC
               LIMIT ?""",
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _normalize_url(url: str) -> Optional[str]:
    """Normalize and validate a URL. Returns None if invalid."""
    url = url.strip()
    if not url or url.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    # Rebuild without fragment
    clean = urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc.lower(), parsed.path or "/",
         parsed.params, parsed.query, "")
    )
    return clean


def _same_domain(domain_a: str, domain_b: str) -> bool:
    """Check if two domains are the same (tolerating 'www.' prefix)."""
    a = domain_a.lstrip("www.").lower()
    b = domain_b.lstrip("www.").lower()
    return a == b


def _is_skip_extension(path: str) -> bool:
    """Check if a URL path has a non-HTML extension."""
    ext = Path(path).suffix.lower()
    return ext in _SKIP_EXTENSIONS


# ---------------------------------------------------------------------------
# Integration: wire into curiosity engine
# ---------------------------------------------------------------------------

def crawl_from_curiosity(
    url: str,
    parent_item_id: Optional[int] = None,
    depth: int = 0,
    max_pages: int = 10,
) -> Dict[str, Any]:
    """Entry point for CuriosityExplorer._explore_url().

    Called when the curiosity engine picks a 'url' item to explore.
    Runs a focused crawl from that URL, returns content + discovered links
    that should be added to the Atlas as follow_up items.

    Args:
        url: The URL to explore.
        parent_item_id: Atlas item ID that triggered this exploration.
        depth: Current chain depth from Atlas.
        max_pages: Max pages to crawl in this focused session.

    Returns:
        Dict with keys: ok, result (markdown), detail, follow_ups (list of
        {item_type, payload, priority, discovery_source} dicts for Atlas).
    """
    crawler = EidosCrawler(max_pages=max_pages, max_depth=MAX_CRAWL_DEPTH)
    page = crawler.crawl_single(url, depth=depth)

    if page.status != 200 or page.error:
        return {
            "ok": False,
            "result": "",
            "detail": page.error,
            "follow_ups": [],
        }

    # Discover links from this page
    html = _page_html_cache.get(url, "")
    links = LinkDiscoverer.discover(html, url, depth) if html else []

    follow_ups: List[Dict[str, Any]] = []
    for link_url, link_priority in links[:5]:  # max 5 follow-ups
        follow_ups.append({
            "item_type": "url",
            "payload": link_url,
            "priority": link_priority,
            "discovery_source": "crawler",
        })

    return {
        "ok": True,
        "result": page.markdown[:8000],
        "detail": f"Crawled: {page.title} ({len(page.text)} chars, {len(links)} links found)",
        "follow_ups": follow_ups,
    }


def add_links_to_atlas(links: List[str], priority: float = 0.3, source: str = "crawler") -> int:
    """Add discovered URLs as new 'url' items in the IgnoranceAtlas."""
    count = 0
    try:
        conn = _get_conn_crawler(str(ATLAS_DB_PATH))
        now = time.time()
        for url in links:
            normalized = _normalize_url(url)
            if not normalized:
                continue
            try:
                conn.execute(
                    """INSERT INTO ignorance_atlas
                       (item_type, payload, priority, status, discovered_at, discovery_source)
                       VALUES ('url', ?, ?, 'pending', ?, ?)""",
                    (normalized, priority, now, source),
                )
                count += 1
            except sqlite3.IntegrityError:
                pass  # duplicate
        conn.commit()
        conn.close()
    except Exception:
        pass
    return count


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_crawler_instance: Optional[EidosCrawler] = None


def get_crawler(
    max_pages: int = MAX_PAGES_PER_SESSION,
    max_depth: int = MAX_CRAWL_DEPTH,
) -> EidosCrawler:
    """Get or create the singleton EidosCrawler instance."""
    global _crawler_instance
    if _crawler_instance is None:
        _crawler_instance = EidosCrawler(max_pages=max_pages, max_depth=max_depth)
    return _crawler_instance


def crawl_url(url: str, max_pages: int = 10) -> CrawlSession:
    """Convenience: crawl starting from a single URL."""
    crawler = get_crawler(max_pages=max_pages)
    return crawler.run(seed_urls=[url], seed_from_atlas=False)
