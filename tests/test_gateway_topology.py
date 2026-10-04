import os
import unittest
from unittest.mock import patch

from core import eidos_gateway
from core import bridge_to_eidos


class TestGatewayTopology(unittest.TestCase):
    def test_gateway_and_bridge_defaults_do_not_collide(self):
        self.assertEqual(eidos_gateway.GATEWAY_PORT, 8003)
        self.assertEqual(eidos_gateway.BACKENDS["bridge"]["port"], 18003)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EIDOS_BRIDGE_PORT", None)
            self.assertEqual(bridge_to_eidos._bridge_port(), 18003)
        self.assertNotEqual(
            eidos_gateway.GATEWAY_PORT,
            eidos_gateway.BACKENDS["bridge"]["port"],
        )

    def test_bridge_port_can_be_overridden(self):
        with patch.dict(os.environ, {"EIDOS_BRIDGE_PORT": "19003"}):
            self.assertEqual(bridge_to_eidos._bridge_port(), 19003)

    def test_invalid_bridge_port_falls_back_safely(self):
        with patch.dict(os.environ, {"EIDOS_BRIDGE_PORT": "not-a-port"}):
            self.assertEqual(bridge_to_eidos._bridge_port(), 18003)


if __name__ == "__main__":
    unittest.main()
