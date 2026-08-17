"""Bounded Windows media-key controls."""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable

from ron.agent.models import (
    ToolArgument,
    ToolArgumentKind,
    ToolResult,
    ToolRisk,
    ToolStatus,
)
from ron.agent.registry import ToolSpec

MEDIA_KEYS = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "stop": 0xB2,
}
MEDIA_LABELS = {
    "play_pause": "Toggled play/pause.",
    "next": "Skipped to the next track.",
    "previous": "Returned to the previous track.",
    "stop": "Stopped media playback.",
}
KeySender = Callable[[int], None]


def _windows_key_sender(virtual_key: int) -> None:
    if os.name != "nt":
        raise OSError("Media controls are available on Windows only")
    user32 = ctypes.windll.user32
    user32.keybd_event(virtual_key, 0, 0, 0)
    user32.keybd_event(virtual_key, 0, 0x0002, 0)


def build_media_tool(key_sender: KeySender = _windows_key_sender) -> ToolSpec:
    def availability() -> tuple[bool, str]:
        if key_sender is _windows_key_sender and os.name != "nt":
            return False, "Media-key control is available on Windows only."
        return True, "Media-key control is ready."

    def control_media(arguments: dict[str, str | int]) -> ToolResult:
        action = str(arguments["action"])
        try:
            key_sender(MEDIA_KEYS[action])
        except OSError:
            return ToolResult(
                tool_name="control_media",
                status=ToolStatus.FAILED,
                message="I couldn't send that media command on this computer.",
            )
        return ToolResult(
            tool_name="control_media",
            status=ToolStatus.SUCCESS,
            message=MEDIA_LABELS[action],
            data={"action": action},
        )

    return ToolSpec(
        name="control_media",
        description="Control only the currently active Windows media session with a fixed key.",
        arguments={
            "action": ToolArgument(
                ToolArgumentKind.ENUM, choices=tuple(MEDIA_KEYS)
            )
        },
        risk=ToolRisk.EXTERNAL,
        handler=control_media,
        timeout_seconds=2.0,
        availability=availability,
    )
