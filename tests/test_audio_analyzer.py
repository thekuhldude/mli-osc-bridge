"""Tests for the audio analyser — uses a synthetic WAV."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from mli_bridge.audio.analyzer import AudioFeatures, analyze


def _write_test_wav(path: Path, dur: float = 2.0, sr: int = 22050) -> None:
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    audio = 0.2 * np.sin(2 * np.pi * 80 * t)
    for tick in np.arange(0.0, dur, 0.5):
        i = int(tick * sr)
        audio[i : i + 200] += 0.6
    audio = np.clip(audio, -1.0, 1.0)
    pcm = (audio * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


@pytest.fixture
def wav(tmp_path: Path) -> Path:
    p = tmp_path / "test.wav"
    _write_test_wav(p)
    return p


@pytest.fixture
def features(wav: Path) -> AudioFeatures:
    return analyze(wav, fps=30)


def test_per_frame_alignment(features: AudioFeatures) -> None:
    n = features.n_frames
    for name, arr in [
        ("rms", features.rms_curve),
        ("onset", features.onset_strength),
        ("centroid", features.spectral_centroid),
        ("bandwidth", features.spectral_bandwidth),
        ("low", features.low_band_energy),
        ("mid", features.mid_band_energy),
        ("high", features.high_band_energy),
    ]:
        assert arr.shape == (n,), f"{name}: expected ({n},) got {arr.shape}"
    assert features.chroma.shape == (n, 12)


def test_beats_detected(features: AudioFeatures) -> None:
    """Periodic kick at 2 Hz should produce at least 2 beats in a 2 s clip."""
    assert len(features.beat_frames) >= 2


def test_rms_normalised(features: AudioFeatures) -> None:
    assert float(features.rms_curve.max()) <= 1.0 + 1e-6
    assert float(features.rms_curve.min()) >= 0.0


def test_structural_segments(features: AudioFeatures) -> None:
    """At least one segment must be detected."""
    assert len(features.structural_segments) >= 1
    for start, end, _ in features.structural_segments:
        assert start < end
        assert end <= features.n_frames


def test_tempo_positive(features: AudioFeatures) -> None:
    assert features.tempo_bpm > 0.0
