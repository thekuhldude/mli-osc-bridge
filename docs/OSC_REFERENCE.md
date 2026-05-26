# OSC Reference

All OSC messages sent by MLI-OSC-Bridge.

## Transport

- Protocol: UDP (connectionless)
- Default port: **8000**
- Max send rate: ~30 messages/second (matches MA3 safe processing limit)

## Address patterns

### `/cmd` — MA3 command-line string

| Field | Value |
|-------|-------|
| Address | `/cmd` |
| Argument | `string` |

Sends any valid grandMA3 command-line string.  Examples:

```
/cmd  "Go+ Seq 1"
/cmd  "Goto Seq 1 Cue 3.000"
/cmd  "Group 2 At 75.0"
/cmd  "Echo MLI-PING"
```

### `/Page1/FaderN` — continuous fader

| Field | Value |
|-------|-------|
| Address | `/PageP/FaderF` where P = page, F = fader number |
| Argument | `float` 0.0 (dark) … 1.0 (full) |

Used for real-time dimmer control (beat flash, energy follow, strobe).

### `/Page1/KeyN` — momentary key

| Field | Value |
|-------|-------|
| Address | `/PageP/KeyK` where P = page, K = key number |
| Argument | `int` 1 (press) or 0 (release) |

Used for Go/executor triggers.

## Event types and their OSC output

| Event type | OSC call | When |
|------------|----------|------|
| `BEAT_FLASH` | `set_fader(page, master, strength)` | Every detected beat |
| `ENERGY` | `set_fader(page, master, rms_level)` | Every frame where RMS changes > 1 % |
| `STROBE` | `set_fader(page, master, 1.0 or 0.0)` | Onset strobe on/off frames |
| `STRUCTURAL` | `/cmd "Goto Seq N Cue M"` | Segment boundary (section change) |

## Rate limiting

The EventScheduler throttles ENERGY events to only emit when the
level changes by more than 1 %.  At 30 fps this typically produces
5–15 energy events per second rather than 30.

Beat, strobe, and structural events are emitted unconditionally.
