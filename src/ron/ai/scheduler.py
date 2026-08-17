"""One priority-aware gate for all local model inference."""

from __future__ import annotations

import heapq
import itertools
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from enum import IntEnum
from typing import TypeVar

from ron.ai.ollama_client import InferenceResult, OllamaClient, TokenHandler

T = TypeVar("T")


class InferencePriority(IntEnum):
    TRANSCRIPTION = 0
    CONVERSATION = 1
    ROUTING = 2
    PLANNING = 3
    BACKGROUND = 4


class InferenceScheduler:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._waiting: list[tuple[int, int, object]] = []
        self._sequence = itertools.count()
        self._active = False
        self._last_success_at: float | None = None
        self._last_error: str | None = None

    def run(self, priority: InferencePriority, operation: Callable[[], T]) -> T:
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


class ScheduledOllamaClient:
    """Ollama-compatible view with a fixed priority for one subsystem."""

    def __init__(
        self,
        client: OllamaClient,
        scheduler: InferenceScheduler,
        priority: InferencePriority,
    ) -> None:
        self.client = client
        self.scheduler = scheduler
        self.priority = priority
        self.settings = client.settings

    def stream_chat(
        self,
        messages: Iterable[Mapping[str, str]],
        *,
        on_token: TokenHandler | None = None,
        think: bool = False,
        max_output_tokens: int = 128,
        temperature: float = 0.2,
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
            ),
        )

    def version(self) -> str:
        return self.scheduler.run(self.priority, self.client.version)

    def has_configured_model(self) -> bool:
        return self.scheduler.run(self.priority, self.client.has_configured_model)

    def preload(self) -> None:
        self.scheduler.run(self.priority, self.client.preload)
