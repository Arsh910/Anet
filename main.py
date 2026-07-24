#!/usr/bin/env python3
"""
main.py — developer entry point for running ANet from a source checkout.

The actual CLI lives in the package at `anet/cli/app.py` so it ships with a
`pip`/`pipx` install (where it's exposed as the `anet` command). This root shim
just forwards to it, so contributors can keep running `python main.py` from the
repo exactly as before. Single source of truth: edit `anet/cli/app.py`.
"""

import os

# Silence HF's "Cannot enable progress bars" warning at the SOURCE.
# We keep HF progress bars off (HF_HUB_DISABLE_PROGRESS_BARS=1); fastembed then calls
# huggingface_hub.enable_progress_bars() in a `finally` on every model load, which
# warns once per call because the env var has priority. warnings filters can't fix
# this — a dependency calls warnings.resetwarnings() mid-run and wipes every filter,
# even startup PYTHONWARNINGS ones (verified). So neutralize the call itself: make
# enable_progress_bars a silent no-op BEFORE fastembed binds it via `from ... import`.
# Bars stay disabled (our intent); the warning never fires. Immune to filter resets.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
try:
    import huggingface_hub.utils.tqdm as _hf_tqdm
    import huggingface_hub.utils as _hf_utils
    def _hf_enable_noop(name=None):  # ponytail: library warns unconditionally; kill at source
        return None
    _hf_tqdm.enable_progress_bars = _hf_enable_noop
    _hf_utils.enable_progress_bars = _hf_enable_noop
except Exception:
    pass

from anet.cli.app import run_cli

if __name__ == "__main__":
    run_cli()
