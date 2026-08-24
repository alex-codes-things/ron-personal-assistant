"""v0.9 service layer: working memory, skills and natural task references."""

from __future__ import annotations

import re
from pathlib import Path

from ron.agent.memory import WorkingMemory
from ron.agent.models import (
    AgentPlan,
    AgentResponse,
    AgentTaskSnapshot,
    AgentTaskStatus,
    ToolStatus,
)
from ron.agent.permissions import PermissionPolicy
from ron.agent.processes import ManagedProcessManager
from ron.agent.service import AgentService
from ron.reminders import ReminderManager
from ron.skills import SkillCatalog

LATEST_TASK_STATUS = re.compile(
    r"\b(?:status|how is|how's|check|what happened to)\b.*"
    r"\b(?:that|the last|last|current)\s+(?:task|job)\b",
    re.IGNORECASE,
)
LATEST_TASK_CANCEL = re.compile(
    r"\b(?:cancel|stop)\b.*\b(?:that|the last|last|current)\s+(?:task|job)\b",
    re.IGNORECASE,
)
REPEAT_CONTEXT = re.compile(
    r"^(?:open|do|run|try|repeat)\s+(?:that|it)"
    r"(?:\s+(?:folder|project|app|application|thing))?\s+again[.!]?$",
    re.IGNORECASE,
)
CORE_INTERACTION = re.compile(
    r"\b(?:prepare|workspace|ron project|ron repo|run the tests|pytest|"
    r"nexus|ron network|that task|last task|current task|"
    r"that process|last process|test run|tests|how are the tests|why are the fans|"
    r"can i run a game)\b",
    re.IGNORECASE,
)


class AgentCoreService(AgentService):
    """AgentService with recent context and capability-level reporting."""

    def __init__(
        self,
        planner,
        registry,
        *,
        project_root: Path | None = None,
        reminder_manager: ReminderManager | None = None,
        memory: WorkingMemory,
        skills: SkillCatalog,
        processes: ManagedProcessManager,
        permission_policy: PermissionPolicy,
    ) -> None:
        self.memory = memory
        self.skills = skills
        self.processes = processes
        self.permission_policy = permission_policy
        self.project_root = project_root
        super().__init__(
            planner,
            registry,
            project_root=project_root,
            reminder_manager=reminder_manager,
        )
        recalled = self.memory.recalled_plans()
        if recalled:
            self._last_successful_plans = recalled

    def claims_interaction(self, prompt: str) -> bool:
        return super().claims_interaction(prompt) or CORE_INTERACTION.search(prompt) is not None

    def respond(self, prompt: str) -> AgentResponse:
        latest_task = self._latest_task_id()
        if latest_task is not None and LATEST_TASK_CANCEL.search(prompt):
            return super().respond(f"cancel task {latest_task}")
        if latest_task is not None and LATEST_TASK_STATUS.search(prompt):
            return super().respond(f"status task {latest_task}")

        if REPEAT_CONTEXT.fullmatch(prompt.strip()):
            with self._lock:
                previous = self._last_successful_plans
            if previous:
                return self._execute_plans(prompt, previous)

        response = super().respond(prompt)
        self._remember_response(response)
        return response

    def capability_status(self) -> str:
        return (
            f"{super().capability_status()} "
            f"{self.skills.status_label()}; "
            f"{self.permission_policy.summary(self.registry)}; "
            f"{self.processes.status_label()}; "
            f"{self.memory.status_label()}."
        )

    def _capture_task_result(self, snapshot: AgentTaskSnapshot) -> None:
        with self._lock:
            plans = self._submitted_plans.get(snapshot.task_id, ())
        super()._capture_task_result(snapshot)
        if snapshot.status is AgentTaskStatus.COMPLETED and plans:
            self.memory.remember_plans(plans)
        if snapshot.status in {
            AgentTaskStatus.QUEUED,
            AgentTaskStatus.RUNNING,
            AgentTaskStatus.COMPLETED,
            AgentTaskStatus.WAITING,
        }:
            self.memory.remember_task(snapshot.task_id)

    def _remember_response(self, response: AgentResponse) -> None:
        plans = response.plans or ((response.plan,) if response.plan.tool_name else ())
        if self.project_root is not None and any(
            plan.tool_name in {"workspace_action", "get_workspace_status"} for plan in plans
        ):
            self.memory.remember_workspace(str(self.project_root.resolve()))
        if response.task is not None:
            self.memory.remember_task(response.task.task_id)
        if response.tool_result is not None and response.tool_result.status is ToolStatus.SUCCESS:
            self.memory.remember_result(response.tool_result)
            if plans:
                self.memory.remember_plans(tuple(plans))

    def _latest_task_id(self) -> int | None:
        remembered = self.memory.last_task_id
        if remembered is not None and self.task_snapshot(remembered) is not None:
            return remembered
        snapshots = self.task_snapshots()
        return snapshots[-1].task_id if snapshots else None
