"""MA3Cue dataclass and CueBuilder: convert grid frames into MA3 cue data.

Each cue stores per-fixture RGB colour and brightness derived from the
grid frame at the fixture's mapped grid coordinate.

Brightness is computed from the ITU-R BT.709 luminance formula:
``lum = 0.2126·R + 0.7152·G + 0.0722·B`` (normalised 0–1).
A configurable floor prevents fixtures from going completely dark.

Fade duration
-------------
Each cue's fade covers 80 % of the gap to the next cue, clamped to
0.1 – 2.0 seconds.  The last cue always gets a 1 s fade.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from loguru import logger

from mli_bridge.mapper.geometry import GridCoord, Stage3D, build_fixture_grid_map, get_grid_value
from mli_bridge.mapper.grid_loader import GridData
from mli_bridge.show.fixture_patch import FixturePatch


@dataclass
class MA3Cue:
    """One MA3 cue built from a single grid video frame.

    Attributes
    ----------
    cue_number:
        MA3 cue number (1.000, 2.000, …).
    time_s:
        Seconds from the start of the show when this cue should fire.
    fade_s:
        Fade-in duration in seconds.
    fixture_colors:
        ``{fixture_id: (R, G, B)}`` — colour values 0–255.
    fixture_brightness:
        ``{fixture_id: brightness}`` — 0.0–1.0 dimmer level.
    """
    cue_number: float
    time_s: float
    fade_s: float
    fixture_colors: dict[int, tuple[int, int, int]] = field(default_factory=dict)
    fixture_brightness: dict[int, float] = field(default_factory=dict)


class CueBuilder:
    """Convert grid video frames to :class:`MA3Cue` objects.

    Parameters
    ----------
    patch:
        Loaded fixture patch.
    stage:
        Physical stage dimensions.
    cols, rows:
        Grid dimensions (must match the .npz grid).
    window:
        Neighbourhood half-size for :func:`~mli_bridge.mapper.geometry.get_grid_value`.
        ``1`` → 3×3 box average.
    brightness_floor:
        Minimum dimmer level (0–1) applied to all fixtures regardless of the
        grid content.  Prevents a fully black grid from blacking out the rig.
    """

    def __init__(
        self,
        patch: FixturePatch,
        stage: Stage3D,
        cols: int = 12,
        rows: int = 8,
        window: int = 1,
        brightness_floor: float = 0.05,
    ) -> None:
        self._patch = patch
        self._stage = stage
        self._cols = cols
        self._rows = rows
        self._window = window
        self._brightness_floor = brightness_floor
        self._grid_map: dict[int, GridCoord] = build_fixture_grid_map(
            patch, stage, cols=cols, rows=rows
        )

    # ------------------------------------------------------------------ public

    def build_cue(
        self,
        cue_number: float,
        grid_frame: np.ndarray,
        time_s: float,
        next_time_s: float | None = None,
        prev_frame: np.ndarray | None = None,
    ) -> MA3Cue:
        """Build one :class:`MA3Cue` from a single grid frame.

        Parameters
        ----------
        cue_number:
            MA3 cue number for this cue.
        grid_frame:
            Shape ``(rows, cols, 3)`` uint8.
        time_s:
            Wall-clock trigger time (seconds from show start).
        next_time_s:
            Trigger time of the following cue.  Used to set fade duration.
            ``None`` → uses 1 s fade.
        prev_frame:
            Previous grid frame (reserved; not used by current implementation).

        Returns
        -------
        MA3Cue
        """
        if next_time_s is not None:
            gap = next_time_s - time_s
            fade_s = max(0.1, min(2.0, gap * 0.8))
        else:
            fade_s = 1.0

        fixture_colors: dict[int, tuple[int, int, int]] = {}
        fixture_brightness: dict[int, float] = {}

        for fx in self._patch.fixtures:
            coord = self._grid_map[fx.id]
            r, g, b = get_grid_value(grid_frame, coord, window=self._window)

            # ITU-R BT.709 luminance as brightness
            lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
            brightness = max(self._brightness_floor, lum)

            fixture_colors[fx.id] = (r, g, b)
            fixture_brightness[fx.id] = round(brightness, 3)

        return MA3Cue(
            cue_number=cue_number,
            time_s=round(time_s, 3),
            fade_s=round(fade_s, 3),
            fixture_colors=fixture_colors,
            fixture_brightness=fixture_brightness,
        )

    def build_all_cues(
        self,
        grid: GridData,
        keyframes: list[int],
    ) -> list[MA3Cue]:
        """Build :class:`MA3Cue` objects for every keyframe.

        Parameters
        ----------
        grid:
            Loaded :class:`~mli_bridge.mapper.grid_loader.GridData`.
        keyframes:
            Sorted frame indices from
            :func:`~mli_bridge.mapper.grid_loader.extract_keyframes`.

        Returns
        -------
        list[MA3Cue]
            One cue per keyframe, cue numbers starting at 1.
        """
        cues: list[MA3Cue] = []
        prev_frame: np.ndarray | None = None

        for i, frame_idx in enumerate(keyframes):
            cue_number = float(i + 1)
            time_s = frame_idx / grid.fps
            next_time_s = (
                keyframes[i + 1] / grid.fps if i + 1 < len(keyframes) else None
            )
            cue = self.build_cue(
                cue_number=cue_number,
                grid_frame=grid.frames[frame_idx],
                time_s=time_s,
                next_time_s=next_time_s,
                prev_frame=prev_frame,
            )
            cues.append(cue)
            prev_frame = grid.frames[frame_idx]

        logger.info(
            "CueBuilder: {} cues built from {} keyframes "
            "({} fixtures each)",
            len(cues), len(keyframes), len(self._patch.fixtures),
        )
        return cues
