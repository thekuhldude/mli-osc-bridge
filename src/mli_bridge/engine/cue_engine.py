"""CueEngine — real-time OSC event dispatcher.

Runs in a dedicated daemon thread.  Uses ``time.perf_counter()`` for
sub-millisecond timing accuracy.  The main thread calls
``start(t0_perf)`` once audio playback has begun, passing the
``perf_counter()`` value captured at the exact moment the first audio
sample was handed to sounddevice.

Architecture
------------
::

    PlaybackController
        │
        ├─ sounddevice stream (audio thread)
        └─ CueEngine thread
               │  polls events every 1 ms
               └─ fires OscEvent.payload["commands"] via client.send_command()

Every OscEvent carries a ``payload["commands"]`` list of pre-built MA3
command strings (assembled by :class:`~mli_bridge.engine.event_scheduler.EventScheduler`).
The engine is deliberately dumb: it just loops over those strings and
calls ``client.send_command()`` for each one.

Past-due events (late by ≤ 50 ms) are still fired with a warning;
events late by > 50 ms are skipped to avoid a cascade of stale commands.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from loguru import logger

from mli_bridge.engine.event_scheduler import CommandType, OscEvent
from mli_bridge.osc.client import MA3OscClient
from mli_bridge.settings import BridgeSettings

# Events late by more than this are skipped to avoid stale commands.
_MAX_LATE_S = 0.050


class CueEngine:
    """Real-time OSC dispatcher running in a daemon thread.

    Parameters
    ----------
    client:
        Connected MA3OscClient.
    events:
        Pre-sorted list from
        :class:`~mli_bridge.engine.event_scheduler.EventScheduler`.
    settings:
        Bridge settings.
    seq_number:
        MA3 sequence number (kept for future extensions; structural cue
        jumps are already embedded in the event payload strings).
    """

    def __init__(
        self,
        client: MA3OscClient,
        events: list[OscEvent],
        settings: BridgeSettings,
        seq_number: int = 1,
    ) -> None:
        self._client = client
        self._events = events
        self._s = settings
        self._seq = seq_number

        self._cursor = 0
        self._t0: float = 0.0
        self._running = False
        self._thread: threading.Thread | None = None

        # Optional callback invoked after each dispatched event (for tests)
        self.on_event: Callable[[OscEvent], None] | None = None

    # ------------------------------------------------------------------ control

    def start(self, t0_perf: float) -> None:
        """Start the dispatch thread.

        Parameters
        ----------
        t0_perf:
            ``time.perf_counter()`` captured at the moment the first
            audio sample was handed to sounddevice.
        """
        self._t0 = t0_perf
        self._cursor = 0
        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            name="CueEngine",
            daemon=True,
        )
        self._thread.start()
        logger.info("CueEngine started (t0={:.6f})", t0_perf)

    def stop(self) -> None:
        """Signal the loop to stop and wait for the thread to exit."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("CueEngine stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------ loop

    def _loop(self) -> None:
        interval = self._s.playback_loop_interval_ms / 1000.0
        while self._running and self._cursor < len(self._events):
            now = time.perf_counter() - self._t0

            while self._cursor < len(self._events):
                ev = self._events[self._cursor]
                if ev.time_s > now:
                    break  # next event is in the future

                latency = now - ev.time_s
                if latency > _MAX_LATE_S:
                    logger.warning(
                        "Skipping stale event t={:.3f}s (late {:.0f}ms) type={}",
                        ev.time_s,
                        latency * 1000,
                        ev.command_type.name,
                    )
                    self._cursor += 1
                    continue

                if latency > 0.010:
                    logger.debug(
                        "Late {:.0f}ms: {} at {:.3f}s",
                        latency * 1000,
                        ev.command_type.name,
                        ev.time_s,
                    )

                self._dispatch(ev)
                ev.fired = True
                if self.on_event:
                    self.on_event(ev)
                self._cursor += 1

            time.sleep(interval)

        self._running = False
        logger.info("CueEngine: all {} events dispatched", len(self._events))

    # ------------------------------------------------------------------ dispatch

    def _dispatch(self, ev: OscEvent) -> None:
        """Fire all pre-built MA3 command strings carried in the event payload.

        A 20 ms gap is inserted between consecutive commands within the same
        event.  MA3 needs this pause to process ``Attribute`` commands after
        a ``Fixture … At …`` selection; skipping it silently drops the colour
        changes.  Single-command events (intensity / blackout) fire instantly.
        """
        commands: list[str] = ev.payload.get("commands", [])
        for i, cmd in enumerate(commands):
            self._client.send_command(cmd)
            logger.trace(
                "[{:.3f}s] {} → {}",
                ev.time_s,
                ev.command_type.name,
                cmd,
            )
            if i < len(commands) - 1:
                time.sleep(0.020)  # 20 ms so MA3 processes each Attribute
