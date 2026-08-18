"""Allowlisted Windows application launcher with no arbitrary command input."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from ron.agent.models import (
    ToolArgument,
    ToolArgumentKind,
    ToolResult,
    ToolRisk,
    ToolStatus,
)
from ron.agent.registry import ToolSpec


@dataclass(frozen=True, slots=True)
class ApplicationTarget:
    label: str
    target: str
    is_uri: bool = False


APPLICATIONS = {
    "notepad": ApplicationTarget("Notepad", "notepad.exe"),
    "calculator": ApplicationTarget("Calculator", "calc.exe"),
    "file_explorer": ApplicationTarget("File Explorer", "explorer.exe"),
    "settings": ApplicationTarget("Windows Settings", "ms-settings:", True),
    "spotify": ApplicationTarget("Spotify", "spotify:", True),
    "browser": ApplicationTarget("your default browser", "https://www.google.com", True),
}

Launcher = Callable[[ApplicationTarget], None]


def _windows_launcher(application: ApplicationTarget) -> None:
    if os.name != "nt":
        raise OSError("Application launching is available on Windows only")
    if application.is_uri:
        start_file = getattr(os, "startfile", None)
        if start_file is None:
            raise OSError("Windows URI launching is unavailable")
        start_file(application.target)
        return
    subprocess.Popen(
        [application.target],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def build_application_tool(launcher: Launcher = _windows_launcher) -> ToolSpec:
    def availability() -> tuple[bool, str]:
        if launcher is _windows_launcher and os.name != "nt":
            return False, "Application launching is available on Windows only."
        return True, "Application launcher is ready."

    def open_application(arguments: dict[str, str | int]) -> ToolResult:
        application_name = str(arguments["application"])
        application = APPLICATIONS[application_name]
        try:
            launcher(application)
        except OSError:
            return ToolResult(
                tool_name="open_application",
                status=ToolStatus.FAILED,
                message=f"I couldn't open {application.label} on this computer.",
            )
        return ToolResult(
            tool_name="open_application",
            status=ToolStatus.SUCCESS,
            message=f"Opening {application.label}.",
            data={"application": application_name},
        )

    return ToolSpec(
        name="open_application",
        description="Open one application from Ron's fixed Windows allowlist.",
        arguments={
            "application": ToolArgument(
                ToolArgumentKind.ENUM, choices=tuple(APPLICATIONS)
            )
        },
        risk=ToolRisk.EXTERNAL,
        handler=open_application,
        timeout_seconds=5.0,
        availability=availability,
    )
