"""MA3PatchReader: read fixture patch directly from grandMA3 terminal.

grandMA3 exposes a TCP command-line terminal on port 30000.
Protocol:
    1. Open TCP connection to host:30000
    2. Wait for initial banner / ">" prompt
    3. Send  ``login Admin \\r\\n``   (Admin has no password by default)
    4. Wait for ">" prompt
    5. Send  ``List Fixture \\r\\n``
    6. Read until next ">" prompt
    7. Parse the tabular output

Typical ``List Fixture`` output
--------------------------------
::

    Id  Name          Type        Univ  Addr   X         Y         Z
     1  RGB_G1        Generic      1     1     1.000m    2.000m    0.500m
     2  RGB_G2        Generic      1     4     3.000m    2.000m    0.500m
     3  Spot_L        Generic      1     7     2.500m    3.000m    4.200m

The parser accepts any column order as long as X/Y/Z appear at the end
of each data line with an ``m`` suffix.

Group assignment
----------------
``generate_patch_yaml`` assigns fixtures to height bands:

==========  ============  ==============
z range     group name    fixture_type
==========  ============  ==============
z < 1 m     floor         par
1 ≤ z < 4   mid           spot
z ≥ 4 m     truss         wash
==========  ============  ==============
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import yaml
from loguru import logger


# ---------------------------------------------------------------------------
# Pure helper functions (no I/O — easily unit-tested)
# ---------------------------------------------------------------------------

def _type_from_name(name: str) -> str:
    """Derive ``fixture_type`` from a fixture name via substring match.

    Checks (in order): ``wash``, ``beam``, ``spot``, ``par``.
    Falls back to ``"par"`` when none match.
    """
    lower = name.lower()
    for keyword in ("wash", "beam", "spot", "par"):
        if keyword in lower:
            return keyword
    return "par"


def _parse_fixture_table(output: str) -> list[dict]:
    """Parse the text from ``List Fixture`` into a list of fixture dicts.

    Extracts every line that ends with three ``<number>m`` values (X, Y, Z)
    and starts with an integer fixture ID followed by a name token.

    Parameters
    ----------
    output:
        Raw text received from the MA3 terminal.

    Returns
    -------
    list[dict]
        Each dict has keys: ``id``, ``name``, ``x_m``, ``y_m``, ``z_m``,
        ``fixture_type``.
    """
    # Match the three trailing position columns
    xyz_re = re.compile(
        r'(\d+\.?\d*)\s*m\s+(\d+\.?\d*)\s*m\s+(\d+\.?\d*)\s*m\s*$'
    )
    # Fixture ID + name at the start of each line
    id_re = re.compile(r'^\s*(\d+)\s+(\S+)')

    fixtures: list[dict] = []
    for line in output.splitlines():
        xyz_m = xyz_re.search(line)
        if not xyz_m:
            continue
        id_m = id_re.match(line)
        if not id_m:
            continue

        fid  = int(id_m.group(1))
        name = id_m.group(2)
        x_m  = float(xyz_m.group(1))
        y_m  = float(xyz_m.group(2))
        z_m  = float(xyz_m.group(3))

        fixtures.append({
            "id":           fid,
            "name":         name,
            "x_m":          x_m,
            "y_m":          y_m,
            "z_m":          z_m,
            "fixture_type": _type_from_name(name),
        })

    return sorted(fixtures, key=lambda f: f["id"])


def _group_by_height(
    fixtures: list[dict],
) -> list[tuple[str, str, str, list[dict]]]:
    """Assign fixtures to named height bands.

    Returns a list of ``(name, label, fixture_type, members)`` tuples
    for every non-empty band, ordered floor → mid → truss.

    Bands
    -----
    * z < 1 m    → ``"floor"`` / ``"Floor"`` / ``"par"``
    * 1 ≤ z < 4  → ``"mid"``   / ``"Mid"``   / ``"spot"``
    * z ≥ 4 m    → ``"truss"`` / ``"Truss"`` / ``"wash"``
    """
    bands: dict[str, dict[str, Any]] = {
        "floor": {"label": "Floor", "fixture_type": "par",  "members": []},
        "mid":   {"label": "Mid",   "fixture_type": "spot", "members": []},
        "truss": {"label": "Truss", "fixture_type": "wash", "members": []},
    }
    for fx in sorted(fixtures, key=lambda f: f["id"]):
        z = fx["z_m"]
        if z < 1.0:
            bands["floor"]["members"].append(fx)
        elif z < 4.0:
            bands["mid"]["members"].append(fx)
        else:
            bands["truss"]["members"].append(fx)

    return [
        (name, b["label"], b["fixture_type"], b["members"])
        for name, b in bands.items()
        if b["members"]
    ]


def _group_by_z_proximity(
    fixtures: list[dict],
    tolerance: float = 0.5,
) -> list[list[dict]]:
    """Group fixtures whose Z values are within *tolerance* of each other.

    Single-pass algorithm: sort by Z, start a new group whenever the gap
    to the last fixture in the current group exceeds *tolerance*.

    Parameters
    ----------
    fixtures:
        Fixture dicts (each must have a ``z_m`` key).
    tolerance:
        Maximum Z difference (metres) within one group.

    Returns
    -------
    list[list[dict]]
        Nested list — outer list is groups, inner list is member fixtures.
    """
    if not fixtures:
        return []

    sorted_fx = sorted(fixtures, key=lambda f: f["z_m"])
    groups: list[list[dict]] = [[sorted_fx[0]]]

    for fx in sorted_fx[1:]:
        if fx["z_m"] - groups[-1][-1]["z_m"] <= tolerance:
            groups[-1].append(fx)
        else:
            groups.append([fx])

    return groups


# ---------------------------------------------------------------------------
# MA3PatchReader
# ---------------------------------------------------------------------------

class MA3PatchReader:
    """Read the grandMA3 fixture patch via the TCP terminal interface.

    Parameters
    ----------
    host:
        IP address of the grandMA3 system.
    port:
        TCP port of the MA3 terminal (default 30000).
    login_timeout:
        Seconds to wait for the initial banner / login response.
    read_timeout:
        Seconds to wait for the full ``List Fixture`` output.
    """

    _PROMPT = b">"

    def __init__(
        self,
        host: str = "192.168.1.14",
        port: int = 30000,
        login_timeout: float = 5.0,
        read_timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self._login_timeout = login_timeout
        self._read_timeout = read_timeout

    # ----------------------------------------------------------------- helpers

    async def _read_until_prompt(
        self,
        reader: asyncio.StreamReader,
        timeout: float,
    ) -> str:
        """Accumulate bytes until the ``>`` prompt appears or *timeout* elapses."""
        buf = bytearray()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                chunk = await asyncio.wait_for(
                    reader.read(4096),
                    timeout=min(remaining, 1.0),
                )
            except asyncio.TimeoutError:
                break

            if not chunk:   # connection closed
                break

            buf.extend(chunk)
            if self._PROMPT in buf:
                break

        text = buf.decode("utf-8", errors="replace")
        logger.trace("← {} chars: {!r}", len(text), text[:120])
        return text

    # ------------------------------------------------------------------ public

    async def read_patch(self) -> list[dict]:
        """Connect to MA3, run ``List Fixture`` and return parsed fixtures.

        Returns
        -------
        list[dict]
            One dict per fixture with keys:
            ``id``, ``name``, ``x_m``, ``y_m``, ``z_m``, ``fixture_type``.

        Raises
        ------
        ConnectionRefusedError
            If the TCP connection is rejected.
        asyncio.TimeoutError
            If the terminal does not respond within the configured timeouts.
        """
        logger.info(
            "Connecting to MA3 terminal {}:{} …", self.host, self.port
        )
        reader, writer = await asyncio.open_connection(self.host, self.port)

        try:
            # Wait for initial banner / prompt
            banner = await self._read_until_prompt(reader, self._login_timeout)
            logger.debug("Banner: {!r}", banner[:80])

            # Login — Admin has no password by default
            logger.debug("→ login Admin")
            writer.write(b"login Admin \r\n")
            await writer.drain()
            await self._read_until_prompt(reader, self._login_timeout)

            # Request fixture list
            logger.debug("→ List Fixture")
            writer.write(b"List Fixture \r\n")
            await writer.drain()
            output = await self._read_until_prompt(reader, self._read_timeout)

        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

        fixtures = _parse_fixture_table(output)
        logger.info(
            "MA3 terminal returned {} fixtures from {} chars of output.",
            len(fixtures), len(output),
        )
        return fixtures

    async def generate_patch_yaml(self, output_path: Path) -> dict[str, Any]:
        """Read the MA3 patch, build a patch YAML and write it to *output_path*.

        Stage dimensions are inferred from the fixture bounding box plus
        a small margin so no fixture sits exactly on the grid edge:

        * ``width_m``  = max(x) + 1.0 m
        * ``depth_m``  = max(y) + 1.0 m
        * ``height_m`` = max(z) + 0.5 m

        Fixtures are placed into height-band groups (floor / mid / truss)
        and MA3 group IDs are assigned sequentially starting at 1.

        Parameters
        ----------
        output_path:
            File path to write the YAML (created or overwritten).

        Returns
        -------
        dict
            The data structure that was written, for immediate inspection.

        Raises
        ------
        RuntimeError
            If no fixtures were returned by MA3.
        """
        fixtures = await self.read_patch()
        if not fixtures:
            raise RuntimeError(
                f"No fixtures returned from {self.host}:{self.port}.  "
                "Check that MA3 is running and the terminal is accessible."
            )

        # ---- stage bounding box ----
        width_m  = max(f["x_m"] for f in fixtures) + 1.0
        depth_m  = max(f["y_m"] for f in fixtures) + 1.0
        height_m = max(f["z_m"] for f in fixtures) + 0.5

        # ---- height-band groups ----
        height_groups = _group_by_height(fixtures)
        groups_data: list[dict] = []
        for group_id, (name, label, fixture_type, members) in enumerate(
            height_groups, start=1
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

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with output_path.open("w", encoding="utf-8") as fh:
            n_fx = len(fixtures)
            n_grp = len(groups_data)
            fh.write(
                "# Fixture patch auto-generated by mli-bridge read-patch\n"
                f"# Source : MA3 terminal at {self.host}:{self.port}\n"
                f"# Fixtures: {n_fx}   Groups: {n_grp}\n\n"
            )
            yaml.dump(
                data,
                fh,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

        logger.info(
            "Wrote {}  ({} fixtures, {} groups)",
            output_path, len(fixtures), len(groups_data),
        )
        return data
