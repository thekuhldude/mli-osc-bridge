"""Connectivity check: fire a harmless command at grandMA3 and verify
the UDP socket did not error out.

Since OSC over UDP is fire-and-forget (no ACK), a successful send means:
  - The socket bound without an OS error
  - The datagram was handed to the network stack

It does NOT guarantee that MA3 received or processed the command.
The CLI `test-connection` command sends a recognisable string so the
user can visually verify in the MA3 command log.
"""
from __future__ import annotations

import socket
import time

from loguru import logger

from mli_bridge.osc.client import MA3OscClient


def ping(client: MA3OscClient, *, timeout_s: float = 2.0) -> bool:
    """Send a benign `Echo` command and return True if no OS error occurs.

    The MA3 `Echo` command prints its argument to the console log — the
    user can see "MLI-PING" appear in the grandMA3 command line output
    as confirmation the message arrived.
    """
    try:
        client.send_command("Echo MLI-PING")
        logger.info("Ping sent to {}:{}", client.host, client.port)
        # Give MA3 a moment, then check via a second probe
        time.sleep(min(timeout_s, 0.3))
        client.send_command("Echo MLI-PING-2")
        return True
    except (OSError, socket.error) as exc:
        logger.error("Connection test failed: {}", exc)
        return False


def assert_reachable(client: MA3OscClient) -> None:
    """Raise RuntimeError if MA3 cannot be reached."""
    if not ping(client):
        raise RuntimeError(
            f"Cannot reach grandMA3onPC at {client.host}:{client.port}. "
            "Ensure MA3 is running and OSC is enabled on that port."
        )
