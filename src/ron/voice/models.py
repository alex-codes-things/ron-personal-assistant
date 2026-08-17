"""Typed messages shared by Ron's voice components."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic


class VoiceState(StrEnum):
    """Small, user-facing lifecycle states for the optional voice input."""

    DISABLED = "disabled"
    STARTING = "starting"
    READY = "ready"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    PROCESSING = "processing"
    RETRYING = "retrying"
    OFFLINE = "offline"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    """One complete local transcription, never a partial audio chunk."""

    text: str
    confidence: float
    duration_seconds: float
    no_speech_probability: float = 0.0


@dataclass(frozen=True, slots=True)
class VoiceInput:
    """Validated voice input passed into the same assistant used by the terminal."""

    raw_text: str
    text: str
    confidence: float
    wake_phrase: str | None
    received_at: float = field(default_factory=monotonic)


@dataclass(frozen=True, slots=True)
class VoiceReply:
    """Result returned to the voice loop after Ron handles a prompt."""

    text: str
    continue_listening: bool = False


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    """Auditable correction result produced before routing or execution."""

    raw_text: str
    text: str
    wake_phrase: str | None
    accepted: bool
    waiting_for_command: bool = False
    clarification: str | None = None
    correction_notes: tuple[str, ...] = ()
