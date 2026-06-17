#!/usr/bin/env python3
"""
core/man_see_also_bridge.py — Extract SEE ALSO references from man pages
and create automatic cross-category edges in EIDOS's knowledge graph.

P0 feature. ZERO LLM cost. +27K potential edges.

Strategy:
  1. Query quality nodes (confidence >= 0.55) from sources that likely
     represent real commands (active:man, active:apt, active:whatis,
     kali_tools).
  2. For each, run `man -P cat <concept>` and extract SEE ALSO section.
  3. Parse command references (format: cmd(section)).
  4. For each referenced command:
     - If it exists as a knowledge_node: create edge with relation_type
       'man_see_also', strength 0.75.
     - If not: create a pending node with category from man section number.
  5. Batch process (100 per batch). Track progress in man_see_also_progress.

Usage:
    python3 core/man_see_also_bridge.py          # full run
    python3 core/man_see_also_bridge.py --dry-run  # preview only
    python3 core/man_see_also_bridge.py --report   # show progress/stats
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from core.db import get_conn

log = logging.getLogger("eidos.man_see_also")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
BATCH_SIZE = 100
MAN_TIMEOUT = 8       # seconds per man -P cat call
MAX_WORKERS = 4       # parallel subprocess workers

# Sources that contain real commands with man pages.
# Order matters: processed sequentially for batches.
# Primary sources (clean command names):
COMMAND_SOURCES_PRIMARY = (
    "active:man",        # 3,107 nodes — clean command names from man pages
    "active:whatis",     # 589 nodes — clean names from whatis DB
    "active:apt",        # 275 nodes — package/command names from APT
)
# Secondary sources (may need name extraction):
COMMAND_SOURCES_EXTENDED = (
    "kali_tools",        # 156 nodes — names like "[Kali/...] cmd"
)

# Combined for queries
COMMAND_SOURCES = COMMAND_SOURCES_PRIMARY + COMMAND_SOURCES_EXTENDED

# Regex: match cmd(section) — e.g. "scp(1)", "ssh_config(5)", "tun(4)"
SEE_ALSO_RE = re.compile(r"(\S+?)\s*\((\d\w*)\)")

# Man section number -> category for new nodes
SECTION_TO_CATEGORY = {
    "1": "user_command",
    "1p": "user_command",
    "2": "system_call",
    "3": "library_function",
    "3p": "library_function",
    "3PCAP": "library_function",
    "4": "device_file",
    "4P": "device_file",
    "5": "file_format",
    "6": "game",
    "7": "miscellaneous",
    "8": "admin_command",
}

# Regex to extract clean command name from bracketed prefixes
# e.g. "[Kali/Reconocimiento] nmap" -> "nmap"
BRACKET_PREFIX_RE = re.compile(r"^\[.*?\]\s*(.+)$")


def clean_concept_name(concept: str) -> str:
    """Extract the clean command name from potentially prefixed concepts.

    Examples:
        "[Kali/Reconocimiento] nmap" -> "nmap"
        "cat" -> "cat"
        "a2enconf, a2disconf" -> "a2enconf"
    """
    # Strip bracketed prefix: e.g. "[Kali/Scan] nmap"
    m = BRACKET_PREFIX_RE.match(concept.strip())
    if m:
        concept = m.group(1).strip()

    # If the concept contains a comma, take the first part
    # (some man page concepts list multiple names)
    if "," in concept:
        concept = concept.split(",")[0].strip()

    return concept


def create_progress_table(conn):
    """Create the progress tracking table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS man_see_also_progress (
            node_id TEXT PRIMARY KEY,
            concept TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            edges_created INTEGER DEFAULT 0,
            nodes_created INTEGER DEFAULT 0,
            error TEXT,
            elapsed_ms INTEGER,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Index for fast status queries
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_msp_status
        ON man_see_also_progress(status)
    """)
    conn.commit()


def get_quality_command_nodes(conn, batch_size: int = BATCH_SIZE,
                              offset: int = 0) -> List[Tuple[str, str, str, str]]:
    """Get quality nodes from command-related sources, not yet processed.

    Returns list of (node_id, concept, category, source).
    """
    sources_placeholder = ",".join("?" for _ in COMMAND_SOURCES)
    rows = conn.execute(f"""
        SELECT kn.id, kn.concept, kn.category, kn.source
        FROM knowledge_nodes kn
        WHERE kn.confidence >= 0.55
          AND kn.source IN ({sources_placeholder})
          AND kn.id NOT IN (
              SELECT node_id FROM man_see_also_progress
              WHERE status IN ('done', 'empty_refs')
          )
        ORDER BY
            CASE kn.source
                WHEN 'active:man' THEN 1
                WHEN 'active:whatis' THEN 2
                WHEN 'active:apt' THEN 3
                ELSE 4
            END,
            kn.confidence DESC,
            kn.concept
        LIMIT ? OFFSET ?
    """, (*COMMAND_SOURCES, batch_size, offset)).fetchall()
    return [(r[0], r[1], r[2] or "", r[3] or "") for r in rows]


def extract_see_also(concept: str) -> List[Tuple[str, str]]:
    """Run man -P cat and extract SEE ALSO references.

    Tries the raw concept name first, then falls back to a cleaned version
    (stripping bracket prefixes like "[Kali/Scan] nmap" -> "nmap").

    Returns list of (command, section) tuples.
    """
    # Try raw concept first, then cleaned version
    candidates = [concept]
    cleaned = clean_concept_name(concept)
    if cleaned != concept:
        candidates.append(cleaned)

    for candidate in candidates:
        refs = _try_man_extract(candidate)
        if refs:
            return refs

    return []


def _try_man_extract(concept: str) -> List[Tuple[str, str]]:
    """Execute man -P cat and parse the SEE ALSO section."""
    try:
        result = subprocess.run(
            ["man", "-P", "cat", concept],
            capture_output=True, text=True, timeout=MAN_TIMEOUT,
        )
        if result.returncode != 0 or not result.stdout:
            return []

        text = result.stdout
        lines = text.split("\n")
        in_see_also = False
        see_also_lines: List[str] = []

        # Known section headers that follow SEE ALSO
        NEXT_SECTIONS = {
            "AUTHOR", "AUTHORS", "BUGS", "COPYRIGHT", "NOTES", "HISTORY",
            "STANDARDS", "AVAILABILITY", "REPORTING BUGS", "EXAMPLES",
            "ENVIRONMENT", "FILES", "DIAGNOSTICS", "SECURITY",
            "CONFORMING TO", "CAVEATS", "RESTRICTIONS",
            "ACKNOWLEDGEMENTS", "ACKNOWLEDGMENTS",
        }

        for line in lines:
            stripped = line.strip()
            if not in_see_also:
                if stripped == "SEE ALSO":
                    in_see_also = True
                continue

            # Stop at next major section
            if stripped and stripped == stripped.upper() and len(stripped) < 40:
                if stripped in NEXT_SECTIONS:
                    break

            if stripped:
                see_also_lines.append(stripped)

            if len(see_also_lines) > 25:
                break

        if not see_also_lines:
            return []

        # Parse: join all lines, find cmd(section) patterns
        full_text = " ".join(see_also_lines)
        references: List[Tuple[str, str]] = []
        seen: Set[str] = set()

        for match in SEE_ALSO_RE.finditer(full_text):
            cmd = match.group(1).strip().rstrip(",)")
            section = match.group(2)
            if cmd and len(cmd) >= 2 and len(cmd) <= 40:
                if cmd.startswith("http") or "/" in cmd:
                    continue
                key = f"{cmd}:{section}"
                if key not in seen:
                    seen.add(key)
                    references.append((cmd, section))

        return references

    except subprocess.TimeoutExpired:
        return []
    except FileNotFoundError:
        return []
    except Exception as e:
        log.debug("_try_man_extract(%s): %s", concept, e)
        return []


def section_to_category(section: str) -> str:
    """Map man page section number to a knowledge node category."""
    return SECTION_TO_CATEGORY.get(section, "man_reference")


def _process_single_concept(args: Tuple[str, str, str, str]) -> Dict:
    """Process a single concept: extract SEE ALSO, return references.

    This runs in a thread pool worker. No DB access.
    """
    node_id, concept, category, source = args
    t0 = time.time()

    references = extract_see_also(concept)
    elapsed_ms = int((time.time() - t0) * 1000)

    # Determine the clean (searchable) concept name for self-reference filtering
    cleaned = clean_concept_name(concept)

    return {
        "node_id": node_id,
        "concept": concept,
        "cleaned_concept": cleaned,
        "category": category,
        "source": source,
        "references": references,
        "elapsed_ms": elapsed_ms,
        "error": None,
    }


def process_batch(conn, batch_concepts: List[Tuple[str, str, str, str]],
                  concept_to_id: Dict[str, str]) -> Dict:
    """Process a batch of concepts in parallel, then write edges to DB.

    Args:
        conn: DB connection (main thread only).
        batch_concepts: List of (node_id, concept, category, source).
        concept_to_id: Dict mapping concept_name -> node_id for fast lookup.

    Returns stats dict.
    """
    total_edges = 0
    total_new_nodes = 0
    processed = 0
    errors = 0
    no_man = 0
    no_refs = 0

    # Phase 1: Run man commands in parallel (IO-bound)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_process_single_concept, args): args
            for args in batch_concepts
        }

        results = []
        for future in as_completed(futures):
            try:
                result = future.result(timeout=MAN_TIMEOUT + 5)
                results.append(result)
            except Exception as e:
                args = futures[future]
                results.append({
                    "node_id": args[0],
                    "concept": args[1],
                    "category": args[2],
                    "source": args[3],
                    "references": [],
                    "elapsed_ms": 0,
                    "error": str(e)[:200],
                })

    # Phase 2: Write edges to DB (main thread, serial)
    # Pre-build insert statements for batching
    edge_inserts = []
    node_inserts = []

    for result in results:
        node_id = result["node_id"]
        concept = result["concept"]
        cleaned_concept = result["cleaned_concept"]
        refs = result["references"]
        elapsed_ms = result["elapsed_ms"]
        error = result["error"]

        if error:
            conn.execute(
                "INSERT OR REPLACE INTO man_see_also_progress "
                "(node_id, concept, status, error, elapsed_ms) "
                "VALUES (?, ?, 'error', ?, ?)",
                (node_id, concept, error, elapsed_ms),
            )
            errors += 1
            continue

        if not refs:
            # No man page or no SEE ALSO section
            status_msg = "empty_refs"
            conn.execute(
                "INSERT OR REPLACE INTO man_see_also_progress "
                "(node_id, concept, status, edges_created, nodes_created, elapsed_ms) "
                "VALUES (?, ?, ?, 0, 0, ?)",
                (node_id, concept, status_msg, elapsed_ms),
            )
            no_refs += 1
            continue

        edges_created = 0
        nodes_created = 0

        for ref_cmd, ref_section in refs:
            # Skip self-references (compare against cleaned concept name)
            if ref_cmd.lower() == cleaned_concept.lower():
                continue

            # Lookup target node
            ref_lower = ref_cmd.lower()
            target_id = concept_to_id.get(ref_lower)

            if target_id and target_id != node_id:
                # Edge to existing node
                edge_inserts.append((node_id, target_id, "man_see_also", 0.75))
                edges_created += 1
            elif not target_id:
                # Create pending node + edge
                new_id = f"man_ref_{node_id}_{ref_cmd}"
                ref_category = section_to_category(ref_section)
                ref_def = f"Referenced by man page: {cleaned_concept}({ref_section})"
                node_inserts.append((new_id, ref_cmd, ref_def, ref_category,
                                     0.35, "man_see_also"))
                edge_inserts.append((node_id, new_id, "man_see_also", 0.75))
                nodes_created += 1
                edges_created += 1
                # Add to concept index so duplicate refs in same batch are caught
                concept_to_id[ref_lower] = new_id

        # Batch-insert new nodes
        if node_inserts:
            conn.executemany(
                "INSERT OR IGNORE INTO knowledge_nodes "
                "(id, concept, definition, category, confidence, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                node_inserts,
            )
            node_inserts.clear()

        # Batch-insert edges (with dedup check)
        if edge_inserts:
            # Filter out duplicate edges that already exist
            # Build a set of existing edge keys for fast check
            # We batch this by querying all potential dupes
            # For simplicity, use INSERT OR IGNORE with a unique constraint
            # But knowledge_edges doesn't have one on (from_node, to_node, relation_type)
            # So we check manually per batch
            conn.executemany(
                "INSERT INTO knowledge_edges "
                "(from_node, to_node, relation_type, strength) "
                "SELECT ?, ?, ?, ? "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM knowledge_edges "
                "  WHERE from_node = ? AND to_node = ? AND relation_type = ?"
                ")",
                [(fn, tn, rt, st, fn, tn, rt) for fn, tn, rt, st in edge_inserts],
            )
            edge_inserts.clear()

        # Update progress
        conn.execute(
            "INSERT OR REPLACE INTO man_see_also_progress "
            "(node_id, concept, status, edges_created, nodes_created, elapsed_ms) "
            "VALUES (?, ?, 'done', ?, ?, ?)",
            (node_id, concept, edges_created, nodes_created, elapsed_ms),
        )

        total_edges += edges_created
        total_new_nodes += nodes_created
        processed += 1

    conn.commit()

    return {
        "processed": processed,
        "edges_created": total_edges,
        "nodes_created": total_new_nodes,
        "errors": errors,
        "no_man": no_refs,
        "empty_refs": no_refs,
    }


def build_concept_index(conn) -> Dict[str, str]:
    """Build a dict mapping concept (lowercase) -> node_id for fast lookups.

    Covers all quality nodes (confidence >= 0.3) so we can link to
    referenced commands even if they're not from active:man.
    """
    index: Dict[str, str] = {}
    rows = conn.execute(
        "SELECT id, LOWER(concept) FROM knowledge_nodes WHERE confidence >= 0.3"
    ).fetchall()
    for nid, concept_lower in rows:
        if concept_lower and concept_lower not in index:
            index[concept_lower] = nid
    log.info("Concept index: %d entries", len(index))
    return index


def get_progress_stats(conn) -> Dict:
    """Get current progress statistics."""
    total = conn.execute(
        "SELECT COUNT(*) FROM man_see_also_progress"
    ).fetchone()[0]
    done = conn.execute(
        "SELECT COUNT(*) FROM man_see_also_progress WHERE status = 'done'"
    ).fetchone()[0]
    errors = conn.execute(
        "SELECT COUNT(*) FROM man_see_also_progress WHERE status = 'error'"
    ).fetchone()[0]
    total_edges = conn.execute(
        "SELECT COALESCE(SUM(edges_created), 0) FROM man_see_also_progress"
    ).fetchone()[0]
    total_nodes = conn.execute(
        "SELECT COALESCE(SUM(nodes_created), 0) FROM man_see_also_progress"
    ).fetchone()[0]
    total_man_see_also_edges = conn.execute(
        "SELECT COUNT(*) FROM knowledge_edges WHERE relation_type = 'man_see_also'"
    ).fetchone()[0]

    return {
        "total_processed": total,
        "done": done,
        "errors": errors,
        "pending": total - done - errors,
        "total_edges_in_progress": total_edges,
        "total_nodes_created": total_nodes,
        "total_man_see_also_edges_in_db": total_man_see_also_edges,
    }


def get_cross_category_stats(conn) -> List[Tuple[str, str, int]]:
    """Get top cross-category connections from man_see_also edges."""
    rows = conn.execute("""
        SELECT
            COALESCE(n1.category, 'unknown') AS source_cat,
            COALESCE(n2.category, 'unknown') AS target_cat,
            COUNT(*) AS cnt
        FROM knowledge_edges ke
        JOIN knowledge_nodes n1 ON ke.from_node = n1.id
        JOIN knowledge_nodes n2 ON ke.to_node = n2.id
        WHERE ke.relation_type = 'man_see_also'
          AND n1.category != n2.category
        GROUP BY source_cat, target_cat
        ORDER BY cnt DESC
        LIMIT 40
    """).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def get_top_connections(conn, limit: int = 10) -> List[Tuple[str, str, str, str]]:
    """Get top man_see_also connections with concept names."""
    rows = conn.execute("""
        SELECT
            n1.concept AS source_concept,
            n1.category AS source_cat,
            n2.concept AS target_concept,
            n2.category AS target_cat
        FROM knowledge_edges ke
        JOIN knowledge_nodes n1 ON ke.from_node = n1.id
        JOIN knowledge_nodes n2 ON ke.to_node = n2.id
        WHERE ke.relation_type = 'man_see_also'
          AND n1.category != n2.category
        ORDER BY ke.strength DESC, n1.confidence DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def run_full(conn, max_batches: Optional[int] = None):
    """Run the full SEE ALSO extraction pipeline."""
    log.info("=" * 60)
    log.info("MAN SEE ALSO BRIDGE — Starting full run")
    log.info("=" * 60)

    # Setup
    create_progress_table(conn)

    # Build concept index for fast lookups
    concept_index = build_concept_index(conn)
    log.info("Concept index built: %d entries", len(concept_index))

    # Count total candidates
    sources_placeholder = ",".join("?" for _ in COMMAND_SOURCES)
    total_candidates = conn.execute(f"""
        SELECT COUNT(*) FROM knowledge_nodes
        WHERE confidence >= 0.55
          AND source IN ({sources_placeholder})
          AND id NOT IN (
              SELECT node_id FROM man_see_also_progress WHERE status = 'done'
          )
    """, COMMAND_SOURCES).fetchone()[0]
    log.info("Total candidates to process: %d", total_candidates)

    # Process in batches
    batch_num = 0
    offset = 0
    grand_total_edges = 0
    grand_total_nodes = 0
    grand_processed = 0

    t_start = time.time()

    while True:
        batch_concepts = get_quality_command_nodes(conn, BATCH_SIZE, offset)
        if not batch_concepts:
            log.info("No more concepts to process.")
            break

        batch_num += 1
        log.info("Batch %d: %d concepts (offset=%d)...",
                 batch_num, len(batch_concepts), offset)

        stats = process_batch(conn, batch_concepts, concept_index)

        grand_processed += stats["processed"]
        grand_total_edges += stats["edges_created"]
        grand_total_nodes += stats["nodes_created"]

        elapsed = time.time() - t_start
        rate = grand_processed / elapsed if elapsed > 0 else 0

        log.info(
            "Batch %d done: %d processed, %d edges, %d nodes, "
            "%d errs, %.1f/s, elapsed %.0fs",
            batch_num, stats["processed"], stats["edges_created"],
            stats["nodes_created"], stats["errors"],
            rate, elapsed,
        )

        offset += BATCH_SIZE

        if max_batches and batch_num >= max_batches:
            log.info("Reached max batches limit (%d).", max_batches)
            break

    total_elapsed = time.time() - t_start
    log.info("=" * 60)
    log.info("FULL RUN COMPLETE")
    log.info("  Total processed: %d concepts", grand_processed)
    log.info("  Total edges created: %d", grand_total_edges)
    log.info("  Total new nodes created: %d", grand_total_nodes)
    log.info("  Total time: %.0fs (%.1f min)", total_elapsed, total_elapsed / 60)
    log.info("=" * 60)

    return {
        "processed": grand_processed,
        "edges_created": grand_total_edges,
        "nodes_created": grand_total_nodes,
        "elapsed_s": total_elapsed,
    }


def run_dry_run(conn, sample_size: int = 30):
    """Preview: show what would be processed without writing to DB."""
    print(f"=== DRY RUN (sample: {sample_size}) ===\n")

    # Sample from primary sources (clean command names) first
    sources_placeholder = ",".join("?" for _ in COMMAND_SOURCES_PRIMARY)
    samples = conn.execute(f"""
        SELECT kn.id, kn.concept, kn.category, kn.source
        FROM knowledge_nodes kn
        WHERE kn.confidence >= 0.55
          AND kn.source IN ({sources_placeholder})
        ORDER BY RANDOM()
        LIMIT ?
    """, (*COMMAND_SOURCES_PRIMARY, sample_size)).fetchall()

    total_refs = 0
    with_refs = 0
    for row in samples:
        nid, concept, cat, src = row
        refs = extract_see_also(concept)
        total_refs += len(refs)
        if refs:
            with_refs += 1
            print(f"  {concept:30s} ({cat:20s}): {len(refs):2d} refs → {[r[0] for r in refs[:6]]}")
        else:
            print(f"  {concept:30s} ({cat:20s}): no SEE ALSO refs")

    print(f"\n  With refs: {with_refs}/{len(samples)}, total refs: {total_refs}")
    print(f"  Est. edges (if 3,107 nodes, {with_refs/len(samples)*100:.0f}% hit rate): ~{int(total_refs/len(samples) * 3107)}")
    print(f"  Checked {len(samples)} concepts (from active:man/whatis/apt).")


def run_report(conn):
    """Show current progress and statistics."""
    print("=== MAN SEE ALSO BRIDGE — Progress Report ===\n")

    stats = get_progress_stats(conn)
    for key, value in stats.items():
        print(f"  {key}: {value}")

    # Cross-category stats
    print("\n=== Top Cross-Category Connections ===")
    cross = get_cross_category_stats(conn)
    if cross:
        for src_cat, tgt_cat, cnt in cross[:20]:
            print(f"  {src_cat:25s} → {tgt_cat:25s} : {cnt} edges")
    else:
        print("  (no man_see_also edges yet)")

    # Top connections
    print("\n=== Top 10 Connections (cross-category) ===")
    top = get_top_connections(conn, limit=10)
    if top:
        for i, (src_concept, src_cat, tgt_concept, tgt_cat) in enumerate(top, 1):
            print(f"  {i}. {src_concept} [{src_cat}] → {tgt_concept} [{tgt_cat}]")
    else:
        print("  (no connections yet)")

    # Count how many active:man concepts still pending
    sources_placeholder = ",".join("?" for _ in COMMAND_SOURCES)
    remaining = conn.execute(f"""
        SELECT COUNT(*) FROM knowledge_nodes
        WHERE confidence >= 0.55
          AND source IN ({sources_placeholder})
          AND id NOT IN (
              SELECT node_id FROM man_see_also_progress WHERE status = 'done'
          )
    """, COMMAND_SOURCES).fetchone()[0]
    print(f"\n  Remaining concepts to process: {remaining}")


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="EIDOS Man Page SEE ALSO Bridge (P0 — ZERO LLM cost)"
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview without writing to DB")
    ap.add_argument("--report", action="store_true",
                    help="Show progress stats and cross-category connections")
    ap.add_argument("--max-batches", type=int, default=None,
                    help="Limit number of batches (for testing)")
    _default_batch = BATCH_SIZE
    _default_workers = MAX_WORKERS
    ap.add_argument("--batch-size", type=int, default=None,
                    help=f"Batch size (default: {_default_batch})")
    ap.add_argument("--workers", type=int, default=None,
                    help=f"Parallel workers (default: {_default_workers})")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="Debug logging")

    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Apply CLI overrides via module-level rebinding
    import core.man_see_also_bridge as _mod
    if args.workers is not None:
        _mod.MAX_WORKERS = args.workers
    if args.batch_size is not None:
        _mod.BATCH_SIZE = args.batch_size

    conn = get_conn(BRAIN_DB, timeout=30)

    try:
        if args.dry_run:
            run_dry_run(conn)
        elif args.report:
            run_report(conn)
        else:
            run_full(conn, max_batches=args.max_batches)
    finally:
        # Don't close — conn is cached and may be reused
        pass


if __name__ == "__main__":
    main()
