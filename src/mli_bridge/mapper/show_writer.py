"""ShowWriter: program MA3Cue objects into grandMA3 via OSC.

Write strategy
--------------
For each cue:

1. ``Fixture <id> At <brightness_pct>``
2. ``Attribute "ColorRGB_R/G/B" At <n>``  (25 ms gaps each)
3. Repeat for every fixture.
4. ``Store Sequence <id> Cue <n>.0 /NoConfirmation``   — store content
5. Set trigger type:
     Cue 1   → TrigType "Time", TrigTime = pre_roll_s
               (fires pre_roll_s after Go+ — gives MA3 prep time)
     Cue 2-N → TrigType "Time", TrigTime = seconds since previous cue
               (MA3 advances automatically, no further OSC needed)
6. ``Clear``

Pre-roll timing
---------------
``Go+ Sequence N`` starts the MA3 internal clock at t=0.
Cue 1 fires at t = pre_roll_s.
Audio playback starts at t = pre_roll_s (simultaneously with cue 1).
This eliminates timing drift by giving MA3 a clean run-up before the
first cue and ensuring audio and MA3 are locked from the same moment.

Pre-roll defaults to ``settings.pre_roll_seconds`` (default 2.0 s).

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
        pre_roll_s: float | None = None,
    ) -> None:
        """Program all cues into MA3 sequence *sequence_id*.

        Cue 1 gets a **Time** trigger at *pre_roll_s* (fires that many
        seconds after Go+).  Cues 2-N get **Time** triggers equal to the
        gap since the previous cue.  MA3 advances automatically — no
        further OSC is needed during playback.

        Parameters
        ----------
        cues:
            Ordered list of :class:`~mli_bridge.mapper.cue_builder.MA3Cue`.
        sequence_id:
            MA3 sequence number.
        sequence_name:
            Label applied after all cues are stored.
        pre_roll_s:
            Seconds from Go+ until cue 1 fires.  Defaults to
            ``settings.pre_roll_seconds``.
        """
        if pre_roll_s is None:
            pre_roll_s = float(self._s.pre_roll_seconds)

        logger.info(
            "ShowWriter: writing {} cues → Seq {}  '{}'  (pre_roll={:.1f}s)",
            len(cues), sequence_id, sequence_name, pre_roll_s,
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

            # ---- set trigger type (all cues use Time trigger) ----
            self._cmd(
                f'Set Cue {cue.cue_number:.1f} Sequence {sequence_id}'
                f' Property "TrigType" "Time"'
            )
            if idx == 0:
                # First cue fires pre_roll_s after Go+
                trig_time = pre_roll_s
            else:
                trig_time = cue.time_s - prev_time_s
            self._cmd(
                f'Set Cue {cue.cue_number:.1f} Sequence {sequence_id}'
                f' Property "TrigTime" {trig_time:.2f}'
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
        pre_roll_s: float | None = None,
    ) -> None:
        """Fire ``Go+ Sequence N``, wait pre-roll, then start audio.

        Pre-roll timing
        ---------------
        1. Clear programmer + park at cue 1.
        2. Send ``Go+ Sequence N`` — MA3 clock starts.
        3. Sleep *pre_roll_s* seconds (MA3 runs silently, preparing for cue 1).
        4. Start audio playback — arrives in sync with MA3 cue 1.

        MA3 advances through all subsequent cues automatically via their
        Time-Trigger values — no further OSC is needed during playback.

        Parameters
        ----------
        sequence_id:
            The MA3 sequence to start.
        audio_path:
            Optional WAV/AIFF file to play after the pre-roll.
            Blocks until the track finishes.
        pre_roll_s:
            Lead-in seconds between Go+ and audio start.  Must match
            the TrigTime set on cue 1 by :meth:`write_show`.
            Defaults to ``settings.pre_roll_seconds``.
        """
        if pre_roll_s is None:
            pre_roll_s = float(self._s.pre_roll_seconds)

        logger.info(
            "ShowWriter: starting Seq {}  (pre_roll={:.1f}s)", sequence_id, pre_roll_s
        )

        # ---- clear + reset to cue 1 ----
        self._client.send_command("ClearAll")
        time.sleep(0.3)
        self._client.send_command(f"Goto Cue 1 Sequence {sequence_id}")
        time.sleep(0.3)

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

            # Fire Go+ — MA3 clock starts now
            self._client.send_command(f"Go+ Sequence {sequence_id}")
            logger.info(
                "Go+ fired.  Waiting {:.1f}s pre-roll before audio …",
                pre_roll_s,
            )

            # Pre-roll: MA3 runs silently, cue 1 fires at t=pre_roll_s
            time.sleep(pre_roll_s)

            # Audio starts simultaneously with MA3 cue 1
            audio_thread.start()
            logger.info("Audio started ({:.1f} s).", duration_s)

            done.wait(timeout=duration_s + pre_roll_s + 10.0)
            logger.info("ShowWriter: playback complete.")

        else:
            # No audio: just start the sequence and return
            self._client.send_command(f"Go+ Sequence {sequence_id}")
            logger.info("ShowWriter: Seq {} started (no audio, pre_roll not waited).", sequence_id)
