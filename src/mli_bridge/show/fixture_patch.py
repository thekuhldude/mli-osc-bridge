"""Fixture patch: load a YAML patch file and expose typed fixture data.

The patch file is the single source of truth for fixture IDs, groups,
and positions.  Everything downstream (group builder, preset builder,
event scheduler) reads from the FixturePatch object.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from loguru import logger


@dataclass
class FixtureInfo:
    """One fixture entry from the patch YAML."""
    id: int
    name: str
    fixture_type: str
    universe: int
    channel: int
    group: str
    position: str
    # Optional 3D stage position in metres (0,0,0 = unknown / use defaults)
    x_m: float = 0.0   # distance from stage left
    y_m: float = 0.0   # distance from stage front
    z_m: float = 0.0   # height from floor


@dataclass
class GroupInfo:
    """One group definition from the patch YAML.

    fixture_ids:
        Explicit list of fixture IDs that belong to this group.
        Populated from ``fixture_ids:`` in the YAML when present;
        otherwise derived from fixtures whose ``group:`` field matches
        this group's name (backward-compatible fallback).
    ma3_group_id:
        The MA3 group number used in ``Store Group <n>``.
        Defaults to ``executor`` when not specified in the YAML.
    """
    name: str
    label: str
    executor: int
    fixture_ids: list[int] = field(default_factory=list)
    ma3_group_id: int = 0   # set to executor in load_patch when absent


@dataclass
class ColorPreset:
    """One colour preset entry."""
    name: str
    rgb: tuple[int, int, int]


@dataclass
class FixturePatch:
    """Loaded and validated fixture patch."""
    fixtures: list[FixtureInfo] = field(default_factory=list)
    groups: list[GroupInfo] = field(default_factory=list)
    color_presets: list[ColorPreset] = field(default_factory=list)
    # Stage physical dimensions from the YAML ``stage:`` section (metres).
    # Keys: width_m, depth_m, height_m.  Empty dict → use geometry defaults.
    stage_config: dict[str, float] = field(default_factory=dict)

    # Fast lookups
    _by_id: dict[int, FixtureInfo] = field(default_factory=dict, repr=False)
    _by_group: dict[str, list[FixtureInfo]] = field(default_factory=dict, repr=False)
    _group_info: dict[str, GroupInfo] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._build_indices()

    def _build_indices(self) -> None:
        self._by_id = {f.id: f for f in self.fixtures}
        self._by_group = {}
        for f in self.fixtures:
            self._by_group.setdefault(f.group, []).append(f)
        self._group_info = {g.name: g for g in self.groups}

    def fixture(self, fid: int) -> FixtureInfo | None:
        return self._by_id.get(fid)

    def fixtures_in_group(self, group: str) -> list[FixtureInfo]:
        return self._by_group.get(group, [])

    def group(self, name: str) -> GroupInfo | None:
        return self._group_info.get(name)

    @property
    def group_names(self) -> list[str]:
        return list(self._group_info.keys())


def load_patch(path: Path) -> FixturePatch:
    """Parse a YAML patch file and return a :class:`FixturePatch`.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    KeyError / TypeError
        If the YAML is missing required fields.
    """
    path = Path(path)
    logger.info("Loading fixture patch from {}", path)

    with path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh)

    fixtures = [
        FixtureInfo(
            id=int(f["id"]),
            name=str(f["name"]),
            fixture_type=str(f.get("fixture_type", "Unknown")),
            universe=int(f.get("universe", 1)),
            channel=int(f.get("channel", 1)),
            group=str(f.get("group", "default")),
            position=str(f.get("position", "unknown")),
            x_m=float(f.get("x_m", 0.0)),
            y_m=float(f.get("y_m", 0.0)),
            z_m=float(f.get("z_m", 0.0)),
        )
        for f in raw.get("fixtures", [])
    ]

    # Build a name→[fixture_id] index from the fixtures list so we can
    # derive fixture_ids for groups that use the group: field on fixtures
    # (backward-compatible format).
    fixture_by_group: dict[str, list[int]] = {}
    for f in fixtures:
        fixture_by_group.setdefault(f.group, []).append(f.id)

    groups: list[GroupInfo] = []
    for g in raw.get("groups", []):
        name = str(g["name"])
        executor = int(g.get("executor", 1))
        ma3_group_id = int(g.get("ma3_group_id", executor))

        # Explicit fixture_ids in YAML take precedence; fall back to the
        # fixtures whose group: field matches this group's name.
        if "fixture_ids" in g:
            fixture_ids = [int(fid) for fid in g["fixture_ids"]]
        else:
            fixture_ids = fixture_by_group.get(name, [])

        groups.append(GroupInfo(
            name=name,
            label=str(g.get("label", name)),
            executor=executor,
            fixture_ids=fixture_ids,
            ma3_group_id=ma3_group_id,
        ))

    presets: list[ColorPreset] = []
    for cp in raw.get("presets", {}).get("color", []):
        rgb = tuple(cp["rgb"])  # type: ignore[assignment]
        presets.append(ColorPreset(name=str(cp["name"]), rgb=rgb))  # type: ignore[arg-type]

    # Stage dimensions (optional section)
    stage_raw = raw.get("stage", {})
    stage_config: dict[str, float] = {}
    for key in ("width_m", "depth_m", "height_m"):
        if key in stage_raw:
            stage_config[key] = float(stage_raw[key])

    patch = FixturePatch(
        fixtures=fixtures,
        groups=groups,
        color_presets=presets,
        stage_config=stage_config,
    )
    logger.info(
        "Patch loaded: {} fixtures, {} groups, {} color presets",
        len(fixtures),
        len(groups),
        len(presets),
    )
    return patch
