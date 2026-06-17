#!/usr/bin/env python3
"""Runner script for RAM Guardian daemon — called by systemd."""
import sys
import time
from pathlib import Path

EIDOS_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(EIDOS_ROOT))

from core.ram_guardian import get_ram_guardian

g = get_ram_guardian()
g.start_monitoring()

# Keep the process alive while monitoring
while g.monitoring:
    time.sleep(10)
