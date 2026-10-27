"""Print the exploratory discovery block report as JSON.

Read-only. Pre-holdout rows only. The report does not change the gate,
qualification, the trial count, or what gets minted.
"""
from __future__ import annotations

import json

from hedge_fund.trading.block_report import build_block_report


def main() -> None:
    print(json.dumps(build_block_report(), indent=2))


if __name__ == "__main__":
    main()
