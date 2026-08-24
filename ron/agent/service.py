"""Safe execution, confirmations, clarification and persistent task handoff."""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from ron.agent.journal import AgentTaskJournal
from ron.agent.models import (
    AgentPlan,
    AgentPlanSource,
    AgentResponse,
    AgentTaskSnapshot,
    AgentTaskStatus,
    ToolStatus,
)
from ron.agent.planner import AgentPlanner
from ron.agent.registry import ToolRegistry
from ron.agent.task_manager import AgentTaskManager, TaskListener
from ron.reminders import Reminder, ReminderManager

type ProgressHandler = Callable[[str], None]

TASK_STATUS_PATTERN = re.compile(
    r"\b(?:status(?: of)?|how is|how's|check)\s+(?:on\s+)?task\s+(\d+)\b",
    re.IGNORECASE,
)
TASK_CANCEL_PATTERN = re.compile(r"\b(?:cancel|stop)\s+task\s+(\d+)\b", re.IGNORECASE)
TASK_LIST_PATTERN = re.compile(
    r"\b(?:list|show|what are|how are|what)\b.*\b(?:tasks|jobs)\b", re.IGNORECASE
)
CONFIRM_YES = re.compile(
    r"^(?:yes|confirm|continue|go ahead|do it|yes,? run it)[.!]?$",
    re.IGNORECASE,
)
CONFIRM_NO = re.compile(r"^(?:no|cancel|never mind|don't|do not)[.!]?$", re.IGNORECASE)
REPEAT_PATTERN = re.compile(
    r"^(?:do|run|try|repeat)\s+(?:that|it)\s+again(?:,?\s+but\s+at\s+(\d{1,3})\s*%?)?[.!]?$",
    re.IGNORECASE,
)
REMINDER_CANCEL_PATTERN = re.compile(
    r"\b(?:cancel|delete|remove)\s+reminder\s+(\d+)\b", re.IGNORECASE
)
REMINDER_LIST_PATTERN = re.compile(r"\b(?:show|list|what are)\b.*\breminders\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class _PendingConfirmation:
    prompt: str
    plans: tuple[AgentPlan, ...]
    expires_at: float


@dataclass(frozen=True, slots=True)
class _PendingClarification:
    task_id: int
    query: str
    candidates: tuple[dict[str, object], ...]
    expires_at: float


class AgentService:
    """Plan, preflight and execute only registered tools."""

    def __init__(
        self,
        planner: AgentPlanner,
        registry: ToolRegistry,
        *,
        project_root: Path | None = None,
        reminder_manager: ReminderManager | None = None,
    ) -> None:
        self.planner = planner
        self.registry = registry
        journal = (
            AgentTaskJournal(project_root / "runtime" / "data" / "agent_tasks.sqlite")
            if project_root is not None
            else None
        )
        self.tasks = AgentTaskManager(registry, journal=journal)
        self.reminders = reminder_manager
        self._logger = logging.getLogger(__name__)
        self._lock = threading.RLock()
        self._pending_confirmation: _PendingConfirmation | None = None
        self._pending_clarification: _PendingClarification | None = None
        self._last_successful_plans: tuple[AgentPlan, ...] = ()
        self._submitted_plans: dict[int, tuple[AgentPlan, ...]] = {}
        self.tasks.add_listener(self._capture_task_result)
        for snapshot in self.tasks.snapshots():
            if snapshot.status is AgentTaskStatus.WAITING:
                self._capture_task_result(snapshot)

    def start(self) -> None:
        self.tasks.start()

    def stop(self) -> None:
        self.tasks.stop()

    def add_task_listener(self, listener: TaskListener) -> None:
        self.tasks.add_listener(listener)

    def add_progress_listener(self, listener: TaskListener) -> None:
        self.tasks.add_progress_listener(listener)

    def claims_interaction(self, prompt: str) -> bool:
        self._expire_pending()
        clean = prompt.strip()
        with self._lock:
            confirmation = self._pending_confirmation
            clarification = self._pending_clarification
        if confirmation is not None:
            return (
                CONFIRM_YES.fullmatch(clean) is not None or CONFIRM_NO.fullmatch(clean) is not None
            )
        if clarification is not None:
            return (
                self._clarification_choice(clean, clarification) is not None
                or CONFIRM_NO.fullmatch(clean) is not None
            )
        return False

    def respond(
        self,
        prompt: str,
        *,
        on_progress: ProgressHandler | None = None,
    ) -> AgentResponse:
        self._expire_pending()
        task_control = self._task_control(prompt)
        if task_control is not None:
            return task_control
        reminder_control = self._reminder_control(prompt)
        if reminder_control is not None:
            return reminder_control
        interaction = self._handle_pending(prompt, on_progress=on_progress)
        if interaction is not None:
            return interaction
        repeated = self._repeat_plans(prompt)
        if repeated is not None:
            if not repeated:
                return AgentResponse(
                    "I don't have a previous successful action to repeat yet.",
                    self._no_plan("No successful action is available to repeat."),
                )
            return self._execute_plans(prompt, repeated, on_progress=on_progress)

        self._progress(on_progress, "Planning the safest approved action…")
        task_plan = self.planner.plan_steps(prompt)
        if not task_plan.steps:
            return AgentResponse(
                "I recognised that as agent work, but I couldn't map every requested "
                "action to an approved tool. Nothing was changed.",
                self._no_plan(task_plan.reason),
            )
        return self._execute_plans(prompt, task_plan.steps, on_progress=on_progress)

    def _execute_plans(
        self,
        prompt: str,
        plans: tuple[AgentPlan, ...],
        *,
        confirmed: bool = False,
        on_progress: ProgressHandler | None = None,
    ) -> AgentResponse:
        self._progress(on_progress, "Checking the complete action before it runs…")
        for index, plan in enumerate(plans, start=1):
            assert plan.tool_name is not None
            preflight = self.registry.preflight(
                plan.tool_name,
                plan.arguments,
                confirmed=confirmed,
            )
            if preflight.status is ToolStatus.CONFIRMATION_REQUIRED:
                with self._lock:
                    self._pending_confirmation = _PendingConfirmation(
                        prompt.strip(), plans, monotonic() + 90.0
                    )
                summary = ", then ".join(self._describe_plan(item) for item in plans)
                text = (
                    f"This exact plan requires confirmation: {summary}. "
                    "Reply 'confirm' within 90 seconds to run it, or 'cancel'."
                )
                return AgentResponse(text, plans[0], preflight, plans=plans)
            if preflight.status is not ToolStatus.READY:
                text = f"Preflight stopped at step {index}: {preflight.message} No step was run."
                return AgentResponse(text, plans[0], preflight, plans=plans)

        first_spec = self.registry.spec(plans[0].tool_name or "")
        should_queue = len(plans) > 1 or (first_spec is not None and first_spec.run_in_background)
        if should_queue:
            self._progress(on_progress, "Queuing the approved task…")
            try:
                snapshot = self.tasks.submit(prompt.strip(), plans, confirmed=confirmed)
            except (RuntimeError, ValueError) as error:
                return AgentResponse(
                    f"I couldn't queue that task safely: {error}",
                    plans[0],
                    plans=plans,
                )
            with self._lock:
                self._submitted_plans[snapshot.task_id] = plans
            self._progress(
                on_progress,
                f"Task {snapshot.task_id} is queued; its steps will update here.",
            )
            text = (
                f"Task {snapshot.task_id} passed full preflight and is queued with "
                f"{snapshot.total_steps} step(s). You can keep chatting, check its "
                "status, or cancel it."
            )
            return AgentResponse(text, plans[0], plans=plans, task=snapshot)

        plan = plans[0]
        assert plan.tool_name is not None
        self._progress(on_progress, f"Running: {self._tool_stage(plan.tool_name)}…")
        result = self.registry.execute(plan.tool_name, plan.arguments, confirmed=confirmed)
        self._logger.debug(
            "Agent tool finished: name=%s status=%s duration=%.3fs",
            result.tool_name,
            result.status.value,
            result.duration_seconds,
        )
        if result.status is ToolStatus.SUCCESS:
            evidence_keys = {
                "level",
                "muted",
                "battery_percent",
                "current_value",
                "state_after",
                "reminder_id",
            }
            verified = result.data.get("verified") is True or (
                result.data.get("state_aware") is True
                or bool(evidence_keys.intersection(result.data))
            )
            prefix = "Verified result" if verified else "Completed request"
            self._progress(on_progress, f"{prefix}: {result.message}")
            with self._lock:
                self._last_successful_plans = plans
            recorder = getattr(self.planner, "record_success", None)
            if callable(recorder):
                recorder(plans, prompt=prompt)
        else:
            self._progress(
                on_progress,
                f"Stopped safely: {result.message}",
            )
        return AgentResponse(result.message, plan, result, plans=plans)

    def task_snapshot(self, task_id: int) -> AgentTaskSnapshot | None:
        return self.tasks.snapshot(task_id)

    def task_snapshots(self) -> tuple[AgentTaskSnapshot, ...]:
        return self.tasks.snapshots()

    def cancel_task(self, task_id: int) -> AgentTaskSnapshot | None:
        return self.tasks.cancel(task_id)

    def drain_notifications(self) -> tuple[AgentTaskSnapshot, ...]:
        return self.tasks.drain_notifications()

    def capability_status(self) -> str:
        report = self.registry.capability_report()
        ready = sum(1 for _, available, _ in report if available)
        unavailable = [name for name, available, _ in report if not available]
        task_states = self.task_snapshots()
        active = sum(
            1
            for snapshot in task_states
            if snapshot.status in {AgentTaskStatus.QUEUED, AgentTaskStatus.RUNNING}
        )
        waiting = sum(1 for snapshot in task_states if snapshot.status is AgentTaskStatus.WAITING)
        unavailable_text = ", ".join(unavailable) if unavailable else "none"
        return (
            f"Agent tools: {ready}/{len(report)} ready; unavailable: {unavailable_text}; "
            f"active tasks: {active}; waiting for input: {waiting}."
        )

    def diagnose(self, task_id: int) -> str:
        snapshot = self.task_snapshot(task_id)
        if snapshot is None:
            return f"I couldn't find task {task_id}."
        details = self.describe_task(snapshot)
        if snapshot.completed_messages:
            details += " Verified completed results: " + " ".join(snapshot.completed_messages)
        if snapshot.recovered:
            details += " This record was recovered after a restart."
        return details

    def reminder_snapshots(self) -> tuple[Reminder, ...]:
        return self.reminders.list_recent() if self.reminders is not None else ()

    def cancel_reminder(self, reminder_id: int) -> Reminder | None:
        return self.reminders.cancel(reminder_id) if self.reminders is not None else None

    def drain_reminders(self) -> tuple[Reminder, ...]:
        return self.reminders.drain_notifications() if self.reminders is not None else ()

    def _handle_pending(
        self,
        prompt: str,
        *,
        on_progress: ProgressHandler | None = None,
    ) -> AgentResponse | None:
        clean = prompt.strip()
        with self._lock:
            confirmation = self._pending_confirmation
            clarification = self._pending_clarification
        if confirmation is not None and CONFIRM_NO.fullmatch(clean):
            with self._lock:
                self._pending_confirmation = None
            return AgentResponse(
                "Cancelled. Nothing was changed.",
                self._no_plan("The user declined the pending plan."),
            )
        if confirmation is not None and CONFIRM_YES.fullmatch(clean):
            with self._lock:
                self._pending_confirmation = None
            return self._execute_plans(
                confirmation.prompt,
                confirmation.plans,
                confirmed=True,
                on_progress=on_progress,
            )
        if clarification is not None and CONFIRM_NO.fullmatch(clean):
            with self._lock:
                self._pending_clarification = None
            self.tasks.close_waiting(
                clarification.task_id,
                status=AgentTaskStatus.CANCELLED,
                message="The requested Spotify selection was cancelled.",
            )
            return AgentResponse(
                "Okay, I cancelled that selection.",
                self._no_plan("The user cancelled a clarification."),
            )
        if clarification is not None:
            choice = self._clarification_choice(clean, clarification)
            if choice is not None:
                with self._lock:
                    self._pending_clarification = None
                plan = AgentPlan(
                    "spotify_play_track",
                    {"query": clarification.query, "choice": choice},
                    "The user selected a clarified Spotify result.",
                    AgentPlanSource.DETERMINISTIC,
                )
                response = self._execute_plans(
                    f"Play Spotify choice {choice} for {clarification.query}",
                    (plan,),
                    on_progress=on_progress,
                )
                replacement = (
                    f" Continued as task {response.task.task_id}."
                    if response.task is not None
                    else " The selection was processed immediately."
                )
                self.tasks.close_waiting(
                    clarification.task_id,
                    status=AgentTaskStatus.RESOLVED,
                    message="Clarification received." + replacement,
                )
                return response
        return None

    def _task_control(self, prompt: str) -> AgentResponse | None:
        cancel = TASK_CANCEL_PATTERN.search(prompt)
        if cancel is not None:
            task_id = int(cancel.group(1))
            snapshot = self.cancel_task(task_id)
            text = (
                f"I couldn't find task {task_id}."
                if snapshot is None
                else self.describe_task(snapshot)
            )
            return AgentResponse(text, self._no_plan("Handled task cancellation."))
        status = TASK_STATUS_PATTERN.search(prompt)
        if status is not None:
            task_id = int(status.group(1))
            snapshot = self.task_snapshot(task_id)
            text = (
                f"I couldn't find task {task_id}."
                if snapshot is None
                else self.describe_task(snapshot)
            )
            return AgentResponse(text, self._no_plan("Handled task status."))
        if TASK_LIST_PATTERN.search(prompt):
            return AgentResponse(
                self.describe_tasks(self.task_snapshots()),
                self._no_plan("Handled task listing."),
            )
        return None

    def _reminder_control(self, prompt: str) -> AgentResponse | None:
        cancel = REMINDER_CANCEL_PATTERN.search(prompt)
        if cancel is not None:
            reminder_id = int(cancel.group(1))
            reminder = self.cancel_reminder(reminder_id)
            text = (
                f"I couldn't find reminder {reminder_id}."
                if reminder is None
                else f"Reminder {reminder_id} is {reminder.status}."
            )
            return AgentResponse(text, self._no_plan("Handled reminder cancellation."))
        if REMINDER_LIST_PATTERN.search(prompt):
            reminders = self.reminder_snapshots()
            if not reminders:
                text = "There are no saved reminders."
            else:
                text = " ".join(
                    f"Reminder {item.reminder_id} is {item.status}, due "
                    f"{item.due_local}: {item.message}."
                    for item in reminders
                )
            return AgentResponse(text, self._no_plan("Handled reminder listing."))
        return None

    def _capture_task_result(self, snapshot: AgentTaskSnapshot) -> None:
        with self._lock:
            plans = self._submitted_plans.pop(snapshot.task_id, ())
            if snapshot.status is AgentTaskStatus.COMPLETED and plans:
                self._last_successful_plans = plans
                recorder = getattr(self.planner, "record_success", None)
                if callable(recorder):
                    recorder(plans, prompt=snapshot.prompt)
            if snapshot.status is AgentTaskStatus.WAITING:
                interaction = snapshot.interaction
                candidates = interaction.get("candidates")
                query = interaction.get("query")
                if (
                    interaction.get("kind") == "spotify_track"
                    and isinstance(query, str)
                    and isinstance(candidates, list)
                ):
                    self._pending_clarification = _PendingClarification(
                        snapshot.task_id,
                        query,
                        tuple(item for item in candidates if isinstance(item, dict)),
                        monotonic() + 120.0,
                    )

    def _repeat_plans(self, prompt: str) -> tuple[AgentPlan, ...] | None:
        match = REPEAT_PATTERN.fullmatch(prompt.strip())
        if match is None:
            return None
        with self._lock:
            previous = self._last_successful_plans
        if not previous:
            return ()
        override = int(match.group(1)) if match.group(1) is not None else None
        if override is None:
            return previous
        if not 0 <= override <= 100:
            return ()
        updated: list[AgentPlan] = []
        changed = False
        for plan in previous:
            arguments = dict(plan.arguments)
            if plan.tool_name == "control_volume" and arguments.get("action") == "set":
                arguments["level"] = override
                changed = True
            updated.append(
                AgentPlan(plan.tool_name, arguments, "Repeated with a user override.", plan.source)
            )
        return tuple(updated) if changed else ()

    def _expire_pending(self) -> None:
        now = monotonic()
        with self._lock:
            if self._pending_confirmation and self._pending_confirmation.expires_at <= now:
                self._pending_confirmation = None
            if self._pending_clarification and self._pending_clarification.expires_at <= now:
                self._pending_clarification = None

    @staticmethod
    def _clarification_choice(prompt: str, clarification: _PendingClarification) -> int | None:
        words = {"first": 1, "one": 1, "second": 2, "two": 2, "third": 3, "three": 3}
        clean = prompt.casefold().strip(" .!?")
        choice = int(clean) if clean.isdecimal() else words.get(clean)
        if choice is not None and 1 <= choice <= len(clarification.candidates):
            return choice
        for candidate in clarification.candidates:
            number = candidate.get("choice")
            name = candidate.get("name")
            artists = candidate.get("artists")
            if not isinstance(number, int):
                continue
            labels = [str(name)] if isinstance(name, str) else []
            if isinstance(artists, list):
                labels.extend(str(artist) for artist in artists)
            if any(label.casefold() in clean for label in labels if label):
                return number
        return None

    @staticmethod
    def _describe_plan(plan: AgentPlan) -> str:
        return f"{plan.tool_name} with {plan.arguments}"

    @staticmethod
    def _progress(handler: ProgressHandler | None, message: str) -> None:
        if handler is not None:
            handler(message)

    @staticmethod
    def _tool_stage(tool_name: str) -> str:
        labels = {
            "control_media": "controlling the current media",
            "spotify_control_playback": "controlling Spotify playback",
            "spotify_play_track": "finding and playing the Spotify track",
            "open_application": "opening the application",
            "control_volume": "adjusting the volume",
            "control_brightness": "adjusting the screen brightness",
            "search_approved_folder": "searching the approved folder",
            "open_folder": "opening the folder",
            "create_reminder": "saving the reminder",
        }
        return labels.get(tool_name, tool_name.replace("_", " "))

    @staticmethod
    def describe_task(snapshot: AgentTaskSnapshot) -> str:
        progress = f"{snapshot.completed_steps}/{snapshot.total_steps} steps"
        return (
            f"Task {snapshot.task_id} is {snapshot.status.value} ({progress}). {snapshot.message}"
        )

    @classmethod
    def describe_tasks(cls, snapshots: tuple[AgentTaskSnapshot, ...]) -> str:
        if not snapshots:
            return "There are no tracked tasks yet."
        return " ".join(cls.describe_task(snapshot) for snapshot in snapshots[-10:])

    @staticmethod
    def _no_plan(reason: str) -> AgentPlan:
        return AgentPlan(None, {}, reason, AgentPlanSource.NONE)
