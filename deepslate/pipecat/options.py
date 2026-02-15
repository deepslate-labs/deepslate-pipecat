import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class DeepslateOptions:
    """Core Deepslate connection and model options."""

    vendor_id: str
    """Deepslate vendor ID."""

    organization_id: str
    """Deepslate organization ID."""

    api_key: str
    """Deepslate API key."""

    base_url: str = "https://app.deepslate.eu"
    """Base URL for the Deepslate API."""

    system_prompt: str = "You are a helpful assistant."
    """System prompt dictating the behavior of the model."""

    ws_url: Optional[str] = None
    """Optional direct WebSocket URL (bypasses standard auth URL construction, useful for local testing)."""

    max_retries: int = 3
    """Maximum number of reconnection attempts before giving up (default: 3)."""

    @classmethod
    def from_env(
        cls,
        vendor_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        api_key: Optional[str] = None,
        **kwargs
    ) -> "DeepslateOptions":
        """Create options, falling back to DEEPSLATE_... environment variables."""
        resolved_vendor_id = vendor_id or os.environ.get("DEEPSLATE_VENDOR_ID")
        if not resolved_vendor_id:
            raise ValueError(
                "Deepslate vendor ID required. Provide vendor_id or set DEEPSLATE_VENDOR_ID env var."
            )

        resolved_org_id = organization_id or os.environ.get("DEEPSLATE_ORGANIZATION_ID")
        if not resolved_org_id:
            raise ValueError(
                "Deepslate organization ID required. Provide organization_id or set DEEPSLATE_ORGANIZATION_ID env var."
            )

        resolved_api_key = api_key or os.environ.get("DEEPSLATE_API_KEY")
        if not resolved_api_key:
            raise ValueError(
                "Deepslate API key required. Provide api_key or set DEEPSLATE_API_KEY env var."
            )

        return cls(
            vendor_id=resolved_vendor_id,
            organization_id=resolved_org_id,
            api_key=resolved_api_key,
            **kwargs
        )


@dataclass
class DeepslateVadConfig:
    """Voice Activity Detection (VAD) configuration handled server-side by Deepslate."""

    confidence_threshold: float = 0.5
    """Minimum confidence required to consider audio as speech (0.0 to 1.0)."""

    min_volume: float = 0.01
    """Minimum volume level to consider audio as speech (0.0 to 1.0)."""

    start_duration_ms: int = 200
    """Duration of speech to detect start of speech (milliseconds)."""

    stop_duration_ms: int = 500
    """Duration of silence to detect end of speech (milliseconds)."""

    backbuffer_duration_ms: int = 1000
    """Duration of audio to buffer before speech detection (milliseconds)."""


@dataclass
class ElevenLabsTtsConfig:
    """
    ElevenLabs TTS configuration for Deepslate-hosted TTS.
    When provided, audio output is enabled directly from Deepslate.
    """

    api_key: str
    """ElevenLabs API key."""

    voice_id: str
    """Voice ID (e.g., '21m00Tcm4TlvDq8ikWAM' for Rachel)."""

    model_id: Optional[str] = None
    """Model ID (e.g., 'eleven_turbo_v2'). Uses ElevenLabs default if not set."""

    @classmethod
    def from_env(
        cls,
        api_key: Optional[str] = None,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> "ElevenLabsTtsConfig":
        """Create config, falling back to ELEVENLABS_... environment variables."""
        resolved_api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")
        if not resolved_api_key:
            raise ValueError(
                "ElevenLabs API key required. Provide api_key or set ELEVENLABS_API_KEY env var."
            )

        resolved_voice_id = voice_id or os.environ.get("ELEVENLABS_VOICE_ID")
        if not resolved_voice_id:
            raise ValueError(
                "ElevenLabs voice ID required. Provide voice_id or set ELEVENLABS_VOICE_ID env var."
            )

        resolved_model_id = model_id or os.environ.get("ELEVENLABS_MODEL_ID")

        return cls(
            api_key=resolved_api_key,
            voice_id=resolved_voice_id,
            model_id=resolved_model_id,
        )