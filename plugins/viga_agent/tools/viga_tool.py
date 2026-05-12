# viga_tool.py — thin re-export of the core VIGA tool
#
# The actual implementation lives in tools/viga/__init__.py (complex subprocess
# management, WSL runner, stall watchdog, registry). This file is the plugin
# entry point that satisfies the ANet Tool Contract.

import sys
from pathlib import Path

# Add the plugin root so the local viga/ package is importable
_plugin_root = Path(__file__).parent.parent
if str(_plugin_root) not in sys.path:
    sys.path.insert(0, str(_plugin_root))

from viga import SCHEMA, run  # noqa: F401  (re-exported for ANet loader)
