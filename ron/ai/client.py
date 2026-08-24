"""The small interface shared by Ron's local, cloud, and hybrid AI clients."""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping
from typing import Protocol

from ron.ai.ollama_client import InferenceResult, TokenHandler


class AIClient(Protocol):
    """Only the inference operations Ron's higher layers are allowed to use."""

    settings: object
    is_local: bool

    @property
    def provider_label(self) -> str: ...

    def stream_chat(
        self,
        messages: Iterable[Mapping[str, str]],
        *,
        on_token: TokenHandler | None = None,
        think: bool = False,
        max_output_tokens: int = 128,
        temperature: float = 0.2,
        cancel_event: threading.Event | None = None,
    ) -> InferenceResult: ...

    def version(self) -> str: ...

    def has_configured_model(self) -> bool: ...

    def preload(self) -> None: ...
