"""Memory records kept small enough to index locally."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class MemoryKind(StrEnum):
    CONVERSATION = "conversation"
    KNOWLEDGE = "knowledge"
    PERSON = "person"
    PROJECT = "project"
    EXPERIENCE = "experience"


class VisualCategory(StrEnum):
    CODING = "coding"
    APPLICATION = "application"
    ERROR = "error"
    GENERAL = "general"


class ScreenshotMode(StrEnum):
    OFF = "off"
    ON_REQUEST = "on_request"
    SESSION = "session"


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    memory_id: str
    kind: MemoryKind
    summary: str
    relative_path: str
    created_utc: str
    project: str | None = None
    importance: int = 50
    queued: bool = False
    sha256: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RecalledMemory:
    record: MemoryRecord
    content: str
    full_content_available: bool


@dataclass(frozen=True, slots=True)
class VisualMemoryRecord:
    visual_id: str
    category: VisualCategory
    image_path: str
    analysis_path: str
    created_utc: str
    summary: str | None = None
    application: str | None = None
    project: str | None = None
    queued: bool = False
    image_sha256: str = ""
    analysis_sha256: str = ""
