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


def _matricks_reset_commands() -> list[str]:
    """Reset MAtricks phase and speed on the Selection after Clear.

    Prevents phase/speed values from a chase cue bleeding into the next
    cue's programmer state.
    """
    return [
        'Set Selection MAtricks "PhaseFromX" 0',
        'Set Selection MAtricks "PhaseToX"   0',
        'Set Selection MAtricks "SpeedFromX" 0',
        'Set Selection MAtricks "SpeedToX"   0',
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


def _fill_pulse(gs: GroupState, grid: dict) -> list[str]:
    """2-step dimmer phaser: intensity ↔ 0, colour constant in both steps.

    MA3 linearly interpolates between steps by default, producing a
    smooth breathing look.  Rate is set post-store via :func:`_set_rate_commands`.
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

    # Switch to step 2 (creates the phaser when stored)
    cmds.append("Step 2")

    # ── Step 2 : off ─────────────────────────────────────────────────────────
    # Keep colour in step 2 so MA3 interpolates colour channels too.
    for fid in fixture_ids:
        cmds.append(f"Fixture {fid} At 0")
        cmds.append(f'Attribute "ColorRGB_R" At {r}')
        cmds.append(f'Attribute "ColorRGB_G" At {g}')
        cmds.append(f'Attribute "ColorRGB_B" At {b}')

    return cmds


def _fill_strobe(gs: GroupState, grid: dict) -> list[str]:
    """2-step hard-snap dimmer phaser: intensity ↔ 0 at the chosen rate.

    Generic RGB fixtures do not have a native strobe/shutter attribute
    (no GDTF Shutter or Strobe channel), so a dimmer phaser is used.
    This is logged so it is easy to identify if a proper strobe fixture
    is later patched that could use its native attribute instead.

    Snap (zero-transition) behaviour is produced by setting a very short
    cycle time so MA3 jumps between the two values without visible fade.
    The rate command is emitted post-store by the caller.
    """
    logger.info(
        "strobe: Generic RGB has no native strobe attribute — "
        "using 2-step dimmer phaser for group {!r} in cue.",
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

    # Switch to step 2
    cmds.append("Step 2")

    # ── Step 2 : hard off ─────────────────────────────────────────────────────
    # Colour omitted in the dark step — dimmer = 0 so it makes no visual
    # difference, and skipping colour commands shortens the command list.
    for fid in fixture_ids:
        cmds.append(f"Fixture {fid} At 0")

    return cmds


def _fill_chase(gs: GroupState, grid: dict) -> list[str]:
    """2-step dimmer phaser + MATricks phase cascade (column order, left→right).

    Build a full/off phaser then spread the starting phase evenly across
    the group's fixtures using MATricks so each fixture begins its cycle
    at a different offset — producing a running-light chase.

    MATricks reference:
    https://help2.malighting.com/Page/grandMA3/keyword_matricks/en/1.2

    MATricks 1 applies to the current programmer selection.
    ``PhaseFrom 0 / PhaseTo 360`` distributes 0°–360° across the fixtures
    in the order they appear in the programmer (resolve_group returns them
    in column order — left → right).
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

    # Switch to step 2
    cmds.append("Step 2")

    # ── Step 2 : off ─────────────────────────────────────────────────────────
    for fid in fixture_ids:
        cmds.append(f"Fixture {fid} At 0")

    # ── MAtricks phase distribution ───────────────────────────────────────────
    # Applied AFTER step 2 data, still in the programmer, before Store.
    # Spreads the phaser start-phase 0°–360° across the Selection in
    # programmer order (= resolve_group column order = left → right).
    # Ref: https://help2.malighting.com/Page/grandMA3/keyword_MAtricks/en/1.9
    cmds.append('Set Selection MAtricks "PhaseFromX" 0')
    cmds.append('Set Selection MAtricks "PhaseToX"   360')

    return cmds


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def _fill_group_state(
    gs: GroupState,
    grid: dict,
) -> list[str]:
    """Route a GroupState to the correct fill function."""
    t = gs.effect.type
    if t == EffectType.NONE:
        return _fill_static(gs, grid)
    if t == EffectType.PULSE:
        return _fill_pulse(gs, grid)
    if t == EffectType.STROBE:
        return _fill_strobe(gs, grid)
    if t == EffectType.CHASE:
        return _fill_chase(gs, grid)
    # Should be unreachable given EffectType enum, but be safe
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

    has_phaser = any(gs.effect.type != EffectType.NONE for gs in cue.states)

    # Programme all group states
    for gs in cue.states:
        cmds.extend(_fill_group_state(gs, grid))

    # Speed — set in programmer before Store via MAtricks SpeedFromX/ToX
    for gs in cue.states:
        if gs.effect.type != EffectType.NONE:
            cmds.extend(_matricks_speed_commands(gs.effect.speed, bpm))

    # Store
    cmds.append(
        f"Store Sequence {sequence_id} Cue {cue.cue_number:.1f} /NoConfirmation"
    )

    # TrigType (TrigTime value added by translate_show)
    cmds.append(
        f'Set Cue {cue.cue_number:.1f} Sequence {sequence_id}'
        f' Property "TrigType" "Time"'
    )

    # Clear programmer (critical — prevents step bleed)
    cmds.append("Clear")

    # MAtricks reset — always emit so phase/speed from a chase/pulse don't
    # linger in the Selection's MAtricks context for the next cue
    if has_phaser:
        cmds.extend(_matricks_reset_commands())

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
