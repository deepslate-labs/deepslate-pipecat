"""
Deepslate Pipecat Integration
A Pipecat service for integrating Deepslate's Realtime Speech-to-Speech Opal model.
"""

from .options import DeepslateOptions, DeepslateVadConfig, ElevenLabsLocation, ElevenLabsTtsConfig
from .service import DeepslateRealtimeLLMService

__all__ = [
    "DeepslateOptions",
    "DeepslateVadConfig",
    "ElevenLabsLocation",
    "ElevenLabsTtsConfig",
    "DeepslateRealtimeLLMService",
]