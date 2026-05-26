"""ShowInitializer — orchestrates the full MA3 show setup sequence.

Call ``ShowInitializer(client, patch, settings).run()`` once before
starting playback.  It is safe to call multiple times (idempotent).

Steps
-----
1. Test the OSC connection (non-fatal warning on failure).
2. Build groups from the patch.
3. Build colour presets.
4. Create / label the main playback sequence.
5. Set the grandmaster to full.
"""
from __future__ import annotations

import time

from loguru import logger

from mli_bridge.osc.client import MA3OscClient
from mli_bridge.osc.commands import set_master_dimmer
from mli_bridge.settings import BridgeSettings
from mli_bridge.show.fixture_patch import FixturePatch
from mli_bridge.show.group_builder import build_groups
from mli_bridge.show.preset_builder import build_color_presets
from mli_bridge.show.sequence_builder import build_sequence


class ShowInitializer:
    def __init__(
        self,
        client: MA3OscClient,
        patch: FixturePatch,
        settings: BridgeSettings,
    ) -> None:
        self._client = client
        self._patch = patch
        self._settings = settings

    def run(self) -> None:
        """Execute all setup steps in order."""
        logger.info("=== Show initialisation start ===")
        s = self._settings
        c = self._client

        # 1. Quick ping
        try:
            from mli_bridge.osc.connection_test import ping
            ok = ping(c, timeout_s=1.0)
            if not ok:
                logger.warning(
                    "Ping to MA3 failed — continuing anyway (UDP is fire-and-forget)"
                )
        except Exception as exc:
            logger.warning("Connection check raised: {} — continuing", exc)

        # 2. Groups
        build_groups(c, self._patch)
        time.sleep(0.05)   # give MA3 a moment between bursts

        # 3. Presets
        build_color_presets(c, self._patch)
        time.sleep(0.05)

        # 4. Sequence
        build_sequence(c, s)
        time.sleep(0.05)

        # 5. Master to full
        set_master_dimmer(c, s.executor_page, s.master_fader, 1.0)

        logger.info("=== Show initialisation complete ===")
