# Architecture

## Overview

```
song.wav
  │
  ▼
audio.analyzer.analyze()          librosa: RMS, onset, beats, bands,
  │                                centroid, chroma, structural segs
  ▼
engine.EventScheduler
  │  .build_timeline(features)
  │  → sorted list[OscEvent]       offline, no network I/O
  │
  ▼
engine.PlaybackController.play()
  │
  ├─ sounddevice OutputStream ─→  audio DAC (platform audio thread)
  │       │
  │       └─ first callback captures t0 = time.perf_counter()
  │
  └─ CueEngine (daemon thread)
         │  polls every 1 ms
         └─ fires OscEvent via MA3OscClient
                 │
                 └─ UDP socket → grandMA3onPC:8000
```

## Key design decisions

### 1. Offline analysis + pre-sorted event list

All CPU-heavy audio analysis (librosa FFT, beat tracking, structural
segmentation) happens *before* playback starts.  The CueEngine sees a
flat list of `(time_s, payload)` pairs and only needs to do a cursor
scan — no audio I/O inside the real-time loop.

### 2. `time.perf_counter()` timing

The CueEngine calculates `now = perf_counter() - t0` every iteration.
`t0` is set at the exact moment the first audio sample is handed to the
sounddevice callback, so lights and audio share the same reference clock
with nanosecond precision.

### 3. Thread model

| Thread | Role |
|--------|------|
| Main | CLI / PlaybackController.play() — blocks |
| Audio (sounddevice) | Writes PCM samples to the DAC |
| CueEngine (daemon) | Polls event list, fires OSC |

The audio and engine threads never share mutable state.  The OSC client
uses a `threading.Lock` to guard the UDP socket.

### 4. Pre-roll

`pre_roll_seconds` (default 0.5 s) of silence is prepended to the audio
buffer.  The CueEngine's `t0` is adjusted forward by the same duration
so event timestamps remain relative to the *audio content*, not the
silence.  This gives MA3 time to reach steady state before the first
beat fires.

### 5. Stale-event policy

Events late by ≤ 50 ms are fired with a warning.  Events late by > 50 ms
are silently skipped.  This prevents a burst of stale strobe / flash
commands from firing out of order if the process was momentarily paused
(e.g. OS scheduler hiccup).

## Module map

```
src/mli_bridge/
  settings.py          BridgeSettings (Pydantic, reads .env)
  osc/
    client.py          MA3OscClient (thread-safe UDP)
    commands.py        Typed MA3 command helpers
    connection_test.py ping / assert_reachable
  audio/
    analyzer.py        AudioFeatures + analyze()
    beat_tracker.py    Frame ↔ seconds helpers
  show/
    fixture_patch.py   YAML patch loader
    group_builder.py   Store groups in MA3
    preset_builder.py  Store colour presets in MA3
    sequence_builder.py Create / label MA3 sequence
    show_initializer.py Orchestrator (groups → presets → seq → master)
  engine/
    event_scheduler.py OscEvent + EventScheduler.build_timeline()
    cue_engine.py      Real-time dispatch thread
    playback.py        PlaybackController (audio + engine)
  cli/
    main.py            Typer app (5 commands)
```
