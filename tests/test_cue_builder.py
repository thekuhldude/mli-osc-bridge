"""Tests for mapper.cue_builder: MA3Cue, CueBuilder."""
from __future__ import annotations

import numpy as np
import pytest

from mli_bridge.mapper.cue_builder import CueBuilder, MA3Cue
from mli_bridge.mapper.geometry import Stage3D
from mli_bridge.mapper.grid_loader import GridData
from mli_bridge.show.fixture_patch import FixtureInfo, FixturePatch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stage() -> Stage3D:
    return Stage3D(width_m=10.0, depth_m=8.0, height_m=4.0)


def _fixture(fid: int, x_m: float = 5.0, z_m: float = 1.0) -> FixtureInfo:
    return FixtureInfo(
        id=fid,
        name=f"Fixture {fid}",
        fixture_type="LED PAR",
        universe=1,
        channel=fid,
        group="default",
        position="unknown",
        x_m=x_m,
        z_m=z_m,
    )


def _patch(*fixture_ids_and_positions) -> FixturePatch:
    """Build a minimal FixturePatch from (id, x_m, z_m) tuples."""
    fixtures = [_fixture(fid, x, z) for fid, x, z in fixture_ids_and_positions]
    return FixturePatch(fixtures=fixtures)


def _uniform_grid(n_frames=10, rows=8, cols=12, color=(128, 64, 32)) -> GridData:
    """Grid where every pixel has the same colour."""
    r, g, b = color
    frames = np.zeros((n_frames, rows, cols, 3), dtype=np.uint8)
    frames[:, :, :, 0] = r
    frames[:, :, :, 1] = g
    frames[:, :, :, 2] = b
    return GridData(frames=frames, fps=30.0)


# ---------------------------------------------------------------------------
# build_cue
# ---------------------------------------------------------------------------

class TestBuildCue:
    def test_correct_cue_number_and_time(self):
        patch = _patch((1, 5.0, 1.0))
        stage = _stage()
        builder = CueBuilder(patch, stage, cols=12, rows=8)
        grid = _uniform_grid(n_frames=60)
        cue = builder.build_cue(
            cue_number=3.0,
            grid_frame=grid.frames[0],
            time_s=10.5,
            next_time_s=12.0,
        )
        assert cue.cue_number == 3.0
        assert cue.time_s == pytest.approx(10.5, abs=0.001)

    def test_fade_is_80_percent_of_gap(self):
        patch = _patch((1, 5.0, 1.0))
        builder = CueBuilder(patch, _stage())
        grid = _uniform_grid(n_frames=60)
        cue = builder.build_cue(
            cue_number=1.0,
            grid_frame=grid.frames[0],
            time_s=0.0,
            next_time_s=2.0,   # gap = 2 s → fade = 2 * 0.8 = 1.6 s
        )
        assert cue.fade_s == pytest.approx(1.6, abs=0.01)

    def test_fade_clamped_minimum(self):
        patch = _patch((1, 5.0, 1.0))
        builder = CueBuilder(patch, _stage())
        grid = _uniform_grid(n_frames=60)
        cue = builder.build_cue(
            cue_number=1.0,
            grid_frame=grid.frames[0],
            time_s=0.0,
            next_time_s=0.05,  # gap = 0.05 s → fade = 0.04 < min 0.1 → 0.1 s
        )
        assert cue.fade_s == pytest.approx(0.1, abs=0.01)

    def test_fade_default_when_no_next(self):
        patch = _patch((1, 5.0, 1.0))
        builder = CueBuilder(patch, _stage())
        grid = _uniform_grid(n_frames=60)
        cue = builder.build_cue(
            cue_number=1.0,
            grid_frame=grid.frames[0],
            time_s=5.0,
            next_time_s=None,  # last cue → 1.0 s
        )
        assert cue.fade_s == pytest.approx(1.0, abs=0.01)

    def test_all_fixtures_in_output(self):
        patch = _patch((1, 2.0, 1.0), (2, 8.0, 1.0), (3, 5.0, 3.5))
        builder = CueBuilder(patch, _stage())
        grid = _uniform_grid(n_frames=60, color=(200, 100, 50))
        cue = builder.build_cue(1.0, grid.frames[0], 0.0)
        assert set(cue.fixture_colors.keys()) == {1, 2, 3}
        assert set(cue.fixture_brightness.keys()) == {1, 2, 3}

    def test_brightness_floor_applied(self):
        """Even a black grid should keep brightness ≥ brightness_floor."""
        patch = _patch((1, 5.0, 1.0))
        builder = CueBuilder(patch, _stage(), brightness_floor=0.05)
        # All-black frame
        black_frame = np.zeros((8, 12, 3), dtype=np.uint8)
        cue = builder.build_cue(1.0, black_frame, 0.0)
        assert cue.fixture_brightness[1] >= 0.05

    def test_rgb_values_in_range(self):
        """All colour components must be 0–255."""
        patch = _patch((1, 1.0, 0.5), (2, 9.0, 3.5))
        builder = CueBuilder(patch, _stage())
        grid = _uniform_grid(n_frames=10, color=(180, 90, 45))
        cue = builder.build_cue(1.0, grid.frames[0], 0.0)
        for fid, (r, g, b) in cue.fixture_colors.items():
            assert 0 <= r <= 255, f"fixture {fid} R={r} out of range"
            assert 0 <= g <= 255, f"fixture {fid} G={g} out of range"
            assert 0 <= b <= 255, f"fixture {fid} B={b} out of range"


# ---------------------------------------------------------------------------
# build_all_cues
# ---------------------------------------------------------------------------

class TestBuildAllCues:
    def test_one_cue_per_keyframe(self):
        patch = _patch((1, 5.0, 1.0))
        builder = CueBuilder(patch, _stage())
        grid = _uniform_grid(n_frames=90)
        keyframes = [0, 30, 60]
        cues = builder.build_all_cues(grid, keyframes)
        assert len(cues) == 3

    def test_cue_numbers_sequential(self):
        patch = _patch((1, 5.0, 1.0))
        builder = CueBuilder(patch, _stage())
        grid = _uniform_grid(n_frames=90)
        keyframes = [0, 10, 20, 30]
        cues = builder.build_all_cues(grid, keyframes)
        for i, cue in enumerate(cues):
            assert cue.cue_number == float(i + 1)

    def test_cue_times_from_fps(self):
        patch = _patch((1, 5.0, 1.0))
        builder = CueBuilder(patch, _stage())
        grid = _uniform_grid(n_frames=90)
        keyframes = [0, 30, 60]   # 30fps → t=0, 1.0, 2.0 s
        cues = builder.build_all_cues(grid, keyframes)
        assert cues[0].time_s == pytest.approx(0.0)
        assert cues[1].time_s == pytest.approx(1.0)
        assert cues[2].time_s == pytest.approx(2.0)

    def test_empty_keyframes_gives_empty_cues(self):
        patch = _patch((1, 5.0, 1.0))
        builder = CueBuilder(patch, _stage())
        grid = _uniform_grid(n_frames=90)
        cues = builder.build_all_cues(grid, [])
        assert cues == []
