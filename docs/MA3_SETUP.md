# grandMA3 OSC Setup

Follow these steps to enable the OSC interface in grandMA3onPC so
MLI-OSC-Bridge can control it.

## 1. Enable the OSC input plugin

1. Open grandMA3onPC.
2. Go to **Menu → Settings → Network**.
3. Click **+ Add** and choose **OSC** as the protocol.
4. Set:
   - **Mode**: Input
   - **IP address**: `127.0.0.1` (loopback; if running on the same machine)
   - **Port**: `8000`
5. Tick **Enabled**.
6. Click **Apply**.

> If MA3 and the bridge run on different machines, replace `127.0.0.1`
> with the MA3 machine's LAN IP and set `MA3_HOST` accordingly in `.env`.

## 2. Verify OSC is working

Run the connection test:

```powershell
mli-bridge test-connection
```

Then switch to the grandMA3 command line view.  You should see:

```
MLI-PING
MLI-PING-2
```

If those messages do not appear, double-check:
- Port is exactly `8000` (or update `MA3_PORT` in `.env`).
- Firewall is not blocking UDP on that port.
- The correct network adapter is selected in MA3 Network settings.

## 3. OSC address map

| Address           | Payload type | Description                          |
|-------------------|-------------|--------------------------------------|
| `/cmd`            | string      | Execute a MA3 command-line string     |
| `/Page1/FaderN`   | float [0-1] | Set fader N on page 1                 |
| `/Page1/KeyN`     | int (0/1)   | Press (1) or release (0) key N        |

The bridge exclusively uses `/cmd` for all MA3 command-line operations
and `/Page1/FaderN` for continuous dimmer updates.

## 4. Recommended MA3 show settings

- **Grandmaster fader** on executor page 1, fader 1 (default).
- Create a **sequence** on executor 1 before running `setup-show`.
- Make sure the sequence is **not locked** (otherwise cue-jumps are ignored).

## 5. Troubleshooting

| Symptom | Fix |
|---------|-----|
| No response in MA3 log | Check port 8000 UDP is open in Windows Firewall |
| Commands execute but nothing lights up | Fixtures not patched; run `setup-show` first |
| Lights flicker irregularly | Raise `PLAYBACK_LOOP_INTERVAL_MS` to `5` |
| MA3 crashes on fast command bursts | Reduce `FPS` to `20` |
