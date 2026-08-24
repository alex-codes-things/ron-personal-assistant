import sys
import time
from pathlib import Path

from ron.agent.core_planner import AgentCorePlanner
from ron.agent.memory import WorkingMemory
from ron.agent.models import (
    AgentPlan,
    AgentPlanSource,
    ToolResult,
    ToolRisk,
    ToolStatus,
)
from ron.agent.permissions import PermissionAwareRegistry, PermissionLevel
from ron.agent.processes import ManagedProcessManager
from ron.agent.registry import ToolSpec
from ron.skills import SkillCatalog


class NeverCalledClient:
    def stream_chat(self, *args, **kwargs):
        del args, kwargs
        raise AssertionError("Deterministic core planning should not call the model")


def test_permission_registry_forces_destructive_confirmation() -> None:
    registry = PermissionAwareRegistry()
    registry.register(
        ToolSpec(
            "dangerous_test",
            "A destructive test tool.",
            {},
            ToolRisk.DESTRUCTIVE,
            lambda arguments: ToolResult(
                "dangerous_test", ToolStatus.SUCCESS, "ran"
            ),
        )
    )

    pending = registry.preflight("dangerous_test", {})
    confirmed = registry.preflight("dangerous_test", {}, confirmed=True)

    assert pending.status is ToolStatus.CONFIRMATION_REQUIRED
    assert pending.data["permission_level"] == PermissionLevel.SENSITIVE.value
    assert confirmed.status is ToolStatus.READY


def test_skill_catalog_activates_only_registered_tools() -> None:
    registry = PermissionAwareRegistry()
    registry.register(
        ToolSpec(
            "get_system_performance",
            "Read performance.",
            {},
            ToolRisk.READ_ONLY,
            lambda arguments: ToolResult(
                "get_system_performance", ToolStatus.SUCCESS, "ok"
            ),
        )
    )
    skills = SkillCatalog(registry)

    assert "system" in skills.names()
    assert skills.active_tools("system") == ("get_system_performance",)
    assert skills.for_tool("get_system_performance").name == "system"


def test_working_memory_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "working_memory.json"
    memory = WorkingMemory(path)
    plans = (
        AgentPlan(
            "open_folder",
            {"folder": "documents"},
            "test",
            AgentPlanSource.DETERMINISTIC,
        ),
    )
    memory.remember_task(7)
    memory.remember_plans(plans)
    memory.remember_result(
        ToolResult(
            "workspace_action",
            ToolStatus.SUCCESS,
            "started",
            data={"workspace": "C:/Ron", "process_id": 4},
        )
    )

    restored = WorkingMemory(path)

    assert restored.last_task_id == 7
    assert restored.last_process_id == 4
    assert restored.workspace == "C:/Ron"
    assert restored.recalled_plans()[0].tool_name == "open_folder"


def test_managed_process_tracks_completion(tmp_path: Path) -> None:
    manager = ManagedProcessManager(tmp_path / "logs")
    snapshot, started = manager.start(
        key="tiny-test",
        label="Tiny test",
        command=(sys.executable, "-c", "print('ok')"),
        cwd=tmp_path,
    )

    assert started
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        final = manager.snapshot(snapshot.process_id)
        if final is not None and final.status != "running":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("Managed process did not finish")
    assert final is not None
    assert final.status == "completed"


def _core_registry() -> PermissionAwareRegistry:
    registry = PermissionAwareRegistry()
    for name, risk in (
        ("workspace_action", ToolRisk.EXTERNAL),
        ("get_workspace_status", ToolRisk.READ_ONLY),
        ("get_network_devices", ToolRisk.READ_ONLY),
        ("get_managed_processes", ToolRisk.READ_ONLY),
        ("stop_managed_process", ToolRisk.REVERSIBLE),
        ("get_system_performance", ToolRisk.READ_ONLY),
        ("get_top_processes", ToolRisk.READ_ONLY),
    ):
        registry.register(
            ToolSpec(
                name,
                f"Test tool {name}.",
                {},
                risk,
                lambda arguments, tool=name: ToolResult(
                    tool, ToolStatus.SUCCESS, "ok"
                ),
            )
        )
    return registry


def test_core_planner_maps_workspace_prepare_without_model() -> None:
    registry = _core_registry()
    planner = AgentCorePlanner(NeverCalledClient(), registry, SkillCatalog(registry))

    plan = planner.plan_steps("I'm going to work on Ron. Get everything ready.")

    assert len(plan.steps) == 1
    assert plan.steps[0].tool_name == "workspace_action"
    assert plan.steps[0].arguments == {"action": "prepare"}


def test_core_planner_maps_live_system_awareness() -> None:
    registry = _core_registry()
    planner = AgentCorePlanner(NeverCalledClient(), registry, SkillCatalog(registry))

    plan = planner.plan_steps("Why are the fans so loud?")

    assert [step.tool_name for step in plan.steps] == [
        "get_system_performance",
        "get_top_processes",
    ]


def test_core_planner_maps_network_awareness() -> None:
    registry = _core_registry()
    planner = AgentCorePlanner(NeverCalledClient(), registry, SkillCatalog(registry))

    plan = planner.plan_steps("Is the Nexus connected?")

    assert plan.steps[0].tool_name == "get_network_devices"
