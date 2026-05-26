# MLI-OSC-Bridge

Real-time audio → grandMA3onPC light show controller via OSC.

Feed it a WAV file and it analyses the audio offline (beat detection,
energy, onset, frequency bands, structural segments), builds a
deterministic OSC event timeline, then fires commands at grandMA3 in
perfect sync with audio playback.

**No GPU. No API key. No network latency to an LLM.** Runs on CPU in
seconds of pre-analysis, then plays in real time.

## What it does

1. **Analyses** your WAV with librosa (RMS, beats, onsets, spectral bands, structure).
2. **Builds** a sorted list of `OscEvent` objects — offline, no MA3 traffic yet.
3. **Plays** audio via sounddevice, capturing the exact `perf_counter()` sample-start time.
4. **Fires** OSC commands from a daemon thread, locked to the same clock as the audio.

### Light behaviours

| Trigger | MA3 action | Knob |
|---------|-----------|------|
| Every beat | Master fader flash (outer / inner col alternation) | `BEAT_FLASH_BRIGHTNESS` |
| Every frame (RMS changes) | Master fader follows energy | `ENERGY_DIMMER_FLOOR` |
| Strong onset inside loud section | Strobe burst (on/off/on) | `ONSET_STROBE_THRESHOLD`, `ONSET_ENERGY_GATE` |
| Structural segment boundary | `Goto Seq N Cue M` | automatic |

## Setup

```powershell
uv sync
copy .env.example .env
# Edit .env: set MA3_HOST, MA3_PORT
```

See [QUICKSTART.md](QUICKSTART.md) for step-by-step instructions.

## Usage

```powershell
# Verify MA3 is reachable
uv run mli-bridge test-connection

# Initialise MA3 show (groups, presets, sequence)
uv run mli-bridge setup-show patch_example.yaml

# Analyse audio (no MA3 traffic)
uv run mli-bridge analyze song.wav

# Preview first 20 events
uv run mli-bridge preview song.wav

# Play with lights
uv run mli-bridge play song.wav

# Play + setup show in one step
uv run mli-bridge play song.wav --patch patch_example.yaml
```

## Architecture

```
song.wav → analyze() → EventScheduler.build_timeline()
                              │
              ┌───────────────┴───────────────┐
              │                               │
   sounddevice stream                    CueEngine thread
   (audio to DAC)                   (OSC → grandMA3onPC:8000)
              │                               │
              └──── shared clock (perf_counter t0) ──────┘
```

Full details in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Tests

```powershell
uv run pytest -q
```

9 tests cover: OSC client thread safety, per-frame array alignment,
beat detection, structural segments, energy throttling, strobe gate,
timeline sort order.

## Configuration

All knobs are in `.env` (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `MA3_HOST` | `127.0.0.1` | grandMA3 IP |
| `MA3_PORT` | `8000` | OSC UDP port |
| `FPS` | `30` | Analysis frame rate |
| `PRE_ROLL_SECONDS` | `0.5` | Silence before audio |
| `BEAT_FLASH_BRIGHTNESS` | `1.0` | Peak flash level |
| `ENERGY_DIMMER_FLOOR` | `0.05` | Never fully black |
| `ONSET_STROBE_THRESHOLD` | `0.80` | Strobe trigger level |
| `ONSET_ENERGY_GATE` | `0.55` | Min RMS for strobe |
| `PLAYBACK_LOOP_INTERVAL_MS` | `1` | Engine poll rate |

## Requirements

- Python 3.11+
- `ffmpeg` not required (pure Python audio via sounddevice + soundfile)
- grandMA3onPC with OSC input enabled on port 8000

## Limitations

- Only controls the master dimmer fader and sequence cues by default.
  Per-fixture RGB control requires patching group executor mappings in
  `engine/cue_engine.py`.
- No feedback from MA3 (OSC is one-way UDP; no ACK).
- Timing precision is OS-dependent; Windows process priority may need
  raising for sub-5 ms jitter on loaded systems.
