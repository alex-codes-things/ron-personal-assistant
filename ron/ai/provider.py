"""Select Ron's AI provider once after the project environment is loaded."""

from __future__ import annotations

import os

from ron.ai.client import AIClient
from ron.ai.groq_client import GroqClient
from ron.ai.hybrid_client import HybridAIClient
from ron.ai.ollama_client import OllamaClient
from ron.ai.openai_client import OpenAIClient
from ron.ai.settings import (
    AIProviderSettings,
    CloudAISettings,
    GroqAISettings,
    SettingsError,
)


def build_ai_client(settings: AIProviderSettings | None = None) -> AIClient:
    """Build a cloud-first, local-only, or automatically selected client."""
    provider = settings or AIProviderSettings.from_environment()
    groq_key_present = bool(os.getenv("GROQ_API_KEY", "").strip())
    openai_key_present = bool(os.getenv("OPENAI_API_KEY", "").strip())

    if provider.provider == "ollama":
        return OllamaClient()
    if provider.provider == "groq" and not groq_key_present:
        raise SettingsError(
            "RON_AI_PROVIDER=groq requires GROQ_API_KEY in the project .env"
        )
    if provider.provider == "openai" and not openai_key_present:
        raise SettingsError(
            "RON_AI_PROVIDER=openai requires OPENAI_API_KEY in the project .env"
        )

    if provider.provider == "groq" or (provider.provider == "auto" and groq_key_present):
        cloud: AIClient = GroqClient(GroqAISettings.from_environment())
    elif provider.provider == "openai" or (
        provider.provider == "auto" and openai_key_present
    ):
        cloud = OpenAIClient(CloudAISettings.from_environment())
    else:
        return OllamaClient()

    if provider.local_fallback:
        return HybridAIClient(cloud, OllamaClient())
    return cloud
