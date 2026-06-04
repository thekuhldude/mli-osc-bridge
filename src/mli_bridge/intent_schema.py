"""Pydantic models for the abstract show-intent format.

A :class:`ShowIntent` is a list of :class:`CueIntent` objects.  Each cue
describes which fixture groups should be at what intensity / colour /
effect at a given point in the show.  The intent layer is fully agnostic
of grandMA3 — it says *what* should happen, not *how* to command it.

The deterministic translator in :mod:`mli_bridge.intent_to_ma3` converts
a :class:`ShowIntent` into ordered MA3 command strings.

Color names
-----------
Use any key from :data:`COLOR_MAP`, or a hex string ``"#RRGGBB"``.

Effect speeds
-------------
Speed is expressed as a note value relative to the current BPM:
``"1/1"`` = one pulse per bar, ``"1/2"`` = half-note, ``"1/4"`` = quarter,
``"1/8"`` = eighth.  When ``bpm`` is given in :class:`ShowIntent` the
translator can compute the period in seconds.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

COLOR_MAP: dict[str, tuple[int, int, int]] = {
    "red":     (255,   0,   0),
    "green":   (  0, 255,   0),
    "blue":    (  0,   0, 255),
    "white":   (255, 255, 255),
    "amber":   (255, 150,   0),
    "cyan":    (  0, 255, 255),
    "magenta": (255,   0, 255),
    "yellow":  (255, 255,   0),
    "orange":  (255, 100,   0),
    "purple":  (150,   0, 255),
    "pink":    (255,  50, 150),
    "off":     (  0,   0,   0),
}

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

VALID_SPEEDS = {"1/1", "1/2", "1/4", "1/8", "1/16"}

# ---------------------------------------------------------------------------
# Effect
# ---------------------------------------------------------------------------

class EffectType(str, Enum):
    """Abstract effect type.  Translated to MA3 phasers (or dimmer steps)."""
    NONE   = "none"
    PULSE  = "pulse"    # smooth sine dimmer undulation
    STROBE = "strobe"   # hard on/off flash
    CHASE  = "chase"    # running light across fixtures (phase offset)


class Effect(BaseModel):
    type:  EffectType = EffectType.NONE
    speed: str = "1/4"   # relative note value: 1/1, 1/2, 1/4, 1/8

    @field_validator("speed")
    @classmethod
    def _check_speed(cls, v: str) -> str:
        if v not in VALID_SPEEDS:
            raise ValueError(
                f"speed must be one of {sorted(VALID_SPEEDS)}, got {v!r}"
            )
        return v


# ---------------------------------------------------------------------------
# GroupState
# ---------------------------------------------------------------------------

class GroupState(BaseModel):
    """The desired lighting state of one named group for the duration of a cue.

    Parameters
    ----------
    group:
        Name of the fixture group.  Must match a key from
        ``derive_groups(grid)`` — typically one of
        ``"all"``, ``"truss"``, ``"mid"``, ``"floor"``, ``"left"``, ``"right"``.
    intensity:
        Dimmer level 0 (off) – 100 (full).
    color:
        Color name from :data:`COLOR_MAP` or a ``"#RRGGBB"`` hex string.
    effect:
        Optional rhythmic effect to apply.
    """
    group:     str
    intensity: int   = Field(..., ge=0, le=100)
    color:     str
    effect:    Effect = Field(default_factory=Effect)

    @field_validator("color")
    @classmethod
    def _check_color(cls, v: str) -> str:
        if v not in COLOR_MAP and not _HEX_RE.match(v):
            raise ValueError(
                f"color must be a known name {sorted(COLOR_MAP)} "
                f"or a '#RRGGBB' hex string, got {v!r}"
            )
        return v


# ---------------------------------------------------------------------------
# CueIntent
# ---------------------------------------------------------------------------

class CueIntent(BaseModel):
    """One discrete lighting cue.

    Parameters
    ----------
    cue_number:
        Integer cue number (MA3 stores it as a float, e.g. ``1.000``).
    start_s:
        Seconds from the start of the show when this cue fires.
    duration_s:
        How long the cue lasts.  Informational only; MA3 advances by
        TrigType=Time from the next cue's start time.
    fade_s:
        Crossfade in seconds into this cue (default 0.5 s).
    states:
        One :class:`GroupState` per group that should change.
        Groups not mentioned keep their previous values.
    """
    cue_number: int   = Field(..., ge=1)
    start_s:    float = Field(..., ge=0.0)
    duration_s: float = Field(..., gt=0.0)
    fade_s:     float = Field(default=0.5, ge=0.0)
    states:     list[GroupState]

    @field_validator("states")
    @classmethod
    def _non_empty(cls, v: list[Any]) -> list[Any]:
        if not v:
            raise ValueError("states must contain at least one GroupState")
        return v


# ---------------------------------------------------------------------------
# ShowIntent
# ---------------------------------------------------------------------------

class ShowIntent(BaseModel):
    """The complete intent for one show.

    Parameters
    ----------
    bpm:
        Tempo in beats-per-minute.  Used by the translator to convert
        note-value speeds (``"1/4"`` etc.) into seconds for phasers.
        Optional — if absent, effect speeds are left as note values.
    cues:
        Ordered list of :class:`CueIntent`.  Should be sorted by
        ``start_s`` ascending (the translator validates this).
    """
    bpm:  float | None = Field(default=None, gt=0)
    cues: list[CueIntent]

    @field_validator("cues")
    @classmethod
    def _sorted_cues(cls, v: list[CueIntent]) -> list[CueIntent]:
        if not v:
            raise ValueError("cues must not be empty")
        for i in range(1, len(v)):
            if v[i].start_s < v[i - 1].start_s:
                raise ValueError(
                    f"cues must be sorted by start_s; "
                    f"cue {v[i].cue_number} (start={v[i].start_s}) "
                    f"is before cue {v[i-1].cue_number} (start={v[i-1].start_s})"
                )
        return v
