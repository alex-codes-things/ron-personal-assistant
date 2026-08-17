"""Audited built-in tools available to Ron's first agent milestone."""

from __future__ import annotations

from pathlib import Path

from ron.agent.registry import ToolRegistry
from ron.agent.tools.applications import build_application_tool
from ron.agent.tools.brightness import build_brightness_tool
from ron.agent.tools.clock import build_date_tool, build_time_tool
from ron.agent.tools.folders import (
    build_blank_document_tool,
    build_open_folder_tool,
    build_search_folder_tool,
)
from ron.agent.tools.media import build_media_tool
from ron.agent.tools.reminders import build_reminder_tool
from ron.agent.tools.spotify import build_spotify_control_tool, build_spotify_tool
from ron.agent.tools.system_status import build_battery_tool, build_performance_tool
from ron.agent.tools.volume import build_volume_tool
from ron.integrations.spotify import (
    SpotifyClient,
    SpotifySettings,
    WindowsProtectedTokenStore,
)
from ron.reminders import ReminderManager


def build_default_registry(
    project_root: Path,
    reminder_manager: ReminderManager | None = None,
) -> ToolRegistry:
    spotify_settings = SpotifySettings.load(project_root)
    spotify_client = (
        SpotifyClient(
            spotify_settings,
            WindowsProtectedTokenStore(project_root / "data" / "spotify_token.dat"),
        )
        if spotify_settings is not None
        else None
    )
    registry = ToolRegistry()
    registry.register(build_time_tool())
    registry.register(build_date_tool())
    registry.register(build_application_tool())
    registry.register(build_media_tool())
    registry.register(build_volume_tool(project_root))
    registry.register(build_spotify_tool(lambda: spotify_client))
    registry.register(build_spotify_control_tool(lambda: spotify_client))
    registry.register(build_battery_tool())
    registry.register(build_performance_tool(project_root))
    registry.register(build_brightness_tool(project_root))
    registry.register(build_open_folder_tool())
    registry.register(build_blank_document_tool())
    registry.register(build_search_folder_tool())
    if reminder_manager is not None:
        registry.register(build_reminder_tool(reminder_manager))
    return registry


__all__ = ["build_default_registry"]
