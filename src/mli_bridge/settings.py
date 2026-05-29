"""BridgeSettings — all tuneable knobs in one place.

All fields are readable from environment variables or a .env file.
Field names map to env vars by uppercasing: ma3_host → MA3_HOST.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BridgeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ MA3
    ma3_host: str = Field(default="127.0.0.1", description="grandMA3onPC IP address")
    ma3_port: int = Field(default=8000, description="grandMA3 OSC UDP port")

    # ---------------------------------------------------------------- Audio
    audio_device: str | None = Field(
        default=None,
        description="sounddevice output device name; None = system default",
    )
    fps: int = Field(default=30, description="Frames per second for analysis & OSC grid")

    # -------------------------------------------------------------- Playback
    pre_roll_seconds: float = Field(
        default=2.0,
        description=(
            "Seconds between Go+ Sequence and audio start.  "
            "MA3 cue 1 TrigTime is set to this value so both fire together."
        ),
    )
    playback_loop_interval_ms: float = Field(
        default=1.0,
        description="CueEngine poll interval in milliseconds; lower = more precise",
    )

    # --------------------------------------------------------------- Show
    show_name: str = Field(default="MLI_Show", description="MA3 show / session label")
    executor_page: int = Field(default=1, description="MA3 executor page number")
    master_fader: int = Field(default=1, description="Grandmaster fader number on executor page")
    sequence_start_cue: float = Field(default=1.0, description="First cue number in the MA3 sequence")

    # ----------------------------------------------------------- Fixtures
    default_dimmer_full: int = Field(
        default=100,
        ge=0,
        le=100,
        description="Default 'full' dimmer level in percent",
    )
    default_color_temp: int = Field(
        default=5600,
        description="Default color temperature in Kelvin for white-light fixtures",
    )

    # ----------------------------------------------------- Beat / energy
    beat_flash_brightness: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Peak brightness for beat-flash OSC commands (0-1)",
    )
    energy_dimmer_floor: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Minimum dimmer level even during silence",
    )
    onset_strobe_threshold: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        description="Onset strength (0-1) required to trigger strobe",
    )
    onset_energy_gate: float = Field(
        default=0.55,
        ge=0.0,
        le=1.0,
        description="RMS floor required for onset strobe to fire",
    )


@lru_cache(maxsize=1)
def get_settings() -> BridgeSettings:
    """Return the process-wide singleton settings (cached after first call)."""
    return BridgeSettings()
