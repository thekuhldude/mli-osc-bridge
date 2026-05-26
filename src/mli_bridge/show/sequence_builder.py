"""Create and label the playback sequence in grandMA3.

The bridge uses a single MA3 sequence as its main playback vehicle.
The CueEngine will jump to specific cue numbers during playback.
"""
from __future__ import annotations

from loguru import logger

from mli_bridge.osc.client import MA3OscClient
from mli_bridge.osc.commands import (
    sequence_assign_executor,
    sequence_goto_cue,
    sequence_label,
    sequence_store_cue,
)
from mli_bridge.settings import BridgeSettings


def build_sequence(
    client: MA3OscClient,
    settings: BridgeSettings,
    seq_number: int = 1,
) -> None:
    """Create a blank sequence, label it, and assign it to an executor.

    Parameters
    ----------
    client:
        OSC client connected to MA3.
    settings:
        Bridge settings (show_name, executor_page, sequence_start_cue, …).
    seq_number:
        MA3 sequence number to use (default: 1).
    """
    # Store an initial 'blackout' cue so the sequence is not empty
    client.send_command("Clear")
    sequence_store_cue(client, seq_number, settings.sequence_start_cue)
    sequence_label(client, seq_number, settings.show_name)
    sequence_assign_executor(client, seq_number, settings.master_fader)

    logger.info(
        "Sequence {} '{}' created, assigned to executor {}, page {}",
        seq_number,
        settings.show_name,
        settings.master_fader,
        settings.executor_page,
    )
