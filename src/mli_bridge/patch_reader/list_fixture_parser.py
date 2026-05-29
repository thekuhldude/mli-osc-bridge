"""Parse the text output of the MA3 "List Fixture" command.

Workflow
--------
1. In MA3, open the System Monitor and run::

       List Fixture

2. Select the full output text, paste it into a ``.txt`` file.
3. Pass the file to ``mli-bridge read-patch-file fixtures.txt``.

Output format
-------------
The MA3 System Monitor prints a fixed-width table.  The first row that
contains *POSX*, *POSY*, and *POSZ* is the **header line**; it tells us
the exact character column where each field starts.  Every subsequent
non-blank row is a fixture data row.

Typical header (single line, uppercase, columns separated by varying
numbers of spaces)::

    LOCK  NO  SCRIBBLE  NOTE  TAGS  APPEARANCE  ...  NAME  FIXTURETYPE  MODE  ...  POSX     POSY     POSZ  ...

Typical data rows::

         1  RGB_G1  ...  1.000    2.000    0.500  ...
         2  RGB_G1  ...  3.000    2.000    0.500  ...

The *character position* of "POSX" in the header line is used to slice the
same character range from every data row — this is robust to varying numbers
of columns between files.

Fallback
--------
When no header line is found the parser falls back to a regex scan that
looks for lines starting with an integer followed by a name token and
ending with three decimal numbers (no units).
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from mli_bridge.patch_reader.ma3_patch_reader import (
    _group_by_height,
    _type_from_name,
)


# ---------------------------------------------------------------------------
# Column-extraction helpers
# ---------------------------------------------------------------------------

def _field_at(line: str, start: int, end: int) -> str:
    """Return the first whitespace-delimited token in ``line[start:end]``."""
    chunk = line[start:end] if end > start else line[start:]
    tokens = chunk.split()
    return tokens[0] if tokens else ""


def _find_header(lines: list[str]) -> tuple[str, int] | tuple[None, None]:
    """Return ``(header_line, line_index)`` or ``(None, None)``."""
    for i, line in enumerate(lines):
        upper = line.upper()
        if "POSX" in upper and "POSY" in upper and "POSZ" in upper:
            return line, i
    return None, None


def _col_positions(header: str) -> dict[str, int]:
    """Map uppercase column names to their start character position."""
    upper = header.upper()
    cols: dict[str, int] = {}
    for name in ("NO", "NAME", "FIXTURETYPE", "POSX", "POSY", "POSZ"):
        pos = upper.find(name)
        if pos >= 0:
            cols[name] = pos
    return cols


def _next_col(cols: dict[str, int], key: str) -> int:
    """Return the start of the column that immediately follows *key*."""
    start = cols[key]
    following = [v for v in cols.values() if v > start]
    return min(following) if following else start + 20


# ---------------------------------------------------------------------------
# Primary parser (header-guided, column-position–based)
# ---------------------------------------------------------------------------

def _parse_with_header(
    lines: list[str],
    header: str,
    header_idx: int,
) -> list[dict]:
    cols = _col_positions(header)

    # Require at minimum the position columns
    required = {"POSX", "POSY", "POSZ"}
    if not required.issubset(cols):
        logger.warning(
            "Header found but missing columns {}; falling back.",
            required - cols.keys(),
        )
        return []

    # Column end-positions (start of the next column)
    posx_end = _next_col(cols, "POSX")
    posy_end = _next_col(cols, "POSY")
    posz_end = _next_col(cols, "POSZ")
    name_end = _next_col(cols, "NAME") if "NAME" in cols else cols.get("FIXTURETYPE", cols["POSX"])
    no_end   = _next_col(cols, "NO")   if "NO"   in cols else cols.get("NAME", 10)

    fixtures: list[dict] = []
    skipped = 0

    for raw in lines[header_idx + 1:]:
        line = raw.rstrip()
        if not line.strip():
            continue
        # Skip separator lines (all dashes/equals) and re-printed headers
        stripped = line.strip()
        if not stripped or set(stripped) <= {"-", "=", " "}:
            continue
        if "POSX" in line.upper() and "POSY" in line.upper():
            continue   # repeated header

        # --- fixture ID ---
        no_token = _field_at(line, cols.get("NO", 0), no_end)
        try:
            fid = int(no_token)
        except ValueError:
            # Try any leading integer on the line
            m = re.match(r"\s*(\d+)", line)
            if not m:
                skipped += 1
                continue
            fid = int(m.group(1))

        # --- positions ---
        try:
            x_m = float(_field_at(line, cols["POSX"], posx_end))
            y_m = float(_field_at(line, cols["POSY"], posy_end))
            z_m = float(_field_at(line, cols["POSZ"], posz_end))
        except (ValueError, IndexError):
            skipped += 1
            logger.debug("Row id={}: cannot parse POSX/POSY/POSZ → skipped", fid)
            continue

        # --- name ---
        if "NAME" in cols:
            name = _field_at(line, cols["NAME"], name_end) or f"Fixture_{fid}"
        else:
            name = f"Fixture_{fid}"

        fixtures.append({
            "id":           fid,
            "name":         name,
            "x_m":          x_m,
            "y_m":          y_m,
            "z_m":          z_m,
            "fixture_type": _type_from_name(name),
        })

    if skipped:
        logger.debug("{} data rows skipped (no parseable ID or position).", skipped)

    return sorted(fixtures, key=lambda f: f["id"])


# ---------------------------------------------------------------------------
# Fallback parser (no header, regex-based)
# ---------------------------------------------------------------------------

# Matches a line that starts with optional whitespace + an integer (fixture
# ID), contains a word token for the name, and has three bare decimal numbers
# somewhere in the line (x, y, z — no "m" suffix).
_FALLBACK_RE = re.compile(
    r"^\s*(\d+)\s+(\S+).*?"      # id, name
    r"(-?\d+\.\d+)\s+"           # x
    r"(-?\d+\.\d+)\s+"           # y
    r"(-?\d+\.\d+)"              # z
)


def _parse_without_header(lines: list[str]) -> list[dict]:
    """Regex-based fallback when no POSX/POSY/POSZ header is present."""
    fixtures: list[dict] = []
    for line in lines:
        m = _FALLBACK_RE.search(line)
        if not m:
            continue
        fid  = int(m.group(1))
        name = m.group(2)
        x_m  = float(m.group(3))
        y_m  = float(m.group(4))
        z_m  = float(m.group(5))
        fixtures.append({
            "id":           fid,
            "name":         name,
            "x_m":          x_m,
            "y_m":          y_m,
            "z_m":          z_m,
            "fixture_type": _type_from_name(name),
        })
    return sorted(fixtures, key=lambda f: f["id"])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_list_fixture_output(text: str) -> list[dict]:
    """Parse the text output of MA3 ``List Fixture`` into fixture dicts.

    Supports two modes automatically:

    1. **Header-guided** (primary): finds the line containing *POSX*, *POSY*,
       *POSZ* and uses character-column positions to extract every field.
       Robust against fixtures with no position (they are silently skipped).

    2. **Regex fallback**: when no header line is found, scans for rows that
       look like ``<id>  <name>  …  <x>  <y>  <z>  …``.

    Parameters
    ----------
    text:
        Full text content of the ``List Fixture`` output file.

    Returns
    -------
    list[dict]
        Each dict has keys: ``id`` (int), ``name`` (str),
        ``x_m`` / ``y_m`` / ``z_m`` (float), ``fixture_type`` (str).
        Sorted ascending by ``id``.
    """
    lines = text.splitlines()
    header, header_idx = _find_header(lines)

    if header is not None:
        logger.info("Header line found at row {} — using column-position parser.", header_idx)
        fixtures = _parse_with_header(lines, header, header_idx)
        if fixtures:
            logger.info("Parsed {} fixtures (header-guided).", len(fixtures))
            return fixtures
        # If the header-guided parse returned nothing, try fallback
        logger.warning("Header-guided parse returned no fixtures; trying regex fallback.")

    logger.info("No POSX/POSY/POSZ header found — using regex fallback parser.")
    fixtures = _parse_without_header(lines)
    logger.info("Parsed {} fixtures (regex fallback).", len(fixtures))
    return fixtures


# ---------------------------------------------------------------------------
# YAML generation (mirrors ma3_patch_reader logic, no TCP dependency)
# ---------------------------------------------------------------------------

def generate_patch_yaml(
    fixtures: list[dict],
    output_path: Path,
    source: str = "List Fixture text file",
) -> dict[str, Any]:
    """Build a patch YAML from *fixtures* and write it to *output_path*.

    Stage dimensions are inferred from the bounding box of all fixture
    positions with a small safety margin.  Fixtures are grouped into
    height bands (floor / mid / truss).

    Parameters
    ----------
    fixtures:
        Output of :func:`parse_list_fixture_output`.
    output_path:
        Destination YAML path (created or overwritten).
    source:
        String used in the file comment header.

    Returns
    -------
    dict
        The data structure written to the file.

    Raises
    ------
    ValueError
        If *fixtures* is empty.
    """
    if not fixtures:
        raise ValueError("No fixtures to generate patch from.")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    width_m  = max(f["x_m"] for f in fixtures) + 1.0
    depth_m  = max(f["y_m"] for f in fixtures) + 1.0
    height_m = max(f["z_m"] for f in fixtures) + 0.5

    groups_data: list[dict] = []
    for group_id, (name, label, fixture_type, members) in enumerate(
        _group_by_height(fixtures), start=1
    ):
        groups_data.append({
            "name":         name,
            "label":        label,
            "fixture_type": fixture_type,
            "ma3_group_id": group_id,
            "fixture_ids":  [m["id"] for m in members],
            "fixtures": [
                {
                    "id":  m["id"],
                    "x_m": round(m["x_m"], 3),
                    "y_m": round(m["y_m"], 3),
                    "z_m": round(m["z_m"], 3),
                }
                for m in members
            ],
        })

    data: dict[str, Any] = {
        "stage": {
            "width_m":  round(width_m,  2),
            "depth_m":  round(depth_m,  2),
            "height_m": round(height_m, 2),
        },
        "groups": groups_data,
        "presets": {
            "color": [
                {"name": "Red",       "rgb": [255,   0,   0]},
                {"name": "Green",     "rgb": [  0, 255,   0]},
                {"name": "Blue",      "rgb": [  0,   0, 255]},
                {"name": "White",     "rgb": [255, 255, 255]},
                {"name": "Amber",     "rgb": [255, 191,   0]},
                {"name": "Deep Blue", "rgb": [  0,   0, 180]},
            ],
        },
    }

    with output_path.open("w", encoding="utf-8") as fh:
        fh.write(
            "# Fixture patch auto-generated by mli-bridge read-patch-file\n"
            f"# Source   : {source}\n"
            f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"# Fixtures : {len(fixtures)}   Groups: {len(groups_data)}\n\n"
        )
        yaml.dump(
            data, fh,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

    logger.info(
        "Wrote {}  ({} fixtures, {} groups)",
        output_path, len(fixtures), len(groups_data),
    )
    return data
