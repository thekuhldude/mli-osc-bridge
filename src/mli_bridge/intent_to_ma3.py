"""Deterministic Intent → grandMA3 command translator.

Converts a :class:`~mli_bridge.intent_schema.ShowIntent` into an ordered list
of MA3 command strings that build the complete show as cues in a sequence.

Phaser mechanism (verified from official docs)
----------------------------------------------
A phaser = multiple programmer steps before Store.  A single step = static
look.  ``Step 2`` creates and activates the second step in the programmer;
further fixture commands after it target that step.  Storing a cue with
two steps creates a repeating phaser between them.

References
----------
* https://help.malighting.com/grandMA3/2.0/HTML/phaser.html
* https://help.malighting.com/grandMA3/2.0/HTML/phaser_create_dimmer.html
* MAtricks for phase distribution:
  https://help2.malighting.com/Page/grandMA3/keyword_MAtricks/en/1.9

Effect implementations
----------------------
``none``   — 1 step (static).  Fully verified, unchanged from v1.
``pulse``  — 2 steps: intensity At <val> → intensity At 0.  Smooth repeat.
``strobe`` — 2 steps: intensity At <val> → intensity At 0.  Fast cycle.
             Generic RGB fixtures have no native strobe attribute; dimmer
             phaser is used and logged.
``chase``  — 2-step dimmer phaser + MATricks PhaseFrom=0 PhaseTo=360 to
             cascade the cycle phase left-to-right across grid column order.

Phaser rate
-----------
Cycle time is derived from ``Effect.speed`` (note value) and ``ShowIntent.bpm``:
    cycle_s = (60 / bpm) × speed_fraction

After storing the cue the cycle time is applied via::

    Set Cue <n>.0 Sequence <seq> Property "PhaseCycleTime" <seconds>

VERIFY: "PhaseCycleTime" is the property name from MA3 2.x.
If it has changed, adjust :func:`_set_rate_commands` — that is the only
place that name appears.
Ref: https://help.malighting.com/grandMA3/2.0/HTML/phaser_properties.html

Programmer hygiene
------------------
After every cue is stored the programmer is explicitly ``Clear``-ed so
that step data does not bleed into the next cue's programming.
"""
from __future__ import annotations

from loguru import logger

from mli_bridge.grid_groups import resolve_group
from mli_bridge.intent_schema import (
    COLOR_MAP,
    CueIntent,
    EffectType,
    GroupState,
    ShowIntent,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SEQUENCE_ID = 1
PRE_ROLL_S          = 2.0   # seconds of MA3-only run-up before audio starts

# Note-value speed multiplier: faster subdivisions = more cycles/minute
# "1/4" at 120 BPM = 480 cycles/minute (the phaser runs 4× per beat)
_SPEED_MULTIPLIERS: dict[str, float] = {
    "1/1":  1.0,
    "1/2":  2.0,
    "1/4":  4.0,
    "1/8":  8.0,
    "1/16": 16.0,
}

_DEFAULT_BPM = 120.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_color(color: str) -> tuple[int, int, int]:
    """Return ``(R, G, B)`` 0–255 from a colour name or ``#RRGGBB`` string."""
    if color.startswith("#"):
        h = color.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return COLOR_MAP[color]


def _phaser_bpm(speed: str, bpm: float | None) -> float:
    """Convert speed notation + show BPM into phaser cycle BPM.

    MA3 MAtricks SpeedFromX/SpeedToX accept a BPM value directly.
    Faster subdivisions produce more cycles per minute:
        "1/4" at 120 show-BPM → 480 phaser BPM

    Falls back to ``_DEFAULT_BPM`` when show BPM is unavailable.

    Ref: https://help2.malighting.com/Page/grandMA3/keyword_MAtricks/en/1.9
    IMPORTANT: if MA3 shows an unexpected speed, this value is the first
    place to adjust.  One function, one number.
    """
    base = bpm if (bpm and bpm > 0) else _DEFAULT_BPM
    mult = _SPEED_MULTIPLIERS.get(speed, 4.0)
    return base * mult


def _matricks_speed_commands(speed: str, bpm: float | None) -> list[str]:
    """Programmer commands to set the phaser speed via MAtricks.

    Applied to the current Selection in the programmer (before Store).
    Sets SpeedFromX = SpeedToX so the speed is uniform across all
    fixtures rather than swept across a range.

    Ref: https://help2.malighting.com/Page/grandMA3/keyword_MAtricks/en/1.9
    """
    pbpm = _phaser_bpm(speed, bpm)
    logger.info(
        "phaser speed: Effect.speed={!r}  show_bpm={}  → phaser_bpm={:.1f}",
        speed, bpm, pbpm,
    )
    return [
        f'Set Selection MAtricks "SpeedFromX" {pbpm:.1f}',
        f'Set Selection MAtricks "SpeedToX"   {pbpm:.1f}',
    ]


def _select_group(fixture_ids: list[int]) -> str:
    """Build a single MA3 selection command for all fixtures using '+' syntax.

    ``Fixture 9 + 10 + 11 + 12``

    Required before applying MAtricks so that SpeedFromX/PhaseFromX act on
    ALL group fixtures simultaneously, not just the last individually selected one.
    """
    return "Fixture " + " + ".join(str(fid) for fid in fixture_ids)


def _apply_matricks(
    fixture_ids: list[int],
    phase_to: int,
    speed: str,
    bpm: float | None,
) -> list[str]:
    """Re-select the full group, reset MAtricks to neutral, then apply values.

    Must be called BEFORE Store, while the programmer still holds step data.
    The re-select ensures MAtricks targets ALL fixtures, not just the last one
    that was individually programmed.

    phase_to:
        0   → whole group pulses/strobes in phase
        360 → chase cascade left → right
    """
    pbpm = _phaser_bpm(speed, bpm)
    logger.info(
        "MAtricks: phase_to={} speed={!r} → phaser_bpm={:.1f}",
        phase_to, speed, pbpm,
    )
    return [
        # Re-select all group fixtures at once (+ syntax)
        _select_group(fixture_ids),
        # Baseline reset first (clean slate in case previous cue's values linger)
        'Set Selection MAtricks "PhaseFromX" 0',
        'Set Selection MAtricks "PhaseToX"   0',
        'Set Selection MAtricks "SpeedFromX" 0',
        'Set Selection MAtricks "SpeedToX"   0',
        # Apply desired values
        f'Set Selection MAtricks "PhaseFromX" 0',
        f'Set Selection MAtricks "PhaseToX"   {phase_to}',
        f'Set Selection MAtricks "SpeedFromX" {pbpm:.1f}',
        f'Set Selection MAtricks "SpeedToX"   {pbpm:.1f}',
    ]


# ---------------------------------------------------------------------------
# Programmer-fill functions
# Each returns the list of MA3 programmer commands (before Store).
# ---------------------------------------------------------------------------

def _fill_static(gs: GroupState, grid: dict) -> list[str]:
    """1-step static look: intensity + colour per fixture (column order)."""
    fixture_ids = resolve_group(gs.group, grid)
    r, g, b = _resolve_color(gs.color)
    cmds: list[str] = []
    for fid in fixture_ids:
        cmds.append(f"Fixture {fid} At {gs.intensity}")
        cmds.append(f'Attribute "ColorRGB_R" At {r}')
        cmds.append(f'Attribute "ColorRGB_G" At {g}')
        cmds.append(f'Attribute "ColorRGB_B" At {b}')
    return cmds


def _fill_pulse(gs: GroupState, grid: dict, bpm: float | None) -> list[str]:
    """2-step dimmer phaser (whole group pulses in phase): intensity ↔ 0.

    PhaseFromX=0 / PhaseToX=0 — all fixtures start at the same phase,
    so they breathe together.  Only speed differs from strobe.
    MAtricks is applied after re-selecting the full group.
    """
    fixture_ids = resolve_group(gs.group, grid)
    r, g, b = _resolve_color(gs.color)
    cmds: list[str] = []

    # ── Step 1 : on ──────────────────────────────────────────────────────────
    for fid in fixture_ids:
        cmds.append(f"Fixture {fid} At {gs.intensity}")
        cmds.append(f'Attribute "ColorRGB_R" At {r}')
        cmds.append(f'Attribute "ColorRGB_G" At {g}')
        cmds.append(f'Attribute "ColorRGB_B" At {b}')

    cmds.append("Step 2")

    # ── Step 2 : off ─────────────────────────────────────────────────────────
    for fid in fixture_ids:
        cmds.append(f"Fixture {fid} At 0")
        cmds.append(f'Attribute "ColorRGB_R" At {r}')
        cmds.append(f'Attribute "ColorRGB_G" At {g}')
        cmds.append(f'Attribute "ColorRGB_B" At {b}')

    # ── MAtricks: re-select all, phase 0→0 (in-phase), set speed ─────────────
    cmds.extend(_apply_matricks(fixture_ids, phase_to=0, speed=gs.effect.speed, bpm=bpm))
    return cmds


def _fill_strobe(gs: GroupState, grid: dict, bpm: float | None) -> list[str]:
    """2-step hard-snap dimmer phaser (same structure as pulse, faster speed).

    Generic RGB fixtures have no native strobe attribute (logged).
    Phase 0→0 keeps the whole group in sync; speed drives the flash rate.
    """
    logger.info(
        "strobe: Generic RGB has no native strobe attribute — "
        "using fast dimmer phaser for group {!r}.",
        gs.group,
    )
    fixture_ids = resolve_group(gs.group, grid)
    r, g, b = _resolve_color(gs.color)
    cmds: list[str] = []

    # ── Step 1 : on ──────────────────────────────────────────────────────────
    for fid in fixture_ids:
        cmds.append(f"Fixture {fid} At {gs.intensity}")
        cmds.append(f'Attribute "ColorRGB_R" At {r}')
        cmds.append(f'Attribute "ColorRGB_G" At {g}')
        cmds.append(f'Attribute "ColorRGB_B" At {b}')

    cmds.append("Step 2")

    # ── Step 2 : hard off ────────────────────────────────────────────────────
    for fid in fixture_ids:
        cmds.append(f"Fixture {fid} At 0")

    # ── MAtricks: re-select all, phase 0→0 (in-phase), set speed ─────────────
    cmds.extend(_apply_matricks(fixture_ids, phase_to=0, speed=gs.effect.speed, bpm=bpm))
    return cmds


def _fill_chase(gs: GroupState, grid: dict, bpm: float | None) -> list[str]:
    """2-step dimmer phaser + MAtricks phase cascade (left→right column order).

    PhaseFromX=0 / PhaseToX=360 distributes the start-phase 0°–360°
    across the group so each fixture begins its cycle at a different
    offset, producing a running-light cascade.
    """
    fixture_ids = resolve_group(gs.group, grid)
    r, g, b = _resolve_color(gs.color)
    cmds: list[str] = []

    # ── Step 1 : on ──────────────────────────────────────────────────────────
    for fid in fixture_ids:
        cmds.append(f"Fixture {fid} At {gs.intensity}")
        cmds.append(f'Attribute "ColorRGB_R" At {r}')
        cmds.append(f'Attribute "ColorRGB_G" At {g}')
        cmds.append(f'Attribute "ColorRGB_B" At {b}')

    cmds.append("Step 2")

    # ── Step 2 : off ─────────────────────────────────────────────────────────
    for fid in fixture_ids:
        cmds.append(f"Fixture {fid} At 0")

    # ── MAtricks: re-select all, phase 0→360 (cascade), set speed ────────────
    cmds.extend(_apply_matricks(fixture_ids, phase_to=360, speed=gs.effect.speed, bpm=bpm))
    return cmds


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def _fill_group_state(
    gs: GroupState,
    grid: dict,
    bpm: float | None = None,
) -> list[str]:
    """Route a GroupState to the correct fill function."""
    t = gs.effect.type
    if t == EffectType.NONE:
        return _fill_static(gs, grid)
    if t == EffectType.PULSE:
        return _fill_pulse(gs, grid, bpm)
    if t == EffectType.STROBE:
        return _fill_strobe(gs, grid, bpm)
    if t == EffectType.CHASE:
        return _fill_chase(gs, grid, bpm)
    logger.error("Unknown effect type {!r}; falling back to static.", t.value)
    return _fill_static(gs, grid)


# ---------------------------------------------------------------------------
# Cue translator
# ---------------------------------------------------------------------------

def translate_cue(
    cue: CueIntent,
    grid: dict,
    sequence_id: int = DEFAULT_SEQUENCE_ID,
    bpm: float | None = None,
) -> list[str]:
    """Translate one CueIntent into MA3 programmer + store + property commands.

    Structure
    ---------
    1. ``Clear``  — fresh programmer (no step bleed from prior cue).
    2. For each GroupState: fill programmer steps + colour + MAtricks phase.
    3. For each phaser state: apply MAtricks speed (before Store).
    4. ``Store Sequence … Cue … /NoConfirmation``
    5. ``Set Cue … Property "TrigType" "Time"``
    6. ``Clear``  — leave programmer clean for the next cue.
    7. MAtricks reset — zero out phase/speed so they don't bleed into
       the next cue's programmer context.

    Returns
    -------
    list[str]
        Commands including everything except TrigTime (set by translate_show).
    """
    cmds: list[str] = ["Clear"]

    # Programme all group states.
    # Phaser fill functions embed the group re-select + MAtricks application
    # BEFORE Store (while the programmer still holds step data).
    for gs in cue.states:
        cmds.extend(_fill_group_state(gs, grid, bpm))

    # Store
    cmds.append(
        f"Store Sequence {sequence_id} Cue {cue.cue_number:.1f} /NoConfirmation"
    )

    # TrigType (TrigTime value added by translate_show)
    cmds.append(
        f'Set Cue {cue.cue_number:.1f} Sequence {sequence_id}'
        f' Property "TrigType" "Time"'
    )

    # Clear programmer — must be last.
    # No post-Clear MAtricks reset: the Selection is empty after Clear so
    # those commands would be no-ops; the next cue's _apply_matricks
    # emits a baseline reset while fixtures are selected.
    cmds.append("Clear")

    return cmds


# ---------------------------------------------------------------------------
# Show translator
# ---------------------------------------------------------------------------

def translate_show(
    show: ShowIntent,
    grid: dict,
    sequence_id: int = DEFAULT_SEQUENCE_ID,
    pre_roll_s: float = PRE_ROLL_S,
) -> list[str]:
    """Translate a complete ShowIntent into an ordered MA3 command list.

    Returns commands to:
    * Suppress the Store confirmation dialog.
    * For each cue: programme → store → TrigType → rate → Clear.
    * Set TrigTime: PRE_ROLL_S for cue 1, delta seconds for cues 2+.
    * Park sequence at cue 1.

    Parameters
    ----------
    show:   ShowIntent to translate.
    grid:   Output of build_grid_from_fixtures().
    sequence_id: MA3 sequence number (default 1).
    pre_roll_s:  Lead-in for cue 1 after Go+ (default 2.0 s).
    """
    # Note: "Set Preference StoreMode/StoreAskForMode" returned
    # "Illegal object" errors on MA3.  /NoConfirmation on each Store
    # is sufficient — those Preference lines are omitted.
    all_cmds: list[str] = ["Clear"]

    prev_time_s = 0.0
    summary_rows: list[tuple[int, str, int]] = []   # (cue_num, effect, n_steps)

    for idx, cue in enumerate(show.cues):
        cue_cmds = translate_cue(cue, grid, sequence_id, bpm=show.bpm)
        all_cmds.extend(cue_cmds)

        trig_time = pre_roll_s if idx == 0 else (cue.start_s - prev_time_s)
        all_cmds.append(
            f'Set Cue {cue.cue_number:.1f} Sequence {sequence_id}'
            f' Property "TrigTime" {trig_time:.2f}'
        )
        prev_time_s = cue.start_s

        # Gather summary info
        effects = [gs.effect.type.value for gs in cue.states]
        n_steps = 2 if any(t != EffectType.NONE for t in
                           [gs.effect.type for gs in cue.states]) else 1
        summary_rows.append((cue.cue_number, "+".join(sorted(set(effects))), n_steps))

        logger.debug(
            "Cue {} translated: effects={}, steps={}, TrigTime={:.2f}s",
            cue.cue_number, effects, n_steps, trig_time,
        )

    all_cmds += ["ClearAll", f"Goto Cue 1 Sequence {sequence_id}"]

    # ── Summary ───────────────────────────────────────────────────────────────
    logger.info("─── translate_show summary ───────────────────────────────")
    for cue_num, effects, steps in summary_rows:
        logger.info("  Cue {:>3}  effects={}  steps={}", cue_num, effects, steps)
    logger.info(
        "  Total: {} cues, {} MA3 commands  (seq {})",
        len(show.cues), len(all_cmds), sequence_id,
    )
    logger.info("──────────────────────────────────────────────────────────")

    return all_cmds
