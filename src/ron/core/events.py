"""Shared event types passed between Ron's independent systems."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
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
