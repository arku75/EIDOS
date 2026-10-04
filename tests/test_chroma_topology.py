import pathlib
import unittest

import core.colony_chroma as colony
import core.eidos_memory_unified as unified
from core import eidos_chroma_server as server

ROOT=pathlib.Path(__file__).resolve().parents[1]


class TestChromaTopology(unittest.TestCase):
    def test_clients_share_canonical_default_port(self):
        self.assertEqual(colony.CHROMA_PORT, 8767)
        self.assertEqual(unified.CHROMA_PORT, 8767)

    def test_legacy_server_is_only_a_compatibility_wrapper(self):
        source=(ROOT/"bin"/"chroma_http_server.py").read_text(encoding="utf-8")
        self.assertIn("from core.eidos_chroma_server import ChromaHandler, main", source)
        self.assertNotIn("ChromaMemory()", source)
        self.assertNotIn("HTTPServer(", source)

    def test_canonical_server_is_loopback_by_default(self):
        source=(ROOT/"core"/"eidos_chroma_server.py").read_text(encoding="utf-8")
        self.assertIn('default="127.0.0.1"', source)
        self.assertIn('EIDOS_CHROMA_PORT', source)
        self.assertIn('"8767"', source)


if __name__=="__main__":
    unittest.main()
