"""Shared filesystem anchors.

Paper-trading state lives under STATE_ROOT (PAPER_STATE, default ``state``).
Web, champions, tournament, run, and heartbeat all resolve paths through
``state_root()`` so a k8s PVC at /app/state and a local checkout agree.

The fork leftover ~/.hedge-fund/ tree (mandates, caches, .env) is still
declared here for non-trading tools. Trading code must not read ENV_PATH
for API keys and must not construct a private-key ccxt client.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

USER_DIR = Path.home() / ".hedge-fund"
MANDATES_DIR = USER_DIR / "mandates"
CACHE_DIR = USER_DIR / "cache"
ENV_PATH = USER_DIR / ".env"  # fork leftover; do not load for trading


def state_root() -> Path:
    """Paper-trading state directory. Honors PAPER_STATE at call time."""
    return Path(os.environ.get("PAPER_STATE", "state"))


STATE_ROOT = state_root()

# The example mandate ships inside the package; it is copied out (never read
# in place) so users edit their copy, not the install.
EXAMPLE_MANDATE = Path(__file__).resolve().parent / "fund" / "example.yaml"


def ensure_mandates_dir() -> Path:
    """Create the mandates dir on first use, seeded with the example."""
    if not MANDATES_DIR.exists():
        MANDATES_DIR.mkdir(parents=True)
        shutil.copy(EXAMPLE_MANDATE, MANDATES_DIR / "example.yaml")
    return MANDATES_DIR
