"""Bounded Windows media-key controls."""

from __future__ import annotations

import asyncio
import ctypes
import os
from collections.abc import Callable
from typing import Protocol

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
    "play": 0xB3,
    "resume": 0xB3,
    "pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "stop": 0xB2,
}
MEDIA_LABELS = {
    "play_pause": "Toggled play/pause.",
    "next": "Skipped to the next track.",
    "previous": "Returned to the previous track.",
    "stop": "Stopped media playback.",
    "play": "Started media playback.",
    "resume": "Resumed media playback.",
    "pause": "Paused media playback.",
}
type KeySender = Callable[[int], None]


class MediaSessionController(Protocol):
    """Small adapter around Windows' current media-session API."""

    def state(self) -> str: ...

    def perform(self, action: str) -> bool: ...


class _WindowsMediaSessionController:
    """Use GSMTC when winsdk is installed; imports stay Windows-only and optional."""

    @staticmethod
    def _session():
        from winsdk.windows.media.control import (  # type: ignore[import-not-found]
            GlobalSystemMediaTransportControlsSessionManager,
        )

        manager = asyncio.run(GlobalSystemMediaTransportControlsSessionManager.request_async())
        session = manager.get_current_session()
        if session is None:
            raise OSError("No active Windows media session")
        return session

    def state(self) -> str:
        status = self._session().get_playback_info().playback_status
        label = getattr(status, "name", str(status)).casefold()
        if "playing" in label:
            return "playing"
        if "paused" in label:
            return "paused"
        if "stopped" in label or "closed" in label:
            return "stopped"
        return "unknown"

    def perform(self, action: str) -> bool:
        session = self._session()
        methods = {
            "play": "try_play_async",
            "resume": "try_play_async",
            "pause": "try_pause_async",
            "next": "try_skip_next_async",
            "previous": "try_skip_previous_async",
            "stop": "try_stop_async",
        }
        method = getattr(session, methods[action])
        return bool(asyncio.run(method()))


def _windows_key_sender(virtual_key: int) -> None:
    if os.name != "nt":
        raise OSError("Media controls are available on Windows only")
    user32 = ctypes.windll.user32
    user32.keybd_event(virtual_key, 0, 0, 0)
    user32.keybd_event(virtual_key, 0, 0x0002, 0)


def build_media_tool(
    key_sender: KeySender = _windows_key_sender,
    *,
    session_controller: MediaSessionController | None = None,
) -> ToolSpec:
    def availability() -> tuple[bool, str]:
        if key_sender is _windows_key_sender and os.name != "nt":
            return False, "Media-key control is available on Windows only."
        return True, "Media-key control is ready."

    def control_media(arguments: dict[str, str | int]) -> ToolResult:
        action = str(arguments["action"])
        controller = session_controller
        if controller is None and os.name == "nt":
            controller = _WindowsMediaSessionController()
        if action in {"play", "resume", "pause"} and controller is not None:
            try:
                before = controller.state()
                target = "playing" if action in {"play", "resume"} else "paused"
                if before == target:
                    return ToolResult(
                        tool_name="control_media",
                        status=ToolStatus.SUCCESS,
                        message=(
                            "Media is already playing."
                            if target == "playing"
                            else "Media is already paused."
                        ),
                        data={
                            "action": action,
                            "state_before": before,
                            "state_aware": True,
                            "changed": False,
                        },
                    )
                if controller.perform(action):
                    return ToolResult(
                        tool_name="control_media",
                        status=ToolStatus.SUCCESS,
                        message=MEDIA_LABELS[action],
                        data={
                            "action": action,
                            "state_before": before,
                            "state_aware": True,
                            "changed": True,
                        },
                    )
            except Exception:
                # Older installations keep working through fixed media keys. The
                # result explicitly reports the fallback instead of pretending the
                # state was verified.
                pass
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
            data={"action": action, "state_aware": False},
        )

    return ToolSpec(
        name="control_media",
        description=(
            "Control the current Windows media session. Explicit play, resume and pause "
            "are state-aware when the Windows session API is available."
        ),
        arguments={"action": ToolArgument(ToolArgumentKind.ENUM, choices=tuple(MEDIA_KEYS))},
        risk=ToolRisk.REVERSIBLE,
        handler=control_media,
        timeout_seconds=2.0,
        availability=availability,
    )
