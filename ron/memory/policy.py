"""Conservative rules for deciding which user statements deserve long-term memory."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ron.memory.models import MemoryKind


class AutoLearnMode(StrEnum):
    OFF = "off"
    CONSERVATIVE = "conservative"


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    content: str
    kind: MemoryKind = MemoryKind.KNOWLEDGE
    project: str | None = None
    importance: int = 65
    metadata: dict[str, Any] = field(default_factory=dict)


_SECRET_PATTERNS = (
    re.compile(r"\bpassword\b", re.IGNORECASE),
    re.compile(r"\bpasscode\b", re.IGNORECASE),
    re.compile(r"\b(?:api|secret|private)\s+key\b", re.IGNORECASE),
    re.compile(r"\b(?:access|refresh|auth(?:entication)?)\s+token\b", re.IGNORECASE),
    re.compile(r"\bone[- ]time\s+(?:password|pin|code)\b", re.IGNORECASE),
    re.compile(r"\botp\b", re.IGNORECASE),
    re.compile(r"\bcvv\b", re.IGNORECASE),
    re.compile(r"\bcredit\s+card\s+(?:number|details?)\b", re.IGNORECASE),
    re.compile(r"\bbank\s+account\s+(?:number|details?)\b", re.IGNORECASE),
)

_TEMPORARY_MARKERS = (
    "right now",
    "at the moment",
    "for now",
    "today",
    "tonight",
    "tomorrow",
    "this morning",
    "this afternoon",
    "this evening",
    "this week",
    "currently",
)

_REQUEST_BOUNDARY = re.compile(
    r"\s*(?:,|;|\band\b)?\s*"
    r"(?:what|how|why|when|where|can|could|would|should|will|do|does|did|please)\b",
    re.IGNORECASE,
)

_EQUIPMENT_PATTERN = re.compile(
    r"\bmy\s+(?P<subject>"
    r"guitar|electric guitar|acoustic guitar|amp|amplifier|laptop|computer|pc|phone|"
    r"tablet|router|microphone|mic|keyboard|drum kit|drums|headphones|speaker"
    r")\s+(?:is|are)\s+(?P<value>[^\n.!?]{2,220})",
    re.IGNORECASE,
)

_NAME_PATTERN = re.compile(
    r"\bmy\s+(?P<subject>preferred name|name)\s+is\s+(?P<value>[^\n.!?]{1,100})",
    re.IGNORECASE,
)

_PREFERENCE_PATTERN = re.compile(
    r"\bI\s+(?P<verb>prefer|like|love|enjoy|dislike|hate)\s+"
    r"(?P<value>[^\n.!?]{2,240})",
    re.IGNORECASE,
)

_INSTRUMENT_PATTERN = re.compile(
    r"\bI\s+play\s+(?P<value>guitar|electric guitar|acoustic guitar|drums|piano|keyboard|bass)"
    r"(?:\s+[^\n.!?]{0,100})?",
    re.IGNORECASE,
)

_PROJECT_PATTERN = re.compile(
    r"\bI(?:'m|\s+am)\s+(?P<verb>working on|building|developing|making)\s+"
    r"(?P<value>[^\n.!?]{2,260})",
    re.IGNORECASE,
)

_RON_GOAL_PATTERN = re.compile(
    r"\bI\s+want\s+Ron\s+to\s+(?P<value>[^\n.!?]{2,260})",
    re.IGNORECASE,
)


class MemoryPolicy:
    """Extract only high-signal, low-risk facts from normal user messages."""

    def __init__(self, mode: AutoLearnMode | None = None) -> None:
        self.mode = mode or _mode_from_environment()

    def candidate_from_user(self, text: str) -> MemoryCandidate | None:
        if self.mode is AutoLearnMode.OFF:
            return None
        clean = " ".join(text.strip().split())
        if len(clean) < 6 or len(clean) > 2_000:
            return None
        if contains_secret(clean) or _looks_temporary(clean):
            return None

        equipment = _EQUIPMENT_PATTERN.search(clean)
        if equipment is not None:
            value = _trim_value(equipment.group("value"))
            if value:
                subject = equipment.group("subject").casefold()
                return MemoryCandidate(
                    content=f"My {subject} is {value}.",
                    importance=72,
                    metadata={"source": "learned", "reason": "stable_equipment_fact"},
                )

        name = _NAME_PATTERN.search(clean)
        if name is not None:
            value = _trim_value(name.group("value"))
            if value:
                return MemoryCandidate(
                    content=f"My name is {value}.",
                    importance=80,
                    metadata={"source": "learned", "reason": "stable_identity_fact"},
                )

        preference = _PREFERENCE_PATTERN.search(clean)
        if preference is not None:
            value = _trim_value(preference.group("value"))
            if value:
                verb = preference.group("verb").casefold()
                return MemoryCandidate(
                    content=f"I {verb} {value}.",
                    importance=66,
                    metadata={"source": "learned", "reason": "preference"},
                )

        instrument = _INSTRUMENT_PATTERN.search(clean)
        if instrument is not None:
            value = _trim_value(instrument.group(0))
            if value:
                return MemoryCandidate(
                    content=_sentence(value),
                    importance=68,
                    metadata={"source": "learned", "reason": "stable_skill_or_hobby"},
                )

        project = _PROJECT_PATTERN.search(clean)
        if project is not None:
            value = _trim_value(project.group("value"))
            if value:
                content = f"I am {project.group('verb').casefold()} {value}."
                project_name = "Ron" if re.search(r"\bRon\b", value, re.IGNORECASE) else None
                return MemoryCandidate(
                    content=content,
                    kind=MemoryKind.PROJECT if project_name else MemoryKind.KNOWLEDGE,
                    project=project_name,
                    importance=72,
                    metadata={"source": "learned", "reason": "active_project"},
                )

        ron_goal = _RON_GOAL_PATTERN.search(clean)
        if ron_goal is not None:
            value = _trim_value(ron_goal.group("value"))
            if value:
                return MemoryCandidate(
                    content=f"I want Ron to {value}.",
                    kind=MemoryKind.PROJECT,
                    project="Ron",
                    importance=74,
                    metadata={"source": "learned", "reason": "ron_project_requirement"},
                )
        return None


def contains_secret(text: str) -> bool:
    """Return True for credential-like content Ron should never persist as memory."""
    return any(pattern.search(text) is not None for pattern in _SECRET_PATTERNS)


def _looks_temporary(text: str) -> bool:
    folded = text.casefold()
    return any(marker in folded for marker in _TEMPORARY_MARKERS)


def _trim_value(value: str) -> str:
    clean = " ".join(value.strip(" ,;:-").split())
    boundary = _REQUEST_BOUNDARY.search(clean)
    if boundary is not None and boundary.start() >= 2:
        clean = clean[: boundary.start()].rstrip(" ,;:-")
    return clean[:300].strip()


def _sentence(value: str) -> str:
    clean = value.strip()
    return clean if clean.endswith((".", "!", "?")) else f"{clean}."


def _mode_from_environment() -> AutoLearnMode:
    raw = os.getenv("RON_MEMORY_AUTO_LEARN", AutoLearnMode.CONSERVATIVE.value).strip().casefold()
    try:
        return AutoLearnMode(raw)
    except ValueError:
        return AutoLearnMode.CONSERVATIVE
