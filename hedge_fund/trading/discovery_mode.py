"""Where discovery walk-forwards run.

Cluster ``live_cycle`` defaults to **off** so the 1 CPU / 1.5GiB sidecar
only does ``run_isolated`` → collect → report. The Windows PC is the
discovery farm. Re-enable in-cycle tournament with ``DISCOVERY_ON_CYCLE=1``
or ``PAPER_DISCOVERY_MODE=on`` (dev / emergency only).

Ingest to prod uses ``PAPER_DISCOVERY_INGEST_TOKEN`` (shared secret).
"""
from __future__ import annotations

import hmac
import os

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def discovery_on_cycle(
    environ: dict[str, str] | None = None,
) -> bool:
    """True only when the cycle sidecar is explicitly told to run tournament.

    Default is False (Windows worker). ``DISCOVERY_ON_CYCLE`` wins when set;
    else ``PAPER_DISCOVERY_MODE`` (on/off). Unset → off.
    """
    env = os.environ if environ is None else environ
    raw_cycle = (env.get("DISCOVERY_ON_CYCLE") or "").strip().lower()
    if raw_cycle:
        if raw_cycle in _TRUE:
            return True
        if raw_cycle in _FALSE:
            return False
    raw_mode = (env.get("PAPER_DISCOVERY_MODE") or "").strip().lower()
    if raw_mode in _TRUE:
        return True
    if raw_mode in _FALSE:
        return False
    return False


def ingest_token(environ: dict[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    tok = (env.get("PAPER_DISCOVERY_INGEST_TOKEN") or "").strip()
    return tok or None


def tokens_match(got: str, expected: str) -> bool:
    """Constant-time compare when lengths match; reject otherwise."""
    if not got or not expected or len(got) != len(expected):
        return False
    return hmac.compare_digest(got.encode("utf-8"), expected.encode("utf-8"))
