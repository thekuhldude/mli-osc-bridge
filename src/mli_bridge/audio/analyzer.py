"""Offline audio analysis: WAV → AudioFeatures (all arrays at *fps* rate).

The analysis runs once before playback begins so the CueEngine can
query any frame in O(1) without touching the audio file again.

Array alignment contract
------------------------
Every per-frame array has exactly ``n_frames`` elements where::

    n_frames = ceil(duration_s * fps)

Frame *k* covers the audio time window  [k/fps, (k+1)/fps).
All arrays are float32 in roughly [0, 1] unless documented otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import librosa
import numpy as np
from loguru import logger


@dataclass
class AudioFeatures:
    """All pre-computed per-frame features for one audio file."""

    audio_path: Path
    sample_rate: int
    duration_s: float
    fps: int
    n_frames: int

    # --- Per-frame energy / dynamics ---
    rms_curve: np.ndarray          # (n_frames,) float32, normalised 0-1
    onset_strength: np.ndarray     # (n_frames,) float32, normalised 0-1

    # --- Beat / tempo ---
    beat_frames: np.ndarray        # (n_beats,) int64 — frame indices of beats
    beat_strength: np.ndarray      # (n_beats,) float32
    tempo_bpm: float

    # --- Frequency bands ---
    low_band_energy: np.ndarray    # (n_frames,) 20-200 Hz bass
    mid_band_energy: np.ndarray    # (n_frames,) 200-4000 Hz mids
    high_band_energy: np.ndarray   # (n_frames,) 4k-20k Hz treble

    # --- Spectral ---
    spectral_centroid: np.ndarray  # (n_frames,) normalised 0-1
    spectral_bandwidth: np.ndarray # (n_frames,) normalised 0-1
    chroma: np.ndarray             # (n_frames, 12) float32

    # --- Structure ---
    structural_segments: list[tuple[int, int, float]] = field(default_factory=list)
    # Each entry: (start_frame, end_frame, mean_rms)


def analyze(wav_path: Path, fps: int = 30) -> AudioFeatures:
    """Load *wav_path* and return ``AudioFeatures`` at *fps* frames/sec.

    Parameters
    ----------
    wav_path:
        Path to a WAV (or any librosa-supported format).
    fps:
        Target frame rate.  All arrays are resampled to this rate.
    """
    wav_path = Path(wav_path)
    logger.info("Analysing {} at {}fps …", wav_path.name, fps)

    y, sr = librosa.load(str(wav_path), sr=None, mono=True)
    duration_s = float(len(y) / sr)
    hop = int(round(sr / fps))
    n_frames = int(np.ceil(duration_s * fps))

    def _resize(arr: np.ndarray) -> np.ndarray:
        """Trim or zero-pad *arr* to exactly *n_frames* elements."""
        if len(arr) >= n_frames:
            return arr[:n_frames].astype(np.float32)
        pad = np.zeros(n_frames - len(arr), dtype=np.float32)
        return np.concatenate([arr.astype(np.float32), pad])

    def _normalise(arr: np.ndarray) -> np.ndarray:
        """Scale to [0, 1]; leaves all-zero arrays unchanged."""
        m = float(arr.max())
        return arr / m if m > 0.0 else arr

    # --- RMS ---
    rms_raw = librosa.feature.rms(y=y, hop_length=hop)[0]
    rms = _normalise(_resize(rms_raw))

    # --- Onset strength ---
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    onset = _normalise(_resize(onset_env))

    # --- Beats ---
    tempo_raw, beat_samples = librosa.beat.beat_track(y=y, sr=sr, hop_length=hop, units="frames")
    # librosa ≥ 0.10 may return tempo as a 0-d or 1-element array
    tempo = float(np.atleast_1d(tempo_raw)[0])
    beat_samples = np.atleast_1d(beat_samples).astype(np.int64)
    beat_frames_arr = np.clip(beat_samples, 0, n_frames - 1)
    beat_str = onset[beat_frames_arr] if len(beat_frames_arr) > 0 else np.zeros(0, np.float32)

    # --- Band energies ---
    def _band_energy(y_in: np.ndarray, f_low: float, f_high: float) -> np.ndarray:
        stft = np.abs(librosa.stft(y_in, hop_length=hop))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=stft.shape[0] * 2 - 2)
        mask = (freqs >= f_low) & (freqs < f_high)
        if mask.sum() == 0:
            return np.zeros(n_frames, dtype=np.float32)
        band = stft[mask, :].mean(axis=0)
        return _normalise(_resize(band))

    low = _band_energy(y, 20.0, 200.0)
    mid = _band_energy(y, 200.0, 4000.0)
    high = _band_energy(y, 4000.0, 20000.0)

    # --- Spectral centroid / bandwidth ---
    cent = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop)[0]
    bw = librosa.feature.spectral_bandwidth(y=y, sr=sr, hop_length=hop)[0]
    centroid = _normalise(_resize(cent))
    bandwidth = _normalise(_resize(bw))

    # --- Chroma ---
    chroma_raw = librosa.feature.chroma_stft(y=y, sr=sr, hop_length=hop)
    chroma_2d = _resize(chroma_raw.T)[:, :12]  # (n_frames, 12)
    if chroma_2d.shape[0] < n_frames:
        pad = np.zeros((n_frames - chroma_2d.shape[0], 12), dtype=np.float32)
        chroma_2d = np.vstack([chroma_2d, pad])

    # --- Structural segmentation ---
    segs = _structural_segments(rms, fps)

    logger.info(
        "Done: {:.1f}s | {:.1f} BPM | {} beats | {} segments",
        duration_s,
        tempo,
        len(beat_frames_arr),
        len(segs),
    )

    return AudioFeatures(
        audio_path=wav_path,
        sample_rate=sr,
        duration_s=duration_s,
        fps=fps,
        n_frames=n_frames,
        rms_curve=rms,
        onset_strength=onset,
        beat_frames=beat_frames_arr,
        beat_strength=beat_str,
        tempo_bpm=tempo,
        low_band_energy=low,
        mid_band_energy=mid,
        high_band_energy=high,
        spectral_centroid=centroid,
        spectral_bandwidth=bandwidth,
        chroma=chroma_2d,
        structural_segments=segs,
    )


def _structural_segments(
    rms: np.ndarray, fps: int, min_seg_frames: int = 30
) -> list[tuple[int, int, float]]:
    """Segment the track by RMS envelope changes.

    Uses a simple threshold approach: split wherever the RMS-smoothed
    signal crosses the track mean.  Segments shorter than
    *min_seg_frames* are merged into their neighbour.
    """
    n = len(rms)
    # Smooth with a 1-second window
    win = max(1, fps)
    kernel = np.ones(win) / float(win)
    smooth = np.convolve(rms, kernel, mode="same")
    mean_level = float(smooth.mean())

    # Find crossings
    above = smooth >= mean_level
    crossings = list(np.where(np.diff(above.astype(np.int8)) != 0)[0] + 1)
    boundaries = [0] + crossings + [n]

    segments: list[tuple[int, int, float]] = []
    for i in range(len(boundaries) - 1):
        s, e = boundaries[i], boundaries[i + 1]
        if e - s < min_seg_frames and segments:
            # Merge short segment into previous
            prev_s, _, _ = segments[-1]
            seg_rms = float(rms[prev_s:e].mean())
            segments[-1] = (prev_s, e, seg_rms)
        else:
            segments.append((s, e, float(rms[s:e].mean())))
    return segments
