"""Build the v0.9 Agent Core registry without disturbing legacy tool builders."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ron.agent.permissions import PermissionAwareRegistry
from ron.agent.processes import ManagedProcessManager
from ron.agent.tools import build_default_registry
from ron.agent.tools.network_devices import build_network_devices_tool
from ron.agent.tools.processes import (
    build_managed_processes_tool,
    build_stop_managed_process_tool,
)
from ron.agent.tools.system_processes import build_top_processes_tool
from ron.agent.tools.workspace import (
    build_workspace_action_tool,
    build_workspace_status_tool,
)
from ron.reminders import ReminderManager

if TYPE_CHECKING:
    from ron.network import NetworkService


@dataclass(frozen=True, slots=True)
class CoreToolBundle:
    registry: PermissionAwareRegistry
    processes: ManagedProcessManager


def build_agent_core_registry(
    project_root: Path,
    reminder_manager: ReminderManager | None,
    network: NetworkService | None,
) -> CoreToolBundle:
    legacy = build_default_registry(project_root, reminder_manager)
    registry = PermissionAwareRegistry()
    for name in legacy.names():
        spec = legacy.spec(name)
        if spec is not None:
            registry.register(spec)

    processes = ManagedProcessManager(
        project_root / "runtime" / "logs" / "processes"
    )
    registry.register(build_top_processes_tool())
    registry.register(build_workspace_status_tool(project_root, network))
    registry.register(build_workspace_action_tool(project_root, processes, network))
    registry.register(build_network_devices_tool(network))
    registry.register(build_managed_processes_tool(processes))
    registry.register(build_stop_managed_process_tool(processes))
    return CoreToolBundle(registry, processes)
