"""Local and cloud-neutral AI building blocks for Ron."""

from ron.ai.ollama_client import (
    InferenceMetrics,
    InferenceResult,
    OllamaClient,
    OllamaConnectionError,
    OllamaError,
    OllamaProtocolError,
)
from ron.ai.settings import LocalAISettings, SettingsError
from ron.ai.scheduler import InferencePriority, InferenceScheduler, ScheduledOllamaClient

__all__ = [
    "InferenceMetrics",
    "InferenceResult",
    "InferencePriority",
    "InferenceScheduler",
    "LocalAISettings",
    "OllamaClient",
    "OllamaConnectionError",
    "OllamaError",
    "OllamaProtocolError",
    "ScheduledOllamaClient",
    "SettingsError",
]
