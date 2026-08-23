#!/usr/bin/env python3
"""Keep-alive watchdog for the paperbot web service.

Checks if the dashboard service is listening on its port; if not, (re)starts
it. Designed to be driven by a schedule (Hermes cron) so the service survives
restarts of the host / Hermes.

Prints nothing on success (so the scheduler stays silent); prints a short
"RESTARTED" line if it had to start the service.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

PORT = 8787
SERVER_MODULE = "hedge_fund.web.server"
VENV_PY = "/opt/data/paper-trading-bot/.venv/bin/python"
REPO = "/opt/data/paper-trading-bot"


def is_up(port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_up() -> None:
    if is_up(PORT):
        return  # still healthy, stay silent
    print(f"RESTARTED paperbot web service on :{PORT}  (was down)")
    subprocess.Popen(
        [str(VENV_PY), "-m", SERVER_MODULE, "--port", str(PORT)],
        cwd=str(REPO),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


if __name__ == "__main__":
    # expose the check for the cron prompt if needed, but keep silent on success
    ensure_up()