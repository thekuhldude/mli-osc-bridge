"""Tests for EventScheduler — no OSC, no audio device needed."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mli_bridge.audio.analyzer import AudioFeatures
from mli_bridge.engine.event_scheduler import CommandType, EventScheduler, OscEvent
from mli_bridge.settings import BridgeSettings


def _fake_features(n: int = 60, fps: int = 30) -> AudioFeatures:
    """Create a minimal AudioFeatures for testing (no real audio)."""
    rms = np.linspace(0.1, 0.9, n, dtype=np.float32)
    onset = np.zeros(n, dtype=np.float32)
    # Two beats at frames 10 and 25
    beat_frames = np.array([10, 25], dtype=np.int64)
    beat_str = np.array([0.8, 0.9], dtype=np.float32)
    return AudioFeatures(
        audio_path=Path("/tmp/fake.wav"),
        sample_rate=22050,
        duration_s=n / fps,
        fps=fps,
        n_frames=n,
        rms_curve=rms,
        onset_strength=onset,
        beat_frames=beat_frames,
        beat_strength=beat_str,
        tempo_bpm=120.0,
        low_band_energy=np.full(n, 0.5, dtype=np.float32),
        mid_band_energy=np.full(n, 0.3, dtype=np.float32),
        high_band_energy=np.full(n, 0.2, dtype=np.float32),
        spectral_centroid=np.full(n, 0.5, dtype=np.float32),
        spectral_bandwidth=np.full(n, 0.3, dtype=np.float32),
        chroma=np.zeros((n, 12), dtype=np.float32),
        structural_segments=[(0, 30, 0.3), (30, 60, 0.7)],
    )


@pytest.fixture
def scheduler() -> EventScheduler:
    return EventScheduler(BridgeSettings())


@pytest.fixture
def features() -> AudioFeatures:
    return _fake_features()


def test_timeline_sorted(scheduler: EventScheduler, features: AudioFeatures) -> None:
    events = scheduler.build_timeline(features)
    times = [e.time_s for e in events]
    assert times == sorted(times), "Events must be sorted by time_s"


def test_beat_events_at_beat_times(
    scheduler: EventScheduler, features: AudioFeatures
) -> None:
    events = scheduler.build_timeline(features)
    beat_event_times = {
        round(e.time_s, 4)
        for e in events
        if e.command_type == CommandType.BEAT_FLASH
    }
    expected = {round(f / features.fps, 4) for f in features.beat_frames}
    assert expected <= beat_event_times, "Missing beat events"


def test_structural_events_match_segments(
    scheduler: EventScheduler, features: AudioFeatures
) -> None:
    events = scheduler.build_timeline(features)
    seg_events = [e for e in events if e.command_type == CommandType.STRUCTURAL]
    assert len(seg_events) == len(features.structural_segments)


def test_energy_events_present(
    scheduler: EventScheduler, features: AudioFeatures
) -> None:
    events = scheduler.build_timeline(features)
    energy_events = [e for e in events if e.command_type == CommandType.ENERGY]
    # Throttled to changes > 1 %; with linearly rising RMS we expect several
    assert len(energy_events) > 0


def test_strobe_below_gate_produces_no_events() -> None:
    """With rms below energy gate, strobe events must not appear."""
    s = BridgeSettings()
    sched = EventScheduler(s)
    n = 60
    f = _fake_features(n)
    # onset above threshold but rms below gate
    f = AudioFeatures(
        audio_path=f.audio_path,
        sample_rate=f.sample_rate,
        duration_s=f.duration_s,
        fps=f.fps,
        n_frames=f.n_frames,
        rms_curve=np.full(n, 0.1, dtype=np.float32),   # below gate
        onset_strength=np.full(n, 0.95, dtype=np.float32),  # above threshold
        beat_frames=f.beat_frames,
        beat_strength=f.beat_strength,
        tempo_bpm=f.tempo_bpm,
        low_band_energy=f.low_band_energy,
        mid_band_energy=f.mid_band_energy,
        high_band_energy=f.high_band_energy,
        spectral_centroid=f.spectral_centroid,
        spectral_bandwidth=f.spectral_bandwidth,
        chroma=f.chroma,
        structural_segments=f.structural_segments,
    )
    events = sched.build_timeline(f)
    strobe = [e for e in events if e.command_type == CommandType.STROBE]
    assert strobe == [], "Strobe must not fire below energy gate"


def test_all_events_have_positive_time(
    scheduler: EventScheduler, features: AudioFeatures
) -> None:
    events = scheduler.build_timeline(features)
    for ev in events:
        assert ev.time_s >= 0.0, f"Negative event time: {ev.time_s}"
