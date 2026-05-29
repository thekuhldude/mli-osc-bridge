"""GridData loader and keyframe extractor for the Grid-to-MA3 mapper.

The grid video format produced by MLI-Rulegen is a ``.npz`` file with:
  - ``"grid"``            (n_frames, rows, cols, 3) uint8 — required
  - ``"fps"``             float — frame rate (default 30)
  - ``"duration_s"``      float — track duration
  - ``"beat_frames"``     int32 array — beat positions in frames
  - ``"onset_strength"``  float32 (n_frames,) — onset envelope 0-1
  - ``"segment_frames"``  int32 (n_segs, 3) — (start, end, id) per segment
  - ``"rms"``             float32 (n_frames,) — loudness curve 0-1

The audio-feature arrays are optional (older .npz without them still
loads fine).  When present, ``extract_keyframes`` uses them to prefer
musically important moments: segment boundaries are always keyframes,
beats and onsets boost the importance score.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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
        Frame rate used during generation.
    beat_frames:
        Optional beat positions (frame indices) from MLI-Rulegen.
    onset_strength:
        Optional onset envelope, shape ``(n_frames,)``, values 0–1.
    segment_frames:
        Optional structural-segment array, shape ``(n_segs, 3)``:
        each row is ``(start_frame, end_frame, seg_id)``.
    rms:
        Optional RMS loudness curve, shape ``(n_frames,)``, values 0–1.
    """

    frames: np.ndarray                                  # (n_frames, rows, cols, 3) uint8
    fps: float
    beat_frames: Optional[np.ndarray] = field(default=None)    # int32 (n_beats,)
    onset_strength: Optional[np.ndarray] = field(default=None) # float32 (n_frames,)
    segment_frames: Optional[np.ndarray] = field(default=None) # int32 (n_segs, 3)
    rms: Optional[np.ndarray] = field(default=None)            # float32 (n_frames,)

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

    # Optional audio-feature arrays
    beat_frames     = data["beat_frames"].astype(np.int32)    if "beat_frames"     in data else None
    onset_strength  = data["onset_strength"].astype(np.float32) if "onset_strength" in data else None
    segment_frames  = data["segment_frames"].astype(np.int32) if "segment_frames"  in data else None
    rms             = data["rms"].astype(np.float32)          if "rms"             in data else None

    n_audio = sum(x is not None for x in [beat_frames, onset_strength, segment_frames, rms])
    logger.info(
        "Grid loaded: {} frames @ {:.1f} fps  ({} rows × {} cols)  "
        "[{} audio-feature arrays]",
        frames.shape[0], fps, frames.shape[1], frames.shape[2], n_audio,
    )
    return GridData(
        frames=frames.astype(np.uint8),
        fps=fps,
        beat_frames=beat_frames,
        onset_strength=onset_strength,
        segment_frames=segment_frames,
        rms=rms,
    )


def extract_keyframes(
    grid: GridData,
    min_change_threshold: float = 10.0,
    max_cues_per_minute: float = 60.0,
) -> list[int]:
    """Extract keyframe indices, preferring musically important moments.

    Algorithm
    ---------
    1. Compute mean absolute pixel difference between consecutive frames
       as a base importance score.
    2. If the .npz contains audio features:
       * Beat frames receive a ×1.5 importance boost.
       * Per-frame onset strength adds +30% modulation.
       * Segment boundaries are **always** included regardless of threshold.
    3. Enforce a minimum gap from *max_cues_per_minute*.
    4. Sort, deduplicate, always include frame 0.

    Parameters
    ----------
    grid:
        Loaded :class:`GridData`.  Audio-feature arrays are used when present.
    min_change_threshold:
        Minimum importance score to mark a keyframe (0–255 scale).
        Lower → more keyframes; higher → fewer.
    max_cues_per_minute:
        Cap on cue density; enforces a minimum inter-keyframe gap.

    Returns
    -------
    list[int]
        Sorted, deduplicated keyframe frame indices.  Always starts with 0.
    """
    min_gap = max(1, int(grid.fps * 60.0 / max(1.0, max_cues_per_minute)))

    # ---- step 1: per-frame pixel-diff importance ----
    importance = np.zeros(grid.n_frames, dtype=np.float32)
    for i in range(1, grid.n_frames):
        importance[i] = float(np.mean(np.abs(
            grid.frames[i].astype(np.float32) - grid.frames[i - 1].astype(np.float32)
        )))

    # ---- step 2: musical boosts ----
    forced_frames: list[int] = []   # segment boundaries always win

    has_audio = (
        grid.beat_frames is not None
        or grid.onset_strength is not None
        or grid.segment_frames is not None
    )

    if has_audio:
        # Beat-frame boost: ×1.5
        if grid.beat_frames is not None:
            for bf in grid.beat_frames:
                if 0 <= int(bf) < grid.n_frames:
                    importance[int(bf)] *= 1.5

        # Onset modulation: +30% of normalised onset strength
        if grid.onset_strength is not None:
            importance *= (1.0 + 0.30 * grid.onset_strength)

        # Segment boundaries: always include
        if grid.segment_frames is not None and len(grid.segment_frames) > 0:
            for row in grid.segment_frames:
                start_frame = int(row[0])
                if 0 <= start_frame < grid.n_frames:
                    forced_frames.append(start_frame)

    # ---- step 3: threshold + min-gap pass ----
    keyframes: set[int] = {0}
    keyframes.update(forced_frames)

    last = 0
    for i in range(1, grid.n_frames):
        if i - last < min_gap:
            continue
        if importance[i] >= min_change_threshold:
            keyframes.add(i)
            last = i

    result = sorted(keyframes)
    audio_note = " (with musical boosts)" if has_audio else ""
    logger.info(
        "Keyframe extraction{}: {} keyframes / {} frames "
        "(threshold={:.1f}, max_cpm={:.0f}, min_gap={}f, forced={})",
        audio_note, len(result), grid.n_frames,
        min_change_threshold, max_cues_per_minute, min_gap, len(forced_frames),
    )
    return result
