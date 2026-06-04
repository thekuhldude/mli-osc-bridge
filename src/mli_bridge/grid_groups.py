"""Stage grid: cluster fixtures into a 3-row × 4-column layout and derive named groups.

The physical stage is modelled as a 3 × 4 grid:

    row 0 = Truss   (highest z — ceiling / top of rig)
    row 1 = Mid     (intermediate height)
    row 2 = Floor   (lowest z — floor level)

    col 0 = far-left   →   col 3 = far-right  (ordered by x_m ascending)

Named groups derived from the grid
-----------------------------------
    all   — every fixture
    truss — row 0
    mid   — row 1
    floor — row 2
    left  — columns 0–1 (all rows)
    right — columns 2–3 (all rows)

Within each group the fixtures are in **column order** (left → right) so
that chase / phase effects run correctly from stage-left to stage-right.
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Grid construction
# ---------------------------------------------------------------------------

def build_grid_from_fixtures(
    fixtures: list[dict[str, Any]],
    n_rows: int = 3,
) -> dict[int, dict[int, int]]:
    """Cluster fixtures into a ``n_rows × n_cols`` height grid.

    Algorithm
    ---------
    1. Sort all fixtures by ``z_m`` ascending.
    2. Split into *n_rows* equal-sized height bands (bottom → top).
    3. Invert so that **row 0 = highest z** (Truss) and
       **row n_rows-1 = lowest z** (Floor).
    4. Within each row, sort by ``x_m`` ascending to assign columns 0 … n_cols-1.

    Parameters
    ----------
    fixtures:
        List of dicts, each with at least ``{"id": int, "x_m": float, "z_m": float}``.
        ``y_m`` and ``name`` are ignored.
    n_rows:
        Number of height bands (default 3 for truss / mid / floor).

    Returns
    -------
    dict[int, dict[int, int]]
        ``grid[row][col] = fixture_id``
    """
    if not fixtures:
        return {}

    n = len(fixtures)
    per_row = n // n_rows     # fixtures per height band
    remainder = n % n_rows

    # Sort by height (ascending: lowest first)
    by_z = sorted(fixtures, key=lambda f: float(f.get("z_m", 0.0)))

    grid: dict[int, dict[int, int]] = {}
    offset = 0
    for band_idx in range(n_rows):
        # distribute any remainder fixtures into the lowest band(s)
        count = per_row + (1 if band_idx < remainder else 0)
        band  = by_z[offset: offset + count]
        offset += count

        # row 0 = highest z  →  invert the band index
        row = (n_rows - 1) - band_idx

        # sort within band by x_m for column assignment
        by_x = sorted(band, key=lambda f: float(f.get("x_m", 0.0)))
        grid[row] = {col: int(f["id"]) for col, f in enumerate(by_x)}

    return grid


# ---------------------------------------------------------------------------
# Group derivation
# ---------------------------------------------------------------------------

def derive_groups(
    grid: dict[int, dict[int, int]],
    left_cols: int = 2,
) -> dict[str, list[int]]:
    """Return all named groups derived from *grid*.

    Parameters
    ----------
    grid:
        Output of :func:`build_grid_from_fixtures`.
    left_cols:
        How many columns are considered "left" (default 2 out of 4).

    Returns
    -------
    dict[str, list[int]]
        Mapping of group name → ordered list of fixture IDs
        (column order within each group, rows top-to-bottom).
    """
    rows = sorted(grid.keys())         # 0, 1, 2  (0 = truss)
    if not rows:
        return {}

    n_cols  = max(len(grid[r]) for r in rows)
    cols    = list(range(n_cols))
    l_cols  = cols[:left_cols]
    r_cols  = cols[left_cols:]

    def _ids(row_subset: list[int], col_subset: list[int]) -> list[int]:
        result = []
        for row in row_subset:
            for col in col_subset:
                if col in grid.get(row, {}):
                    result.append(grid[row][col])
        return result

    groups: dict[str, list[int]] = {
        "all":   _ids(rows, cols),
        "truss": _ids([rows[0]], cols)          if len(rows) >= 1 else [],
        "mid":   _ids([rows[1]], cols)          if len(rows) >= 2 else [],
        "floor": _ids([rows[-1]], cols),
        "left":  _ids(rows, l_cols),
        "right": _ids(rows, r_cols),
    }
    return groups


def resolve_group(group_name: str, grid: dict[int, dict[int, int]]) -> list[int]:
    """Return the ordered fixture-ID list for *group_name*.

    Order is column-major (left → right) within each row so that
    chase phasers run from stage-left to stage-right.

    Parameters
    ----------
    group_name:
        One of ``"all"``, ``"truss"``, ``"mid"``, ``"floor"``,
        ``"left"``, ``"right"``.
    grid:
        Output of :func:`build_grid_from_fixtures`.

    Raises
    ------
    ValueError
        If *group_name* is not recognised.
    """
    groups = derive_groups(grid)
    if group_name not in groups:
        raise ValueError(
            f"Unknown group {group_name!r}.  "
            f"Available groups: {sorted(groups.keys())}"
        )
    return groups[group_name]
