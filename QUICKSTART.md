# Quick Start — MLI-OSC-Bridge

Get lights reacting to music in grandMA3onPC in under 5 minutes.

## Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (`pip install uv`)
- grandMA3onPC installed and running
- A WAV file to play

## 1. Install

```powershell
cd "D:\Claude Code\MLI-osc-bridge"
uv sync
```

## 2. Configure

```powershell
copy .env.example .env
```

Edit `.env`:

```env
MA3_HOST=127.0.0.1   # IP of the machine running MA3 (loopback if same machine)
MA3_PORT=8000         # must match MA3 → Settings → Network → OSC port
```

See [`docs/MA3_SETUP.md`](docs/MA3_SETUP.md) for how to enable OSC in MA3.

## 3. Test the connection

```powershell
uv run mli-bridge test-connection
```

Check the **grandMA3 command line** for `MLI-PING`.

## 4. (Optional) Set up your show

Edit `patch_example.yaml` to match your MA3 patch, then:

```powershell
uv run mli-bridge setup-show patch_example.yaml
```

This creates groups, colour presets, and the main sequence in MA3.

## 5. Preview the event timeline

```powershell
uv run mli-bridge preview path/to/song.wav --events 30
```

Shows the first 30 OSC events without sending anything.

## 6. Play

```powershell
uv run mli-bridge play path/to/song.wav
```

Audio plays through your default output device; OSC commands fire in sync.

To also run show setup before playing:

```powershell
uv run mli-bridge play path/to/song.wav --patch patch_example.yaml
```

### Dry run (no OSC)

```powershell
uv run mli-bridge play path/to/song.wav --dry-run
```

## 7. Tune the show

All weights and thresholds live in `.env`.  Common tweaks:

```env
# Stronger beat flash:
BEAT_FLASH_BRIGHTNESS=1.0

# No strobe (ambient material):
ONSET_STROBE_THRESHOLD=1.1   # impossible threshold = disabled

# Snappier response (less smoothing):
PLAYBACK_LOOP_INTERVAL_MS=0.5

# Earlier audio start (shorter pre-roll):
PRE_ROLL_SECONDS=0.2
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `test-connection` fails | Check MA3 is running and OSC is enabled on port 8000 |
| Lights flash but colours are wrong | Run `setup-show` to load colour presets |
| Audio crackles | Raise sounddevice buffer size (not configurable via .env yet; edit `playback.py`) |
| Events arrive late | Raise `PLAYBACK_LOOP_INTERVAL_MS` to `5` |

## Run the tests

```powershell
uv run pytest -q
```

All tests run without a real MA3 or audio device.
