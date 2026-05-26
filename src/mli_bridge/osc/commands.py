"""Typed MA3 command helpers built on top of MA3OscClient.

Every function corresponds to one logical MA3 action.  They all take
the client as their first argument so they are easy to test by passing
a mock.

MA3 command-line reference used here:
  - Go+ Seq <n>          advance a sequence
  - Go- Seq <n>          step back
  - Goto Seq <n> Cue <c> jump to a specific cue
  - Off Seq <n>          deactivate sequence
  - Store Seq <n> Cue <c> [/merge] store current output as cue
  - Label Seq <n> "<label>" rename a sequence
  - Assign Seq <n> /executor=<e> assign to executor
  - Temp <val> If Seq <n>  set a temp value on the sequence
"""
from __future__ import annotations

from mli_bridge.osc.client import MA3OscClient


# ------------------------------------------------------------------ Sequences

def sequence_go(client: MA3OscClient, seq: int) -> None:
    """Advance sequence *seq* by one cue."""
    client.send_command(f"Go+ Seq {seq}")


def sequence_go_back(client: MA3OscClient, seq: int) -> None:
    """Step sequence *seq* back by one cue."""
    client.send_command(f"Go- Seq {seq}")


def sequence_goto_cue(client: MA3OscClient, seq: int, cue: float) -> None:
    """Jump sequence *seq* directly to cue *cue*."""
    client.send_command(f"Goto Seq {seq} Cue {cue:.3f}")


def sequence_off(client: MA3OscClient, seq: int) -> None:
    """Deactivate sequence *seq*."""
    client.send_command(f"Off Seq {seq}")


def sequence_store_cue(
    client: MA3OscClient,
    seq: int,
    cue: float,
    merge: bool = False,
) -> None:
    """Store the current programmer output as cue *cue* in sequence *seq*."""
    flag = " /merge" if merge else ""
    client.send_command(f"Store Seq {seq} Cue {cue:.3f}{flag}")


def sequence_label(client: MA3OscClient, seq: int, label: str) -> None:
    """Rename sequence *seq*."""
    client.send_command(f'Label Seq {seq} "{label}"')


def sequence_assign_executor(
    client: MA3OscClient, seq: int, executor: int
) -> None:
    """Assign sequence *seq* to executor slot *executor*."""
    client.send_command(f"Assign Seq {seq} /executor={executor}")


# ------------------------------------------------------------------ Groups / fixtures

def group_at(client: MA3OscClient, group: int, dimmer: float) -> None:
    """Set group *group* dimmer to *dimmer* (0 – 100 %)."""
    pct = max(0.0, min(100.0, dimmer * 100.0))
    client.send_command(f"Group {group} At {pct:.1f}")


def fixture_at(client: MA3OscClient, fixture: int, dimmer: float) -> None:
    """Set single fixture *fixture* dimmer to *dimmer* (0 – 100 %)."""
    pct = max(0.0, min(100.0, dimmer * 100.0))
    client.send_command(f"Fixture {fixture} At {pct:.1f}")


def fixture_color(
    client: MA3OscClient, fixture: int, r: int, g: int, b: int
) -> None:
    """Set RGB color on fixture *fixture* (0 – 255 each channel)."""
    client.send_command(f"Fixture {fixture} At Color {r},{g},{b}")


# ------------------------------------------------------------------ Executors / faders

def executor_master_go(client: MA3OscClient, page: int, executor: int) -> None:
    """Press the Go key for *executor* on *page*."""
    client.press_key(page, executor)


def set_master_dimmer(client: MA3OscClient, page: int, fader: int, level: float) -> None:
    """Set the master dimmer fader to *level* (0.0 – 1.0)."""
    client.set_fader(page, fader, level)


# ------------------------------------------------------------------ Presets

def apply_color_preset(
    client: MA3OscClient, group: int, preset: int
) -> None:
    """Apply color preset *preset* to group *group*."""
    client.send_command(f"Group {group} At ColorPreset {preset}")


# ------------------------------------------------------------------ Macros

def run_macro(client: MA3OscClient, macro: int) -> None:
    """Execute MA3 macro *macro* by number."""
    client.send_command(f"Do Macro {macro}")


# ------------------------------------------------------------------ Utility

def clear_all(client: MA3OscClient) -> None:
    """Clear programmer on the MA3 console."""
    client.send_command("Clear")


def blackout_on(client: MA3OscClient, page: int, key: int) -> None:
    """Press the Grand Master blackout key."""
    client.hold_key(page, key, pressed=True)


def blackout_off(client: MA3OscClient, page: int, key: int) -> None:
    """Release the Grand Master blackout key."""
    client.hold_key(page, key, pressed=False)
