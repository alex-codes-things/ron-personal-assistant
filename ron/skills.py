"""Composable skill bundles that organise Ron's approved tools."""

from __future__ import annotations

from dataclasses import dataclass

from ron.agent.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class SkillSpec:
    name: str
    description: str
    tools: tuple[str, ...]
    aliases: tuple[str, ...] = ()


DEFAULT_SKILLS = (
    SkillSpec(
        "system",
        "Read local computer health, battery, performance and running processes.",
        (
            "get_local_time",
            "get_local_date",
            "get_battery_status",
            "get_system_performance",
            "get_top_processes",
            "control_brightness",
        ),
        ("computer", "windows", "performance"),
    ),
    SkillSpec(
        "volume",
        "Read and adjust Windows audio volume.",
        ("control_volume",),
        ("sound", "audio"),
    ),
    SkillSpec(
        "apps",
        "Open approved desktop applications.",
        ("open_application",),
        ("applications", "launcher"),
    ),
    SkillSpec(
        "browser",
        "Open Ron's approved browser target.",
        ("open_application",),
        ("web", "internet"),
    ),
    SkillSpec(
        "music",
        "Control local media playback.",
        ("control_media", "spotify_play_track", "spotify_control_playback"),
        ("media", "playback"),
    ),
    SkillSpec(
        "spotify",
        "Search, play and control Spotify through Ron's approved integration.",
        ("spotify_play_track", "spotify_control_playback"),
        ("songs", "tracks"),
    ),
    SkillSpec(
        "files",
        "Work with approved folders and simple local documents.",
        ("open_folder", "search_approved_folder", "create_blank_text_document"),
        ("folders", "documents"),
    ),
    SkillSpec(
        "git",
        "Inspect the current Ron repository and development state.",
        ("get_workspace_status",),
        ("repo", "repository"),
    ),
    SkillSpec(
        "workspace",
        "Prepare Ron's development workspace, run tests, and track Ron-launched processes.",
        (
            "get_workspace_status",
            "workspace_action",
            "get_managed_processes",
            "stop_managed_process",
        ),
        ("development", "dev", "project", "tests"),
    ),
    SkillSpec(
        "tablet",
        "Read the Nexus/Ron Face state through Ron Network.",
        ("get_network_devices", "get_workspace_status"),
        ("nexus", "face", "display"),
    ),
    SkillSpec(
        "network",
        "Read the health and capabilities of trusted Ron Network devices.",
        ("get_network_devices",),
        ("devices", "lan"),
    ),
    SkillSpec(
        "reminders",
        "Create and track local reminders.",
        ("set_reminder",),
        ("timer", "timers"),
    ),
)


class SkillCatalog:
    """A registry of named capabilities layered over the low-level tool allowlist."""

    def __init__(
        self,
        registry: ToolRegistry,
        skills: tuple[SkillSpec, ...] = DEFAULT_SKILLS,
    ) -> None:
        self.registry = registry
        self._skills: dict[str, SkillSpec] = {}
        for skill in skills:
            self.register(skill)

    def register(self, skill: SkillSpec) -> None:
        if not skill.name or not skill.name.replace("_", "").isalnum():
            raise ValueError("Skill names must contain only letters, numbers, and underscores")
        if skill.name in self._skills:
            raise ValueError(f"Skill {skill.name!r} is already registered")
        self._skills[skill.name] = skill

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._skills))

    def active_tools(self, skill_name: str) -> tuple[str, ...]:
        skill = self._skills[skill_name]
        available = set(self.registry.names())
        return tuple(tool for tool in skill.tools if tool in available)

    def active_skills(self) -> tuple[SkillSpec, ...]:
        return tuple(
            self._skills[name]
            for name in self.names()
            if self.active_tools(name)
        )

    def for_tool(self, tool_name: str) -> SkillSpec | None:
        return next(
            (skill for skill in self.active_skills() if tool_name in skill.tools),
            None,
        )

    def model_context(self) -> str:
        groups = []
        for skill in self.active_skills():
            tools = ", ".join(self.active_tools(skill.name))
            groups.append(f"{skill.name}: {tools}")
        return "Skill groups: " + " | ".join(groups)

    def status_label(self) -> str:
        active = self.active_skills()
        return f"skills: {len(active)} active ({', '.join(skill.name for skill in active)})"
