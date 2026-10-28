"""Build one discovery-tape pin in a fresh process and exit.

The web process starts this and then reads only the manifest. The numpy
tape and the compressed npz live in this process, so their memory is
returned when it exits.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write one discovery tape pin and exit")
    parser.add_argument("--state", required=True)
    parser.add_argument("--pin", type=int, required=True)
    parser.add_argument("--active-pin", type=int, required=True)
    parser.add_argument("--raw-end", type=int, default=-1)
    args = parser.parse_args(argv)
    os.environ["PAPER_STATE"] = args.state
    from hedge_fund.trading.price_history import _build_pin

    raw = None if args.raw_end < 0 else int(args.raw_end)
    try:
        _build_pin(
            Path(args.state),
            int(args.pin),
            active_pin=int(args.active_pin),
            raw_end=raw,
        )
    except Exception as exc:  # noqa: BLE001 — the parent logs the exit code
        print(f"[tape-build] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
