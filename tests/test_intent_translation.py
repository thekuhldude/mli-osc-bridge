"""Tests for the intent schema, grid groups, and MA3 translator.

Run with:
    uv run pytest tests/test_intent_translation.py -v
"""
from __future__ import annotations

import pytest

from mli_bridge.grid_groups import (
    build_grid_from_fixtures,
    derive_groups,
    resolve_group,
)
from mli_bridge.intent_schema import (
    COLOR_MAP,
    CueIntent,
    Effect,
    EffectType,
    GroupState,
    ShowIntent,
)
from mli_bridge.intent_to_ma3 import translate_cue, translate_show


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_fixtures(
    n_rows: int = 3,
    n_cols: int = 4,
    z_values: tuple = (0.5, 2.0, 4.0),
    x_values: tuple = (1.0, 3.0, 7.0, 9.0),
) -> list[dict]:
    """Build a synthetic 3×4 fixture grid (12 fixtures, IDs 1–12)."""
    fixtures = []
    fid = 1
    for z in z_values:
        for x in x_values:
            fixtures.append({"id": fid, "name": f"F{fid}", "x_m": x, "y_m": 0.0, "z_m": z})
            fid += 1
    return fixtures


# ---------------------------------------------------------------------------
# grid_groups
# ---------------------------------------------------------------------------

class TestBuildGrid:
    def test_shape_3x4(self):
        grid = build_grid_from_fixtures(_make_fixtures())
        assert len(grid) == 3
        for row in grid.values():
            assert len(row) == 4

    def test_truss_is_row_0(self):
        """Highest z (4.0) maps to row 0 (Truss)."""
        grid = build_grid_from_fixtures(_make_fixtures())
        # z=4.0 → IDs 9–12
        truss_ids = sorted(grid[0].values())
        assert truss_ids == [9, 10, 11, 12]

    def test_floor_is_last_row(self):
        """Lowest z (0.5) maps to the highest row index (Floor)."""
        grid = build_grid_from_fixtures(_make_fixtures())
        floor_ids = sorted(grid[2].values())
        assert floor_ids == [1, 2, 3, 4]

    def test_mid_is_row_1(self):
        grid = build_grid_from_fixtures(_make_fixtures())
        mid_ids = sorted(grid[1].values())
        assert mid_ids == [5, 6, 7, 8]

    def test_columns_sorted_left_to_right(self):
        """Within each row, col 0 has the smallest x_m."""
        grid = build_grid_from_fixtures(_make_fixtures())
        for row in grid.values():
            ids_in_col_order = [row[c] for c in sorted(row.keys())]
            # With our fixture set, IDs within a band also happen to be
            # sorted by x because we created them that way.
            assert ids_in_col_order == sorted(ids_in_col_order)

    def test_empty_fixtures(self):
        grid = build_grid_from_fixtures([])
        assert grid == {}

    def test_noisy_z_values(self):
        """Slight noise in z should still cluster correctly."""
        fixtures = [
            {"id": i+1, "name": f"F{i+1}",
             "x_m": float(i % 4), "y_m": 0.0,
             "z_m": [0.48, 0.52, 0.50, 0.51][i % 4] if i < 4
                    else ([2.0, 2.1, 1.9, 2.0][i % 4] if i < 8
                          else [4.0, 3.9, 4.1, 4.0][i % 4])}
            for i in range(12)
        ]
        grid = build_grid_from_fixtures(fixtures)
        assert len(grid) == 3
        assert all(len(row) == 4 for row in grid.values())


class TestDeriveGroups:
    def test_all_has_12(self):
        grid = build_grid_from_fixtures(_make_fixtures())
        g = derive_groups(grid)
        assert len(g["all"]) == 12
        assert set(g["all"]) == set(range(1, 13))

    def test_truss_4_fixtures(self):
        grid = build_grid_from_fixtures(_make_fixtures())
        assert len(derive_groups(grid)["truss"]) == 4

    def test_left_right_split(self):
        grid = build_grid_from_fixtures(_make_fixtures())
        g = derive_groups(grid)
        assert len(g["left"]) == 6
        assert len(g["right"]) == 6
        assert set(g["left"]) | set(g["right"]) == set(g["all"])
        assert set(g["left"]) & set(g["right"]) == set()

    def test_left_is_smaller_x(self):
        """Left group should contain the 2 smallest-x fixtures per row."""
        grid = build_grid_from_fixtures(_make_fixtures())
        # col 0 = x=1.0, col 1 = x=3.0, col 2 = x=7.0, col 3 = x=9.0
        # left = cols 0-1: IDs 1,2,5,6,9,10 (first two per row)
        g = derive_groups(grid)
        assert set(g["left"]) == {1, 2, 5, 6, 9, 10}
        assert set(g["right"]) == {3, 4, 7, 8, 11, 12}


class TestResolveGroup:
    def test_valid_groups(self):
        grid = build_grid_from_fixtures(_make_fixtures())
        for name in ("all", "truss", "mid", "floor", "left", "right"):
            ids = resolve_group(name, grid)
            assert isinstance(ids, list)
            assert len(ids) > 0

    def test_invalid_group_raises(self):
        grid = build_grid_from_fixtures(_make_fixtures())
        with pytest.raises(ValueError, match="Unknown group"):
            resolve_group("diagonal", grid)

    def test_column_order_in_truss(self):
        """Truss fixtures should be in ascending x order (col 0 → col 3)."""
        grid = build_grid_from_fixtures(_make_fixtures())
        ids = resolve_group("truss", grid)
        assert ids == [9, 10, 11, 12]   # left → right


# ---------------------------------------------------------------------------
# intent_schema
# ---------------------------------------------------------------------------

class TestGroupState:
    def test_valid_color_name(self):
        gs = GroupState(group="all", intensity=80, color="blue")
        assert gs.color == "blue"

    def test_valid_hex_color(self):
        gs = GroupState(group="truss", intensity=100, color="#FF8000")
        assert gs.color == "#FF8000"

    def test_invalid_color_raises(self):
        with pytest.raises(Exception):
            GroupState(group="all", intensity=50, color="chartreuse_spectre")

    def test_intensity_bounds(self):
        with pytest.raises(Exception):
            GroupState(group="all", intensity=101, color="red")
        with pytest.raises(Exception):
            GroupState(group="all", intensity=-1, color="red")

    def test_boundary_intensity(self):
        GroupState(group="all", intensity=0,   color="off")
        GroupState(group="all", intensity=100, color="white")


class TestCueIntent:
    def test_empty_states_raises(self):
        with pytest.raises(Exception):
            CueIntent(cue_number=1, start_s=0.0, duration_s=4.0, states=[])


class TestShowIntent:
    def test_unsorted_cues_raises(self):
        with pytest.raises(Exception):
            ShowIntent(cues=[
                CueIntent(cue_number=2, start_s=4.0, duration_s=2.0, states=[
                    GroupState(group="all", intensity=100, color="white")]),
                CueIntent(cue_number=1, start_s=0.0, duration_s=2.0, states=[
                    GroupState(group="all", intensity=80, color="blue")]),
            ])

    def test_empty_cues_raises(self):
        with pytest.raises(Exception):
            ShowIntent(cues=[])


# ---------------------------------------------------------------------------
# intent_to_ma3
# ---------------------------------------------------------------------------

def _simple_show() -> tuple[ShowIntent, dict]:
    fixtures = _make_fixtures()
    grid = build_grid_from_fixtures(fixtures)
    show = ShowIntent(
        bpm=120.0,
        cues=[
            CueIntent(
                cue_number=1, start_s=0.0, duration_s=4.0, fade_s=0.5,
                states=[GroupState(group="truss", intensity=80, color="blue")],
            ),
            CueIntent(
                cue_number=2, start_s=4.0, duration_s=4.0, fade_s=0.5,
                states=[GroupState(group="floor", intensity=100, color="red")],
            ),
        ],
    )
    return show, grid


class TestTranslateCue:
    def test_static_none_produces_fixture_commands(self):
        _, grid = _simple_show()
        cue = CueIntent(
            cue_number=3, start_s=8.0, duration_s=2.0,
            states=[GroupState(group="truss", intensity=80, color="blue")],
        )
        cmds = translate_cue(cue, grid, sequence_id=1)

        fixture_cmds = [c for c in cmds if c.startswith("Fixture")]
        assert len(fixture_cmds) == 4
        assert any("ColorRGB_B" in c and "At 255" in c for c in cmds)
        assert any("ColorRGB_R" in c and "At 0" in c for c in cmds)
        assert all("At 80" in c for c in fixture_cmds)

    def test_static_has_no_step2(self):
        _, grid = _simple_show()
        cue = CueIntent(
            cue_number=1, start_s=0.0, duration_s=2.0,
            states=[GroupState(group="all", intensity=100, color="white")],
        )
        assert "Step 2" not in translate_cue(cue, grid)

    def test_store_command_present(self):
        _, grid = _simple_show()
        cue = CueIntent(
            cue_number=5, start_s=0.0, duration_s=2.0,
            states=[GroupState(group="all", intensity=50, color="white")],
        )
        cmds = translate_cue(cue, grid, sequence_id=2)
        assert any("Store Sequence 2 Cue 5.0" in c for c in cmds)

    def test_trigtype_time_present(self):
        _, grid = _simple_show()
        cue = CueIntent(
            cue_number=1, start_s=0.0, duration_s=2.0,
            states=[GroupState(group="all", intensity=100, color="white")],
        )
        assert any('TrigType" "Time"' in c for c in translate_cue(cue, grid))

    def test_clear_at_start_and_end(self):
        _, grid = _simple_show()
        cue = CueIntent(
            cue_number=1, start_s=0.0, duration_s=2.0,
            states=[GroupState(group="mid", intensity=60, color="amber")],
        )
        cmds = translate_cue(cue, grid)
        assert cmds[0] == "Clear"
        assert cmds[-1] == "Clear"

    def test_hex_color_command(self):
        _, grid = _simple_show()
        cue = CueIntent(
            cue_number=1, start_s=0.0, duration_s=2.0,
            states=[GroupState(group="floor", intensity=100, color="#FF8000")],
        )
        cmds = translate_cue(cue, grid)
        assert any("ColorRGB_R" in c and "At 255" in c for c in cmds)
        assert any("ColorRGB_G" in c and "At 128" in c for c in cmds)
        assert any("ColorRGB_B" in c and "At 0"   in c for c in cmds)

    # ── Pulse ─────────────────────────────────────────────────────────────────

    def test_pulse_has_step2(self):
        _, grid = _simple_show()
        cue = CueIntent(
            cue_number=1, start_s=0.0, duration_s=4.0,
            states=[GroupState(group="truss", intensity=100, color="white",
                               effect=Effect(type=EffectType.PULSE, speed="1/4"))],
        )
        assert "Step 2" in translate_cue(cue, grid)

    def test_pulse_step1_on_step2_off(self):
        _, grid = _simple_show()
        cue = CueIntent(
            cue_number=1, start_s=0.0, duration_s=4.0,
            states=[GroupState(group="truss", intensity=80, color="blue",
                               effect=Effect(type=EffectType.PULSE, speed="1/4"))],
        )
        cmds = translate_cue(cue, grid)
        step2_idx = cmds.index("Step 2")
        on_cmds  = [c for c in cmds[:step2_idx]        if c.startswith("Fixture")]
        off_cmds = [c for c in cmds[step2_idx + 1:]    if c.startswith("Fixture")]
        assert all("At 80" in c for c in on_cmds)
        assert all("At 0"  in c for c in off_cmds)

    def test_pulse_speed_command_with_bpm(self):
        # 1/4 at 120 BPM → 120 * 4 = 480 phaser BPM
        _, grid = _simple_show()
        cue = CueIntent(
            cue_number=1, start_s=0.0, duration_s=4.0,
            states=[GroupState(group="all", intensity=100, color="white",
                               effect=Effect(type=EffectType.PULSE, speed="1/4"))],
        )
        cmds = translate_cue(cue, grid, bpm=120.0)
        assert any("SpeedFromX" in c and "480" in c for c in cmds)
        assert not any("PhaseCycleTime" in c for c in cmds)

    def test_pulse_speed_uses_default_bpm_when_none(self):
        # No BPM supplied → falls back to 120 default → 1/4 = 480
        _, grid = _simple_show()
        cue = CueIntent(
            cue_number=1, start_s=0.0, duration_s=4.0,
            states=[GroupState(group="all", intensity=100, color="white",
                               effect=Effect(type=EffectType.PULSE, speed="1/4"))],
        )
        cmds = translate_cue(cue, grid, bpm=None)
        assert any("SpeedFromX" in c for c in cmds)
        assert not any("PhaseCycleTime" in c for c in cmds)

    # ── Strobe ────────────────────────────────────────────────────────────────

    def test_strobe_has_step2(self):
        _, grid = _simple_show()
        cue = CueIntent(
            cue_number=1, start_s=0.0, duration_s=4.0,
            states=[GroupState(group="floor", intensity=100, color="red",
                               effect=Effect(type=EffectType.STROBE, speed="1/8"))],
        )
        assert "Step 2" in translate_cue(cue, grid)

    def test_strobe_speed_faster_than_pulse(self):
        # 1/8 at 120 BPM → 120 * 8 = 960 phaser BPM (faster than pulse at 1/4)
        _, grid = _simple_show()
        cue = CueIntent(
            cue_number=1, start_s=0.0, duration_s=4.0,
            states=[GroupState(group="all", intensity=100, color="white",
                               effect=Effect(type=EffectType.STROBE, speed="1/8"))],
        )
        cmds = translate_cue(cue, grid, bpm=120.0)
        speed_cmd = next(c for c in cmds if "SpeedFromX" in c)
        assert float(speed_cmd.split()[-1]) >= 900   # 960 > pulse 480

    # ── Chase ─────────────────────────────────────────────────────────────────

    def test_chase_has_step2(self):
        _, grid = _simple_show()
        cue = CueIntent(
            cue_number=1, start_s=0.0, duration_s=8.0,
            states=[GroupState(group="all", intensity=80, color="cyan",
                               effect=Effect(type=EffectType.CHASE, speed="1/4"))],
        )
        assert "Step 2" in translate_cue(cue, grid)

    def test_chase_has_matricks_phase_selection_syntax(self):
        _, grid = _simple_show()
        cue = CueIntent(
            cue_number=1, start_s=0.0, duration_s=8.0,
            states=[GroupState(group="all", intensity=80, color="cyan",
                               effect=Effect(type=EffectType.CHASE, speed="1/4"))],
        )
        cmds = translate_cue(cue, grid)
        assert any("Set Selection MAtricks" in c and "PhaseFromX" in c for c in cmds)
        assert any("Set Selection MAtricks" in c and "PhaseToX"   in c and "360" in c
                   for c in cmds)

    def test_chase_phase_uses_selection_not_pool_object(self):
        """Must use 'Set Selection MAtricks', NOT 'Set MATricks 1'."""
        _, grid = _simple_show()
        cue = CueIntent(
            cue_number=2, start_s=4.0, duration_s=4.0,
            states=[GroupState(group="left", intensity=90, color="green",
                               effect=Effect(type=EffectType.CHASE, speed="1/2"))],
        )
        cmds = translate_cue(cue, grid)
        assert not any("Set MATricks 1" in c for c in cmds)
        assert any('"PhaseFromX" 0'   in c for c in cmds)
        assert any('"PhaseToX"'       in c and "360" in c for c in cmds)

    # ── Programmer hygiene ────────────────────────────────────────────────────

    def test_clear_present_in_every_cue(self):
        """Clear must appear in every cue's command list to prevent step bleed."""
        fixtures = _make_fixtures()
        grid     = build_grid_from_fixtures(fixtures)
        for etype in (EffectType.PULSE, EffectType.STROBE, EffectType.CHASE, EffectType.NONE):
            cue = CueIntent(
                cue_number=1, start_s=0.0, duration_s=4.0,
                states=[GroupState(group="all", intensity=100, color="white",
                                   effect=Effect(type=etype, speed="1/4"))],
            )
            cmds = translate_cue(cue, grid)
            assert "Clear" in cmds, f"Clear missing for {etype}"

    def test_matricks_reset_after_phaser_cue(self):
        """Phaser cues must emit MAtricks reset after Clear to prevent bleed."""
        fixtures = _make_fixtures()
        grid     = build_grid_from_fixtures(fixtures)
        for etype in (EffectType.PULSE, EffectType.STROBE, EffectType.CHASE):
            cue = CueIntent(
                cue_number=1, start_s=0.0, duration_s=4.0,
                states=[GroupState(group="all", intensity=100, color="white",
                                   effect=Effect(type=etype, speed="1/4"))],
            )
            cmds = translate_cue(cue, grid)
            clear_idx = max(i for i, c in enumerate(cmds) if c == "Clear")
            tail = cmds[clear_idx:]
            assert any("PhaseFromX" in c for c in tail), f"No PhaseFromX reset after Clear for {etype}"
            assert any("SpeedFromX" in c for c in tail), f"No SpeedFromX reset after Clear for {etype}"

    def test_static_cue_ends_with_clear(self):
        """Static cue has no phaser reset — last command is Clear."""
        _, grid = _simple_show()
        cue = CueIntent(
            cue_number=1, start_s=0.0, duration_s=2.0,
            states=[GroupState(group="mid", intensity=60, color="amber")],
        )
        assert translate_cue(cue, grid)[-1] == "Clear"


class TestTranslateShow:
    def test_first_cue_gets_pre_roll(self):
        show, grid = _simple_show()
        cmds = translate_show(show, grid, pre_roll_s=2.0)
        pre_roll_cmd = next(c for c in cmds if "TrigTime" in c and "Cue 1.0" in c)
        assert "2.00" in pre_roll_cmd

    def test_second_cue_trig_time_is_delta(self):
        show, grid = _simple_show()
        cmds = translate_show(show, grid)
        cue2_cmd = next(c for c in cmds if "TrigTime" in c and "Cue 2.0" in c)
        assert "4.00" in cue2_cmd

    def test_clearall_and_goto_at_end(self):
        show, grid = _simple_show()
        cmds = translate_show(show, grid)
        assert "ClearAll" in cmds
        assert "Goto Cue 1 Sequence 1" in cmds

    def test_no_illegal_preference_commands(self):
        """'Set Preference StoreMode/StoreAskForMode' returned Illegal object in MA3."""
        show, grid = _simple_show()
        cmds = translate_show(show, grid)
        assert not any("StoreMode"       in c for c in cmds)
        assert not any("StoreAskForMode" in c for c in cmds)
        assert not any("Set Preference"  in c for c in cmds)

    def test_all_cues_stored(self):
        show, grid = _simple_show()
        cmds = translate_show(show, grid)
        assert any("Store Sequence 1 Cue 1.0" in c for c in cmds)
        assert any("Store Sequence 1 Cue 2.0" in c for c in cmds)

    def test_bpm_propagates_to_speed_commands(self):
        fixtures = _make_fixtures()
        grid     = build_grid_from_fixtures(fixtures)
        show = ShowIntent(
            bpm=120.0,
            cues=[CueIntent(
                cue_number=1, start_s=0.0, duration_s=4.0,
                states=[GroupState(group="all", intensity=100, color="white",
                                   effect=Effect(type=EffectType.PULSE, speed="1/4"))],
            )],
        )
        cmds = translate_show(show, grid)
        assert any("SpeedFromX" in c for c in cmds)
        assert not any("PhaseCycleTime" in c for c in cmds)

    def test_command_count_reasonable(self):
        show, grid = _simple_show()
        assert len(translate_show(show, grid)) > 20
