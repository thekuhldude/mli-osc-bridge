"""Grid-to-MA3 mapper: convert grid video to grandMA3 cue show.

Public API
----------
GridData, load_grid, extract_keyframes  →  mapper.grid_loader
Stage3D, GridCoord, fixture_to_grid     →  mapper.geometry
MA3Cue, CueBuilder                      →  mapper.cue_builder
ShowWriter                              →  mapper.show_writer
GridToMA3Pipeline, PipelineResult       →  mapper.pipeline
"""
from mli_bridge.mapper.cue_builder import CueBuilder, MA3Cue
from mli_bridge.mapper.geometry import (
    GridCoord,
    Stage3D,
    build_fixture_grid_map,
    fixture_to_grid,
    get_grid_value,
)
from mli_bridge.mapper.grid_loader import GridData, extract_keyframes, load_grid
from mli_bridge.mapper.pipeline import GridToMA3Pipeline, PipelineResult
from mli_bridge.mapper.show_writer import ShowWriter

__all__ = [
    "GridData",
    "load_grid",
    "extract_keyframes",
    "Stage3D",
    "GridCoord",
    "fixture_to_grid",
    "get_grid_value",
    "build_fixture_grid_map",
    "MA3Cue",
    "CueBuilder",
    "ShowWriter",
    "GridToMA3Pipeline",
    "PipelineResult",
]
