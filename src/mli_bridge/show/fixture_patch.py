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


@dataclass
class GroupInfo:
    """One group definition from the patch YAML."""
    name: str
    label: str
    executor: int


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
        )
        for f in raw.get("fixtures", [])
    ]

    groups = [
        GroupInfo(
            name=str(g["name"]),
            label=str(g.get("label", g["name"])),
            executor=int(g.get("executor", 1)),
        )
        for g in raw.get("groups", [])
    ]

    presets: list[ColorPreset] = []
    for cp in raw.get("presets", {}).get("color", []):
        rgb = tuple(cp["rgb"])  # type: ignore[assignment]
        presets.append(ColorPreset(name=str(cp["name"]), rgb=rgb))  # type: ignore[arg-type]

    patch = FixturePatch(fixtures=fixtures, groups=groups, color_presets=presets)
    logger.info(
        "Patch loaded: {} fixtures, {} groups, {} color presets",
        len(fixtures),
        len(groups),
        len(presets),
    )
    return patch
