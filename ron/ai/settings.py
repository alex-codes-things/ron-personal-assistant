"""Validated settings for Ron's local and cloud inference providers."""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse


class SettingsError(ValueError):
    """Raised when an AI setting is invalid or unsafe."""


def _read_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise SettingsError(f"{name} must be a whole number") from error
    if not minimum <= value <= maximum:
        raise SettingsError(f"{name} must be between {minimum} and {maximum}")
    return value


def _read_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = float(raw_value)
    except ValueError as error:
        raise SettingsError(f"{name} must be a number") from error
    if not minimum <= value <= maximum:
        raise SettingsError(f"{name} must be between {minimum} and {maximum}")
    return value


def _read_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name, "true" if default else "false").strip().casefold()
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"{name} must be true or false")


def _normalise_loopback_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme != "http":
        raise SettingsError("RON_OLLAMA_URL must use http on the local computer")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SettingsError("RON_OLLAMA_URL cannot contain credentials, a query, or a fragment")
    if parsed.path not in ("", "/"):
        raise SettingsError("RON_OLLAMA_URL must not contain an API path")
    try:
        port = parsed.port
    except ValueError as error:
        raise SettingsError("RON_OLLAMA_URL contains an invalid port") from error
    if parsed.hostname is None or port is None:
        raise SettingsError("RON_OLLAMA_URL must include a host and port")

    hostname = parsed.hostname.lower()
    if hostname != "localhost":
        try:
            if not ipaddress.ip_address(hostname).is_loopback:
                raise SettingsError("RON_OLLAMA_URL must point to this computer only")
        except ValueError as error:
            raise SettingsError("RON_OLLAMA_URL must point to this computer only") from error

    return value.strip().rstrip("/")


def _normalise_keep_alive(value: int | str) -> int | str:
    if isinstance(value, bool):
        raise SettingsError("RON_MODEL_KEEP_ALIVE must be a duration, not true or false")
    if isinstance(value, int):
        if value < -1:
            raise SettingsError("RON_MODEL_KEEP_ALIVE cannot be less than -1")
        return value
    value = value.strip()
    if value.lstrip("-").isdigit():
        return _normalise_keep_alive(int(value))
    if not value or len(value) > 16:
        raise SettingsError("RON_MODEL_KEEP_ALIVE is invalid")
    if value[-1] not in {"s", "m", "h"} or not value[:-1].isdigit():
        raise SettingsError("RON_MODEL_KEEP_ALIVE must be -1, 0, or a duration such as 10m")
    return value


def _read_keep_alive() -> int | str:
    return _normalise_keep_alive(os.getenv("RON_MODEL_KEEP_ALIVE", "-1"))


@dataclass(frozen=True, slots=True)
class LocalAISettings:
    """Safe local-model configuration loaded from environment variables."""

    model: str = "qwen3.5:4b"
    base_url: str = "http://127.0.0.1:11434"
    keep_alive: int | str = -1
    context_size: int = 8_192
    request_timeout_seconds: float = 120.0
    max_prompt_characters: int = 64_000

    def __post_init__(self) -> None:
        model = self.model.strip()
        if not model or len(model) > 128:
            raise SettingsError("RON_LOCAL_MODEL must contain a valid model name")
        if any(character.isspace() for character in model):
            raise SettingsError("RON_LOCAL_MODEL cannot contain whitespace")
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "base_url", _normalise_loopback_url(self.base_url))
        object.__setattr__(self, "keep_alive", _normalise_keep_alive(self.keep_alive))

        if not 512 <= self.context_size <= 131_072:
            raise SettingsError("context_size must be between 512 and 131072")
        if not 1.0 <= self.request_timeout_seconds <= 600.0:
            raise SettingsError("request_timeout_seconds must be between 1 and 600")
        if not 1_000 <= self.max_prompt_characters <= 1_000_000:
            raise SettingsError("max_prompt_characters must be between 1000 and 1000000")

    @classmethod
    def from_environment(cls) -> LocalAISettings:
        """Load settings once so later environment changes cannot alter a live client."""
        return cls(
            model=os.getenv("RON_LOCAL_MODEL", "qwen3.5:4b"),
            base_url=os.getenv("RON_OLLAMA_URL", "http://127.0.0.1:11434"),
            keep_alive=_read_keep_alive(),
            context_size=_read_int("RON_MODEL_CONTEXT", 8_192, 512, 131_072),
            request_timeout_seconds=_read_float("RON_AI_TIMEOUT", 120.0, 1.0, 600.0),
            max_prompt_characters=_read_int(
                "RON_MAX_PROMPT_CHARACTERS", 64_000, 1_000, 1_000_000
            ),
        )


@dataclass(frozen=True, slots=True)
class CloudAISettings:
    """Bounded OpenAI settings. The API endpoint cannot be redirected by .env."""

    api_key: str = field(repr=False)
    model: str = "gpt-5.4-mini"
    base_url: str = "https://api.openai.com/v1"
    request_timeout_seconds: float = 30.0
    max_prompt_characters: int = 64_000
    reasoning_effort: str = "none"
    project_id: str = ""

    def __post_init__(self) -> None:
        api_key = self.api_key.strip()
        if len(api_key) < 20 or any(character.isspace() for character in api_key):
            raise SettingsError("OPENAI_API_KEY is missing or invalid")
        object.__setattr__(self, "api_key", api_key)
        model = self.model.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model):
            raise SettingsError("RON_OPENAI_MODEL contains an invalid model name")
        object.__setattr__(self, "model", model)
        if self.base_url != "https://api.openai.com/v1":
            raise SettingsError("Ron's cloud client must use the official OpenAI API endpoint")
        if not 1.0 <= self.request_timeout_seconds <= 120.0:
            raise SettingsError("RON_OPENAI_TIMEOUT must be between 1 and 120")
        if not 1_000 <= self.max_prompt_characters <= 1_000_000:
            raise SettingsError("max_prompt_characters must be between 1000 and 1000000")
        effort = self.reasoning_effort.strip().casefold()
        if effort not in {"", "none", "low", "medium", "high"}:
            raise SettingsError(
                "RON_OPENAI_REASONING_EFFORT must be none, low, medium, or high"
            )
        object.__setattr__(self, "reasoning_effort", effort)
        project_id = self.project_id.strip()
        if len(project_id) > 160 or any(character in project_id for character in "\r\n\0"):
            raise SettingsError("OPENAI_PROJECT_ID is invalid")
        object.__setattr__(self, "project_id", project_id)

    @classmethod
    def from_environment(cls) -> CloudAISettings:
        return cls(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("RON_OPENAI_MODEL", "gpt-5.4-mini"),
            request_timeout_seconds=_read_float(
                "RON_OPENAI_TIMEOUT", 30.0, 1.0, 120.0
            ),
            max_prompt_characters=_read_int(
                "RON_MAX_PROMPT_CHARACTERS", 64_000, 1_000, 1_000_000
            ),
            reasoning_effort=os.getenv("RON_OPENAI_REASONING_EFFORT", "none"),
            project_id=os.getenv("OPENAI_PROJECT_ID", ""),
        )


@dataclass(frozen=True, slots=True)
class GroqAISettings:
    """Bounded Groq settings. The API endpoint cannot be redirected by .env."""

    api_key: str = field(repr=False)
    model: str = "openai/gpt-oss-120b"
    base_url: str = "https://api.groq.com/openai/v1"
    request_timeout_seconds: float = 30.0
    max_prompt_characters: int = 24_000
    reasoning_effort: str = "low"

    def __post_init__(self) -> None:
        api_key = self.api_key.strip()
        if len(api_key) < 20 or any(character.isspace() for character in api_key):
            raise SettingsError("GROQ_API_KEY is missing or invalid")
        object.__setattr__(self, "api_key", api_key)
        model = self.model.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", model):
            raise SettingsError("RON_GROQ_MODEL contains an invalid model name")
        object.__setattr__(self, "model", model)
        if self.base_url != "https://api.groq.com/openai/v1":
            raise SettingsError("Ron's Groq client must use the official Groq API endpoint")
        if not 1.0 <= self.request_timeout_seconds <= 120.0:
            raise SettingsError("RON_GROQ_TIMEOUT must be between 1 and 120")
        if not 1_000 <= self.max_prompt_characters <= 100_000:
            raise SettingsError(
                "RON_GROQ_MAX_PROMPT_CHARACTERS must be between 1000 and 100000"
            )
        effort = self.reasoning_effort.strip().casefold()
        if effort not in {"low", "medium", "high"}:
            raise SettingsError(
                "RON_GROQ_REASONING_EFFORT must be low, medium, or high"
            )
        object.__setattr__(self, "reasoning_effort", effort)

    @classmethod
    def from_environment(cls) -> GroqAISettings:
        return cls(
            api_key=os.getenv("GROQ_API_KEY", ""),
            model=os.getenv("RON_GROQ_MODEL", "openai/gpt-oss-120b"),
            request_timeout_seconds=_read_float(
                "RON_GROQ_TIMEOUT", 30.0, 1.0, 120.0
            ),
            max_prompt_characters=_read_int(
                "RON_GROQ_MAX_PROMPT_CHARACTERS", 24_000, 1_000, 100_000
            ),
            reasoning_effort=os.getenv("RON_GROQ_REASONING_EFFORT", "low"),
        )


@dataclass(frozen=True, slots=True)
class AIProviderSettings:
    """Choose cloud, local, or automatic cloud-first inference."""

    provider: str = "auto"
    local_fallback: bool = True

    def __post_init__(self) -> None:
        provider = self.provider.strip().casefold()
        if provider not in {"auto", "groq", "openai", "ollama"}:
            raise SettingsError("RON_AI_PROVIDER must be auto, groq, openai, or ollama")
        object.__setattr__(self, "provider", provider)

    @classmethod
    def from_environment(cls) -> AIProviderSettings:
        return cls(
            provider=os.getenv("RON_AI_PROVIDER", "auto"),
            local_fallback=_read_bool("RON_AI_FALLBACK_LOCAL", True),
        )
