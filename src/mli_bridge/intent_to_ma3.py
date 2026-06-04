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
* MATricks for phase distribution:
  https://help2.malighting.com/Page/grandMA3/keyword_matricks/en/1.2

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

# Note-value fraction → float multiplier on one beat
_SPEED_FRACTIONS: dict[str, float] = {
    "1/1":  1.0,
    "1/2":  0.5,
    "1/4":  0.25,
    "1/8":  0.125,
    "1/16": 0.0625,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_color(color: str) -> tuple[int, int, int]:
    """Return ``(R, G, B)`` 0–255 from a colour name or ``#RRGGBB`` string."""
    if color.startswith("#"):
        h = color.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return COLOR_MAP[color]


def _cycle_s(speed: str, bpm: float | None) -> float | None:
    """Convert speed notation + BPM into a cycle time in seconds.

    Returns ``None`` when BPM is unavailable (no rate command will be emitted).
    """
    if not bpm or bpm <= 0:
        return None
    frac = _SPEED_FRACTIONS.get(speed, 0.25)
    return (60.0 / bpm) * frac


def _set_rate_commands(
    cue_number: float,
    sequence_id: int,
    speed: str,
    bpm: float | None,
) -> list[str]:
    """Post-store commands to apply the phaser cycle time to a cue.

    VERIFY: "PhaseCycleTime" is the property name in MA3 2.x.
    One-stop function — change the property name here if MA3 uses a
    different spelling.  Ref:
    https://help.malighting.com/grandMA3/2.0/HTML/phaser_properties.html
    """
    t = _cycle_s(speed, bpm)
    if t is None:
        logger.debug(
            "No BPM set for cue {} — phaser rate not programmed (MA3 default).",
            cue_number,
        )
        return []
    return [
        f'Set Cue {cue_number:.1f} Sequence {sequence_id}'
        f' Property "PhaseCycleTime" {t:.3f}'
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

    # ── MATricks phase distribution ───────────────────────────────────────────
    # Applied AFTER step 2 data, still in the programmer, before Store.
    # This spreads the phaser starting phase 0°–360° across all selected
    # fixtures so the chase cascades left-to-right.
    cmds.append('Set MATricks 1 "PhaseFrom" 0')
    cmds.append('Set MATricks 1 "PhaseTo" 360')

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
    1. ``Clear``  — fresh programmer (no step bleed from prior cue)
    2. For each GroupState: fill programmer steps + colour + MATricks.
    3. ``Store Sequence … Cue … /NoConfirmation``
    4. ``Set Cue … Property "TrigType" "Time"``
    5. Post-store rate commands (one per effect state that has a BPM speed).
    6. ``Clear``  — leave programmer clean for the next cue.

    Returns
    -------
    list[str]
        Commands including everything except TrigTime (set by translate_show).
    """
    cmds: list[str] = ["Clear"]

    for gs in cue.states:
        cmds.extend(_fill_group_state(gs, grid))

    # Store
    cmds.append(
        f"Store Sequence {sequence_id} Cue {cue.cue_number:.1f} /NoConfirmation"
    )

    # TrigType (TrigTime value added by translate_show)
    cmds.append(
        f'Set Cue {cue.cue_number:.1f} Sequence {sequence_id}'
        f' Property "TrigType" "Time"'
    )

    # Rate commands (one per phaser state)
    for gs in cue.states:
        if gs.effect.type != EffectType.NONE:
            cmds.extend(_set_rate_commands(cue.cue_number, sequence_id,
                                           gs.effect.speed, bpm))

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
    all_cmds: list[str] = [
        'Set Preference "StoreMode" "CueOnly"',
        'Set Preference "StoreAskForMode" "Never"',
        "Clear",
    ]

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
