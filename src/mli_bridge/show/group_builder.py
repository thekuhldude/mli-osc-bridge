"""Create MA3 groups from the fixture patch via OSC commands."""
from __future__ import annotations

import time

from loguru import logger

from mli_bridge.osc.client import MA3OscClient
from mli_bridge.show.fixture_patch import FixturePatch


def build_groups(client: MA3OscClient, patch: FixturePatch) -> None:
    """For every group in *patch*, send individual fixture-select commands
    to MA3 then store the group.

    Each fixture is selected with its own ``/cmd`` call (with a 50 ms
    pause between them) so MA3 processes each fixture before the next
    is added to the selection.  A single compound ``Fixture 1 + 2`` string
    can be silently ignored by MA3 when the selection engine is busy.

    After storing the group the programmer is cleared so the next group
    starts with a clean slate.

    This is idempotent: calling it on an existing show just re-assigns
    fixtures to the named groups, which is harmless.
    """
    for group in patch.groups:
        if not group.fixture_ids:
            logger.warning(
                "Group '{}' has no fixture_ids — skipping "
                "(add fixture_ids: [...] to the group in your patch YAML)",
                group.name,
            )
            continue

        logger.info(
            "Building group '{}' (MA3 group {}) with fixture IDs: {}",
            group.label,
            group.ma3_group_id,
            group.fixture_ids,
        )

        # Select each fixture individually so MA3's selection engine
        # processes them one at a time.
        for fid in group.fixture_ids:
            client.send_command(f"Fixture {fid}")
            time.sleep(0.05)

        # Store the current selection as a named group.
        client.send_command(f'Store Group {group.ma3_group_id} "{group.label}"')
        time.sleep(0.05)

        # Clear programmer so the next group starts fresh.
        client.send_command("Clear")
        time.sleep(0.05)

        logger.info(
            "Stored group '{}' → MA3 Group {} ({} fixtures)",
            group.label,
            group.ma3_group_id,
            len(group.fixture_ids),
        )
