"""Regime gate — crypto market regime as a hard precondition for the loop.

Wraps the ported `crypto-regime-analyzer` (a 0-100 composite across six
components: BTC trend, alt breadth, dominance, funding, drawdown/vol,
momentum thrust; keyless CoinGecko + Binance public data).

Policy (strict, per user's decision):
- **Longs are allowed only when the regime zone is RISK_ON or NEUTRAL.**
- **RISK_OFF hard-blocks new longs** — the loop may not open a position.
  Existing positions are still manageable (stop/TP) via the broker.

The gate is a *decision-layer input*: it does not size or place orders, and
it never touches keys. It only decides whether the deterministic risk/broker
layer may accept a new entry. Attribution to tradermonty/claude-trading-skills
(crypto-regime-analyzer, MIT-ish public repo) is kept in REGIME_SKILL.md and
the references/.

Cache: the analyzer is heavy (several CoinGecko fetches). We cache the regime
result for `cache_hours` and reuse it within that window so a fast swing loop
(near-daily decisions) doesn't hammer the free tier.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Zones where longs are allowed.
ALLOWED_ZONES = {"RISK_ON", "NEUTRAL"}
# Zone that hard-blocks.
BLOCK_ZONE = "RISK_OFF"

REGIME_SCRIPT = Path(__file__).resolve().parent / "scripts" / "crypto_regime_analyzer.py"


class RegimeError(RuntimeError):
    """Raised when the regime cannot be determined (fail-closed)."""


class RegimeGate:
    def __init__(
        self,
        state_dir: str | Path = "state",
        cache_hours: float = 6.0,
        top_n: int = 10,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path = self.state_dir / "regime_cache.json"
        self.cache_hours = cache_hours
        self.top_n = top_n
        self._last: dict | None = None

    # -- public API ------------------------------------------------------
    def allowed_to_trade(self) -> bool:
        """True if new longs are permitted under the regime policy.

        Fails closed: if the regime cannot be determined (no cache, analyzer
        errors), we do NOT allow a new long. Better a missed entry than an
        un-gated one.
        """
        regime = self.current_regime()
        zone = regime["composite"].get("zone")
        return zone in ALLOWED_ZONES

    def current_regime(self) -> dict:
        """Return the regime dict, using a fresh cache or running the analyzer."""
        if self._last is not None:
            return self._last
        cached = self._read_cache()
        if cached is not None and self._fresh(cached):
            self._last = cached
            return cached
        result = self._run_analyzer()
        self._write_cache(result)
        self._last = result
        return result

    def zone(self) -> str | None:
        try:
            return self.current_regime()["composite"].get("zone")
        except RegimeError:
            return None

    def score(self) -> float | None:
        try:
            return self.current_regime()["composite"].get("score")
        except RegimeError:
            return None

    # -- internals -------------------------------------------------------
    def _fresh(self, cached: dict) -> bool:
        try:
            as_of = datetime.fromisoformat(cached["metadata"]["as_of"])
        except (KeyError, ValueError):
            return False
        age = datetime.now(timezone.utc) - as_of
        return age < timedelta(hours=self.cache_hours)

    def _run_analyzer(self) -> dict:
        out_dir = self.state_dir / "regime_runs"
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, str(REGIME_SCRIPT),
            "--output-dir", str(out_dir),
            "--top-n", str(self.top_n),
            "--quiet",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired as exc:
            raise RegimeError(f"regime analyzer timed out: {exc}") from exc
        if proc.returncode != 0:
            raise RegimeError(
                f"regime analyzer failed rc={proc.returncode}: {proc.stderr[-500:]}"
            )
        out_path = out_dir / "crypto_regime.json"
        if not out_path.exists():
            raise RegimeError("regime analyzer ran but produced no json")
        try:
            return json.loads(out_path.read_text())
        except (OSError, ValueError) as exc:
            raise RegimeError(f"bad regime json: {exc}") from exc

    def _read_cache(self) -> dict | None:
        if not self.cache_path.exists():
            return None
        try:
            return json.loads(self.cache_path.read_text())
        except (OSError, ValueError):
            return None

    def _write_cache(self, result: dict) -> None:
        try:
            self.cache_path.write_text(json.dumps(result, indent=2))
        except OSError:
            pass  # cache is best-effort; a write failure shouldn't break trading
