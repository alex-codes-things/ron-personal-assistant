"""Validated settings for Ron's local inference server."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
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
