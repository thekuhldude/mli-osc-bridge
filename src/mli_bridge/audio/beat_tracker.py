"""Beat tracking helpers: frame index ↔ wall-clock time conversions.

These utilities are used by the EventScheduler to translate beat frame
indices (as stored in AudioFeatures) into precise wall-clock timestamps
for the CueEngine.
"""
from __future__ import annotations

import numpy as np

from mli_bridge.audio.analyzer import AudioFeatures


def frame_to_seconds(frame: int, fps: int) -> float:
    """Convert a frame index to wall-clock seconds."""
    return frame / fps


def seconds_to_frame(t: float, fps: int) -> int:
    """Convert a wall-clock time to the nearest frame index."""
    return int(round(t * fps))


def beat_times(features: AudioFeatures) -> np.ndarray:
    """Return beat timestamps in seconds as a float64 array."""
    return features.beat_frames.astype(np.float64) / features.fps


def nearest_beat(features: AudioFeatures, t: float) -> int:
    """Return the index of the beat closest to time *t* seconds."""
    times = beat_times(features)
    if len(times) == 0:
        return 0
    return int(np.argmin(np.abs(times - t)))


def beats_in_range(features: AudioFeatures, t_start: float, t_end: float) -> np.ndarray:
    """Return the frame indices of all beats in [t_start, t_end)."""
    times = beat_times(features)
    mask = (times >= t_start) & (times < t_end)
    return features.beat_frames[mask]


def inter_beat_intervals(features: AudioFeatures) -> np.ndarray:
    """Return inter-beat intervals in seconds (length = n_beats - 1)."""
    times = beat_times(features)
    if len(times) < 2:
        return np.array([], dtype=np.float64)
    return np.diff(times)


def tempo_at_frame(features: AudioFeatures, frame: int) -> float:
    """Estimate the local tempo (BPM) around *frame* using the nearest
    three beats.  Falls back to the global tempo if fewer beats exist.
    """
    times = beat_times(features)
    if len(times) < 2:
        return features.tempo_bpm
    t = frame / features.fps
    diffs = np.abs(times - t)
    nearest = np.argsort(diffs)[:3]
    local_times = np.sort(times[nearest])
    if len(local_times) < 2:
        return features.tempo_bpm
    mean_ibi = float(np.mean(np.diff(local_times)))
    return 60.0 / mean_ibi if mean_ibi > 0 else features.tempo_bpm
