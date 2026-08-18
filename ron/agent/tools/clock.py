"""Read-only local clock tools that never call an AI model or network."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ron.agent.models import ToolResult, ToolRisk, ToolStatus
from ron.agent.registry import ToolSpec

Clock = Callable[[], datetime]


def _local_now() -> datetime:
    return datetime.now().astimezone()


def build_time_tool(clock: Clock = _local_now) -> ToolSpec:
    def get_time(arguments: dict[str, str | int]) -> ToolResult:
        del arguments
        now = clock()
        hour = now.strftime("%I").lstrip("0") or "12"
        text = f"{hour}:{now:%M %p}"
        zone = now.tzname()
        message = f"It's {text}." if not zone else f"It's {text} ({zone})."
        return ToolResult(
            tool_name="get_local_time",
            status=ToolStatus.SUCCESS,
            message=message,
            data={"iso": now.isoformat(), "time_zone": zone or "local"},
        )

    return ToolSpec(
        name="get_local_time",
        description="Read the computer's current local time.",
        arguments={},
        risk=ToolRisk.READ_ONLY,
        handler=get_time,
        timeout_seconds=1.0,
    )


def build_date_tool(clock: Clock = _local_now) -> ToolSpec:
    def get_date(arguments: dict[str, str | int]) -> ToolResult:
        del arguments
        now = clock()
        text = f"{now:%A}, {now.day} {now:%B %Y}"
        return ToolResult(
            tool_name="get_local_date",
            status=ToolStatus.SUCCESS,
            message=f"Today is {text}.",
            data={"iso_date": now.date().isoformat()},
        )

    return ToolSpec(
        name="get_local_date",
        description="Read the computer's current local date.",
        arguments={},
        risk=ToolRisk.READ_ONLY,
        handler=get_date,
        timeout_seconds=1.0,
    )
