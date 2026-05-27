"""EventScheduler: offline audio analysis → sorted OscEvent timeline.

The scheduler runs *before* playback starts.  It iterates over all
audio features and creates a list of :class:`OscEvent` objects sorted
by ``time_s``.  The :class:`CueEngine` then fires them in order using
``time.perf_counter()`` for sub-millisecond timing.

Every OscEvent payload carries a ``"commands"`` key — a list of
ready-to-fire MA3 command strings built with the typed helpers from
:mod:`mli_bridge.osc.commands`.  The CueEngine calls
``client.send_command(cmd)`` for each string, keeping all business
logic here and the engine dumb.

Event priority (lower = fires first at ties):
  0 — sequence / structural cue changes
  1 — beat flash
  2 — energy / dimmer update
  3 — onset strobe
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

import numpy as np
from loguru import logger

from mli_bridge.audio.analyzer import AudioFeatures
from mli_bridge.osc.commands import (
    blackout,
    set_intensity,
    set_intensity_and_color,
    set_color_rgb,
)
from mli_bridge.settings import BridgeSettings


class CommandType(IntEnum):
    """Broad category of OSC action; used for logging and filtering."""
    STRUCTURAL = 0   # segment change / cue jump
    BEAT_FLASH = 1   # beat-driven flash
    ENERGY = 2       # continuous dimmer follow
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
        Dict with at least a ``"commands": list[str]`` key containing
        the pre-built MA3 command strings to fire at ``time_s``.
    fired:
        Set to True by the CueEngine after the event is dispatched.
    """
    time_s: float
    priority: int = field(compare=False)
    command_type: CommandType = field(compare=False)
    payload: dict[str, Any] = field(compare=False)
    fired: bool = field(default=False, compare=False)


# ------------------------------------------------------------------ colour helpers

def _segment_color(mean_rms: float) -> tuple[int, int, int]:
    """Map segment energy to a RGB tint.

    Quiet segments → cool deep blue.  Loud segments → warm amber.
    """
    cool = (10, 30, 200)
    warm = (255, 140, 10)
    t = float(np.clip(mean_rms, 0.0, 1.0))
    r = int(cool[0] + (warm[0] - cool[0]) * t)
    g = int(cool[1] + (warm[1] - cool[1]) * t)
    b = int(cool[2] + (warm[2] - cool[2]) * t)
    return r, g, b


def _band_color(low: float, mid: float, high: float) -> tuple[int, int, int]:
    """Map dominant frequency band to an RGB color.

    Low → red/orange bass look.  Mid → green-teal mids.  High → blue highs.
    """
    dominant = int(np.argmax([low, mid, high]))
    if dominant == 0:
        return (255, 60, 0)    # bass: red-orange
    elif dominant == 1:
        return (0, 200, 80)    # mids: green-teal
    else:
        return (0, 80, 255)    # highs: blue


class EventScheduler:
    """Convert :class:`AudioFeatures` into a sorted :class:`OscEvent` list.

    Parameters
    ----------
    settings:
        Bridge settings.
    fixture_ids:
        All fixture IDs that the bridge should address.  Used to build
        ``Fixture <lo> Thru <hi>`` ranges in MA3 commands.
        Defaults to ``[1]`` when not provided.
    seq_number:
        MA3 sequence number used for structural cue-jump commands.
    """

    def __init__(
        self,
        settings: BridgeSettings,
        fixture_ids: list[int] | None = None,
        seq_number: int = 1,
    ) -> None:
        self._s = settings
        self._ids = sorted(fixture_ids) if fixture_ids else [1]
        self._seq = seq_number

    def build_timeline(self, features: AudioFeatures) -> list[OscEvent]:
        """Run all rule passes and return a fully sorted event list.

        Parameters
        ----------
        features:
            Pre-computed audio features from
            :func:`~mli_bridge.audio.analyzer.analyze`.

        Returns
        -------
        list[OscEvent]
            Events sorted by ``(time_s, priority)`` ascending.
            Each event's ``payload["commands"]`` is a list of MA3
            command strings ready to fire.
        """
        events: list[OscEvent] = []
        events.extend(self._beat_flash_events(features))
        events.extend(self._energy_events(features))
        events.extend(self._onset_strobe_events(features))
        events.extend(self._structural_events(features))

        events.sort(key=lambda e: (e.time_s, e.priority))
        logger.info(
            "Timeline built: {} events for fixtures {} Thru {}",
            len(events),
            self._ids[0],
            self._ids[-1],
        )
        return events

    # ---------------------------------------------------------------- beats

    def _beat_flash_events(self, f: AudioFeatures) -> list[OscEvent]:
        """White full-intensity flash on every beat, strength-scaled."""
        events: list[OscEvent] = []
        for beat_idx, frame in enumerate(f.beat_frames):
            t = float(frame) / f.fps
            strength = (
                float(f.beat_strength[beat_idx])
                if beat_idx < len(f.beat_strength)
                else 1.0
            )
            intensity = strength * self._s.beat_flash_brightness * 100.0
            # White flash — frequency colour rule recolours on the energy track
            cmd = set_intensity_and_color(self._ids, intensity, 255, 255, 255)
            events.append(OscEvent(
                time_s=t,
                priority=int(CommandType.BEAT_FLASH),
                command_type=CommandType.BEAT_FLASH,
                payload={
                    "commands": [cmd],
                    "strength": round(strength, 3),
                    "intensity_pct": round(intensity, 1),
                },
            ))
        return events

    # ---------------------------------------------------------------- energy

    def _energy_events(self, f: AudioFeatures) -> list[OscEvent]:
        """Emit one dimmer-update event per frame (throttled to > 1 % change)."""
        events: list[OscEvent] = []
        floor_pct = self._s.energy_dimmer_floor * 100.0
        prev_pct = -1.0
        for frame in range(f.n_frames):
            t = frame / f.fps
            rms = float(f.rms_curve[frame])
            intensity_pct = floor_pct + (100.0 - floor_pct) * rms
            if abs(intensity_pct - prev_pct) < 1.0:
                continue
            prev_pct = intensity_pct
            cmd = set_intensity(self._ids, intensity_pct)
            events.append(OscEvent(
                time_s=t,
                priority=int(CommandType.ENERGY),
                command_type=CommandType.ENERGY,
                payload={
                    "commands": [cmd],
                    "intensity_pct": round(intensity_pct, 1),
                    "rms": round(rms, 3),
                },
            ))
        return events

    # ---------------------------------------------------------------- strobe

    def _onset_strobe_events(self, f: AudioFeatures) -> list[OscEvent]:
        """Hard on/off strobe bursts at strong onsets inside loud sections."""
        events: list[OscEvent] = []
        thr = self._s.onset_strobe_threshold
        gate = self._s.onset_energy_gate
        n_strobe_frames = 3

        triggers = (f.onset_strength >= thr) & (f.rms_curve >= gate)
        trigger_idxs = np.where(triggers)[0]

        last = -10
        for idx in trigger_idxs:
            idx = int(idx)
            if idx - last < 4:
                continue
            last = idx
            for k in range(n_strobe_frames):
                ft = idx + k
                if ft >= f.n_frames:
                    break
                t = ft / f.fps
                on = k % 2 == 0
                cmd = (
                    set_intensity_and_color(self._ids, 100, 255, 255, 255)
                    if on
                    else blackout(self._ids)
                )
                events.append(OscEvent(
                    time_s=t,
                    priority=int(CommandType.STROBE),
                    command_type=CommandType.STROBE,
                    payload={"commands": [cmd], "on": on},
                ))
        return events

    # ----------------------------------------------------------- structure

    def _structural_events(self, f: AudioFeatures) -> list[OscEvent]:
        """Cue-jump + colour-tint change at every structural segment boundary."""
        events: list[OscEvent] = []
        for i, (start_frame, _end_frame, mean_rms) in enumerate(f.structural_segments):
            t = start_frame / f.fps
            cue = float(i + 1)
            r, g, b = _segment_color(mean_rms)
            cmds = [
                f"Goto Seq {self._seq} Cue {cue:.3f}",
                set_color_rgb(self._ids, r, g, b),
            ]
            events.append(OscEvent(
                time_s=t,
                priority=int(CommandType.STRUCTURAL),
                command_type=CommandType.STRUCTURAL,
                payload={
                    "commands": cmds,
                    "segment_index": i,
                    "cue_number": cue,
                    "color_rgb": (r, g, b),
                    "mean_rms": round(mean_rms, 3),
                },
            ))
        return events
