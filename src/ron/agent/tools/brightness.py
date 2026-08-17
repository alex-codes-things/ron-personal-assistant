"""Bounded laptop-screen brightness control through a fixed PowerShell helper."""

from __future__ import annotations

import json
import os
import subprocess
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


def build_brightness_tool(project_root: Path) -> ToolSpec:
    script = project_root / "scripts" / "windows_brightness.ps1"

    def availability() -> tuple[bool, str]:
        if os.name != "nt":
            return False, "Brightness control is available on Windows only."
        if not script.is_file():
            return False, "The fixed Windows brightness helper is missing."
        return True, "Windows brightness control is ready."

    def run_helper(
        action: str,
        level: int | None,
        context: ToolExecutionContext,
    ) -> int:
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
        if isinstance(level, int):
            command.extend(["-Level", str(level)])
        context.checkpoint()
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(0.1, context.remaining_seconds),
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            raise OSError("Brightness helper failed")
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        payload = json.loads(lines[-1])
        output = payload.get("level") if isinstance(payload, dict) else None
        if not isinstance(output, int) or not 0 <= output <= 100:
            raise ValueError("Windows returned an invalid brightness reading")
        return output

    def control_brightness(
        arguments: dict[str, str | int], context: ToolExecutionContext
    ) -> ToolResult:
        action = str(arguments["action"])
        level = arguments.get("level")
        delta = arguments.get("delta")
        if action == "set" and not isinstance(level, int):
            return ToolResult(
                "control_brightness",
                ToolStatus.FAILED,
                "A brightness percentage is required.",
            )
        if action == "get" and level is not None:
            return ToolResult(
                "control_brightness",
                ToolStatus.FAILED,
                "A percentage is only valid when setting brightness.",
            )
        if action != "set" and level is not None:
            return ToolResult(
                "control_brightness",
                ToolStatus.FAILED,
                "An exact percentage is only valid when setting brightness.",
            )
        if action == "adjust" and not isinstance(delta, int):
            return ToolResult(
                "control_brightness",
                ToolStatus.FAILED,
                "A relative brightness change is required.",
            )
        if action != "adjust" and delta is not None:
            return ToolResult(
                "control_brightness",
                ToolStatus.FAILED,
                "A relative change is only valid when adjusting brightness.",
            )
        if os.name != "nt" or not script.is_file():
            return ToolResult(
                "control_brightness",
                ToolStatus.UNSUPPORTED,
                "Brightness control is unavailable on this computer.",
            )
        previous_level: int | None = None
        try:
            if action in {"set", "adjust"}:
                previous_level = run_helper("get", None, context)
            if action == "adjust":
                target = max(0, min(100, previous_level + int(delta or 0)))
                output = run_helper("set", target, context)
            else:
                output = run_helper(action, level, context)
        except (
            OSError,
            ValueError,
            subprocess.SubprocessError,
            json.JSONDecodeError,
            IndexError,
        ):
            return ToolResult(
                "control_brightness",
                ToolStatus.FAILED,
                "I couldn't safely control the internal display brightness.",
            )
        message = (
            f"Brightness is {output}%."
            if action == "get"
            else f"Brightness set to {output}%."
        )
        return ToolResult(
            "control_brightness",
            ToolStatus.SUCCESS,
            message,
            data={
                "level": output,
                "previous_level": previous_level,
                "changed": action in {"set", "adjust"},
            },
        )

    def restore_brightness(
        result: ToolResult, context: ToolExecutionContext
    ) -> ToolResult:
        if result.data.get("changed") is not True:
            return ToolResult(
                "control_brightness",
                ToolStatus.SUCCESS,
                "brightness was read only; no rollback was needed",
            )
        previous_level = result.data.get("previous_level")
        if not isinstance(previous_level, int):
            return ToolResult(
                "control_brightness",
                ToolStatus.FAILED,
                "The previous brightness state was unavailable.",
            )
        try:
            restored = run_helper("set", previous_level, context)
        except (
            OSError,
            ValueError,
            subprocess.SubprocessError,
            json.JSONDecodeError,
            IndexError,
        ):
            return ToolResult(
                "control_brightness",
                ToolStatus.FAILED,
                "The previous brightness could not be restored.",
            )
        return ToolResult(
            "control_brightness",
            ToolStatus.SUCCESS,
            f"brightness restored to {restored}%",
        )

    return ToolSpec(
        "control_brightness",
        "Read or set the internal Windows display brightness.",
        {
            "action": ToolArgument(
                ToolArgumentKind.ENUM,
                choices=("get", "set", "adjust"),
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
        ToolRisk.REVERSIBLE,
        control_brightness,
        timeout_seconds=8.0,
        compensator=restore_brightness,
        availability=availability,
    )
