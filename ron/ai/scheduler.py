"""One priority-aware gate for Ron's routed model requests."""

from __future__ import annotations

import heapq
import itertools
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from enum import IntEnum

from ron.ai.client import AIClient
from ron.ai.ollama_client import InferenceResult, TokenHandler


class InferencePriority(IntEnum):
    TRANSCRIPTION = 0
    SPEECH = 1
    CONVERSATION = 2
    ROUTING = 3
    PLANNING = 4
    BACKGROUND = 5


class InferenceScheduler:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._waiting: list[tuple[int, int, object]] = []
        self._sequence = itertools.count()
        self._active = False
        self._last_success_at: float | None = None
        self._last_error: str | None = None

    def run[T](self, priority: InferencePriority, operation: Callable[[], T]) -> T:
        ticket = object()
        with self._condition:
            heapq.heappush(self._waiting, (int(priority), next(self._sequence), ticket))
            while self._active or self._waiting[0][2] is not ticket:
                self._condition.wait()
            heapq.heappop(self._waiting)
            self._active = True
        try:
            result = operation()
            with self._condition:
                self._last_success_at = time.time()
                self._last_error = None
            return result
        except Exception as error:
            with self._condition:
                self._last_error = type(error).__name__
            raise
        finally:
            with self._condition:
                self._active = False
                self._condition.notify_all()

    def status(self) -> tuple[bool, int]:
        with self._condition:
            return self._active, len(self._waiting)

    def health_label(self) -> str:
        with self._condition:
            if self._last_error is not None:
                return f"last request failed ({self._last_error})"
            if self._last_success_at is not None:
                return "ready"
            return "not checked yet"


class ScheduledAIClient:
    """Provider-neutral view with a fixed priority for one subsystem."""

    def __init__(
        self,
        client: AIClient,
        scheduler: InferenceScheduler,
        priority: InferencePriority,
    ) -> None:
        self.client = client
        self.scheduler = scheduler
        self.priority = priority
        self.settings = client.settings
        self.is_local = client.is_local

    @property
    def provider_label(self) -> str:
        return self.client.provider_label

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
        return self.scheduler.run(
            self.priority,
            lambda: self.client.stream_chat(
                message_list,
                on_token=on_token,
                think=think,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                cancel_event=cancel_event,
            ),
        )

    def version(self) -> str:
        return self.scheduler.run(self.priority, self.client.version)

    def has_configured_model(self) -> bool:
        return self.scheduler.run(self.priority, self.client.has_configured_model)

    def preload(self) -> None:
        self.scheduler.run(self.priority, self.client.preload)


# Compatibility for extensions which imported the old v0.10 class name.
ScheduledOllamaClient = ScheduledAIClient
