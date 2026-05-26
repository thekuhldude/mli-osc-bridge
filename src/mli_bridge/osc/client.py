"""MA3OscClient — thread-safe UDP OSC sender for grandMA3onPC.

Usage
-----
    from mli_bridge.osc.client import MA3OscClient

    client = MA3OscClient(host="127.0.0.1", port=8000)
    client.send_command("Go+ Seq 1")
    client.set_fader(1, 1.0)   # Page 1, Fader 1, full
    client.press_key(1, 5)     # Page 1, Key 5
"""
from __future__ import annotations

import threading
from typing import Union

from loguru import logger
from pythonosc import udp_client
from pythonosc.osc_message_builder import OscMessageBuilder


class MA3OscClient:
    """Thread-safe UDP OSC client wired to one grandMA3onPC instance.

    All public methods acquire a lock before sending so they are safe to
    call from the CueEngine thread *and* the CLI thread simultaneously.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8000) -> None:
        self._host = host
        self._port = port
        self._lock = threading.Lock()
        self._client = udp_client.SimpleUDPClient(host, port)
        logger.debug("MA3OscClient ready → {}:{}", host, port)

    # ------------------------------------------------------------------ low-level

    def _send(self, address: str, *args: Union[str, int, float]) -> None:
        """Build and fire an OSC message.  Blocks on the internal lock."""
        with self._lock:
            builder = OscMessageBuilder(address=address)
            for arg in args:
                if isinstance(arg, bool):
                    builder.add_arg(int(arg))
                elif isinstance(arg, float):
                    builder.add_arg(arg, arg_type="f")
                elif isinstance(arg, int):
                    builder.add_arg(arg, arg_type="i")
                else:
                    builder.add_arg(str(arg), arg_type="s")
            msg = builder.build()
            self._client._sock.sendto(msg.dgram, (self._host, self._port))
        logger.trace("OSC {} {}", address, args)

    # ------------------------------------------------------------------ MA3 helpers

    def send_command(self, cmd: str) -> None:
        """Send an arbitrary MA3 command string via /cmd."""
        self._send("/cmd", cmd)

    def set_fader(self, page: int, fader: int, value: float) -> None:
        """Set a fader on *page* to *value* (0.0 – 1.0).

        Maps to /PageN/FaderM with a float payload.
        """
        address = f"/Page{page}/Fader{fader}"
        self._send(address, float(value))

    def press_key(self, page: int, key: int) -> None:
        """Momentary key press on *page*. Sends value 1 then 0."""
        address = f"/Page{page}/Key{key}"
        self._send(address, 1)
        self._send(address, 0)

    def hold_key(self, page: int, key: int, pressed: bool) -> None:
        """Hold or release a key (1 = press, 0 = release)."""
        address = f"/Page{page}/Key{key}"
        self._send(address, int(pressed))

    # ------------------------------------------------------------------ utils

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    def __repr__(self) -> str:
        return f"MA3OscClient({self._host}:{self._port})"
