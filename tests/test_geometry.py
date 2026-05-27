"""Tests for mapper.geometry: fixture_to_grid, get_grid_value, build_fixture_grid_map."""
from __future__ import annotations

import numpy as np
import pytest

from mli_bridge.mapper.geometry import (
    GridCoord,
    Stage3D,
    build_fixture_grid_map,
    fixture_to_grid,
    get_grid_value,
)
from mli_bridge.show.fixture_patch import FixtureInfo, FixturePatch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stage(width=10.0, depth=8.0, height=4.0) -> Stage3D:
    return Stage3D(width_m=width, depth_m=depth, height_m=height)


def _red_frame(rows=8, cols=12) -> np.ndarray:
    """Solid red grid frame."""
    frame = np.zeros((rows, cols, 3), dtype=np.uint8)
    frame[:, :, 0] = 255   # R=255, G=0, B=0
    return frame


def _patch_with_positions(*fixtures: FixtureInfo) -> FixturePatch:
    return FixturePatch(fixtures=list(fixtures))


# ---------------------------------------------------------------------------
# fixture_to_grid
# ---------------------------------------------------------------------------

class TestFixtureToGrid:
    def test_centre_fixture(self):
        """Stage centre (5 m, 2 m) maps to middle of grid."""
        stage = _stage()
        coord = fixture_to_grid(5.0, 2.0, stage, cols=12, rows=8)
        assert coord.col == 6    # 5/10 * 12 = 6
        assert coord.row == 4    # (1 - 2/4) * 8 = 4

    def test_top_left_corner(self):
        """Far left, full height → row 0, col 0."""
        stage = _stage()
        coord = fixture_to_grid(0.0, 4.0, stage, cols=12, rows=8)
        assert coord.col == 0
        assert coord.row == 0

    def test_bottom_right_corner(self):
        """Far right, floor level → last row, last col."""
        stage = _stage()
        # x=10 → col = min(12, 12-1) = 11  (clamped)
        # z=0  → row = (1-0/4)*8 = 8 → clamped to 7
        coord = fixture_to_grid(10.0, 0.0, stage, cols=12, rows=8)
        assert coord.col == 11
        assert coord.row == 7

    def test_clamping_out_of_bounds(self):
        """Positions outside the stage are clamped to grid edges."""
        stage = _stage()
        coord = fixture_to_grid(-5.0, -2.0, stage, cols=12, rows=8)
        assert coord.col == 0
        assert coord.row == 7   # z very negative → row clamped to 7

    def test_truss_level(self):
        """Fixture at truss height maps to row 0."""
        stage = _stage()
        coord = fixture_to_grid(5.0, 4.0, stage, cols=12, rows=8)
        assert coord.row == 0

    def test_floor_level(self):
        """Fixture at floor level maps to last row."""
        stage = _stage()
        coord = fixture_to_grid(5.0, 0.0, stage, cols=12, rows=8)
        assert coord.row == 7

    def test_different_grid_size(self):
        """Works with non-default grid size."""
        stage = _stage()
        coord = fixture_to_grid(5.0, 2.0, stage, cols=6, rows=4)
        assert coord.col == 3    # 5/10 * 6 = 3
        assert coord.row == 2    # (1 - 2/4) * 4 = 2


# ---------------------------------------------------------------------------
# get_grid_value
# ---------------------------------------------------------------------------

class TestGetGridValue:
    def test_exact_cell_no_window(self):
        """window=0 reads the exact cell."""
        frame = _red_frame()
        frame[3, 6] = [100, 150, 200]
        r, g, b = get_grid_value(frame, GridCoord(row=3, col=6), window=0)
        assert (r, g, b) == (100, 150, 200)

    def test_averaged_window(self):
        """window=1 averages a 3×3 neighbourhood."""
        frame = np.zeros((8, 12, 3), dtype=np.uint8)
        # 3×3 block at centre: all pixels set to (90, 0, 0)
        frame[2:5, 5:8, 0] = 90
        r, g, b = get_grid_value(frame, GridCoord(row=3, col=6), window=1)
        # Full 3×3 region is (90, 0, 0) → mean = (90, 0, 0)
        assert r == 90
        assert g == 0
        assert b == 0

    def test_solid_red_frame(self):
        """Solid red frame → all values read (255, 0, 0)."""
        frame = _red_frame()
        r, g, b = get_grid_value(frame, GridCoord(row=0, col=0), window=1)
        assert r == 255
        assert g == 0
        assert b == 0

    def test_edge_clipping(self):
        """Window at edge does not raise; clips to grid bounds."""
        frame = _red_frame(rows=8, cols=12)
        # Corner cell — window extends out of bounds, should clip gracefully
        r, g, b = get_grid_value(frame, GridCoord(row=0, col=0), window=1)
        assert r == 255   # still red (all in-bounds pixels)

    def test_return_type_ints(self):
        """Return values are plain Python ints."""
        frame = _red_frame()
        r, g, b = get_grid_value(frame, GridCoord(row=4, col=6), window=1)
        assert isinstance(r, int)
        assert isinstance(g, int)
        assert isinstance(b, int)


# ---------------------------------------------------------------------------
# build_fixture_grid_map
# ---------------------------------------------------------------------------

class TestBuildFixtureGridMap:
    def _make_fixture(self, fid: int, x_m=0.0, z_m=0.0, fixture_type="Unknown"):
        return FixtureInfo(
            id=fid,
            name=f"Fixture {fid}",
            fixture_type=fixture_type,
            universe=1,
            channel=fid,
            group="default",
            position="unknown",
            x_m=x_m,
            z_m=z_m,
        )

    def test_explicit_positions_used(self):
        """Fixtures with non-zero x_m/z_m use their own position."""
        stage = _stage()
        fx = self._make_fixture(1, x_m=2.0, z_m=1.0)
        patch = _patch_with_positions(fx)
        mapping = build_fixture_grid_map(patch, stage)
        coord = mapping[1]
        expected = fixture_to_grid(2.0, 1.0, stage)
        assert coord == expected

    def test_all_fixture_ids_present(self):
        """Every fixture in the patch appears in the mapping."""
        stage = _stage()
        fixtures = [self._make_fixture(i, x_m=float(i), z_m=1.0) for i in range(1, 5)]
        patch = _patch_with_positions(*fixtures)
        mapping = build_fixture_grid_map(patch, stage)
        assert set(mapping.keys()) == {1, 2, 3, 4}

    def test_fallback_for_unknown_type(self):
        """Zero-position fixture falls back to default for its fixture_type."""
        stage = _stage()
        fx_led = self._make_fixture(1, fixture_type="LED PAR")
        fx_mh = self._make_fixture(2, fixture_type="Moving Head")
        patch = _patch_with_positions(fx_led, fx_mh)
        mapping = build_fixture_grid_map(patch, stage)
        # LED PAR defaults to (5.0, 0.5) → near floor
        # Moving Head defaults to (5.0, 3.5) → near truss
        assert mapping[1].row > mapping[2].row   # LED PAR lower than Moving Head

    def test_grid_coord_is_frozen_dataclass(self):
        """GridCoord instances are frozen (hashable, immutable)."""
        coord = GridCoord(row=3, col=5)
        assert coord.row == 3
        assert coord.col == 5
        with pytest.raises((TypeError, AttributeError)):
            coord.row = 0  # type: ignore[misc]
