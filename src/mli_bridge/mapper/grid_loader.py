"""GridData loader and keyframe extractor for the Grid-to-MA3 mapper.

The grid video format produced by MLI-Rulegen is a ``.npz`` file with:
  - ``"grid"`` key: shape ``(n_frames, rows, cols, 3)`` uint8 RGB values
  - ``"fps"`` key (optional): frames per second (default 30)

Keyframe extraction uses frame-to-frame mean absolute pixel difference to
identify frames where the grid changes significantly enough to warrant a
new MA3 cue.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from loguru import logger


@dataclass
class GridData:
    """RGB grid video loaded from an .npz file.

    Attributes
    ----------
    frames:
        Shape ``(n_frames, rows, cols, 3)`` uint8.  Values 0–255.
    fps:
        Frame rate used during generation (from .npz or default 30).
    """

    frames: np.ndarray   # (n_frames, rows, cols, 3) uint8
    fps: float

    @property
    def n_frames(self) -> int:
        return int(self.frames.shape[0])

    @property
    def rows(self) -> int:
        return int(self.frames.shape[1])

    @property
    def cols(self) -> int:
        return int(self.frames.shape[2])

    @property
    def duration_s(self) -> float:
        return self.n_frames / self.fps


def load_grid(npz_path: Path) -> GridData:
    """Load an .npz grid video file produced by MLI-Rulegen.

    Parameters
    ----------
    npz_path:
        Path to a ``.npz`` file containing a ``"grid"`` key.

    Returns
    -------
    GridData

    Raises
    ------
    FileNotFoundError
        If *npz_path* does not exist.
    KeyError
        If the ``"grid"`` key is missing.
    ValueError
        If the array shape is not ``(n_frames, rows, cols, 3)``.
    """
    npz_path = Path(npz_path)
    logger.info("Loading grid from {}", npz_path)

    data = np.load(npz_path)
    if "grid" not in data:
        raise KeyError(
            f"'grid' key not found in {npz_path}.  "
            f"Available keys: {list(data.keys())}"
        )

    frames = data["grid"]
    if frames.ndim != 4 or frames.shape[-1] != 3:
        raise ValueError(
            f"Expected shape (n_frames, rows, cols, 3), got {frames.shape}"
        )

    fps = float(data["fps"]) if "fps" in data else 30.0

    logger.info(
        "Grid loaded: {} frames @ {:.1f} fps  ({} rows × {} cols)",
        frames.shape[0], fps, frames.shape[1], frames.shape[2],
    )
    return GridData(frames=frames.astype(np.uint8), fps=fps)


def extract_keyframes(
    grid: GridData,
    min_change_threshold: float = 10.0,
    max_cues_per_minute: float = 60.0,
) -> list[int]:
    """Extract frame indices where the grid changes significantly.

    Algorithm
    ---------
    1. Compute mean absolute pixel difference between each consecutive pair.
    2. Mark as keyframe when diff ≥ *min_change_threshold*.
    3. Enforce a minimum gap derived from *max_cues_per_minute*.
    4. Frame 0 is always included.

    Parameters
    ----------
    grid:
        Loaded :class:`GridData`.
    min_change_threshold:
        Minimum mean absolute pixel change (0–255) to mark a keyframe.
        Lower → more keyframes; higher → fewer.
    max_cues_per_minute:
        Soft cap on cue density.  Enforces a minimum inter-keyframe gap
        of ``fps * 60 / max_cues_per_minute`` frames.

    Returns
    -------
    list[int]
        Sorted list of keyframe frame indices.  Always starts with 0.
    """
    min_gap = max(1, int(grid.fps * 60.0 / max(1.0, max_cues_per_minute)))

    keyframes: list[int] = [0]
    last_keyframe = 0

    for i in range(1, grid.n_frames):
        if i - last_keyframe < min_gap:
            continue
        diff = float(np.mean(np.abs(
            grid.frames[i].astype(np.float32) - grid.frames[i - 1].astype(np.float32)
        )))
        if diff >= min_change_threshold:
            keyframes.append(i)
            last_keyframe = i

    logger.info(
        "Keyframe extraction: {} keyframes / {} frames "
        "(threshold={:.1f}, max_cpm={:.0f}, min_gap={}f)",
        len(keyframes), grid.n_frames,
        min_change_threshold, max_cues_per_minute, min_gap,
    )
    return keyframes
