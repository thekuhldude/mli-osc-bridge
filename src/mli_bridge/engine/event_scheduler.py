"""EventScheduler: offline audio analysis → sorted OscEvent timeline.

The scheduler runs *before* playback starts.  It iterates over all
audio features and creates a list of :class:`OscEvent` objects sorted
by ``time_s``.  The :class:`CueEngine` then fires them in order using
``time.perf_counter()`` for sub-millisecond timing.

Every OscEvent payload carries a ``"commands"`` key — a list of
individual MA3 command strings.  The CueEngine calls
``client.send_command(cmd)`` for each string with a 20 ms gap between
them (required by MA3 for Attribute commands to take effect).

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
    set_color_rgb,
    set_intensity,
    set_intensity_and_color,
)
from mli_bridge.settings import BridgeSettings


class CommandType(IntEnum):
    """Broad category of OSC action; used for logging and filtering."""
    STRUCTURAL = 0   # segment change / cue jump + colour tint
    BEAT_FLASH = 1   # beat-driven intensity flash
    ENERGY = 2       # continuous dimmer follow + beat restore
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
        individual MA3 command strings to fire (20 ms apart) at ``time_s``.
    fired:
        Set to True by the CueEngine after the event is dispatched.
    """
    time_s: float
    priority: int = field(compare=False)
    command_type: CommandType = field(compare=False)
    payload: dict[str, Any] = field(compare=False)
    fired: bool = field(default=False, compare=False)


# ------------------------------------------------------------------ segment colour palette

# Fixed 5-colour palette for structural segments; cycles if there are more
# than 5 segments in the track.
_SEGMENT_PALETTE: list[tuple[int, int, int]] = [
    (0,   100, 255),   # 0: blue
    (255, 50,  0),     # 1: orange
    (0,   255, 50),    # 2: green
    (255, 0,   150),   # 3: pink
    (100, 0,   255),   # 4: purple
]


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

        # Split fixtures into left and right halves for alternating beat flashes.
        # With only one fixture, both halves point to the same ID.
        mid = max(1, len(self._ids) // 2)
        self._left_ids = self._ids[:mid]
        self._right_ids = self._ids[mid:] if len(self._ids) > 1 else self._ids

    def build_timeline(self, features: AudioFeatures) -> list[OscEvent]:
        """Run all rule passes and return a fully sorted event list.

        Returns
        -------
        list[OscEvent]
            Events sorted by ``(time_s, priority)`` ascending.
            Each event's ``payload["commands"]`` is a list of individual
            MA3 command strings ready to fire (20 ms apart).
        """
        events: list[OscEvent] = []
        events.extend(self._beat_flash_events(features))
        events.extend(self._energy_events(features))
        events.extend(self._onset_strobe_events(features))
        events.extend(self._structural_events(features))

        events.sort(key=lambda e: (e.time_s, e.priority))
        logger.info(
            "Timeline built: {} events  |  fixtures {} Thru {}  |  left {} / right {}",
            len(events),
            self._ids[0],
            self._ids[-1],
            self._left_ids,
            self._right_ids,
        )
        return events

    # ---------------------------------------------------------------- beats

    def _beat_flash_events(self, f: AudioFeatures) -> list[OscEvent]:
        """Intensity flash on every beat with left/right alternation.

        Even beats  → left half of fixtures flash to full brightness.
        Odd beats   → right half of fixtures flash to full brightness.

        One frame after the flash a restore event (type ENERGY) returns
        all fixtures to the current RMS dimmer level.
        """
        events: list[OscEvent] = []
        floor_pct = self._s.energy_dimmer_floor * 100.0

        for beat_idx, frame in enumerate(f.beat_frames):
            t = float(frame) / f.fps
            strength = (
                float(f.beat_strength[beat_idx])
                if beat_idx < len(f.beat_strength)
                else 1.0
            )
            intensity = strength * self._s.beat_flash_brightness * 100.0
            flash_ids = self._left_ids if beat_idx % 2 == 0 else self._right_ids

            # Intensity-only flash (no colour change — colour is set by segment events)
            events.append(OscEvent(
                time_s=t,
                priority=int(CommandType.BEAT_FLASH),
                command_type=CommandType.BEAT_FLASH,
                payload={
                    "commands": [set_intensity(flash_ids, intensity)],
                    "strength": round(strength, 3),
                    "intensity_pct": round(intensity, 1),
                    "side": "left" if beat_idx % 2 == 0 else "right",
                },
            ))

            # Restore: 1 frame later, all fixtures back to RMS level
            restore_frame = int(frame) + 1
            if restore_frame < f.n_frames:
                t_restore = restore_frame / f.fps
                rms = float(f.rms_curve[restore_frame])
                restore_pct = floor_pct + (100.0 - floor_pct) * rms
                events.append(OscEvent(
                    time_s=t_restore,
                    priority=int(CommandType.ENERGY),
                    command_type=CommandType.ENERGY,
                    payload={
                        "commands": [set_intensity(self._ids, restore_pct)],
                        "intensity_pct": round(restore_pct, 1),
                        "rms": round(rms, 3),
                    },
                ))

        return events

    # ---------------------------------------------------------------- energy

    def _energy_events(self, f: AudioFeatures) -> list[OscEvent]:
        """Dimmer-follow: one event per frame where level changes > 1 %."""
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
            events.append(OscEvent(
                time_s=t,
                priority=int(CommandType.ENERGY),
                command_type=CommandType.ENERGY,
                payload={
                    "commands": [set_intensity(self._ids, intensity_pct)],
                    "intensity_pct": round(intensity_pct, 1),
                    "rms": round(rms, 3),
                },
            ))
        return events

    # ---------------------------------------------------------------- strobe

    def _onset_strobe_events(self, f: AudioFeatures) -> list[OscEvent]:
        """Hard on/off strobe bursts at strong onsets inside loud sections.

        Uses intensity-only commands (no colour change) so the strobe fires
        instantly without the 20 ms Attribute gaps.
        """
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
                    set_intensity(self._ids, 100)
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
        """Cue-jump + fixed-palette colour change at every segment boundary."""
        events: list[OscEvent] = []
        for i, (start_frame, _end_frame, mean_rms) in enumerate(f.structural_segments):
            t = start_frame / f.fps
            cue = float(i + 1)
            r, g, b = _SEGMENT_PALETTE[i % len(_SEGMENT_PALETTE)]
            cmds: list[str] = [f"Goto Seq {self._seq} Cue {cue:.3f}"]
            cmds.extend(set_color_rgb(self._ids, r, g, b))
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
