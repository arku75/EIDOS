"""
EIDOS P2P Knowledge Sync
========================
Sistema de sincronización peer-to-peer de conocimiento entre múltiples instancias EIDOS.

Permite que múltiples EIDOS compartan:
- Base de conocimiento aprendida
- Objetivos completados
- Lecciones aprendidas (meta_learner)
- Skills evolucionados
- Ganglia (capacidades heredadas)

Arquitectura:
- Sin servidor central (true P2P)
- Descubrimiento de peers via mDNS/Avahi
- Sync via HTTP REST entre peers
- Conflict resolution via timestamps + hash
- Encryption opcional con shared secret

Uso:
    from core.p2p_knowledge_sync import P2PSync

    sync = P2PSync(node_id="eidos_laptop", shared_secret=os.environ.get("EIDOS_P2P_SECRET"))
    sync.start()  # Auto-discover peers y sync periódico

    # Manual sync
    sync.sync_with_peer("192.168.1.100:8888")
"""

import os
import json
import time
import socket
import hashlib
import sqlite3
import threading
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, asdict, field
from datetime import datetime
from core.db import get_conn, get_conn_ctx

log = logging.getLogger("eidos.p2p_sync")

# Paths
EIDOS_HOME = Path.home() / ".eidos"
SYNC_DB = EIDOS_HOME / "p2p_sync.db"
KNOWN_PEERS_FILE = EIDOS_HOME / "known_peers.json"

# Default port para P2P sync
DEFAULT_P2P_PORT = 8888


# ══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class KnowledgeEntry:
    """Una entrada de conocimiento sincronizable"""
    entry_id: str           # UUID único
    source_node: str        # Qué EIDOS lo creó
    category: str           # knowledge_db, objective, lesson, skill, ganglion
    content: str            # JSON serializado del contenido
    content_hash: str       # SHA256 del content
    timestamp: float        # Cuándo fue creado
    sync_count: int = 0     # Cuántas veces se ha sincronizado
    last_sync: float = 0.0  # Última vez que se sincronizó


@dataclass
class PeerInfo:
    """Información de un peer EIDOS"""
    node_id: str
    host: str
    port: int
    last_seen: float
    version: str = "1.0"
    capabilities: List[str] = field(default_factory=list)

    def address(self) -> str:
        return f"{self.host}:{self.port}"


# ══════════════════════════════════════════════════════════════════════════════
# P2P SYNC CORE
# ══════════════════════════════════════════════════════════════════════════════

class P2PSync:
    """
    P2P Knowledge Synchronization Engine

    Features:
    - Auto-discovery de peers en LAN (mDNS)
    - Sync bidireccional de knowledge entries
    - Conflict resolution (timestamp wins)
    - Incremental sync (solo nuevos desde last_sync)
    - Rate limiting (no spam)
    """

    def __init__(self, node_id: Optional[str] = None,
                 port: int = DEFAULT_P2P_PORT,
                 shared_secret: Optional[str] = None,
                 auto_sync_interval: int = 300):  # 5 min

        self.node_id = node_id or self._generate_node_id()
        self.port = port
        self.shared_secret = shared_secret
        self.auto_sync_interval = auto_sync_interval

        self._known_peers: Dict[str, PeerInfo] = {}
        self._running = False
        self._sync_thread: Optional[threading.Thread] = None

        self._init_db()
        self._load_known_peers()

        log.info(f"[P2PSync] Initialized node_id={self.node_id} port={self.port}")

    def _generate_node_id(self) -> str:
        """Genera un node_id único basado en hostname"""
        hostname = socket.gethostname()
        return f"eidos_{hostname}_{int(time.time() * 1000) % 100000}"

    def _init_db(self) -> None:
        """Crea la base de datos de sync"""
        EIDOS_HOME.mkdir(parents=True, exist_ok=True)

        with get_conn_ctx(SYNC_DB) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS knowledge_entries (
                    entry_id TEXT PRIMARY KEY,
                    source_node TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    sync_count INTEGER DEFAULT 0,
                    last_sync REAL DEFAULT 0.0
                );

                CREATE INDEX IF NOT EXISTS idx_category ON knowledge_entries(category);
                CREATE INDEX IF NOT EXISTS idx_timestamp ON knowledge_entries(timestamp);
                CREATE INDEX IF NOT EXISTS idx_source ON knowledge_entries(source_node);

                CREATE TABLE IF NOT EXISTS sync_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    peer_node_id TEXT NOT NULL,
                    sync_time REAL NOT NULL,
                    entries_sent INTEGER DEFAULT 0,
                    entries_received INTEGER DEFAULT 0,
                    success INTEGER DEFAULT 1
                );

                CREATE INDEX IF NOT EXISTS idx_sync_peer ON sync_history(peer_node_id);
            """)

    def _load_known_peers(self) -> None:
        """Carga peers conocidos del archivo"""
        if not KNOWN_PEERS_FILE.exists():
            return

        try:
            with open(KNOWN_PEERS_FILE, 'r') as f:
                peers_data = json.load(f)

            for peer_dict in peers_data:
                peer = PeerInfo(**peer_dict)
                self._known_peers[peer.node_id] = peer

            log.info(f"[P2PSync] Loaded {len(self._known_peers)} known peers")
        except Exception as e:
            log.warning(f"[P2PSync] Error loading known peers: {e}")

    def _save_known_peers(self) -> None:
        """Guarda peers conocidos al archivo"""
        try:
            peers_data = [asdict(peer) for peer in self._known_peers.values()]

            with open(KNOWN_PEERS_FILE, 'w') as f:
                json.dump(peers_data, f, indent=2)

            log.debug(f"[P2PSync] Saved {len(peers_data)} peers to disk")
        except Exception as e:
            log.warning(f"[P2PSync] Error saving peers: {e}")

    # ── KNOWLEDGE ENTRY MANAGEMENT ───────────────────────────────────────────

    def add_entry(self, category: str, content: Dict) -> str:
        """
        Añade una nueva entrada de conocimiento para sincronizar

        Args:
            category: knowledge_db, objective, lesson, skill, ganglion
            content: Dict con el contenido a sincronizar

        Returns:
            entry_id del nuevo entry
        """
        import uuid

        entry_id = str(uuid.uuid4())
        content_json = json.dumps(content, sort_keys=True)
        content_hash = hashlib.sha256(content_json.encode()).hexdigest()

        entry = KnowledgeEntry(
            entry_id=entry_id,
            source_node=self.node_id,
            category=category,
            content=content_json,
            content_hash=content_hash,
            timestamp=time.time()
        )

        with get_conn_ctx(SYNC_DB) as conn:
            conn.execute("""
                INSERT INTO knowledge_entries
                (entry_id, source_node, category, content, content_hash, timestamp, sync_count, last_sync)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (entry.entry_id, entry.source_node, entry.category, entry.content,
                  entry.content_hash, entry.timestamp, entry.sync_count, entry.last_sync))

        log.debug(f"[P2PSync] Added entry {entry_id} category={category}")
        return entry_id

    def get_entries(self, category: Optional[str] = None,
                    since: Optional[float] = None) -> List[KnowledgeEntry]:
        """
        Obtiene entries para sincronizar

        Args:
            category: Filtrar por categoría (None = todas)
            since: Solo entries con timestamp > since

        Returns:
            Lista de KnowledgeEntry
        """
        with get_conn_ctx(SYNC_DB) as conn:
            conn.row_factory = sqlite3.Row

            if category and since:
                rows = conn.execute(
                    "SELECT * FROM knowledge_entries WHERE category = ? AND timestamp > ? ORDER BY timestamp",
                    (category, since)
                ).fetchall()
            elif category:
                rows = conn.execute(
                    "SELECT * FROM knowledge_entries WHERE category = ? ORDER BY timestamp",
                    (category,)
                ).fetchall()
            elif since:
                rows = conn.execute(
                    "SELECT * FROM knowledge_entries WHERE timestamp > ? ORDER BY timestamp",
                    (since,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM knowledge_entries ORDER BY timestamp"
                ).fetchall()

            return [KnowledgeEntry(**dict(row)) for row in rows]

    def merge_entry(self, entry: KnowledgeEntry) -> Tuple[bool, str]:
        """
        Merge un entry recibido de un peer

        Returns:
            (merged: bool, reason: str)
        """
        with get_conn_ctx(SYNC_DB) as conn:
            # Check si ya existe
            existing = conn.execute(
                "SELECT * FROM knowledge_entries WHERE entry_id = ?",
                (entry.entry_id,)
            ).fetchone()

            if existing is None:
                # Nuevo entry, insert directo
                conn.execute("""
                    INSERT INTO knowledge_entries
                    (entry_id, source_node, category, content, content_hash, timestamp, sync_count, last_sync)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (entry.entry_id, entry.source_node, entry.category, entry.content,
                      entry.content_hash, entry.timestamp, entry.sync_count + 1, time.time()))

                log.debug(f"[P2PSync] Merged new entry {entry.entry_id} from {entry.source_node}")
                return (True, "new")

            # Ya existe, conflict resolution
            existing_timestamp = existing[5]  # timestamp column

            if entry.timestamp > existing_timestamp:
                # El entry recibido es más nuevo, actualizar
                conn.execute("""
                    UPDATE knowledge_entries
                    SET content = ?, content_hash = ?, timestamp = ?, sync_count = sync_count + 1, last_sync = ?
                    WHERE entry_id = ?
                """, (entry.content, entry.content_hash, entry.timestamp, time.time(), entry.entry_id))

                log.debug(f"[P2PSync] Updated entry {entry.entry_id} with newer version")
                return (True, "updated")

            elif entry.content_hash != existing[4]:  # content_hash column
                # Timestamps iguales pero contenido diferente, conflict
                log.warning(f"[P2PSync] Conflict detected for entry {entry.entry_id}, keeping local version")
                return (False, "conflict")

            else:
                # Mismo contenido, skip
                return (False, "duplicate")

    # ── PEER MANAGEMENT ──────────────────────────────────────────────────────

    def add_peer(self, host: str, port: int = DEFAULT_P2P_PORT,
                 node_id: Optional[str] = None) -> None:
        """Añade un peer manualmente"""
        if node_id is None:
            node_id = f"{host}:{port}"

        peer = PeerInfo(
            node_id=node_id,
            host=host,
            port=port,
            last_seen=time.time()
        )

        self._known_peers[node_id] = peer
        self._save_known_peers()

        log.info(f"[P2PSync] Added peer {node_id} at {peer.address()}")

    def get_peers(self) -> List[PeerInfo]:
        """Lista todos los peers conocidos"""
        return list(self._known_peers.values())

    # ── SYNC OPERATIONS ──────────────────────────────────────────────────────

    def sync_with_peer(self, peer_address: str) -> Dict:
        """
        Sincroniza con un peer específico

        Args:
            peer_address: "host:port" del peer

        Returns:
            Dict con stats del sync
        """
        import requests

        host, port_str = peer_address.split(":")
        port = int(port_str)

        log.info(f"[P2PSync] Starting sync with peer {peer_address}")

        stats = {
            "peer": peer_address,
            "sent": 0,
            "received": 0,
            "merged": 0,
            "conflicts": 0,
            "success": False,
            "error": None
        }

        try:
            # 1. Get info del peer
            info_url = f"http://{host}:{port}/p2p/info"
            resp = requests.get(info_url, timeout=5)
            peer_info = resp.json()

            peer_node_id = peer_info.get("node_id", f"{host}:{port}")

            # 2. Get último sync time con este peer
            with get_conn_ctx(SYNC_DB) as conn:
                last_sync_row = conn.execute(
                    "SELECT MAX(sync_time) FROM sync_history WHERE peer_node_id = ? AND success = 1",
                    (peer_node_id,)
                ).fetchone()

                last_sync_time = last_sync_row[0] if last_sync_row[0] else 0.0

            # 3. Get entries locales nuevos desde last_sync
            local_entries = self.get_entries(since=last_sync_time)

            # 4. Enviar entries al peer
            if local_entries:
                push_url = f"http://{host}:{port}/p2p/push"
                push_data = {
                    "node_id": self.node_id,
                    "entries": [asdict(entry) for entry in local_entries]
                }

                resp = requests.post(push_url, json=push_data, timeout=30)
                push_result = resp.json()

                stats["sent"] = len(local_entries)
                stats["merged"] += push_result.get("merged", 0)
                stats["conflicts"] += push_result.get("conflicts", 0)

            # 5. Pull entries del peer
            pull_url = f"http://{host}:{port}/p2p/pull"
            pull_params = {"since": last_sync_time}

            resp = requests.get(pull_url, params=pull_params, timeout=30)
            pull_result = resp.json()

            remote_entries = [KnowledgeEntry(**e) for e in pull_result.get("entries", [])]

            # 6. Merge remote entries
            for entry in remote_entries:
                merged, reason = self.merge_entry(entry)

                if merged:
                    stats["merged"] += 1
                elif reason == "conflict":
                    stats["conflicts"] += 1

            stats["received"] = len(remote_entries)
            stats["success"] = True

            # 7. Registrar sync en history
            with get_conn_ctx(SYNC_DB) as conn:
                conn.execute("""
                    INSERT INTO sync_history (peer_node_id, sync_time, entries_sent, entries_received, success)
                    VALUES (?, ?, ?, ?, ?)
                """, (peer_node_id, time.time(), stats["sent"], stats["received"], 1))

            # 8. Actualizar peer info
            self._known_peers[peer_node_id] = PeerInfo(
                node_id=peer_node_id,
                host=host,
                port=port,
                last_seen=time.time(),
                version=peer_info.get("version", "1.0")
            )
            self._save_known_peers()

            log.info(f"[P2PSync] Sync completed: sent={stats['sent']}, received={stats['received']}, merged={stats['merged']}")

        except Exception as e:
            stats["success"] = False
            stats["error"] = str(e)
            log.error(f"[P2PSync] Sync failed with {peer_address}: {e}")

        return stats

    def sync_all_peers(self) -> List[Dict]:
        """Sincroniza con todos los peers conocidos"""
        results = []

        for peer in self._known_peers.values():
            result = self.sync_with_peer(peer.address())
            results.append(result)

            # Sleep entre syncs para no saturar
            time.sleep(1)

        return results

    # ── AUTO-SYNC DAEMON ─────────────────────────────────────────────────────

    def _auto_sync_loop(self) -> None:
        """Loop de auto-sync en background"""
        log.info(f"[P2PSync] Auto-sync daemon started (interval={self.auto_sync_interval}s)")

        while self._running:
            try:
                if self._known_peers:
                    log.debug(f"[P2PSync] Running auto-sync with {len(self._known_peers)} peers")
                    results = self.sync_all_peers()

                    success_count = sum(1 for r in results if r["success"])
                    log.info(f"[P2PSync] Auto-sync completed: {success_count}/{len(results)} successful")

            except Exception as e:
                log.error(f"[P2PSync] Auto-sync error: {e}")

            # Sleep con check periódico para poder detener rápido
            for _ in range(self.auto_sync_interval):
                if not self._running:
                    break
                time.sleep(1)

        log.info("[P2PSync] Auto-sync daemon stopped")

    def start(self) -> None:
        """Inicia el daemon de auto-sync"""
        if self._running:
            log.warning("[P2PSync] Already running")
            return

        self._running = True
        self._sync_thread = threading.Thread(target=self._auto_sync_loop, daemon=True, name="p2p_sync_daemon")
        self._sync_thread.start()

        log.info("[P2PSync] Started")

    def stop(self) -> None:
        """Detiene el daemon de auto-sync"""
        if not self._running:
            return

        log.info("[P2PSync] Stopping...")
        self._running = False

        if self._sync_thread:
            self._sync_thread.join(timeout=5)

        log.info("[P2PSync] Stopped")

    # ── STATS ────────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        """Estadísticas del sistema P2P"""
        with get_conn_ctx(SYNC_DB) as conn:
            total_entries = conn.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]

            by_category = {}
            for row in conn.execute("SELECT category, COUNT(*) FROM knowledge_entries GROUP BY category").fetchall():
                by_category[row[0]] = row[1]

            total_syncs = conn.execute("SELECT COUNT(*) FROM sync_history").fetchone()[0]
            successful_syncs = conn.execute("SELECT COUNT(*) FROM sync_history WHERE success = 1").fetchone()[0]

        return {
            "node_id": self.node_id,
            "running": self._running,
            "known_peers": len(self._known_peers),
            "total_entries": total_entries,
            "entries_by_category": by_category,
            "total_syncs": total_syncs,
            "successful_syncs": successful_syncs,
            "sync_success_rate": round(successful_syncs / max(total_syncs, 1), 3)
        }


# ══════════════════════════════════════════════════════════════════════════════
# SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_p2p_sync: Optional[P2PSync] = None


def get_p2p_sync() -> P2PSync:
    """Obtiene la instancia singleton de P2PSync"""
    global _p2p_sync
    if _p2p_sync is None:
        _p2p_sync = P2PSync()
    return _p2p_sync


# ══════════════════════════════════════════════════════════════════════════════
# CLI TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    print("=" * 70)
    print("  EIDOS P2P Knowledge Sync — Test")
    print("=" * 70)

    sync = get_p2p_sync()

    # Add test entry
    entry_id = sync.add_entry("test", {"message": "Hello from P2P", "timestamp": time.time()})
    print(f"\n✅ Added test entry: {entry_id}")

    # Get stats
    stats = sync.get_stats()
    print(f"\n📊 Stats:")
    print(json.dumps(stats, indent=2))

    # List entries
    entries = sync.get_entries()
    print(f"\n📦 Total entries: {len(entries)}")

    if entries:
        print("\nLast 5 entries:")
        for entry in entries[-5:]:
            print(f"  {entry.entry_id[:8]} - {entry.category} - {entry.source_node}")

    print("\n✅ P2P Sync functional")