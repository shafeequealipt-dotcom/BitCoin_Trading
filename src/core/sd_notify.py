"""Minimal systemd sd_notify client — no external dependency.

2026-08-01 hang fix (Phase 2 of the pybit-freeze root-cause fix): the
in-process brain_liveness_watchdog cannot detect its own host event loop
freezing (proven by the 2026-07-27 -> 08-01 ~5-day silent hang, where
the entire workers.py event loop wedged on a blocking pybit call and
every in-process worker, including the watchdog, froze with it).
systemd's own watchdog (WatchdogSec=) is external to our process, so it
is the one thing that can catch a total event-loop freeze regardless of
cause. This module implements the sd_notify protocol directly (a
newline-delimited key=value datagram to the AF_UNIX socket named by
$NOTIFY_SOCKET) since it is ~20 lines and avoids adding a dependency for
something this small.

No-op everywhere $NOTIFY_SOCKET is unset (local dev, manual runs,
anything not launched by systemd) — safe to call unconditionally.
"""

from __future__ import annotations

import os
import socket

from src.core.logging import get_logger

log = get_logger("worker")

_ADDR = os.environ.get("NOTIFY_SOCKET")


def _send(payload: str) -> None:
    if not _ADDR:
        return
    addr = _ADDR
    if addr.startswith("@"):
        # Abstract namespace socket (leading '@' -> NUL per the sd_notify spec).
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(payload.encode("utf-8"))
    except OSError as e:
        log.debug(f"SD_NOTIFY_SEND_FAIL | err='{str(e)[:100]}' | payload='{payload}'")


def notify_ready() -> None:
    """Tell systemd the service finished starting (Type=notify)."""
    _send("READY=1")


def notify_watchdog() -> None:
    """Pet the systemd watchdog. Call at less than half of WatchdogSec."""
    _send("WATCHDOG=1")


def notify_stopping() -> None:
    """Tell systemd a graceful shutdown is in progress."""
    _send("STOPPING=1")


def notify_status(message: str) -> None:
    """Set the one-line status systemd shows in `systemctl status`."""
    _send(f"STATUS={message}")
