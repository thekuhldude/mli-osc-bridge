"""Deterministic Intent → grandMA3 command translator.

Converts a :class:`~mli_bridge.intent_schema.ShowIntent` into an ordered list
of MA3 command strings that, when sent sequentially via OSC, build the
complete show as cues in a grandMA3 sequence.

Reuses the exact command style established in this repository
(``show_writer.py``, ``commands.py``):

    Fixture <id> At <pct>
    Attribute "ColorRGB_R" At <r>
    Attribute "ColorRGB_G" At <g>
    Attribute "ColorRGB_B" At <b>
    Store Sequence <seq> Cue <n>.0 /NoConfirmation
    Set Cue <n>.0 Sequence <seq> Property "TrigType" "Time"
    Set Cue <n>.0 Sequence <seq> Property "TrigTime" <t>

Effect implementation status
-----------------------------
``none``
    Fully implemented — static intensity + colour.

``pulse`` / ``strobe`` / ``chase``
    **NOT YET IMPLEMENTED** — these require MA3 Phasers whose exact
    command-line syntax has not been confirmed from official documentation.

    TODO: Verify phaser syntax against:
      https://help2.malighting.com/Page/grandMA3/csk_function_keywords/en/1.2
      https://help2.malighting.com/Page/grandMA3/gs_effects_phaser/en/1.2

    Possible MA3 phaser command pattern (UNVERIFIED — do NOT send to MA3):
      Store Phaser "<name>" Sequence <seq> Cue <n>
      (further sub-steps required for wave shape, period, phase)

    Until verified, all three effects fall back to static (none) programming
    and emit a :class:`loguru.logger` warning.  This is intentional: a
    correct static cue is always preferable to a fabricated phaser command
    that could error out or behave unexpectedly in a live show.
"""
from __future__ import annotations

from loguru import logger

from mli_bridge.grid_groups import resolve_group
from mli_bridge.intent_schema import COLOR_MAP, CueIntent, EffectType, GroupState, ShowIntent

# ---------------------------------------------------------------------------
# Constants — must match show_writer.py / settings.py
# ---------------------------------------------------------------------------

DEFAULT_SEQUENCE_ID = 1
PRE_ROLL_S          = 2.0   # seconds of MA3-only run-up before audio starts


# ---------------------------------------------------------------------------
# Colour resolution
# ---------------------------------------------------------------------------

def _resolve_color(color: str) -> tuple[int, int, int]:
    """Return ``(R, G, B)`` integers 0–255 for a colour name or hex string."""
    if color.startswith("#"):
        hex_str = color.lstrip("#")
        return (
            int(hex_str[0:2], 16),
            int(hex_str[2:4], 16),
            int(hex_str[4:6], 16),
        )
    return COLOR_MAP[color]


# ---------------------------------------------------------------------------
# Programmer-fill helpers (one GroupState → list of command strings)
# ---------------------------------------------------------------------------

def _fill_static(gs: GroupState, grid: dict) -> list[str]:
    """Generate programmer commands for a static (no-effect) GroupState.

    For each fixture in the group, in column order:
        Fixture <id> At <intensity>
        Attribute "ColorRGB_R" At <r>
        Attribute "ColorRGB_G" At <g>
        Attribute "ColorRGB_B" At <b>

    This matches the per-fixture pattern used in show_writer.py and is
    robust to non-contiguous fixture ID ranges (unlike ``Thru``).
    """
    fixture_ids = resolve_group(gs.group, grid)
    r, g, b     = _resolve_color(gs.color)
    cmds: list[str] = []
    for fid in fixture_ids:
        cmds.append(f"Fixture {fid} At {gs.intensity}")
        cmds.append(f'Attribute "ColorRGB_R" At {r}')
        cmds.append(f'Attribute "ColorRGB_G" At {g}')
        cmds.append(f'Attribute "ColorRGB_B" At {b}')
    return cmds


def _fill_effect(gs: GroupState, grid: dict, cue: CueIntent) -> list[str]:
    """Generate programmer commands for an effect GroupState.

    Currently falls back to :func:`_fill_static` for all effect types
    (``pulse``, ``strobe``, ``chase``) because MA3 Phaser command syntax
    requires verification against official documentation before it can be
    safely used in a live show environment.

    TODO: Implement MA3 Phaser creation for:

      pulse  — smooth sine dimmer phaser (period = speed × 60/bpm seconds)
               See: https://help2.malighting.com/Page/grandMA3/gs_effects_phaser/en/1.2

      strobe — hard on/off dimmer phaser (or native Shutter attribute
               if the patched fixture supports it; check GDTF file for
               "Shutter(n)" / "Strobe(n)" channels and emit a warning
               if the attribute is absent, falling back to a 50% duty
               cycle dimmer phaser at the requested speed).

      chase  — dimmer phaser with phase offset distributed evenly across
               fixtures in column order so the light travels left → right.
               phase_offset_per_fixture = 360° / len(fixture_ids)
               Fixture at column c gets phase = c × phase_offset_per_fixture.
    """
    effect_type = gs.effect.type

    if effect_type == EffectType.NONE:
        return _fill_static(gs, grid)

    # TODO: implement pulse/strobe/chase phaser commands (see module docstring)
    logger.warning(
        "Effect {!r} is not yet implemented in intent_to_ma3; "
        "falling back to static (none) for group {!r} in cue {}. "
        "See: https://help2.malighting.com/Page/grandMA3/gs_effects_phaser/en/1.2",
        effect_type.value, gs.group, cue.cue_number,
    )
    return _fill_static(gs, grid)


# ---------------------------------------------------------------------------
# Cue translator
# ---------------------------------------------------------------------------

def translate_cue(
    cue: CueIntent,
    grid: dict,
    sequence_id: int = DEFAULT_SEQUENCE_ID,
) -> list[str]:
    """Translate one :class:`~mli_bridge.intent_schema.CueIntent` to commands.

    Returns MA3 commands that:
    1. Clear the programmer.
    2. Fill each GroupState into the programmer (intensity + colour).
    3. Store the cue in the sequence with ``/NoConfirmation``.
    4. Set ``TrigType = Time`` (TrigTime is set by :func:`translate_show`).
    5. Clear the programmer again.

    Parameters
    ----------
    cue:
        The cue to translate.
    grid:
        Output of :func:`~mli_bridge.grid_groups.build_grid_from_fixtures`.
    sequence_id:
        MA3 sequence number (default 1).

    Returns
    -------
    list[str]
        Ordered MA3 command strings (without TrigTime — see :func:`translate_show`).
    """
    cmds: list[str] = ["Clear"]

    for gs in cue.states:
        if gs.effect.type == EffectType.NONE:
            cmds.extend(_fill_static(gs, grid))
        else:
            cmds.extend(_fill_effect(gs, grid, cue))

    # Store the cue
    cmds.append(
        f"Store Sequence {sequence_id} Cue {cue.cue_number:.1f}"
        f" /NoConfirmation"
    )

    # All cues use Time triggers (TrigTime set by translate_show)
    cmds.append(
        f'Set Cue {cue.cue_number:.1f} Sequence {sequence_id}'
        f' Property "TrigType" "Time"'
    )

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
    """Translate a complete :class:`~mli_bridge.intent_schema.ShowIntent`.

    Returns an ordered list of MA3 command strings that build the entire
    show as cues in *sequence_id*.  The commands follow the established
    pattern from ``show_writer.py``:

    * Suppress the Store confirmation dialog once at the start.
    * For each cue: fill programmer → store → set TrigType → TrigTime.
    * After all cues: ``ClearAll`` and park at cue 1.

    Timing
    ------
    Cue 1 fires ``pre_roll_s`` seconds after ``Go+`` (gives MA3 a clean
    run-up before audio starts).  Subsequent cues fire
    ``cue_n.start_s − cue_{n-1}.start_s`` seconds after the previous cue.

    Parameters
    ----------
    show:
        The show intent to translate.
    grid:
        Output of :func:`~mli_bridge.grid_groups.build_grid_from_fixtures`.
    sequence_id:
        MA3 sequence number (default 1).
    pre_roll_s:
        Lead-in silence for cue 1 (default 2.0 s).

    Returns
    -------
    list[str]
        Complete ordered MA3 command strings.
    """
    all_cmds: list[str] = [
        'Set Preference "StoreMode" "CueOnly"',
        'Set Preference "StoreAskForMode" "Never"',
        "Clear",
    ]

    prev_time_s = 0.0

    for idx, cue in enumerate(show.cues):
        all_cmds.extend(translate_cue(cue, grid, sequence_id))

        # Set TrigTime: pre-roll for the first cue, delta for the rest
        trig_time = pre_roll_s if idx == 0 else (cue.start_s - prev_time_s)
        all_cmds.append(
            f'Set Cue {cue.cue_number:.1f} Sequence {sequence_id}'
            f' Property "TrigTime" {trig_time:.2f}'
        )

        prev_time_s = cue.start_s

        logger.debug(
            "Cue {} translated: {} states, TrigTime={:.2f}s",
            cue.cue_number, len(cue.states), trig_time,
        )

    # Park at cue 1 ready for playback
    all_cmds += [
        "ClearAll",
        f"Goto Cue 1 Sequence {sequence_id}",
    ]

    logger.info(
        "translate_show: {} cues → {} MA3 commands (seq {})",
        len(show.cues), len(all_cmds), sequence_id,
    )
    return all_cmds
