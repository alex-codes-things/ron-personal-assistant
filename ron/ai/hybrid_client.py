"""Cloud-first inference with a cold local fallback."""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping
from threading import RLock

from ron.ai.client import AIClient
from ron.ai.errors import AIConnectionError, AIError, InferenceCancelled
from ron.ai.ollama_client import InferenceResult, TokenHandler


class HybridAIClient:
    """Use cloud inference normally and wake Ollama only after a cloud outage."""

    is_local = False

    def __init__(self, primary: AIClient, fallback: AIClient) -> None:
        self.primary = primary
        self.fallback = fallback
        self.settings = primary.settings
        self._last_provider = primary.provider_label
        self._lock = RLock()

    @property
    def provider_label(self) -> str:
        with self._lock:
            active = self._last_provider
        return f"{active}; local fallback available"

    def version(self) -> str:
        return self.primary.version()

    def has_configured_model(self) -> bool:
        return self.primary.has_configured_model()

    def preload(self) -> None:
        """Do not load the fallback model onto the laptop during normal startup."""
        self.primary.preload()

    def stream_chat(
        self,
        messages: Iterable[Mapping[str, str]],
        *,
        on_token: TokenHandler | None = None,
        think: bool = False,
        max_output_tokens: int = 128,
        temperature: float = 0.2,
        cancel_event: threading.Event | None = None,
    ) -> InferenceResult:
        message_list = list(messages)
        visible_output = False

        def receive(token: str) -> None:
            nonlocal visible_output
            visible_output = visible_output or bool(token)
            if on_token is not None:
                on_token(token)

        try:
            result = self.primary.stream_chat(
                message_list,
                on_token=receive,
                think=think,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                cancel_event=cancel_event,
            )
        except InferenceCancelled:
            raise
        except AIConnectionError as cloud_error:
            if visible_output:
                raise
            try:
                result = self.fallback.stream_chat(
                    message_list,
                    on_token=on_token,
                    think=think,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                    cancel_event=cancel_event,
                )
            except AIError as fallback_error:
                raise AIConnectionError(
                    "Cloud AI was unavailable and the local fallback could not start "
                    f"({type(fallback_error).__name__})."
                ) from cloud_error
            with self._lock:
                self._last_provider = f"{self.fallback.provider_label} (fallback in use)"
            return result
        with self._lock:
            self._last_provider = self.primary.provider_label
        return result
