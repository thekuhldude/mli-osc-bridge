"""Stage 3D geometry: fixture world-position → grid cell coordinate.

Coordinate conventions
----------------------
x_m : distance from **stage left** (0 = far left, stage.width_m = far right)
y_m : distance from **stage front** (not used for grid mapping)
z_m : height from **floor** (0 = floor, stage.height_m = truss/grid top)

Grid mapping
------------
``col = clamp(int(x_m / width_m * cols), 0, cols-1)``
``row = clamp(int((1 − z_m / height_m) * rows), 0, rows-1)``

Row 0 is the **top** of the grid (truss/ceiling).
Row rows-1 is the **bottom** of the grid (floor level).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from loguru import logger

from mli_bridge.show.fixture_patch import FixturePatch


# ---------------------------------------------------------------------------
# Fallback positions (x_m, z_m) by fixture_type when patch has 0,0,0.
# Assumes a 10 m wide × 4 m tall stage.
# ---------------------------------------------------------------------------
_FIXTURE_TYPE_DEFAULTS: dict[str, tuple[float, float]] = {
    "LED PAR":     (5.0, 0.5),    # mid-width, near floor
    "Moving Head": (5.0, 3.5),   # mid-width, near truss
    "Fresnel":     (5.0, 0.5),
    "Spot":        (5.0, 3.5),
    "Wash":        (5.0, 0.5),
    "Strobe":      (5.0, 4.0),
    "LED Bar":     (5.0, 0.3),
}
_FALLBACK_XZ: tuple[float, float] = (5.0, 1.0)


@dataclass
class Stage3D:
    """Physical stage dimensions (metres).

    width_m:
        Left-to-right span.
    depth_m:
        Front-to-back span (not used for grid mapping but kept for completeness).
    height_m:
        Floor to grid top (where the highest row of the visual grid sits).
    """
    width_m: float = 10.0
    depth_m: float = 8.0
    height_m: float = 4.0


@dataclass(frozen=True)
class GridCoord:
    """Immutable grid cell address.

    row : 0 = top (truss), rows-1 = bottom (floor)
    col : 0 = stage left, cols-1 = stage right
    """
    row: int
    col: int


def fixture_to_grid(
    x_m: float,
    z_m: float,
    stage: Stage3D,
    cols: int = 12,
    rows: int = 8,
) -> GridCoord:
    """Convert a fixture 3D position to a grid cell coordinate.

    Parameters
    ----------
    x_m:
        Fixture horizontal position from stage left (metres).
    z_m:
        Fixture height from floor (metres).
    stage:
        Physical stage dimensions.
    cols, rows:
        Grid dimensions (should match the .npz grid shape).

    Returns
    -------
    GridCoord
        Clamped to ``[0, cols-1]`` × ``[0, rows-1]``.
    """
    col = int(x_m / stage.width_m * cols)
    row = int((1.0 - z_m / stage.height_m) * rows)
    col = max(0, min(cols - 1, col))
    row = max(0, min(rows - 1, row))
    return GridCoord(row=row, col=col)


def get_grid_value(
    grid_frame: np.ndarray,
    coord: GridCoord,
    window: int = 1,
) -> tuple[int, int, int]:
    """Read an averaged RGB value from a grid frame neighbourhood.

    Parameters
    ----------
    grid_frame:
        Shape ``(rows, cols, 3)`` uint8.
    coord:
        Target grid cell.
    window:
        Half-size of the averaging neighbourhood.
        ``window=1`` → 3×3 box, ``window=0`` → exact cell only.

    Returns
    -------
    tuple[int, int, int]
        Mean RGB over the neighbourhood, clipped to grid bounds.
    """
    rows, cols = grid_frame.shape[:2]
    r0 = max(0, coord.row - window)
    r1 = min(rows, coord.row + window + 1)
    c0 = max(0, coord.col - window)
    c1 = min(cols, coord.col + window + 1)
    patch = grid_frame[r0:r1, c0:c1]          # (h, w, 3)
    mean_rgb = patch.reshape(-1, 3).mean(axis=0)
    r = int(round(float(mean_rgb[0])))
    g = int(round(float(mean_rgb[1])))
    b = int(round(float(mean_rgb[2])))
    return (r, g, b)


def build_fixture_grid_map(
    patch: FixturePatch,
    stage: Stage3D,
    cols: int = 12,
    rows: int = 8,
) -> dict[int, GridCoord]:
    """Map every fixture ID in the patch to a GridCoord.

    Priority for position data
    --------------------------
    1. Explicit ``x_m`` / ``z_m`` from the patch YAML (non-zero).
    2. Default position for the fixture's ``fixture_type``.
    3. Global fallback ``(5.0, 1.0)`` (stage centre, 1 m height).

    Parameters
    ----------
    patch:
        Loaded :class:`~mli_bridge.show.fixture_patch.FixturePatch`.
    stage:
        Stage physical dimensions.
    cols, rows:
        Grid dimensions.

    Returns
    -------
    dict[int, GridCoord]
        ``{fixture_id: GridCoord}`` for every fixture in the patch.
    """
    mapping: dict[int, GridCoord] = {}
    for fx in patch.fixtures:
        if fx.x_m != 0.0 or fx.z_m != 0.0:
            x_m, z_m = fx.x_m, fx.z_m
            source = "patch"
        else:
            x_m, z_m = _FIXTURE_TYPE_DEFAULTS.get(fx.fixture_type, _FALLBACK_XZ)
            source = f"default[{fx.fixture_type}]"

        coord = fixture_to_grid(x_m, z_m, stage, cols=cols, rows=rows)
        mapping[fx.id] = coord
        logger.debug(
            "Fixture {:>3} {:30s} → x={:.1f}m z={:.1f}m → grid[row={}, col={}]  ({})",
            fx.id, f"({fx.name})", x_m, z_m, coord.row, coord.col, source,
        )

    return mapping
