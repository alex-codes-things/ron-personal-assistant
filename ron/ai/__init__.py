"""Local, cloud, and hybrid AI building blocks for Ron."""

from ron.ai.client import AIClient
from ron.ai.errors import (
    AIAuthenticationError,
    AIConnectionError,
    AIError,
    AIProtocolError,
    InferenceCancelled,
)
from ron.ai.groq_client import (
    GroqAuthenticationError,
    GroqClient,
    GroqConnectionError,
    GroqError,
    GroqProtocolError,
)
from ron.ai.hybrid_client import HybridAIClient
from ron.ai.ollama_client import (
    InferenceMetrics,
    InferenceResult,
    OllamaClient,
    OllamaConnectionError,
    OllamaError,
    OllamaProtocolError,
)
from ron.ai.openai_client import (
    OpenAIAuthenticationError,
    OpenAIClient,
    OpenAIConnectionError,
    OpenAIError,
    OpenAIProtocolError,
)
from ron.ai.provider import build_ai_client
from ron.ai.scheduler import (
    InferencePriority,
    InferenceScheduler,
    ScheduledAIClient,
    ScheduledOllamaClient,
)
from ron.ai.settings import (
    AIProviderSettings,
    CloudAISettings,
    GroqAISettings,
    LocalAISettings,
    SettingsError,
)

__all__ = [
    "AIAuthenticationError",
    "AIClient",
    "AIConnectionError",
    "AIError",
    "AIProtocolError",
    "AIProviderSettings",
    "CloudAISettings",
    "GroqAISettings",
    "GroqAuthenticationError",
    "GroqClient",
    "GroqConnectionError",
    "GroqError",
    "GroqProtocolError",
    "HybridAIClient",
    "InferenceCancelled",
    "InferenceMetrics",
    "InferenceResult",
    "InferencePriority",
    "InferenceScheduler",
    "LocalAISettings",
    "OllamaClient",
    "OllamaConnectionError",
    "OllamaError",
    "OllamaProtocolError",
    "OpenAIAuthenticationError",
    "OpenAIClient",
    "OpenAIConnectionError",
    "OpenAIError",
    "OpenAIProtocolError",
    "ScheduledAIClient",
    "ScheduledOllamaClient",
    "SettingsError",
    "build_ai_client",
]
