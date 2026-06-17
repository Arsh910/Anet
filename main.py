#!/usr/bin/env python3
"""
main.py — developer entry point for running ANet from a source checkout.

The actual CLI lives in the package at `anet/cli/app.py` so it ships with a
`pip`/`pipx` install (where it's exposed as the `anet` command). This root shim
just forwards to it, so contributors can keep running `python main.py` from the
repo exactly as before. Single source of truth: edit `anet/cli/app.py`.
"""

from anet.cli.app import run_cli

if __name__ == "__main__":
    run_cli()
