"""Typed plans and results that cross Ron's agent boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from threading import Event
from time import monotonic
from typing import Any


class ToolArgumentKind(StrEnum):
    ENUM = "enum"
    INTEGER = "integer"
    TEXT = "text"


class ToolRisk(StrEnum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    EXTERNAL = "external"
    DESTRUCTIVE = "destructive"


class ToolStatus(StrEnum):
    READY = "ready"
    SUCCESS = "success"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    CONFIRMATION_REQUIRED = "confirmation_required"
    CLARIFICATION_REQUIRED = "clarification_required"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class AgentPlanSource(StrEnum):
    DETERMINISTIC = "deterministic"
    LOCAL_MODEL = "local_model"
    NONE = "none"


class AgentTaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    RESOLVED = "resolved"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ToolExecutionStopped(RuntimeError):
    """Base class for cooperative tool interruption."""


class ToolCancelled(ToolExecutionStopped):
    pass


class ToolTimedOut(ToolExecutionStopped):
    pass


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    deadline: float
    cancel_event: Event
    max_output_bytes: int = 64 * 1024

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - monotonic())

    def checkpoint(self) -> None:
        if self.cancel_event.is_set():
            raise ToolCancelled("The tool was cancelled")
        if monotonic() >= self.deadline:
            raise ToolTimedOut("The tool exceeded its deadline")


@dataclass(frozen=True, slots=True)
class ToolArgument:
    kind: ToolArgumentKind
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    required: bool = True
    minimum_length: int | None = None
    maximum_length: int | None = None

    def validate(self, name: str, value: Any) -> str | int:
        if self.kind is ToolArgumentKind.ENUM:
            if not isinstance(value, str) or value not in self.choices:
                allowed = ", ".join(self.choices)
                raise ValueError(f"{name} must be one of: {allowed}")
            return value
        if self.kind is ToolArgumentKind.INTEGER:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be a whole number")
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"{name} cannot be below {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise ValueError(f"{name} cannot exceed {self.maximum}")
            return value
        if self.kind is ToolArgumentKind.TEXT:
            if not isinstance(value, str):
                raise ValueError(f"{name} must be text")
            clean_value = value.strip()
            if not clean_value:
                raise ValueError(f"{name} cannot be empty")
            if self.minimum_length is not None and len(clean_value) < self.minimum_length:
                raise ValueError(
                    f"{name} must contain at least {self.minimum_length} characters"
                )
            if self.maximum_length is not None and len(clean_value) > self.maximum_length:
                raise ValueError(
                    f"{name} cannot exceed {self.maximum_length} characters"
                )
            if any(character in clean_value for character in "\r\n\0"):
                raise ValueError(f"{name} contains unsupported control characters")
            return clean_value
        raise ValueError(f"{name} uses an unsupported argument type")

    def schema(self) -> dict[str, object]:
        result: dict[str, object] = {"type": self.kind.value, "required": self.required}
        if self.choices:
            result["choices"] = list(self.choices)
        if self.minimum is not None:
            result["minimum"] = self.minimum
        if self.maximum is not None:
            result["maximum"] = self.maximum
        if self.minimum_length is not None:
            result["minimum_length"] = self.minimum_length
        if self.maximum_length is not None:
            result["maximum_length"] = self.maximum_length
        return result


@dataclass(frozen=True, slots=True)
class AgentPlan:
    tool_name: str | None
    arguments: dict[str, object]
    reason: str
    source: AgentPlanSource


@dataclass(frozen=True, slots=True)
class AgentTaskPlan:
    steps: tuple[AgentPlan, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class AgentTaskSnapshot:
    task_id: int
    prompt: str
    status: AgentTaskStatus
    total_steps: int
    completed_steps: int
    current_tool: str | None
    message: str
    cancel_requested: bool = False
    completed_messages: tuple[str, ...] = ()
    recovered: bool = False
    interaction: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_name: str
    status: ToolStatus
    message: str
    data: dict[str, object] = field(default_factory=dict)
    duration_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class AgentResponse:
    text: str
    plan: AgentPlan
    tool_result: ToolResult | None = None
    plans: tuple[AgentPlan, ...] = ()
    task: AgentTaskSnapshot | None = None
