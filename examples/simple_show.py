"""Minimal example: analyse a WAV and play it with MA3 lighting.

Run with:
    uv run python examples/simple_show.py path/to/song.wav

This script wires together all the MLI-OSC-Bridge components without
the CLI overhead, so you can see exactly what is happening at each step.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the package importable when running from the repo root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mli_bridge.audio.analyzer import analyze
from mli_bridge.engine.event_scheduler import EventScheduler
from mli_bridge.engine.playback import PlaybackController
from mli_bridge.osc.client import MA3OscClient
from mli_bridge.settings import BridgeSettings


def main(wav_path: str) -> None:
    wav = Path(wav_path)
    if not wav.exists():
        print(f"Error: {wav} not found")
        sys.exit(1)

    # 1. Load settings (reads .env if present)
    settings = BridgeSettings()
    print(f"MA3 target: {settings.ma3_host}:{settings.ma3_port}")

    # 2. Connect to MA3
    client = MA3OscClient(host=settings.ma3_host, port=settings.ma3_port)

    # 3. Analyse audio (offline)
    print(f"Analysing {wav.name} …")
    features = analyze(wav, fps=settings.fps)
    print(
        f"  {features.duration_s:.1f}s  |  {features.tempo_bpm:.1f} BPM  |"
        f"  {len(features.beat_frames)} beats  |  {len(features.structural_segments)} segments"
    )

    # 4. Build OSC event timeline (offline, no MA3 traffic yet)
    print("Building event timeline …")
    events = EventScheduler(settings).build_timeline(features)
    print(f"  {len(events)} events scheduled")

    # 5. Play (blocks until the track ends)
    print(f"Playing → {wav.name}")
    ctrl = PlaybackController(client, events, settings)
    try:
        ctrl.play(wav)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        ctrl.stop()

    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python simple_show.py <path/to/song.wav>")
        sys.exit(1)
    main(sys.argv[1])
