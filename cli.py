#!/usr/bin/env python
"""
ANet CLI entry point.

Usage:
    python cli.py init <name>
    python cli.py validate
    python cli.py connect
    python cli.py disconnect [name]
    python cli.py status
    python cli.py list-tools
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from anet.cli.main import cli

if __name__ == "__main__":
    cli()
