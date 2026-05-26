"""Tests for MA3OscClient — no real MA3 needed; patches the UDP socket."""
from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest

from mli_bridge.osc.client import MA3OscClient


@pytest.fixture
def client() -> MA3OscClient:
    """Return a client with a mocked UDP socket."""
    with patch("pythonosc.udp_client.SimpleUDPClient.__init__", return_value=None):
        c = MA3OscClient(host="127.0.0.1", port=8000)
        # Replace the internal socket with a mock
        c._client._sock = MagicMock()
        return c


def test_send_command_fires_osc(client: MA3OscClient) -> None:
    client.send_command("Go+ Seq 1")
    assert client._client._sock.sendto.called


def test_set_fader_address_format(client: MA3OscClient) -> None:
    """Fader address must be /PageN/FaderM."""
    sent_addresses = []

    original_send = client._send

    def capturing_send(address, *args):
        sent_addresses.append(address)
        original_send(address, *args)

    client._send = capturing_send
    client.set_fader(1, 3, 0.75)
    assert "/Page1/Fader3" in sent_addresses


def test_press_key_sends_1_then_0(client: MA3OscClient) -> None:
    """press_key must send value 1 followed by value 0."""
    calls: list[tuple] = []
    original_send = client._send

    def capturing_send(address, *args):
        calls.append((address, args))
        original_send(address, *args)

    client._send = capturing_send
    client.press_key(1, 5)
    assert len(calls) >= 2
    assert calls[-2] == ("/Page1/Key5", (1,))
    assert calls[-1] == ("/Page1/Key5", (0,))


def test_thread_safety(client: MA3OscClient) -> None:
    """Multiple threads sending commands must not raise."""
    import threading

    errors: list[Exception] = []

    def _send_many():
        try:
            for _ in range(50):
                client.send_command("Echo test")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_send_many) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread errors: {errors}"
