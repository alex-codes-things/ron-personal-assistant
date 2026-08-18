"""Shared events and the small thread-safe coordinator that connects Ron's systems."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock
from time import monotonic
from typing import Any

class FaceExpression(StrEnum):
    """Expressions understood by every Ron face implementation."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    HAPPY = "happy"
    CONFUSED = "confused"
    ERROR = "error"
    SLEEPING = "sleeping"


class EventType(StrEnum):
    """Events that Ron's coordinator can route between systems."""

    FACE_EXPRESSION = "face.expression"
    SPEECH_STARTED = "speech.started"
    SPEECH_LEVEL = "speech.level"
    SPEECH_ENDED = "speech.ended"
    SHUTDOWN = "system.shutdown"


@dataclass(frozen=True, slots=True)
class RonEvent:
    """One immutable message moving through Ron's coordinator."""

    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=monotonic)


EventHandler = Callable[[RonEvent], None]


class Coordinator:
    """Connect systems without making them import or own one another."""

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[EventHandler]] = defaultdict(list)
        self._lock = RLock()
        self._logger = logging.getLogger(__name__)

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Register a handler once for an event type."""
        with self._lock:
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Remove a handler without failing when it is already absent."""
        with self._lock:
            try:
                self._handlers[event_type].remove(handler)
            except ValueError:
                return

    def publish(self, event: RonEvent) -> None:
        """Deliver an event while isolating failures in individual systems."""
        with self._lock:
            handlers = tuple(self._handlers[event.type])

        for handler in handlers:
            try:
                handler(event)
            except Exception:
                self._logger.exception("A handler failed while processing %s", event.type)
