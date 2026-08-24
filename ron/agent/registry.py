"""Allowlisted tool registry with exact schemas and confirmation gates."""

from __future__ import annotations

import inspect
import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import monotonic

from ron.agent.models import (
    ToolArgument,
    ToolCancelled,
    ToolExecutionContext,
    ToolResult,
    ToolRisk,
    ToolStatus,
    ToolTimedOut,
)

type ToolHandler = Callable[..., ToolResult]
type ToolCompensator = Callable[..., ToolResult]
type AvailabilityCheck = Callable[[], tuple[bool, str]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    arguments: Mapping[str, ToolArgument]
    risk: ToolRisk
    handler: ToolHandler
    timeout_seconds: float = 10.0
    requires_confirmation: bool = False
    availability: AvailabilityCheck | None = None
    compensator: ToolCompensator | None = None
    max_output_bytes: int = 64 * 1024
    run_in_background: bool = False

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ValueError("Tool names must contain only letters, numbers, and underscores")
        if not self.description or len(self.description) > 240:
            raise ValueError("Tool description is invalid")
        if not 0.1 <= self.timeout_seconds <= 60.0:
            raise ValueError("Tool timeout must be between 0.1 and 60 seconds")
        if not 1_024 <= self.max_output_bytes <= 1_048_576:
            raise ValueError("Tool output limit must be between 1 KiB and 1 MiB")

    def schema(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": {
                name: argument.schema() for name, argument in self.arguments.items()
            },
            "risk": self.risk.value,
            "requires_confirmation": self.requires_confirmation,
            "run_in_background": self.run_in_background,
        }


class ToolRegistry:
    """The only component authorised to call an agent tool handler."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, tool: ToolSpec) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def schemas(self) -> list[dict[str, object]]:
        return [self._tools[name].schema() for name in self.names()]

    def planner_schemas(self) -> list[dict[str, object]]:
        """Return live capability schemas without granting the planner authority."""
        schemas: list[dict[str, object]] = []
        for name in self.names():
            tool = self._tools[name]
            available = True
            reason = "ready"
            if tool.availability is not None:
                try:
                    available, reason = tool.availability()
                except Exception:
                    available, reason = False, "availability check failed"
            schema = tool.schema()
            schema["available"] = bool(available)
            schema["availability_reason"] = " ".join(str(reason).split())[:160]
            schemas.append(schema)
        return schemas

    def spec(self, tool_name: str) -> ToolSpec | None:
        return self._tools.get(tool_name)

    def preflight(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        *,
        confirmed: bool = False,
    ) -> ToolResult:
        """Validate a tool call without running its handler."""
        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolResult(
                tool_name=tool_name,
                status=ToolStatus.UNSUPPORTED,
                message="I don't have an approved tool for that action yet.",
            )
        try:
            self._validate_arguments(tool, arguments)
        except ValueError as error:
            return ToolResult(
                tool_name=tool_name,
                status=ToolStatus.FAILED,
                message=f"I rejected unsafe or invalid tool arguments: {error}",
            )
        if tool.requires_confirmation and not confirmed:
            return ToolResult(
                tool_name=tool_name,
                status=ToolStatus.CONFIRMATION_REQUIRED,
                message=f"The {tool_name} action requires your confirmation before it can run.",
            )
        if tool.availability is not None:
            try:
                available, reason = tool.availability()
            except Exception:
                available, reason = False, "Its required local integration is unavailable."
            if not available:
                return ToolResult(
                    tool_name=tool_name,
                    status=ToolStatus.UNSUPPORTED,
                    message=reason,
                )
        return ToolResult(
            tool_name=tool_name,
            status=ToolStatus.READY,
            message=f"The {tool_name} step passed preflight validation.",
        )

    def execute(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        *,
        confirmed: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> ToolResult:
        preflight = self.preflight(tool_name, arguments, confirmed=confirmed)
        if preflight.status is not ToolStatus.READY:
            return preflight
        tool = self._tools[tool_name]
        validated = self._validate_arguments(tool, arguments)

        started = monotonic()
        context = ToolExecutionContext(
            deadline=started + tool.timeout_seconds,
            cancel_event=cancel_event or threading.Event(),
            max_output_bytes=tool.max_output_bytes,
        )
        try:
            context.checkpoint()
            result = self._call_handler(tool.handler, validated, context)
            context.checkpoint()
        except ToolCancelled:
            return ToolResult(
                tool_name=tool_name,
                status=ToolStatus.CANCELLED,
                message=f"The {tool_name} step stopped at a safe cancellation point.",
                duration_seconds=monotonic() - started,
            )
        except ToolTimedOut:
            return ToolResult(
                tool_name=tool_name,
                status=ToolStatus.TIMED_OUT,
                message=f"The {tool_name} step reached its safety deadline and stopped.",
                duration_seconds=monotonic() - started,
            )
        except Exception:
            return ToolResult(
                tool_name=tool_name,
                status=ToolStatus.FAILED,
                message=f"The approved {tool_name} tool failed safely without completing.",
                duration_seconds=monotonic() - started,
            )
        bounded = ToolResult(
            tool_name=tool.name,
            status=result.status,
            message=result.message,
            data=result.data,
            duration_seconds=monotonic() - started,
        )
        try:
            encoded = json.dumps(
                {"message": bounded.message, "data": bounded.data},
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError):
            return ToolResult(
                tool_name=tool.name,
                status=ToolStatus.FAILED,
                message=f"The {tool.name} tool returned invalid structured output.",
                duration_seconds=monotonic() - started,
            )
        if len(encoded) > tool.max_output_bytes:
            return ToolResult(
                tool_name=tool.name,
                status=ToolStatus.FAILED,
                message=f"The {tool.name} tool exceeded its output safety limit.",
                duration_seconds=monotonic() - started,
            )
        return bounded

    def compensate(
        self,
        tool_name: str,
        result: ToolResult,
        *,
        cancel_event: threading.Event | None = None,
    ) -> ToolResult | None:
        tool = self._tools.get(tool_name)
        if tool is None or tool.compensator is None:
            return None
        if result.data.get("changed") is False:
            return None
        started = monotonic()
        context = ToolExecutionContext(
            deadline=started + tool.timeout_seconds,
            cancel_event=cancel_event or threading.Event(),
            max_output_bytes=tool.max_output_bytes,
        )
        try:
            return self._call_handler(tool.compensator, result, context)
        except Exception:
            return ToolResult(
                tool_name=tool_name,
                status=ToolStatus.FAILED,
                message=f"The {tool_name} rollback could not complete safely.",
                duration_seconds=monotonic() - started,
            )

    def capability_report(self) -> tuple[tuple[str, bool, str], ...]:
        report: list[tuple[str, bool, str]] = []
        for name in self.names():
            tool = self._tools[name]
            if tool.availability is None:
                report.append((name, True, "ready"))
                continue
            try:
                available, reason = tool.availability()
            except Exception:
                available, reason = False, "availability check failed"
            report.append((name, available, reason))
        return tuple(report)

    @staticmethod
    def _call_handler(
        handler: Callable[..., ToolResult], first: object, context: object
    ) -> ToolResult:
        parameters = tuple(inspect.signature(handler).parameters.values())
        accepts_context = len(parameters) >= 2 and parameters[1].name == "context"
        return handler(first, context) if accepts_context else handler(first)

    @staticmethod
    def _validate_arguments(
        tool: ToolSpec, arguments: Mapping[str, object]
    ) -> dict[str, str | int]:
        unknown = set(arguments) - set(tool.arguments)
        if unknown:
            raise ValueError(f"unexpected argument: {sorted(unknown)[0]}")

        validated: dict[str, str | int] = {}
        for name, argument in tool.arguments.items():
            if name not in arguments:
                if argument.required:
                    raise ValueError(f"missing argument: {name}")
                continue
            validated[name] = argument.validate(name, arguments[name])
        return validated
