"""Create MA3 groups from the fixture patch via OSC commands."""
from __future__ import annotations

from loguru import logger

from mli_bridge.osc.client import MA3OscClient
from mli_bridge.show.fixture_patch import FixturePatch


def build_groups(client: MA3OscClient, patch: FixturePatch) -> None:
    """For every group in *patch*, issue MA3 commands to create the group
    and assign the relevant fixture IDs.

    This is idempotent: calling it on an existing show just re-assigns
    fixtures to the named groups, which is harmless.
    """
    for group in patch.groups:
        fixtures = patch.fixtures_in_group(group.name)
        if not fixtures:
            logger.warning("Group '{}' has no fixtures — skipping", group.name)
            continue

        # Build a fixture selection string: "Fixture 1 + 2 + 3"
        fid_str = " + ".join(f"Fixture {f.id}" for f in fixtures)

        # Select the fixtures, then store them as a group
        client.send_command(fid_str)
        client.send_command(f'Store Group {group.executor} "{group.label}"')
        logger.info(
            "Built group '{}' (executor {}) with {} fixtures",
            group.label,
            group.executor,
            len(fixtures),
        )
