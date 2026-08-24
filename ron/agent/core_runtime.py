"""Assemble the v0.9 Agent Core as one cohesive runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ron.agent.core_planner import AgentCorePlanner
from ron.agent.core_service import AgentCoreService
from ron.agent.core_tools import build_agent_core_registry
from ron.agent.memory import WorkingMemory
from ron.agent.permissions import PermissionAwareRegistry
from ron.agent.processes import ManagedProcessManager
from ron.reminders import ReminderManager
from ron.skills import SkillCatalog

if TYPE_CHECKING:
    from ron.ai import OllamaClient
    from ron.network import NetworkService


@dataclass(frozen=True, slots=True)
class AgentCoreRuntime:
    registry: PermissionAwareRegistry
    planner: AgentCorePlanner
    service: AgentCoreService
    skills: SkillCatalog
    processes: ManagedProcessManager
    memory: WorkingMemory


def build_agent_core(
    project_root: Path,
    planning_client: OllamaClient,
    reminder_manager: ReminderManager | None,
    network: NetworkService | None,
) -> AgentCoreRuntime:
    tools = build_agent_core_registry(project_root, reminder_manager, network)
    skills = SkillCatalog(tools.registry)
    memory = WorkingMemory(project_root / "runtime" / "data" / "working_memory.json")
    planner = AgentCorePlanner(planning_client, tools.registry, skills)
    service = AgentCoreService(
        planner,
        tools.registry,
        project_root=project_root,
        reminder_manager=reminder_manager,
        memory=memory,
        skills=skills,
        processes=tools.processes,
        permission_policy=tools.registry.permission_policy,
    )
    return AgentCoreRuntime(
        tools.registry,
        planner,
        service,
        skills,
        tools.processes,
        memory,
    )
