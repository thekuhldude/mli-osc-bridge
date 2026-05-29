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
6. ``Store Seq <id> Cue <n>.000 /NoConfirm``     — stores cue content
7. ``Store Sequence <id> Cue <n>.0               — attaches timecode trigger
      Timecode <slot> At HH:MM:SS.FF /NoConfirm``
8. ``Clear``

Before the first cue, two preference commands suppress the interactive
Store confirmation dialog for the duration of the write:
  ``Set Preference "StoreMode" "CueOnly"``
  ``Set Preference "StoreAskForMode" "Never"``

After all cues are written the sequence is labelled and cued up at cue 1.

Timecode playback
-----------------
After writing, ``start_timecode_playback()`` configures MA3's internal
timecode clock and (optionally) starts audio playback simultaneously.
MA3 then fires cues automatically when the timecode clock reaches each
cue's stored timecode value — no further OSC commands are needed during
playback.

Timing note
-----------
MA3 processes /cmd packets at ~30 msg/s safely.  ``cmd_gap_s=0.025``
(25 ms) gives ~40 msg/s — fine for offline programming.

Rate estimate per cue (4 fixtures × 4 cmds + 3 store/tc/clear):
    4 × 4 × 25 ms + 3 × 25 ms + 100 ms cue_gap ≈ 0.55 s per cue.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_timecode(time_s: float, fps: int = 30) -> str:
    """Format *time_s* as ``HH:MM:SS.FF`` SMPTE timecode.

    Parameters
    ----------
    time_s:
        Position in seconds.
    fps:
        Frames per second (must match the MA3 timecode slot frame rate).

    Returns
    -------
    str
        e.g. ``"00:01:23.15"`` for 83.5 s at 30 fps.
    """
    total_frames = int(time_s * fps)
    hh = total_frames // (fps * 3600)
    mm = (total_frames % (fps * 3600)) // (fps * 60)
    ss = (total_frames % (fps * 60)) // fps
    ff = total_frames % fps
    return f"{hh:02d}:{mm:02d}:{ss:02d}.{ff:02d}"


# ---------------------------------------------------------------------------
# ShowWriter
# ---------------------------------------------------------------------------

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
        Extra pause after storing each cue before clearing and starting
        the next one.  100 ms gives MA3 time to index the new cue.
    tc_fps:
        Frame rate used when formatting timecode values.
        Should match the grid video FPS (default 30).
    """

    def __init__(
        self,
        client: MA3OscClient,
        settings: BridgeSettings,
        cmd_gap_s: float = 0.025,
        cue_gap_s: float = 0.100,
        tc_fps: int = 30,
    ) -> None:
        self._client = client
        self._s = settings
        self._cmd_gap = cmd_gap_s
        self._cue_gap = cue_gap_s
        self._tc_fps = tc_fps

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
        tc_slot: int = 1,
    ) -> None:
        """Program all cues into MA3 sequence *sequence_id*.

        Each cue is stored with its content (RGB + brightness) **and** a
        timecode trigger so MA3 can fire it automatically when the timecode
        clock reaches the stored timestamp.

        Parameters
        ----------
        cues:
            Ordered list of :class:`~mli_bridge.mapper.cue_builder.MA3Cue`.
        sequence_id:
            MA3 sequence number (``Store Seq <id> Cue N``).
        sequence_name:
            Label applied after all cues are stored.
        tc_slot:
            MA3 timecode slot number used for the trigger assignments.
        """
        logger.info(
            "ShowWriter: writing {} cues → Seq {} '{}' (tc_slot={}, fps={})",
            len(cues), sequence_id, sequence_name, tc_slot, self._tc_fps,
        )
        self._cmd("Clear")   # start with a clean programmer

        # Disable the interactive Store confirmation dialog so every
        # Store command runs silently without waiting for user input.
        self._cmd('Set Preference "StoreMode" "CueOnly"')
        self._cmd('Set Preference "StoreAskForMode" "Never"')

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

            # ---- store cue content ----
            self._cmd(
                f"Store Seq {sequence_id} Cue {cue.cue_number:.3f} /NoConfirm"
            )
            time.sleep(self._cue_gap)

            # ---- attach timecode trigger ----
            tc = _format_timecode(cue.time_s, self._tc_fps)
            self._cmd(
                f"Store Sequence {sequence_id} Cue {cue.cue_number:.1f}"
                f" Timecode {tc_slot} At {tc} /NoConfirm"
            )

            self._cmd("Clear")

            # Yield to event loop every 10 cues so progress updates can print
            if idx % 10 == 9:
                await asyncio.sleep(0)

        # ---- finalise sequence ----
        self._cmd(f'Label Seq {sequence_id} "{sequence_name}"')
        self._cmd(f"Goto Seq {sequence_id} Cue 1.000")
        logger.info(
            "ShowWriter: Seq {} '{}' ready ({} cues, timecode slot {}).",
            sequence_id, sequence_name, total, tc_slot,
        )

    # -------------------------------------------------------- timecode playback

    def start_timecode_playback(
        self,
        sequence_id: int = 1,
        timecode_slot: int = 1,
        audio_path: Path | None = None,
    ) -> None:
        """Configure MA3 timecode and start playback.

        Steps
        -----
        1. Set timecode slot to **Internal** clock (MA3 drives its own clock).
        2. Bind *sequence_id* to the timecode slot so MA3 fires cues
           automatically when the clock reaches each stored timestamp.
        3. If *audio_path* is provided:

           a. Pre-load audio into memory (eliminates disk-seek latency at
              start time).
           b. Start the MA3 timecode clock and audio playback **simultaneously**
              (OSC send + thread.start() back-to-back, <1 ms apart).
           c. Block until audio finishes.

        4. If no *audio_path*: just send ``Go+ TimecodeSlot`` and return.

        Parameters
        ----------
        sequence_id:
            The MA3 sequence that was programmed with timecode triggers.
        timecode_slot:
            MA3 timecode slot to use (must match the slot used in
            :meth:`write_show`).
        audio_path:
            Optional WAV/AIFF file to play in sync with the timecode clock.
            When provided this method blocks until playback is complete.
        """
        logger.info(
            "ShowWriter: configuring timecode slot {} for Seq {}",
            timecode_slot, sequence_id,
        )

        # ---- 1. Internal clock ----
        self._cmd(f"Assign TimecodeSlot {timecode_slot} /Type Internal")

        # ---- 2. Bind sequence to slot ----
        self._cmd(f"Assign Sequence {sequence_id} /TimecodeSlot {timecode_slot}")

        if audio_path is not None:
            # ---- 3a. Pre-load audio ----
            import sounddevice as sd
            import soundfile as sf

            audio_path = Path(audio_path)
            logger.info("Loading audio from {} …", audio_path)
            data, samplerate = sf.read(str(audio_path), dtype="float32", always_2d=True)
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

            # ---- 3b. Fire timecode + audio together ----
            logger.info(
                "Starting timecode slot {} and audio ({:.1f} s) simultaneously …",
                timecode_slot, duration_s,
            )
            self._client.send_command(f"Go+ TimecodeSlot {timecode_slot}")
            audio_thread.start()   # starts immediately after OSC send (<1 ms)

            # ---- 3c. Block until done ----
            done.wait(timeout=duration_s + 10.0)
            logger.info("ShowWriter: audio + timecode playback complete.")

        else:
            # ---- No audio: just start the clock ----
            self._cmd(f"Go+ TimecodeSlot {timecode_slot}")
            logger.info(
                "ShowWriter: timecode slot {} started (no audio).", timecode_slot
            )
