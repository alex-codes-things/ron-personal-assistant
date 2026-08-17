from datetime import datetime, timedelta, timezone
from pathlib import Path

from ron.agent import ToolRegistry, ToolStatus
from ron.agent.tools.applications import build_application_tool
from ron.agent.tools.clock import build_date_tool, build_time_tool
from ron.agent.tools.media import MEDIA_KEYS, build_media_tool
from ron.agent.tools.volume import build_volume_tool


def test_clock_tools_use_supplied_local_clock() -> None:
    local_zone = timezone(timedelta(hours=2), "SAST")
    fixed_time = datetime(2026, 8, 14, 9, 5, tzinfo=local_zone)
    registry = ToolRegistry()
    registry.register(build_time_tool(lambda: fixed_time))
    registry.register(build_date_tool(lambda: fixed_time))

    time_result = registry.execute("get_local_time", {})
    date_result = registry.execute("get_local_date", {})

    assert time_result.message == "It's 9:05 AM (SAST)."
    assert date_result.message == "Today is Friday, 14 August 2026."


def test_application_tool_launches_only_allowlisted_target() -> None:
    launched = []
    registry = ToolRegistry()
    registry.register(build_application_tool(launched.append))

    success = registry.execute("open_application", {"application": "calculator"})
    rejected = registry.execute("open_application", {"application": "cmd"})

    assert success.status is ToolStatus.SUCCESS
    assert launched[0].target == "calc.exe"
    assert rejected.status is ToolStatus.FAILED
    assert len(launched) == 1


def test_media_tool_sends_exact_allowlisted_virtual_key() -> None:
    keys: list[int] = []
    registry = ToolRegistry()
    registry.register(build_media_tool(keys.append))

    result = registry.execute("control_media", {"action": "next"})

    assert result.status is ToolStatus.SUCCESS
    assert keys == [MEDIA_KEYS["next"]]


def test_volume_tool_sets_bounded_exact_level() -> None:
    calls: list[tuple[str, int | None]] = []

    def runner(action: str, level: int | None):
        calls.append((action, level))
        return {"ok": True, "level": level if level is not None else 42, "muted": False}

    registry = ToolRegistry()
    registry.register(build_volume_tool(Path("."), runner=runner))

    result = registry.execute(
        "control_volume", {"action": "set", "level": 30}
    )
    rejected = registry.execute(
        "control_volume", {"action": "set", "level": 130}
    )

    assert result.status is ToolStatus.SUCCESS
    assert result.message == "Volume set to 30%."
    assert calls == [("get", None), ("set", 30)]
    assert rejected.status is ToolStatus.FAILED
