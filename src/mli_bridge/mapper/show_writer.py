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
6. ``Store Sequence <id> Cue <n>.0 /NoConfirmation``   — store content
7. Set trigger type:
     Cue 1   → TrigType "Go"    (waits for a manual Go+ before playing)
     Cue 2-N → TrigType "Time", TrigTime = seconds since previous cue
               (MA3 advances automatically, no OSC needed during playback)
8. ``Clear``

Before the first cue, two preference commands suppress the interactive
Store confirmation dialog for the duration of the write:
  ``Set Preference "StoreMode" "CueOnly"``
  ``Set Preference "StoreAskForMode" "Never"``

After all cues are written the sequence is labelled and cued up at cue 1.

Playback
--------
After writing, ``start_playback()`` fires ``Go+ Sequence N`` and
(optionally) starts audio simultaneously so the first cue triggers and
MA3 advances through the rest automatically.

Timing note
-----------
MA3 processes /cmd packets at ~30 msg/s safely.  ``cmd_gap_s=0.025``
(25 ms) gives ~40 msg/s — fine for offline programming.
"""
from __future__ import annotations

import asyncio
import threading
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
        Bridge settings (audio device, sequence config, etc.).
    cmd_gap_s:
        Pause between consecutive MA3 commands (seconds).
        25 ms is the safe minimum for Attribute processing.
    cue_gap_s:
        Extra pause after storing each cue before setting trigger
        properties.  50 ms gives MA3 time to index the new cue.
    """

    def __init__(
        self,
        client: MA3OscClient,
        settings: BridgeSettings,
        cmd_gap_s: float = 0.025,
        cue_gap_s: float = 0.050,
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

        Cue 1 is set to **Go** trigger (waits for a manual start).
        Cues 2-N are set to **Time** trigger so MA3 advances through them
        automatically once playback begins — no further OSC is needed.

        Parameters
        ----------
        cues:
            Ordered list of :class:`~mli_bridge.mapper.cue_builder.MA3Cue`.
        sequence_id:
            MA3 sequence number.
        sequence_name:
            Label applied after all cues are stored.
        """
        logger.info(
            "ShowWriter: writing {} cues → Seq {}  '{}'",
            len(cues), sequence_id, sequence_name,
        )
        self._cmd("Clear")

        # Suppress the interactive Store confirmation dialog.
        self._cmd('Set Preference "StoreMode" "CueOnly"')
        self._cmd('Set Preference "StoreAskForMode" "Never"')

        total = len(cues)
        prev_time_s = 0.0

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
                self._cmd(f"Fixture {fid} At {pct}")
                self._cmd(f'Attribute "ColorRGB_R" At {r}')
                self._cmd(f'Attribute "ColorRGB_G" At {g}')
                self._cmd(f'Attribute "ColorRGB_B" At {b}')

            # ---- store cue ----
            self._cmd(
                f"Store Sequence {sequence_id} Cue {cue.cue_number:.1f}"
                f" /NoConfirmation"
            )
            # Short gap so MA3 indexes the cue before we set properties.
            await asyncio.sleep(self._cue_gap)

            # ---- set trigger type ----
            if idx == 0:
                # First cue: wait for a manual Go+ before advancing.
                self._cmd(
                    f'Set Cue {cue.cue_number:.1f} Sequence {sequence_id}'
                    f' Property "TrigType" "Go"'
                )
            else:
                time_since_last = cue.time_s - prev_time_s
                self._cmd(
                    f'Set Cue {cue.cue_number:.1f} Sequence {sequence_id}'
                    f' Property "TrigType" "Time"'
                )
                self._cmd(
                    f'Set Cue {cue.cue_number:.1f} Sequence {sequence_id}'
                    f' Property "TrigTime" {time_since_last:.2f}'
                )

            self._cmd("Clear")
            prev_time_s = cue.time_s

            # Yield to event loop every 10 cues so progress can print.
            if idx % 10 == 9:
                await asyncio.sleep(0)

        # ---- finalise sequence ----
        self._cmd(f'Label Seq {sequence_id} "{sequence_name}"')
        # Clear programmer and park the sequence at cue 1 so it is
        # ready for an immediate Go+ without any residual programmer state.
        self._cmd("ClearAll")
        self._cmd(f"Goto Cue 1 Sequence {sequence_id}")
        logger.info(
            "ShowWriter: Seq {} '{}' ready ({} cues, Time-Trigger).",
            sequence_id, sequence_name, total,
        )

    # ---------------------------------------------------------------- playback

    def start_playback(
        self,
        sequence_id: int = 1,
        audio_path: Path | None = None,
    ) -> None:
        """Fire ``Go+ Sequence N`` and start audio simultaneously.

        Sends ``Go+ Sequence {sequence_id}`` to trigger the first cue, then
        starts audio playback back-to-back (<1 ms apart).  MA3 advances
        through all subsequent cues automatically via their Time-Trigger
        values — no further OSC is needed during playback.

        If *audio_path* is provided this method blocks until the track ends.
        Without audio it fires ``Go+`` and returns immediately.

        Parameters
        ----------
        sequence_id:
            The MA3 sequence to start.
        audio_path:
            Optional WAV/AIFF file to play in sync with the sequence.
        """
        logger.info("ShowWriter: starting Seq {}", sequence_id)

        if audio_path is not None:
            import sounddevice as sd
            import soundfile as sf

            audio_path = Path(audio_path)
            logger.info("Loading audio from {} …", audio_path)
            data, samplerate = sf.read(
                str(audio_path), dtype="float32", always_2d=True
            )
            device = self._s.audio_device
            duration_s = len(data) / samplerate

            done = threading.Event()

            def _play() -> None:
                sd.play(data, samplerate, device=device)
                sd.wait()
                done.set()

            audio_thread = threading.Thread(
                target=_play, daemon=True, name="AudioPlayback"
            )

            # ---- clear + reset to cue 1 before firing ----
            self._client.send_command("ClearAll")
            time.sleep(0.3)
            self._client.send_command(f"Goto Cue 1 Sequence {sequence_id}")
            time.sleep(0.3)

            logger.info(
                "Firing Go+ Seq {} and audio ({:.1f} s) simultaneously …",
                sequence_id, duration_s,
            )
            # Fire OSC first, then start audio thread (<1 ms later).
            self._client.send_command(f"Go+ Sequence {sequence_id}")
            audio_thread.start()

            done.wait(timeout=duration_s + 10.0)
            logger.info("ShowWriter: playback complete.")

        else:
            self._client.send_command("ClearAll")
            time.sleep(0.3)
            self._client.send_command(f"Goto Cue 1 Sequence {sequence_id}")
            time.sleep(0.3)
            self._client.send_command(f"Go+ Sequence {sequence_id}")
            logger.info("ShowWriter: Seq {} started (no audio).", sequence_id)
