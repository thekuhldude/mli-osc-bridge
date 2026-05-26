"""EventScheduler: offline audio analysis → sorted OscEvent timeline.

The scheduler runs *before* playback starts.  It iterates over all
audio features and creates a list of :class:`OscEvent` objects sorted
by ``time_s``.  The :class:`CueEngine` then fires them in order using
``time.perf_counter()`` for sub-millisecond timing.

Event priority (lower = fires first at ties):
  0 — sequence / structural cue changes
  1 — beat flash
  2 — energy / dimmer update
  3 — onset strobe
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

from mli_bridge.audio.analyzer import AudioFeatures
from mli_bridge.settings import BridgeSettings


class CommandType(IntEnum):
    """Broad category of OSC action; used for logging and filtering."""
    STRUCTURAL = 0   # segment change / cue jump
    BEAT_FLASH = 1   # beat-driven flash
    ENERGY = 2       # continuous dimmer / color
    STROBE = 3       # onset strobe burst


@dataclass(order=True)
class OscEvent:
    """One discrete action to fire at a specific wall-clock time.

    Fields
    ------
    time_s:
        Seconds from the start of playback (after pre-roll).
    priority:
        Tie-breaker when two events share the same ``time_s``.
    command_type:
        Category label (for logging / ablation).
    payload:
        Arbitrary dict passed to the dispatcher.  Keys depend on
        ``command_type``; see :func:`build_timeline` for examples.
    fired:
        Set to True by the CueEngine after the event is dispatched.
    """
    time_s: float
    priority: int = field(compare=False)
    command_type: CommandType = field(compare=False)
    payload: dict[str, Any] = field(compare=False)
    fired: bool = field(default=False, compare=False)


class EventScheduler:
    """Convert :class:`AudioFeatures` into a sorted :class:`OscEvent` list."""

    def __init__(self, settings: BridgeSettings) -> None:
        self._s = settings

    def build_timeline(self, features: AudioFeatures) -> list[OscEvent]:
        """Run all rule passes and return a fully sorted event list.

        Parameters
        ----------
        features:
            Pre-computed audio features from :func:`~mli_bridge.audio.analyzer.analyze`.

        Returns
        -------
        list[OscEvent]
            Events sorted by ``(time_s, priority)`` ascending.
        """
        events: list[OscEvent] = []
        events.extend(self._beat_flash_events(features))
        events.extend(self._energy_events(features))
        events.extend(self._onset_strobe_events(features))
        events.extend(self._structural_events(features))

        events.sort(key=lambda e: (e.time_s, e.priority))
        logger.info("Timeline built: {} events", len(events))
        return events

    # ---------------------------------------------------------------- beats

    def _beat_flash_events(self, f: AudioFeatures) -> list[OscEvent]:
        events: list[OscEvent] = []
        decay_frames = 4
        for beat_idx, frame in enumerate(f.beat_frames):
            t = float(frame) / f.fps
            strength = (
                float(f.beat_strength[beat_idx])
                if beat_idx < len(f.beat_strength)
                else 1.0
            )
            pattern = "outer" if beat_idx % 2 == 0 else "inner"
            events.append(OscEvent(
                time_s=t,
                priority=int(CommandType.BEAT_FLASH),
                command_type=CommandType.BEAT_FLASH,
                payload={
                    "pattern": pattern,
                    "strength": strength,
                    "decay_frames": decay_frames,
                    "fps": f.fps,
                },
            ))
        return events

    # ---------------------------------------------------------------- energy

    def _energy_events(self, f: AudioFeatures) -> list[OscEvent]:
        """Emit one energy-dimmer event per frame."""
        events: list[OscEvent] = []
        floor = self._s.energy_dimmer_floor
        # Throttle: only emit when the change is > 1 % to reduce OSC traffic
        prev_level = -1.0
        for frame in range(f.n_frames):
            t = frame / f.fps
            rms = float(f.rms_curve[frame])
            level = floor + (1.0 - floor) * rms
            if abs(level - prev_level) < 0.01:
                continue
            prev_level = level
            events.append(OscEvent(
                time_s=t,
                priority=int(CommandType.ENERGY),
                command_type=CommandType.ENERGY,
                payload={
                    "level": level,
                    "low": float(f.low_band_energy[frame]),
                    "mid": float(f.mid_band_energy[frame]),
                    "high": float(f.high_band_energy[frame]),
                },
            ))
        return events

    # ---------------------------------------------------------------- strobe

    def _onset_strobe_events(self, f: AudioFeatures) -> list[OscEvent]:
        events: list[OscEvent] = []
        thr = self._s.onset_strobe_threshold
        gate = self._s.onset_energy_gate
        n_frames = 3  # strobe burst length

        triggers = (f.onset_strength >= thr) & (f.rms_curve >= gate)
        trigger_idxs = np.where(triggers)[0]

        last = -10
        for idx in trigger_idxs:
            idx = int(idx)
            if idx - last < 4:
                continue
            last = idx
            for k in range(n_frames):
                ft = idx + k
                if ft >= f.n_frames:
                    break
                t = ft / f.fps
                events.append(OscEvent(
                    time_s=t,
                    priority=int(CommandType.STROBE),
                    command_type=CommandType.STROBE,
                    payload={"on": k % 2 == 0},
                ))
        return events

    # ----------------------------------------------------------- structure

    def _structural_events(self, f: AudioFeatures) -> list[OscEvent]:
        """One cue-jump event per structural segment boundary."""
        events: list[OscEvent] = []
        for i, (start_frame, _end_frame, mean_rms) in enumerate(f.structural_segments):
            t = start_frame / f.fps
            events.append(OscEvent(
                time_s=t,
                priority=int(CommandType.STRUCTURAL),
                command_type=CommandType.STRUCTURAL,
                payload={
                    "segment_index": i,
                    "mean_rms": mean_rms,
                    "cue_number": float(i + 1),
                },
            ))
        return events
