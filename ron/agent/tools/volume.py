"""Exact, bounded Windows endpoint-volume control through a fixed script."""

from __future__ import annotations

import inspect
import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from ron.agent.models import (
    ToolArgument,
    ToolArgumentKind,
    ToolExecutionContext,
    ToolResult,
    ToolRisk,
    ToolStatus,
)
from ron.agent.registry import ToolSpec

type AudioRunner = Callable[..., dict[str, object]]


def _powershell_runner(project_root: Path) -> AudioRunner:
    script = project_root / "scripts" / "windows_audio.ps1"

    def run_audio(
        action: str,
        level: int | None,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, object]:
        if os.name != "nt":
            raise OSError("Volume control is available on Windows only")
        if not script.is_file():
            raise OSError("The fixed Windows audio helper is missing")
        command = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Action",
            action,
        ]
        if level is not None:
            command.extend(["-Level", str(level)])
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=(
                max(0.1, min(10.0, context.remaining_seconds))
                if context is not None
                else 10.0
            ),
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            raise OSError("Windows rejected the audio request")
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        if not lines:
            raise OSError("The audio helper returned no result")
        payload = json.loads(lines[-1])
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise OSError("The audio helper reported failure")
        return payload

    return run_audio


def build_volume_tool(
    project_root: Path, runner: AudioRunner | None = None
) -> ToolSpec:
    script = project_root / "scripts" / "windows_audio.ps1"
    run_audio = runner or _powershell_runner(project_root)

    def availability() -> tuple[bool, str]:
        if runner is not None:
            return True, "Volume controller is ready."
        if os.name != "nt":
            return False, "Volume control is available on Windows only."
        if not script.is_file():
            return False, "The fixed Windows audio helper is missing."
        return True, "Windows volume control is ready."

    def call_runner(
        action: str,
        level: int | None,
        context: ToolExecutionContext,
    ) -> dict[str, object]:
        return (
            run_audio(action, level, context)
            if len(inspect.signature(run_audio).parameters) >= 3
            else run_audio(action, level)
        )

    def control_volume(
        arguments: dict[str, str | int], context: ToolExecutionContext
    ) -> ToolResult:
        action = str(arguments["action"])
        level_value = arguments.get("level")
        level = int(level_value) if isinstance(level_value, int) else None
        delta_value = arguments.get("delta")
        delta = int(delta_value) if isinstance(delta_value, int) else None
        if action == "set" and level is None:
            return ToolResult(
                tool_name="control_volume",
                status=ToolStatus.FAILED,
                message="A volume percentage is required for that command.",
            )
        if action != "set" and level is not None:
            return ToolResult(
                tool_name="control_volume",
                status=ToolStatus.FAILED,
                message="A volume percentage is only valid with the set action.",
            )
        if action == "adjust" and delta is None:
            return ToolResult(
                "control_volume",
                ToolStatus.FAILED,
                "A relative volume change is required.",
            )
        if action != "adjust" and delta is not None:
            return ToolResult(
                "control_volume",
                ToolStatus.FAILED,
                "A relative change is only valid with the adjust action.",
            )
        previous: dict[str, object] | None = None
        try:
            context.checkpoint()
            if action != "get":
                previous = call_runner("get", None, context)
            if action == "adjust":
                previous_level = previous.get("level") if previous is not None else None
                if not isinstance(previous_level, int):
                    raise OSError("Windows returned no previous volume")
                level = max(0, min(100, previous_level + int(delta or 0)))
                payload = call_runner("set", level, context)
            else:
                payload = call_runner(action, level, context)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            return ToolResult(
                tool_name="control_volume",
                status=ToolStatus.FAILED,
                message="I couldn't safely control Windows volume.",
            )

        output_level = payload.get("level")
        muted = payload.get("muted")
        if not isinstance(output_level, int) or not 0 <= output_level <= 100:
            return ToolResult(
                tool_name="control_volume",
                status=ToolStatus.FAILED,
                message="Windows returned an invalid volume reading.",
            )
        if not isinstance(muted, bool):
            return ToolResult(
                tool_name="control_volume",
                status=ToolStatus.FAILED,
                message="Windows returned an invalid mute reading.",
            )

        if action == "get":
            message = f"Your volume is {output_level}%"
            message += " and it is muted." if muted else "."
        elif action in {"set", "adjust"}:
            message = f"Volume set to {output_level}%."
        elif action == "mute":
            message = "Volume muted."
        elif action == "unmute":
            message = f"Volume unmuted at {output_level}%."
        else:
            message = "Volume unmuted." if not muted else "Volume muted."
        return ToolResult(
            tool_name="control_volume",
            status=ToolStatus.SUCCESS,
            message=message,
            data={
                "level": output_level,
                "muted": muted,
                "previous_level": previous.get("level") if previous else None,
                "previous_muted": previous.get("muted") if previous else None,
                "changed": action != "get",
            },
        )

    def restore_volume(
        result: ToolResult, context: ToolExecutionContext
    ) -> ToolResult:
        if result.data.get("changed") is not True:
            return ToolResult(
                "control_volume",
                ToolStatus.SUCCESS,
                "volume was read only; no rollback was needed",
            )
        previous_level = result.data.get("previous_level")
        previous_muted = result.data.get("previous_muted")
        if not isinstance(previous_level, int) or not isinstance(previous_muted, bool):
            return ToolResult(
                "control_volume",
                ToolStatus.FAILED,
                "The previous volume state was unavailable.",
            )
        try:
            call_runner("set", previous_level, context)
            call_runner("mute" if previous_muted else "unmute", None, context)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            return ToolResult(
                "control_volume",
                ToolStatus.FAILED,
                "The previous volume could not be restored.",
            )
        return ToolResult(
            "control_volume",
            ToolStatus.SUCCESS,
            f"volume restored to {previous_level}%",
        )

    return ToolSpec(
        name="control_volume",
        description="Read or change the Windows master endpoint volume.",
        arguments={
            "action": ToolArgument(
                ToolArgumentKind.ENUM,
                choices=("get", "set", "adjust", "mute", "unmute", "toggle"),
            ),
            "level": ToolArgument(
                ToolArgumentKind.INTEGER,
                minimum=0,
                maximum=100,
                required=False,
            ),
            "delta": ToolArgument(
                ToolArgumentKind.INTEGER,
                minimum=-100,
                maximum=100,
                required=False,
            ),
        },
        risk=ToolRisk.REVERSIBLE,
        handler=control_volume,
        timeout_seconds=12.0,
        compensator=restore_volume,
        availability=availability,
    )
