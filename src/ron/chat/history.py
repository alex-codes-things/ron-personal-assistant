"""Small bounded conversation history for responsive local chat."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    user: str
    assistant: str

    @property
    def character_count(self) -> int:
        return len(self.user) + len(self.assistant)


class ConversationHistory:
    """Keep recent complete turns without allowing context to grow forever."""

    def __init__(
        self,
        *,
        ready_turn_limit: int = 4,
        continuous_turn_limit: int = 16,
        character_limit: int = 48_000,
    ) -> None:
        if not 1 <= ready_turn_limit <= continuous_turn_limit <= 100:
            raise ValueError("Conversation turn limits are invalid")
        if not 1_000 <= character_limit <= 250_000:
            raise ValueError("Conversation character limit is invalid")
        self._ready_turn_limit = ready_turn_limit
        self._continuous_turn_limit = continuous_turn_limit
        self._character_limit = character_limit
        self._turns: list[ConversationTurn] = []
        self._continuous = False
        self._lock = RLock()

    @property
    def continuous(self) -> bool:
        with self._lock:
            return self._continuous

    @property
    def turn_count(self) -> int:
        with self._lock:
            return len(self._turns)

    def set_continuous(self, enabled: bool) -> None:
        with self._lock:
            self._continuous = enabled
            self._trim()

    def clear(self) -> None:
        with self._lock:
            self._turns.clear()

    def record(self, user: str, assistant: str) -> None:
        if not user or not assistant:
            raise ValueError("A conversation turn cannot be empty")
        with self._lock:
            self._turns.append(ConversationTurn(user=user, assistant=assistant))
            self._trim()

    def messages(self) -> list[dict[str, str]]:
        with self._lock:
            turns = tuple(self._turns)
        result: list[dict[str, str]] = []
        for turn in turns:
            result.append({"role": "user", "content": turn.user})
            result.append({"role": "assistant", "content": turn.assistant})
        return result

    def _trim(self) -> None:
        turn_limit = (
            self._continuous_turn_limit if self._continuous else self._ready_turn_limit
        )
        while len(self._turns) > turn_limit:
            self._turns.pop(0)
        character_count = sum(turn.character_count for turn in self._turns)
        while self._turns and character_count > self._character_limit:
            character_count -= self._turns.pop(0).character_count
