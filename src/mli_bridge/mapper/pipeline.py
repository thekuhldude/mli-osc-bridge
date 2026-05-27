"""GridToMA3Pipeline: full end-to-end workflow in one async call.

Pipeline steps
--------------
1. Load the grid video (``.npz`` from MLI-Rulegen).
2. Load the fixture patch (``.yaml``).
3. Build a :class:`~mli_bridge.mapper.geometry.Stage3D` from the patch's
   ``stage:`` section (or use defaults).
4. Extract keyframes.
5. Build :class:`~mli_bridge.mapper.cue_builder.MA3Cue` objects.
6. Write the show to grandMA3 via OSC (unless *dry_run*).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from mli_bridge.mapper.cue_builder import CueBuilder, MA3Cue
from mli_bridge.mapper.geometry import Stage3D
from mli_bridge.mapper.grid_loader import GridData, extract_keyframes, load_grid
from mli_bridge.mapper.show_writer import ShowWriter
from mli_bridge.osc.client import MA3OscClient
from mli_bridge.settings import BridgeSettings
from mli_bridge.show.fixture_patch import FixturePatch, load_patch


@dataclass
class PipelineResult:
    """Summary statistics returned after a pipeline run."""
    n_frames: int
    n_keyframes: int
    n_cues: int
    n_fixtures: int
    duration_s: float
    sequence_id: int


class GridToMA3Pipeline:
    """End-to-end pipeline: grid .npz → grandMA3 cue show.

    Parameters
    ----------
    client:
        Connected :class:`~mli_bridge.osc.client.MA3OscClient`.
    settings:
        Bridge settings.
    """

    def __init__(self, client: MA3OscClient, settings: BridgeSettings) -> None:
        self._client = client
        self._settings = settings

    async def run(
        self,
        grid_path: Path,
        patch_path: Path,
        sequence_id: int = 1,
        sequence_name: str = "MLI_Show",
        min_change_threshold: float = 10.0,
        max_cues_per_minute: float = 60.0,
        cols: int = 12,
        rows: int = 8,
        dry_run: bool = False,
    ) -> PipelineResult:
        """Execute the full pipeline.

        Parameters
        ----------
        grid_path:
            Path to ``.npz`` grid video.
        patch_path:
            Path to fixture patch YAML.
        sequence_id:
            MA3 sequence number to program.
        sequence_name:
            Label for the MA3 sequence.
        min_change_threshold:
            Minimum mean pixel diff to mark a keyframe (0–255).
        max_cues_per_minute:
            Maximum keyframe density (cap on cue count).
        cols, rows:
            Grid dimensions.  Must match the .npz shape.
        dry_run:
            If ``True``, skip the OSC write step (steps 1-5 still run).

        Returns
        -------
        PipelineResult
        """
        # 1. Grid
        grid: GridData = load_grid(grid_path)

        # 2. Patch
        patch: FixturePatch = load_patch(patch_path)

        # 3. Stage
        stage: Stage3D = _stage_from_patch(patch)
        logger.info(
            "Stage: {:.1f} m wide × {:.1f} m tall",
            stage.width_m, stage.height_m,
        )

        # 4. Keyframes
        keyframes: list[int] = extract_keyframes(
            grid,
            min_change_threshold=min_change_threshold,
            max_cues_per_minute=max_cues_per_minute,
        )

        # 5. Cues
        builder = CueBuilder(patch, stage, cols=cols, rows=rows)
        cues: list[MA3Cue] = builder.build_all_cues(grid, keyframes)

        if not dry_run:
            # 6. Write show
            writer = ShowWriter(self._client, self._settings)
            await writer.write_show(
                cues,
                sequence_id=sequence_id,
                sequence_name=sequence_name,
            )
        else:
            logger.info("Dry run — OSC write skipped.")

        return PipelineResult(
            n_frames=grid.n_frames,
            n_keyframes=len(keyframes),
            n_cues=len(cues),
            n_fixtures=len(patch.fixtures),
            duration_s=grid.duration_s,
            sequence_id=sequence_id,
        )


# ---------------------------------------------------------------------------
# helpers

def _stage_from_patch(patch: FixturePatch) -> Stage3D:
    """Construct a :class:`Stage3D` from ``patch.stage_config`` (with defaults)."""
    cfg = patch.stage_config
    return Stage3D(
        width_m=float(cfg.get("width_m", 10.0)),
        depth_m=float(cfg.get("depth_m", 8.0)),
        height_m=float(cfg.get("height_m", 4.0)),
    )
