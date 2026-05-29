"""MLI-OSC-Bridge CLI — nine commands.

    mli-bridge test-connection   Verify grandMA3 is reachable via OSC
    mli-bridge setup-show        Init MA3 groups / presets / sequence
    mli-bridge analyze           Analyse a WAV and print feature summary
    mli-bridge play              Analyse + play + send OSC in real time
    mli-bridge preview           Print the first N events without sending OSC
    mli-bridge list-devices      List available audio output devices
    mli-bridge play-show         Fire Go+ Sequence + audio simultaneously
    mli-bridge grid-to-ma3       Convert grid .npz to MA3 cue show (offline)
    mli-bridge read-patch        Auto-read MA3 fixture patch via TCP terminal
    mli-bridge read-patch-file   Parse a saved "List Fixture" text file
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="mli-bridge",
    help="Real-time audio → grandMA3 OSC light-show controller.",
    add_completion=False,
)
console = Console()


# ------------------------------------------------------------------ helpers

def _make_client():
    from mli_bridge.osc.client import MA3OscClient
    from mli_bridge.settings import get_settings
    s = get_settings()
    return MA3OscClient(host=s.ma3_host, port=s.ma3_port), s


# ------------------------------------------------------------------ commands

@app.command("test-connection")
def cmd_test_connection(
    host: str = typer.Option("", help="Override MA3_HOST env var"),
    port: int = typer.Option(0, help="Override MA3_PORT env var"),
) -> None:
    """Verify that grandMA3onPC is reachable via OSC UDP.

    Sends two ``Echo MLI-PING`` commands.  Check the MA3 command log
    for the \"MLI-PING\" and \"MLI-PING-2\" messages to confirm receipt.
    """
    from mli_bridge.osc.client import MA3OscClient
    from mli_bridge.osc.connection_test import ping
    from mli_bridge.settings import get_settings

    s = get_settings()
    h = host or s.ma3_host
    p = port or s.ma3_port

    console.print(f"[bold]Testing connection to {h}:{p} …[/bold]")
    client = MA3OscClient(host=h, port=p)
    ok = ping(client)
    if ok:
        console.print("[green]✓ Ping sent successfully.[/green]")
        console.print("[dim]Check the grandMA3 command log for 'MLI-PING'.[/dim]")
    else:
        console.print("[red]✗ Ping failed — check host/port and that MA3 OSC is enabled.[/red]")
        raise typer.Exit(code=1)


@app.command("setup-show")
def cmd_setup_show(
    patch: Path = typer.Argument(
        ...,
        help="Path to fixture patch YAML (e.g. patch_example.yaml)",
        exists=True,
    ),
) -> None:
    """Initialise groups, presets, and the main sequence in grandMA3.

    Reads the fixture patch YAML, creates groups and colour presets in
    MA3, then stores a blank starting sequence.
    """
    from mli_bridge.show.fixture_patch import load_patch
    from mli_bridge.show.show_initializer import ShowInitializer

    client, settings = _make_client()
    p = load_patch(patch)

    console.print(
        f"[bold]Setting up show '{settings.show_name}' on {settings.ma3_host}:{settings.ma3_port} …[/bold]"
    )
    ShowInitializer(client, p, settings).run()
    console.print("[green]✓ Show initialisation complete.[/green]")


@app.command("analyze")
def cmd_analyze(
    wav: Path = typer.Argument(..., help="Path to WAV file", exists=True),
    fps: int = typer.Option(30, help="Analysis frame rate"),
) -> None:
    """Analyse a WAV file and print a feature summary table."""
    from mli_bridge.audio.analyzer import analyze

    console.print(f"[bold]Analysing {wav.name} …[/bold]")
    f = analyze(wav, fps=fps)

    table = Table(title=f"Audio Features — {wav.name}")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Duration", f"{f.duration_s:.2f} s")
    table.add_row("Sample rate", f"{f.sample_rate} Hz")
    table.add_row("Frames", str(f.n_frames))
    table.add_row("FPS", str(f.fps))
    table.add_row("Tempo", f"{f.tempo_bpm:.1f} BPM")
    table.add_row("Beats detected", str(len(f.beat_frames)))
    table.add_row("Structural segments", str(len(f.structural_segments)))
    table.add_row("Mean RMS", f"{float(f.rms_curve.mean()):.3f}")
    table.add_row("Peak RMS", f"{float(f.rms_curve.max()):.3f}")
    table.add_row("Mean onset strength", f"{float(f.onset_strength.mean()):.3f}")

    console.print(table)


@app.command("play")
def cmd_play(
    wav: Path = typer.Argument(..., help="Path to WAV file", exists=True),
    patch: Optional[Path] = typer.Option(
        None,
        help="Fixture patch YAML; omit to skip show setup",
        exists=True,
    ),
    fps: int = typer.Option(30, help="Analysis frame rate"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Analyse but do not send OSC"),
) -> None:
    """Analyse a WAV file and play it back with synchronised MA3 lighting.

    1. Analyses the audio to extract beat/energy/onset features.
    2. Builds an event timeline (offline, no OSC yet).
    3. Starts audio playback + CueEngine simultaneously.
    4. Fires OSC events in real time, synced to audio.
    """
    from mli_bridge.audio.analyzer import analyze
    from mli_bridge.engine.event_scheduler import EventScheduler
    from mli_bridge.engine.playback import PlaybackController
    from mli_bridge.show.fixture_patch import load_patch
    from mli_bridge.show.show_initializer import ShowInitializer

    client, settings = _make_client()

    fixture_ids: list[int] = []
    if patch:
        p = load_patch(patch)
        ShowInitializer(client, p, settings).run()
        fixture_ids = [f.id for f in p.fixtures]

    console.print(f"[bold]Analysing {wav.name} …[/bold]")
    features = analyze(wav, fps=fps)

    console.print("[bold]Building event timeline …[/bold]")
    scheduler = EventScheduler(settings, fixture_ids=fixture_ids or None)
    events = scheduler.build_timeline(features)
    console.print(f"[green]{len(events)} events scheduled[/green]")

    if dry_run:
        console.print("[yellow]--dry-run: not sending OSC.[/yellow]")
        return

    console.print(f"[bold]Playing {wav.name} → {settings.ma3_host}:{settings.ma3_port} …[/bold]")
    ctrl = PlaybackController(client, events, settings)
    try:
        ctrl.play(wav)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        ctrl.stop()


@app.command("preview")
def cmd_preview(
    wav: Path = typer.Argument(..., help="Path to WAV file", exists=True),
    n: int = typer.Option(20, "--events", "-n", help="Number of events to print"),
    fps: int = typer.Option(30, help="Analysis frame rate"),
) -> None:
    """Show the first N scheduled events without sending any OSC.

    Useful for verifying the timeline before connecting to MA3.
    """
    from mli_bridge.audio.analyzer import analyze
    from mli_bridge.engine.event_scheduler import EventScheduler

    _, settings = _make_client()
    features = analyze(wav, fps=fps)
    events = EventScheduler(settings).build_timeline(features)

    table = Table(title=f"Event preview — {wav.name} (first {n})")
    table.add_column("#", style="dim", width=5)
    table.add_column("Time (s)", style="cyan", width=10)
    table.add_column("Type", style="magenta")
    table.add_column("MA3 command(s)", style="white")

    for i, ev in enumerate(events[:n]):
        # Show the actual MA3 command strings — the most useful preview info
        cmds = ev.payload.get("commands", [])
        cmd_str = " | ".join(cmds) if cmds else str(ev.payload)
        table.add_row(str(i + 1), f"{ev.time_s:.3f}", ev.command_type.name, cmd_str)

    console.print(table)
    if len(events) > n:
        console.print(f"[dim]… and {len(events) - n} more events[/dim]")


@app.command("list-devices")
def cmd_list_devices() -> None:
    """List available audio output devices (sounddevice)."""
    import sounddevice as sd

    devices = sd.query_devices()
    table = Table(title="Audio Output Devices")
    table.add_column("ID", style="dim", width=5)
    table.add_column("Name", style="cyan")
    table.add_column("Channels", width=10)
    table.add_column("Default?", width=10)

    default_out = sd.default.device[1]
    for i, d in enumerate(devices):
        if d["max_output_channels"] > 0:
            is_default = "✓" if i == default_out else ""
            table.add_row(
                str(i),
                d["name"],
                str(d["max_output_channels"]),
                is_default,
            )

    console.print(table)
    console.print("[dim]Set AUDIO_DEVICE=<name> in .env to select a device.[/dim]")


@app.command("play-show")
def cmd_play_show(
    wav: Path = typer.Argument(..., help="Path to WAV file", exists=True),
    sequence_id: int = typer.Option(1, "--seq", "-s", help="MA3 sequence number"),
) -> None:
    """Fire Go+ Sequence and audio playback simultaneously.

    Assumes the MA3 sequence has already been programmed with Time-Trigger
    cues (e.g. via ``grid-to-ma3``).

    \\b
    1. Sends ``Go+ Sequence N`` to trigger the first cue.
    2. Starts audio playback back-to-back (<1 ms apart).
    3. MA3 advances through all cues automatically via Time-Trigger.
    4. Blocks until the audio track finishes.

    \\b
    Example:
        mli-bridge play-show song.wav
        mli-bridge play-show song.wav --seq 2
    """
    from mli_bridge.mapper.show_writer import ShowWriter

    client, settings = _make_client()
    writer = ShowWriter(client, settings)

    console.print(
        f"[bold]Show playback[/bold]  "
        f"audio=[cyan]{wav.name}[/cyan]  "
        f"Seq=[green]{sequence_id}[/green]"
    )
    console.print(f"[dim]→ {settings.ma3_host}:{settings.ma3_port}[/dim]")

    try:
        writer.start_playback(sequence_id=sequence_id, audio_path=wav)
        console.print("[green]✓ Playback complete.[/green]")
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")


@app.command("grid-to-ma3")
def cmd_grid_to_ma3(
    grid: Path = typer.Argument(
        ...,
        help="Path to grid video .npz (from MLI-Rulegen)",
        exists=True,
    ),
    patch: Path = typer.Argument(
        ...,
        help="Path to fixture patch YAML",
        exists=True,
    ),
    sequence_id: int = typer.Option(1, "--seq", "-s", help="MA3 sequence number"),
    sequence_name: str = typer.Option("MLI_Show", "--name", "-n", help="MA3 sequence label"),
    threshold: float = typer.Option(
        10.0, "--threshold", "-t",
        help="Minimum mean pixel change to create a cue (0–255)",
    ),
    max_cpm: float = typer.Option(
        60.0, "--max-cpm", "-m",
        help="Maximum cues per minute (limits cue density)",
    ),
    cols: int = typer.Option(12, help="Grid column count (must match .npz)"),
    rows: int = typer.Option(8, help="Grid row count (must match .npz)"),
    audio: Optional[Path] = typer.Option(
        None, "--audio", "-a",
        help="WAV file to play immediately after writing (use with --play)",
        exists=True,
    ),
    play: bool = typer.Option(
        False, "--play", "-p",
        help=(
            "Fire Go+ Sequence + audio immediately after writing. "
            "MA3 advances through cues automatically via Time-Trigger."
        ),
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Analyse and build cues but do not send OSC",
    ),
) -> None:
    """Convert a grid video .npz into a grandMA3 cue show (offline programming).

    Reads the grid video produced by MLI-Rulegen, maps each fixture to a
    grid coordinate using 3D stage geometry from the patch YAML, extracts
    keyframes, builds MA3 cues and writes them all to grandMA3 via OSC.
    Cue 1 gets a Go-Trigger; cues 2-N get Time-Triggers so MA3 advances
    automatically once playback starts.

    \\b
    Write only (start playback later with play-show):
        mli-bridge grid-to-ma3 show.npz patch.yaml
        mli-bridge play-show song.wav

    Write + play immediately:
        mli-bridge grid-to-ma3 show.npz patch.yaml --play --audio track.wav
    """
    from mli_bridge.mapper.pipeline import GridToMA3Pipeline

    client, settings = _make_client()

    console.print(
        f"[bold]Grid → MA3 mapper[/bold]  "
        f"grid=[cyan]{grid.name}[/cyan]  "
        f"patch=[cyan]{patch.name}[/cyan]  "
        f"Seq=[green]{sequence_id}[/green]"
    )

    if dry_run:
        console.print("[yellow]--dry-run: analysis only, no OSC will be sent.[/yellow]")
    if play and audio:
        console.print(f"[bold]Playback:[/bold] audio=[cyan]{audio.name}[/cyan]")
    elif play:
        console.print(
            "[bold]Playback:[/bold] "
            "[yellow](no audio — use --audio to start a WAV)[/yellow]"
        )

    pipeline = GridToMA3Pipeline(client, settings)
    result = asyncio.run(
        pipeline.run(
            grid_path=grid,
            patch_path=patch,
            sequence_id=sequence_id,
            sequence_name=sequence_name,
            min_change_threshold=threshold,
            max_cues_per_minute=max_cpm,
            cols=cols,
            rows=rows,
            dry_run=dry_run,
            audio_path=audio,
            play_after_write=play,
        )
    )

    table = Table(title="Pipeline result")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Grid frames", str(result.n_frames))
    table.add_row("Duration", f"{result.duration_s:.1f} s")
    table.add_row("Keyframes extracted", str(result.n_keyframes))
    table.add_row("MA3 cues built", str(result.n_cues))
    table.add_row("Fixtures addressed", str(result.n_fixtures))
    table.add_row("Sequence", str(result.sequence_id))
    if not dry_run:
        table.add_row("Played", "yes" if result.played else "no")

    console.print(table)
    if not dry_run:
        console.print(
            f"[green]✓ Seq {result.sequence_id} '{sequence_name}' "
            f"programmed with {result.n_cues} cues (Time-Trigger).[/green]"
        )
        if result.played:
            console.print("[green]✓ Playback complete.[/green]")
    else:
        console.print("[yellow]Dry run complete — no OSC sent.[/yellow]")


@app.command("read-patch")
def cmd_read_patch(
    output: Path = typer.Option(
        Path("patch.yaml"),
        "--output", "-o",
        help="Output YAML file path",
    ),
    host: str = typer.Option(
        "",
        "--host",
        help="MA3 IP address (default: from MA3_HOST env / .env)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print detected fixtures without writing YAML",
    ),
) -> None:
    """Read the fixture patch from grandMA3 and generate patch.yaml automatically.

    Connects to the MA3 TCP terminal (port 30000), runs ``List Fixture``,
    parses 3D positions and groups fixtures by height:

    \\b
      z < 1 m    → Floor group (par)
      1 ≤ z < 4  → Mid group  (spot)
      z ≥ 4 m    → Truss group (wash)

    \\b
    Prerequisites: Enable the MA3 terminal in
    Menu → Settings → Network → Remote Control → Enable Terminal.

    \\b
    Examples:
        mli-bridge read-patch
        mli-bridge read-patch --output my_stage.yaml
        mli-bridge read-patch --dry-run
    """
    from mli_bridge.patch_reader.ma3_patch_reader import MA3PatchReader
    from mli_bridge.settings import get_settings

    settings = get_settings()
    ma3_host = host or settings.ma3_host

    console.print(
        f"[bold]Reading MA3 patch from[/bold] [cyan]{ma3_host}:30000[/cyan] …"
    )

    reader = MA3PatchReader(host=ma3_host)

    try:
        fixtures = asyncio.run(reader.read_patch())
    except ConnectionRefusedError:
        console.print(
            f"[red]✗ Connection refused at {ma3_host}:30000.[/red]\n"
            "[dim]Enable the terminal in MA3: "
            "Menu → Settings → Network → Enable Terminal[/dim]"
        )
        raise typer.Exit(code=1)
    except TimeoutError:
        console.print(
            f"[red]✗ Timeout connecting to {ma3_host}:30000.[/red]"
        )
        raise typer.Exit(code=1)

    if not fixtures:
        console.print("[red]No fixtures found. Check MA3 patch is populated.[/red]")
        raise typer.Exit(code=1)

    # Show what was found
    table = Table(title=f"Fixtures from MA3 at {ma3_host}")
    table.add_column("ID",   style="dim",  width=5)
    table.add_column("Name", style="cyan")
    table.add_column("X",    width=8)
    table.add_column("Y",    width=8)
    table.add_column("Z",    width=8)
    table.add_column("Type", style="magenta")
    for fx in fixtures:
        table.add_row(
            str(fx["id"]), fx["name"],
            f'{fx["x_m"]:.2f}m', f'{fx["y_m"]:.2f}m', f'{fx["z_m"]:.2f}m',
            fx["fixture_type"],
        )
    console.print(table)

    if dry_run:
        console.print("[yellow]--dry-run: YAML not written.[/yellow]")
        return

    try:
        data = asyncio.run(reader.generate_patch_yaml(output))
    except RuntimeError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(code=1)

    result_table = Table(title="Generated patch")
    result_table.add_column("Property", style="cyan")
    result_table.add_column("Value",    style="green")
    result_table.add_row("Fixtures", str(len(fixtures)))
    result_table.add_row("Groups",   str(len(data["groups"])))
    result_table.add_row("Stage width",  f'{data["stage"]["width_m"]:.1f} m')
    result_table.add_row("Stage depth",  f'{data["stage"]["depth_m"]:.1f} m')
    result_table.add_row("Stage height", f'{data["stage"]["height_m"]:.1f} m')
    for g in data["groups"]:
        result_table.add_row(
            f'Group: {g["label"]}',
            f'{len(g["fixtures"])} fixtures  type={g["fixture_type"]}',
        )
    console.print(result_table)

    console.print(f"[green]✓ {output} written.[/green]")
    console.print(
        "[dim]Next: uv run mli-bridge grid-to-ma3 grid.npz patch.yaml[/dim]"
    )


@app.command("read-patch-file")
def cmd_read_patch_file(
    fixtures_file: Path = typer.Argument(
        ...,
        help='Path to a text file containing the MA3 "List Fixture" output',
        exists=True,
    ),
    output: Path = typer.Option(
        Path("patch.yaml"), "--output", "-o",
        help="Output YAML file path",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print detected fixtures without writing YAML",
    ),
) -> None:
    r"""Parse a saved MA3 "List Fixture" text file and generate patch.yaml.

    Works fully offline — no MA3 connection required.

    \\b
    How to get the input file:
      1. In MA3, open the System Monitor.
      2. Run:  List Fixture
      3. Select all output text and save it to a .txt file.

    \\b
    The parser finds the header line (contains POSX / POSY / POSZ) and
    uses character-column positions to extract fixture ID, name and
    3D coordinates from every data row.

    \\b
    Fixtures are grouped by height:
      z < 1 m    → Floor group (par)
      1 ≤ z < 4  → Mid group  (spot)
      z ≥ 4 m    → Truss group (wash)

    \\b
    Example:
        mli-bridge read-patch-file fixtures.txt
        mli-bridge read-patch-file fixtures.txt --output my_stage.yaml
        mli-bridge read-patch-file fixtures.txt --dry-run
    """
    from mli_bridge.patch_reader.list_fixture_parser import (
        parse_list_fixture_output,
        generate_patch_yaml,
    )

    console.print(
        f"[bold]Parsing List Fixture file[/bold]  [cyan]{fixtures_file.name}[/cyan]"
    )

    text = fixtures_file.read_text(encoding="utf-8", errors="replace")
    fixtures = parse_list_fixture_output(text)

    if not fixtures:
        console.print(
            "[red]No fixtures found in the file.[/red]\n"
            "[dim]Make sure the file contains a header line with POSX / POSY / POSZ.[/dim]"
        )
        raise typer.Exit(code=1)

    table = Table(title=f"Fixtures from {fixtures_file.name}")
    table.add_column("ID",   style="dim",  width=5)
    table.add_column("Name", style="cyan")
    table.add_column("X",    width=9)
    table.add_column("Y",    width=9)
    table.add_column("Z",    width=9)
    table.add_column("Type", style="magenta")
    for fx in fixtures[:30]:
        table.add_row(
            str(fx["id"]), fx["name"],
            f'{fx["x_m"]:.3f}m', f'{fx["y_m"]:.3f}m', f'{fx["z_m"]:.3f}m',
            fx["fixture_type"],
        )
    if len(fixtures) > 30:
        console.print(f"[dim]… showing first 30 of {len(fixtures)} fixtures[/dim]")
    console.print(table)

    if dry_run:
        console.print("[yellow]--dry-run: YAML not written.[/yellow]")
        return

    data = generate_patch_yaml(fixtures, output, source=fixtures_file.name)

    result_table = Table(title="Generated patch")
    result_table.add_column("Property", style="cyan")
    result_table.add_column("Value",    style="green")
    result_table.add_row("Fixtures",     str(len(fixtures)))
    result_table.add_row("Groups",       str(len(data["groups"])))
    result_table.add_row("Stage width",  f'{data["stage"]["width_m"]:.1f} m')
    result_table.add_row("Stage depth",  f'{data["stage"]["depth_m"]:.1f} m')
    result_table.add_row("Stage height", f'{data["stage"]["height_m"]:.1f} m')
    for g in data["groups"]:
        result_table.add_row(
            f'Group: {g["label"]}',
            f'{len(g["fixtures"])} fixtures  type={g["fixture_type"]}',
        )
    console.print(result_table)

    console.print(f"[green]✓ {output} written.[/green]")
    console.print(
        "[dim]Next: uv run mli-bridge grid-to-ma3 grid.npz patch.yaml[/dim]"
    )


if __name__ == "__main__":
    app()
