"""PlaybackController — synchronises audio playback and light engine.

Responsibilities
----------------
* Apply the pre-roll delay (silence before audio starts).
* Start sounddevice audio stream at a precise moment, recording the
  ``perf_counter()`` value at the first audio callback (``t0``).
* Hand ``t0`` to :class:`CueEngine` so it references the same clock.
* Block until the audio track has finished, then stop the engine.

Thread model
------------
::

    main thread:  PlaybackController.play()  ←─ blocks until done
    audio thread: sounddevice callback       ─→ writes samples to output
    engine thread: CueEngine._loop()         ─→ fires OSC events
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
from loguru import logger

from mli_bridge.engine.cue_engine import CueEngine
from mli_bridge.engine.event_scheduler import OscEvent
from mli_bridge.osc.client import MA3OscClient
from mli_bridge.settings import BridgeSettings


class PlaybackController:
    """Coordinates audio playback (sounddevice) with the CueEngine.

    Parameters
    ----------
    client:
        Connected MA3OscClient.
    events:
        Pre-sorted event list from :class:`~mli_bridge.engine.event_scheduler.EventScheduler`.
    settings:
        Bridge settings.
    seq_number:
        MA3 sequence number used for structural cue-jumps.
    """

    def __init__(
        self,
        client: MA3OscClient,
        events: list[OscEvent],
        settings: BridgeSettings,
        seq_number: int = 1,
    ) -> None:
        self._client = client
        self._engine = CueEngine(client, events, settings, seq_number)
        self._s = settings

    def play(self, wav_path: Path) -> None:
        """Start audio + lights synchronously.  Blocks until the track ends.

        Parameters
        ----------
        wav_path:
            Path to the WAV file to play.
        """
        wav_path = Path(wav_path)
        logger.info("Loading audio: {}", wav_path)

        audio_data, sample_rate = sf.read(str(wav_path), dtype="float32", always_2d=True)
        duration_s = len(audio_data) / sample_rate
        logger.info("Track: {:.1f}s @ {} Hz, {} ch", duration_s, sample_rate, audio_data.shape[1])

        pre_roll = self._s.pre_roll_seconds
        device = self._s.audio_device or None  # None = system default

        # The t0 is captured inside the first audio callback so it reflects
        # the exact moment samples hit the DAC, not just when we called play().
        t0_holder: list[float] = [0.0]
        engine_started: list[bool] = [False]

        # Frame cursor for the audio callback
        frame_pos: list[int] = [0]
        # Pre-roll as sample frames
        pre_roll_frames = int(pre_roll * sample_rate)
        silence = np.zeros((pre_roll_frames, audio_data.shape[1]), dtype=np.float32)
        full_audio = np.vstack([silence, audio_data])

        done_event = sd.Event()

        def _callback(
            outdata: np.ndarray,
            frames: int,
            _time_info: object,
            status: sd.CallbackFlags,
        ) -> None:
            if status:
                logger.warning("sounddevice status: {}", status)

            pos = frame_pos[0]
            remaining = len(full_audio) - pos

            if remaining <= 0:
                outdata[:] = 0
                raise sd.CallbackStop

            chunk = min(frames, remaining)
            outdata[:chunk] = full_audio[pos : pos + chunk]
            if chunk < frames:
                outdata[chunk:] = 0

            # Capture t0 on the very first callback (pos == 0)
            if pos == 0 and not engine_started[0]:
                t0_holder[0] = time.perf_counter()
                # Start the engine offset by pre-roll so events align to
                # the audio content (not the silence).
                adjusted_t0 = t0_holder[0] + pre_roll
                self._engine.start(adjusted_t0)
                engine_started[0] = True

            frame_pos[0] += chunk
            if remaining <= frames:
                raise sd.CallbackStop

        logger.info("Starting playback (pre-roll {:.1f}s) …", pre_roll)
        with sd.OutputStream(
            samplerate=sample_rate,
            channels=audio_data.shape[1],
            dtype="float32",
            device=device,
            callback=_callback,
            finished_callback=done_event.set,
        ):
            done_event.wait(timeout=duration_s + pre_roll + 5.0)

        self._engine.stop()
        logger.info("Playback complete")

    def stop(self) -> None:
        """Immediately halt the engine (audio will stop naturally)."""
        self._engine.stop()
