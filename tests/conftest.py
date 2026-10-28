"""Suite-wide test environment.

Gate v6 (2026-10-28) makes ``library`` the default discovery source in
production (``PAPER_DISCOVERY_SOURCE`` unset). The pre-v6 claim, refill,
mint and tournament tests exercise the random-mint path, so the suite runs
with ``PAPER_DISCOVERY_SOURCE=random`` unless a test removes it. The
library tests delete the variable to test the real default.
"""
from __future__ import annotations

import os

os.environ.setdefault("PAPER_DISCOVERY_SOURCE", "random")
