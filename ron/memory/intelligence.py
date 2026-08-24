"""Natural-language controls for Ron's explicit long-term memory."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from ron.memory.models import MemoryRecord
from ron.memory.service import MemoryService


class MemoryIntent(StrEnum):
    REMEMBER = "remember"
    RECALL = "recall"
    LIST = "list"
    FORGET = "forget"


@dataclass(frozen=True, slots=True)
class ParsedMemoryIntent:
    intent: MemoryIntent
    value: str = ""


@dataclass(slots=True)
class _ForgetState:
    candidates: tuple[MemoryRecord, ...]
    selected: MemoryRecord | None = None


class MemoryIntelligence:
    """Handle explicit memory actions without sending them through the LLM."""

    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory
        self._forget: _ForgetState | None = None

    def claims_interaction(self, prompt: str) -> bool:
        if self._forget is not None:
            clean = _normalise(prompt)
            if clean in _CANCEL_WORDS or clean in _CONFIRM_WORDS or clean in _DENY_WORDS:
                return True
            if re.fullmatch(r"(?:forget\s+)?\d+", clean):
                return True
        return parse_memory_intent(prompt) is not None

    def handle(self, prompt: str) -> str | None:
        if self._forget is not None:
            pending = self._handle_pending_forget(prompt)
            if pending is not None:
                return pending

        parsed = parse_memory_intent(prompt)
        if parsed is None:
            return None
        if parsed.intent is MemoryIntent.REMEMBER:
            return self._remember(parsed.value)
        if parsed.intent is MemoryIntent.RECALL:
            return self._recall(parsed.value)
        if parsed.intent is MemoryIntent.LIST:
            return self._list_recent()
        return self._begin_forget(parsed.value)

    def _remember(self, value: str) -> str:
        try:
            record, created = self.memory.remember_explicit(value)
        except ValueError as error:
            return str(error)
        if not created:
            return f"I already remember that. Its memory ID starts with {record.memory_id[:8]}."
        if record.queued:
            return (
                "Got it — I'll remember that. External memory is offline, so the new "
                "memory is safely queued on this laptop until RON_STORAGE returns."
            )
        return "Got it — I'll remember that."

    def _recall(self, value: str) -> str:
        clean = value.strip()
        if not clean:
            return self._list_recent()
        memories = self.memory.recall(clean, limit=5)
        if not memories:
            return f"I don't have a saved memory that matches “{clean}” yet."
        lines = ["I found these relevant memories:"]
        for index, item in enumerate(memories, 1):
            offline = (
                " (summary only — external memory is offline)"
                if not item.full_content_available
                else ""
            )
            lines.append(f"{index}. {item.content}{offline}")
        return "\n".join(lines)

    def _list_recent(self) -> str:
        records = self.memory.recent_user_memories(limit=8)
        if not records:
            return "I don't have any durable user memories saved yet."
        lines = ["Here are my most recent durable memories:"]
        for index, record in enumerate(records, 1):
            lines.append(
                f"{index}. {record.summary} [{record.kind.value}, {record.memory_id[:8]}]"
            )
        return "\n".join(lines)

    def _begin_forget(self, value: str) -> str:
        clean = value.strip()
        if not clean:
            return "Tell me what memory you want me to forget."
        candidates = self.memory.find_for_forget(clean, limit=5)
        if not candidates:
            return f"I couldn't find a saved memory matching “{clean}”."
        if len(candidates) == 1:
            selected = candidates[0]
            self._forget = _ForgetState(candidates, selected)
            return (
                f"I found this memory: “{selected.summary}”\n"
                "Forgetting is permanent. Say “yes, forget it” to delete it, or “cancel”."
            )
        self._forget = _ForgetState(candidates)
        lines = ["I found a few possible memories. Which one should I forget?"]
        for index, record in enumerate(candidates, 1):
            lines.append(f"{index}. {record.summary} [{record.memory_id[:8]}]")
        lines.append('Say “forget 2” (for example), or “cancel”.')
        return "\n".join(lines)

    def _handle_pending_forget(self, prompt: str) -> str | None:
        state = self._forget
        if state is None:
            return None
        clean = _normalise(prompt)
        if clean in _CANCEL_WORDS:
            self._forget = None
            return "Okay — I didn't forget anything."

        if state.selected is not None:
            if clean in _CONFIRM_WORDS:
                selected = state.selected
                self._forget = None
                try:
                    self.memory.forget(selected.memory_id)
                except KeyError:
                    return "That memory was already gone."
                return f"Done — I permanently forgot: “{selected.summary}”"
            if clean in _DENY_WORDS:
                self._forget = None
                return "Okay — I kept the memory."
            self._forget = None
            return None

        match = re.fullmatch(r"(?:forget\s+)?(?P<number>\d+)", clean)
        if match is None:
            self._forget = None
            return None
        index = int(match.group("number")) - 1
        if not 0 <= index < len(state.candidates):
            return f"Choose a number from 1 to {len(state.candidates)}, or say “cancel”."
        selected = state.candidates[index]
        state.selected = selected
        return (
            f"You chose: “{selected.summary}”\n"
            "Forgetting is permanent. Say “yes, forget it” to delete it, or “cancel”."
        )


_REMEMBER_PATTERNS = (
    re.compile(
        r"^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?remember\s+that\s+"
        r"(?P<value>.+)$",
        re.IGNORECASE,
    ),
    re.compile(r"^remember\s+this\s*[:,-]?\s*(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"^remember\s*:\s*(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"^save\s+(?:this\s+)?to\s+memory\s*[:,-]?\s*(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"^keep\s+(?:this\s+)?in\s+mind\s*[:,-]?\s*(?P<value>.+)$", re.IGNORECASE),
)

_RECALL_PATTERNS = (
    re.compile(
        r"^(?:(?:can|could|would)\s+you\s+(?:tell\s+me\s+)?)?what\s+do\s+you\s+"
        r"remember\s+about\s+(?P<value>.+?)[?.!]*$",
        re.IGNORECASE,
    ),
    re.compile(r"^do\s+you\s+remember\s+(?P<value>.+?)[?.!]*$", re.IGNORECASE),
    re.compile(r"^recall\s+(?P<value>.+?)[?.!]*$", re.IGNORECASE),
    re.compile(
        r"^(?:show|find)\s+(?:me\s+)?memories\s+about\s+(?P<value>.+?)[?.!]*$",
        re.IGNORECASE,
    ),
    re.compile(r"^remember\s+when\s+(?P<value>.+?)[?.!]*$", re.IGNORECASE),
)

_FORGET_PATTERNS = (
    re.compile(
        r"^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?forget\s+that\s+"
        r"(?P<value>.+?)[?.!]*$",
        re.IGNORECASE,
    ),
    re.compile(r"^forget\s+about\s+(?P<value>.+?)[?.!]*$", re.IGNORECASE),
    re.compile(
        r"^forget\s+what\s+I\s+(?:said|told\s+you)\s+about\s+(?P<value>.+?)[?.!]*$",
        re.IGNORECASE,
    ),
    re.compile(r"^forget\s+(?P<value>[0-9a-f]{6,32})$", re.IGNORECASE),
    re.compile(r"^remove\s+(?:the\s+)?memory\s+about\s+(?P<value>.+?)[?.!]*$", re.IGNORECASE),
)

_LIST_PATTERNS = {
    "what do you remember",
    "what do you remember?",
    "show memories",
    "show me memories",
    "list memories",
    "memory list",
}

_CONFIRM_WORDS = {
    "yes",
    "yes forget it",
    "yes, forget it",
    "confirm",
    "confirm forget",
    "delete it",
}
_DENY_WORDS = {"no", "nope", "keep it", "dont forget it", "don't forget it"}
_CANCEL_WORDS = {"cancel", "never mind", "nevermind", "stop"}


def parse_memory_intent(prompt: str) -> ParsedMemoryIntent | None:
    clean = " ".join(prompt.strip().split())
    if not clean:
        return None
    if clean.casefold() in _LIST_PATTERNS:
        return ParsedMemoryIntent(MemoryIntent.LIST)
    for pattern in _REMEMBER_PATTERNS:
        match = pattern.match(clean)
        if match is not None:
            value = match.group("value").strip()
            if value.endswith("?"):
                value = value[:-1].rstrip()
            return ParsedMemoryIntent(MemoryIntent.REMEMBER, value)
    for pattern in _RECALL_PATTERNS:
        match = pattern.match(clean)
        if match is not None:
            return ParsedMemoryIntent(MemoryIntent.RECALL, match.group("value").strip())
    for pattern in _FORGET_PATTERNS:
        match = pattern.match(clean)
        if match is not None:
            return ParsedMemoryIntent(MemoryIntent.FORGET, match.group("value").strip())
    return None


def _normalise(value: str) -> str:
    return " ".join(value.strip().casefold().strip(" .!?").split())
