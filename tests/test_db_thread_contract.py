from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.db import close_all, get_conn


class DBThreadContractTests(unittest.TestCase):
    def tearDown(self) -> None:
        close_all()

    def test_default_cached_connections_are_thread_local(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-db-thread-") as td:
            db_path = Path(td) / "thread-contract.db"
            barrier = threading.Barrier(6)

            def worker(index: int) -> tuple[int, int]:
                first = get_conn(db_path)
                second = get_conn(db_path)
                self.assertIs(first, second)
                barrier.wait(timeout=10)
                first.execute(
                    "CREATE TABLE IF NOT EXISTS events "
                    "(worker INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL)"
                )
                first.execute(
                    "INSERT OR REPLACE INTO events(worker, thread_id) VALUES (?, ?)",
                    (index, threading.get_ident()),
                )
                first.commit()
                return threading.get_ident(), id(first)

            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(worker, range(6)))

            thread_ids = {thread_id for thread_id, _ in results}
            connection_ids = {connection_id for _, connection_id in results}
            self.assertEqual(len(thread_ids), 6)
            self.assertEqual(len(connection_ids), 6)

            reader = get_conn(db_path, cache=False)
            try:
                count = reader.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            finally:
                reader.close()
            self.assertEqual(count, 6)


if __name__ == "__main__":
    unittest.main()
