"""Typed MA3 command helpers built on top of MA3OscClient.

Two families of helpers live here:

1. **String builders** (no client argument) — pure functions used by
   EventScheduler to pre-build command strings at analysis time.

   Correct MA3 syntax for Generic RGB fixtures
   -------------------------------------------
   Commands MUST be sent as separate /cmd calls; semicolon-chaining
   does NOT work for Attribute commands.  The CueEngine sleeps 20 ms
   between commands so MA3 processes each one before the next arrives.

     Select + intensity : "Fixture 1 Thru 4 At 80"
     Red channel        : 'Attribute "ColorRGB_R" At 255'
     Green channel      : 'Attribute "ColorRGB_G" At 0'
     Blue channel       : 'Attribute "ColorRGB_B" At 0'
     Blackout           : "Fixture 1 Thru 4 At 0"

   Return types
   ------------
   * Single-command operations → ``str``
   * Multi-command operations  → ``list[str]``  (intensity + 3 attrs)

2. **Client dispatchers** (take MA3OscClient) — thin wrappers around
   ``client.send_command()`` kept for show-setup code (group_builder,
   sequence_builder, show_initializer).
"""
from __future__ import annotations

from mli_bridge.osc.client import MA3OscClient


# ================================================================== String builders

def set_intensity(fixture_ids: list[int], value_percent: float) -> str:
    """Return a single MA3 intensity command (selects fixtures, sets dimmer).

    Parameters
    ----------
    fixture_ids:
        Target fixture IDs.  Addressed as ``Fixture <lo> Thru <hi>``.
    value_percent:
        Dimmer level 0 – 100.
    """
    lo, hi = min(fixture_ids), max(fixture_ids)
    pct = max(0.0, min(100.0, value_percent))
    return f"Fixture {lo} Thru {hi} At {pct:.0f}"


def blackout(fixture_ids: list[int]) -> str:
    """Return a single MA3 command that sets all fixtures to 0 %."""
    lo, hi = min(fixture_ids), max(fixture_ids)
    return f"Fixture {lo} Thru {hi} At 0"


def set_color_rgb(
    fixture_ids: list[int],
    r: float,
    g: float,
    b: float,
) -> list[str]:
    """Return four separate MA3 commands that select fixtures and set RGB color.

    The first command selects the fixture range at full intensity.
    The next three set each color channel via Attribute commands.
    Each must be sent as a separate /cmd call with a 20 ms gap.

    Parameters
    ----------
    fixture_ids:
        Target fixture ID range.
    r, g, b:
        Color channel values 0 – 255.
    """
    lo, hi = min(fixture_ids), max(fixture_ids)
    r_val = int(max(0, min(255, round(r))))
    g_val = int(max(0, min(255, round(g))))
    b_val = int(max(0, min(255, round(b))))
    return [
        f"Fixture {lo} Thru {hi} At 100",
        f'Attribute "ColorRGB_R" At {r_val}',
        f'Attribute "ColorRGB_G" At {g_val}',
        f'Attribute "ColorRGB_B" At {b_val}',
    ]


def set_intensity_and_color(
    fixture_ids: list[int],
    intensity: float,
    r: float,
    g: float,
    b: float,
) -> list[str]:
    """Return four separate MA3 commands that set intensity and RGB color.

    Parameters
    ----------
    fixture_ids:
        Target fixture ID range.
    intensity:
        Dimmer level 0 – 100.
    r, g, b:
        Color channel values 0 – 255.
    """
    lo, hi = min(fixture_ids), max(fixture_ids)
    pct = max(0.0, min(100.0, intensity))
    r_val = int(max(0, min(255, round(r))))
    g_val = int(max(0, min(255, round(g))))
    b_val = int(max(0, min(255, round(b))))
    return [
        f"Fixture {lo} Thru {hi} At {pct:.0f}",
        f'Attribute "ColorRGB_R" At {r_val}',
        f'Attribute "ColorRGB_G" At {g_val}',
        f'Attribute "ColorRGB_B" At {b_val}',
    ]


# ================================================================== Client dispatchers

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


def set_master_dimmer(client: MA3OscClient, page: int, fader: int, level: float) -> None:
    """Set the master dimmer fader to *level* (0.0 – 1.0)."""
    client.set_fader(page, fader, level)


def run_macro(client: MA3OscClient, macro: int) -> None:
    """Execute MA3 macro *macro* by number."""
    client.send_command(f"Do Macro {macro}")


def clear_all(client: MA3OscClient) -> None:
    """Clear programmer on the MA3 console."""
    client.send_command("Clear")


def blackout_on(client: MA3OscClient, page: int, key: int) -> None:
    """Press the Grand Master blackout key."""
    client.hold_key(page, key, pressed=True)


def blackout_off(client: MA3OscClient, page: int, key: int) -> None:
    """Release the Grand Master blackout key."""
    client.hold_key(page, key, pressed=False)
