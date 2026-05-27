"""ShowWriter: program MA3Cue objects into grandMA3 via OSC.

Write strategy
--------------
For each cue:

1. ``Fixture <id> At <brightness_pct>``
   — selects the fixture and sets its dimmer in the programmer.
2. ``Attribute "ColorRGB_R" At <r>``   (25 ms gap)
3. ``Attribute "ColorRGB_G" At <g>``   (25 ms gap)
4. ``Attribute "ColorRGB_B" At <b>``   (25 ms gap)
   — MA3 requires separate /cmd calls; semicolon-chained commands
   silently drop Attribute changes.
5. Repeat steps 1-4 for every fixture.
6. ``Store Seq <id> Cue <n>.000``
7. ``Clear``

After all cues are written, the sequence is labelled and cued up at cue 1.

Timing note: MA3 processes /cmd packets at roughly 30 msg/s safely.
``cmd_gap_s=0.025`` (25 ms) gives ~40 msg/s — fine for offline programming
where exact timing is not critical.

Rate estimate per cue (6 fixtures × 4 cmds + 2 store/clear):
    6 × 4 × 25 ms = 600 ms/fixture-block + 100 ms store gap ≈ 0.7 s per cue.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from loguru import logger

from mli_bridge.mapper.cue_builder import MA3Cue
from mli_bridge.osc.client import MA3OscClient
from mli_bridge.settings import BridgeSettings


class ShowWriter:
    """Write a :class:`~mli_bridge.mapper.cue_builder.MA3Cue` list to grandMA3.

    Parameters
    ----------
    client:
        Connected :class:`~mli_bridge.osc.client.MA3OscClient`.
    settings:
        Bridge settings (for sequence / executor config).
    cmd_gap_s:
        Pause between consecutive MA3 commands (seconds).
        25 ms is the safe minimum for Attribute processing.
    cue_gap_s:
        Extra pause after storing each cue before clearing and starting
        the next one.  100 ms gives MA3 time to index the new cue.
    """

    def __init__(
        self,
        client: MA3OscClient,
        settings: BridgeSettings,
        cmd_gap_s: float = 0.025,
        cue_gap_s: float = 0.100,
    ) -> None:
        self._client = client
        self._s = settings
        self._cmd_gap = cmd_gap_s
        self._cue_gap = cue_gap_s

    # ----------------------------------------------------------------- helpers

    def _cmd(self, command: str) -> None:
        """Send one MA3 command and sleep for cmd_gap_s."""
        self._client.send_command(command)
        time.sleep(self._cmd_gap)

    # ------------------------------------------------------------------ write

    async def write_show(
        self,
        cues: list[MA3Cue],
        sequence_id: int = 1,
        sequence_name: str = "MLI_Show",
    ) -> None:
        """Program all cues into MA3 sequence *sequence_id*.

        This is a long-running I/O operation (~0.7 s per cue for a 6-fixture
        patch).  The async interface lets the caller remain responsive
        (e.g. printing progress) while the write is in progress.

        Parameters
        ----------
        cues:
            Ordered list of :class:`~mli_bridge.mapper.cue_builder.MA3Cue`.
        sequence_id:
            MA3 sequence number (``Store Seq <id> Cue N``).
        sequence_name:
            Label applied after all cues are stored.
        """
        logger.info(
            "ShowWriter: writing {} cues → Seq {} '{}'",
            len(cues), sequence_id, sequence_name,
        )
        self._cmd("Clear")   # start with a clean programmer

        total = len(cues)
        for idx, cue in enumerate(cues):
            logger.debug(
                "[{}/{}] Cue {:>6.3f}  t={:.2f}s  fade={:.2f}s",
                idx + 1, total, cue.cue_number, cue.time_s, cue.fade_s,
            )

            # ---- programme every fixture ----
            for fid in sorted(cue.fixture_colors.keys()):
                r, g, b = cue.fixture_colors[fid]
                brightness = cue.fixture_brightness.get(fid, 1.0)
                pct = max(0, min(100, int(round(brightness * 100))))

                # Select + dimmer
                self._cmd(f"Fixture {fid} At {pct}")
                # Colour channels (each as a separate /cmd)
                self._cmd(f'Attribute "ColorRGB_R" At {r}')
                self._cmd(f'Attribute "ColorRGB_G" At {g}')
                self._cmd(f'Attribute "ColorRGB_B" At {b}')

            # ---- store cue ----
            self._cmd(f"Store Seq {sequence_id} Cue {cue.cue_number:.3f}")
            time.sleep(self._cue_gap)
            self._cmd("Clear")

            # Yield to event loop every 10 cues so progress updates can print
            if idx % 10 == 9:
                await asyncio.sleep(0)

        # ---- finalise sequence ----
        self._cmd(f'Label Seq {sequence_id} "{sequence_name}"')
        self._cmd(f"Goto Seq {sequence_id} Cue 1.000")
        logger.info(
            "ShowWriter: Seq {} '{}' ready ({} cues).",
            sequence_id, sequence_name, total,
        )

    # ---------------------------------------------------------------- playback

    async def play_show_with_timecode(
        self,
        cues: list[MA3Cue],
        sequence_id: int = 1,
        audio_path: Path | None = None,
    ) -> None:
        """Fire cue-jump commands at the correct wall-clock times.

        A lightweight timecode player for shows that were previously
        written with :meth:`write_show`.  Uses ``asyncio.sleep`` for timing
        rather than the full :class:`~mli_bridge.engine.cue_engine.CueEngine`.

        Audio playback is not currently implemented; pass *audio_path* for
        future compatibility.

        Parameters
        ----------
        cues:
            The same cue list passed to :meth:`write_show`.
        sequence_id:
            MA3 sequence that contains the pre-programmed cues.
        audio_path:
            Reserved for future audio integration.
        """
        if audio_path is not None:
            logger.warning(
                "Audio playback in play_show_with_timecode is not yet "
                "implemented — audio_path ignored."
            )

        logger.info(
            "ShowWriter: timecode playback — {} cues on Seq {}",
            len(cues), sequence_id,
        )
        loop = asyncio.get_event_loop()
        t0 = loop.time()

        for cue in cues:
            now = loop.time() - t0
            wait = cue.time_s - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._client.send_command(
                f"Goto Seq {sequence_id} Cue {cue.cue_number:.3f}"
            )
            logger.debug(
                "Cue {:.3f} fired at t={:.3f}s", cue.cue_number, cue.time_s
            )

        logger.info("ShowWriter: timecode playback complete.")
