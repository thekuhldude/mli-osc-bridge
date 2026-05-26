"""Create MA3 color presets from the fixture patch via OSC commands."""
from __future__ import annotations

from loguru import logger

from mli_bridge.osc.client import MA3OscClient
from mli_bridge.show.fixture_patch import ColorPreset, FixturePatch


def build_color_presets(client: MA3OscClient, patch: FixturePatch) -> None:
    """Store each colour preset from *patch* into the MA3 preset pool.

    Preset numbering starts at 1 and increments per preset.  The preset
    name is used as the MA3 label so operators can identify them.
    """
    for idx, preset in enumerate(patch.color_presets, start=1):
        _store_color_preset(client, idx, preset)


def _store_color_preset(
    client: MA3OscClient, number: int, preset: ColorPreset
) -> None:
    r, g, b = preset.rgb
    # Select all fixtures, set the colour, store as colour preset
    client.send_command("SelectAll")
    client.send_command(f"Attribute \"Color\" At {r},{g},{b}")
    client.send_command(f'Store ColorPreset {number} "{preset.name}"')
    client.send_command("Clear")
    logger.info("Stored color preset {} '{}' RGB({},{},{})", number, preset.name, r, g, b)
