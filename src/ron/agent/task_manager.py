"""Persistent single-worker tasks with progress and cooperative cancellation."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from ron.agent.journal import AgentTaskJournal, JournalEntry
from ron.agent.models import (
    AgentPlan,
    AgentTaskSnapshot,
    AgentTaskStatus,
    ToolResult,
    ToolStatus,
)
from ron.agent.registry import ToolRegistry

TaskListener = Callable[[AgentTaskSnapshot], None]
CLOSED_TASK_STATES = {
    AgentTaskStatus.COMPLETED,
    AgentTaskStatus.FAILED,
    AgentTaskStatus.CANCELLED,
    AgentTaskStatus.RESOLVED,
    AgentTaskStatus.TIMED_OUT,
}


@dataclass(slots=True)
class _TaskRecord:
    task_id: int
    prompt: str
    steps: tuple[AgentPlan, ...]
    confirmed: bool = False
    status: AgentTaskStatus = AgentTaskStatus.QUEUED
    completed_steps: int = 0
    current_tool: str | None = None
    message: str = "Waiting to start."
    cancel_requested: bool = False
    completed_messages: list[str] = field(default_factory=list)
    completed_results: list[tuple[AgentPlan, ToolResult]] = field(default_factory=list)
    recovered: bool = False
    interaction: dict[str, object] = field(default_factory=dict)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def snapshot(self) -> AgentTaskSnapshot:
        with self.lock:
            return AgentTaskSnapshot(
                task_id=self.task_id,
                prompt=self.prompt,
                status=self.status,
                total_steps=len(self.steps),
                completed_steps=self.completed_steps,
                current_tool=self.current_tool,
                message=self.message,
                cancel_requested=self.cancel_requested,
                completed_messages=tuple(self.completed_messages),
                recovered=self.recovered,
                interaction=dict(self.interaction),
            )


class AgentTaskManager:
    """Run one task step at a time and retain safe state across restarts."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        journal: AgentTaskJournal | None = None,
        maximum_tasks: int = 50,
        retained_tasks: int = 30,
    ) -> None:
        if not 1 <= maximum_tasks <= 100 or not 1 <= retained_tasks <= maximum_tasks:
            raise ValueError("Agent task limits are invalid")
        self.registry = registry
        self.journal = journal
        self.maximum_tasks = maximum_tasks
        self.retained_tasks = retained_tasks
        self._tasks: dict[int, _TaskRecord] = {}
        self._queue: queue.Queue[_TaskRecord | None] = queue.Queue(maximum_tasks)
        self._notifications: queue.SimpleQueue[AgentTaskSnapshot] = queue.SimpleQueue()
        self._listeners: list[TaskListener] = []
        self._progress_listeners: list[TaskListener] = []
        self._next_id = 1
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._logger = logging.getLogger(__name__)
        self._recover()

    def start(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stop.clear()
            self._worker = threading.Thread(
                target=self._run,
                name="ron-agent-worker",
                daemon=True,
            )
            self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            records = tuple(self._tasks.values())
            worker = self._worker
        for record in records:
            with record.lock:
                if record.status in {AgentTaskStatus.QUEUED, AgentTaskStatus.RUNNING}:
                    record.cancel_requested = True
                    record.cancel_event.set()
                    record.message = "Ron shut down; this task will not resume automatically."
                    self._save(record)
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=2.0)

    def add_listener(self, listener: TaskListener) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def add_progress_listener(self, listener: TaskListener) -> None:
        with self._lock:
            if listener not in self._progress_listeners:
                self._progress_listeners.append(listener)

    def submit(
        self,
        prompt: str,
        steps: tuple[AgentPlan, ...],
        *,
        confirmed: bool = False,
    ) -> AgentTaskSnapshot:
        if not 1 <= len(steps) <= 4:
            raise ValueError("Background tasks must contain between one and four steps")
        self.start()
        with self._lock:
            if self._queue.full():
                raise RuntimeError("Ron's task queue is full")
            task_id = self._next_id
            self._next_id += 1
            record = _TaskRecord(
                task_id=task_id,
                prompt=prompt,
                steps=steps,
                confirmed=confirmed,
            )
            self._tasks[task_id] = record
            self._trim_locked()
            self._save(record)
            self._queue.put_nowait(record)
            self._notify_progress(record)
            return record.snapshot()

    def snapshot(self, task_id: int) -> AgentTaskSnapshot | None:
        with self._lock:
            record = self._tasks.get(task_id)
        return record.snapshot() if record is not None else None

    def snapshots(self) -> tuple[AgentTaskSnapshot, ...]:
        with self._lock:
            records = tuple(self._tasks[task_id] for task_id in sorted(self._tasks))
        return tuple(record.snapshot() for record in records)

    def cancel(self, task_id: int) -> AgentTaskSnapshot | None:
        with self._lock:
            record = self._tasks.get(task_id)
        if record is None:
            return None
        with record.lock:
            if record.status in CLOSED_TASK_STATES:
                return record.snapshot()
            record.cancel_requested = True
            record.cancel_event.set()
            if record.status in {AgentTaskStatus.QUEUED, AgentTaskStatus.WAITING}:
                was_waiting = record.status is AgentTaskStatus.WAITING
                record.status = AgentTaskStatus.CANCELLED
                record.message = (
                    "Cancelled while waiting for your choice."
                    if was_waiting
                    else "Cancelled before the first step ran."
                )
                record.current_tool = None
                self._finish(record)
            else:
                record.message = (
                    "Cancellation requested; the current tool will stop at its next "
                    "safe checkpoint."
                )
                self._save(record)
                self._notify_progress(record)
            return record.snapshot()

    def close_waiting(
        self,
        task_id: int,
        *,
        status: AgentTaskStatus,
        message: str,
    ) -> AgentTaskSnapshot | None:
        if status not in {AgentTaskStatus.RESOLVED, AgentTaskStatus.CANCELLED}:
            raise ValueError("A waiting task can only be resolved or cancelled")
        with self._lock:
            record = self._tasks.get(task_id)
        if record is None:
            return None
        with record.lock:
            if record.status is not AgentTaskStatus.WAITING:
                return record.snapshot()
            record.status = status
            record.message = message
            record.current_tool = None
            record.interaction = {}
        self._finish(record)
        return record.snapshot()

    def drain_notifications(self) -> tuple[AgentTaskSnapshot, ...]:
        notifications: list[AgentTaskSnapshot] = []
        while True:
            try:
                notifications.append(self._notifications.get_nowait())
            except queue.Empty:
                return tuple(notifications)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                record = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if record is None:
                return
            try:
                self._execute(record)
            finally:
                self._queue.task_done()

    def _execute(self, record: _TaskRecord) -> None:
        with record.lock:
            if record.status is AgentTaskStatus.CANCELLED or record.cancel_requested:
                return

        for index, plan in enumerate(record.steps, start=1):
            assert plan.tool_name is not None
            preflight = self.registry.preflight(
                plan.tool_name,
                plan.arguments,
                confirmed=record.confirmed,
            )
            if preflight.status is not ToolStatus.READY:
                with record.lock:
                    record.status = AgentTaskStatus.FAILED
                    record.current_tool = None
                    record.message = (
                        f"Final preflight failed at step {index}; no task step ran. "
                        f"{preflight.message}"
                    )
                self._finish(record)
                return

        with record.lock:
            record.status = AgentTaskStatus.RUNNING
            record.message = f"Starting {len(record.steps)} validated steps."
            self._save(record)
        self._notify_progress(record)

        for index, plan in enumerate(record.steps, start=1):
            assert plan.tool_name is not None
            with record.lock:
                if record.cancel_requested or self._stop.is_set():
                    self._cancel_running(record)
                    return
                record.current_tool = plan.tool_name
                record.message = f"Running step {index} of {len(record.steps)}: {plan.tool_name}."
                self._save(record)
            self._notify_progress(record)

            result = self.registry.execute(
                plan.tool_name,
                plan.arguments,
                confirmed=record.confirmed,
                cancel_event=record.cancel_event,
            )
            self._logger.debug(
                "Agent task step finished: task=%s step=%s tool=%s status=%s duration=%.3fs",
                record.task_id,
                index,
                result.tool_name,
                result.status.value,
                result.duration_seconds,
            )
            if result.status is ToolStatus.CLARIFICATION_REQUIRED:
                with record.lock:
                    record.status = AgentTaskStatus.WAITING
                    record.current_tool = None
                    record.message = result.message
                    record.interaction = dict(result.data)
                self._finish(record)
                return
            if result.status is ToolStatus.CANCELLED:
                self._interrupt_and_compensate(
                    record,
                    AgentTaskStatus.CANCELLED,
                    result.message,
                )
                return
            if result.status is ToolStatus.TIMED_OUT:
                self._interrupt_and_compensate(
                    record,
                    AgentTaskStatus.TIMED_OUT,
                    result.message,
                )
                return
            if result.status is not ToolStatus.SUCCESS:
                self._fail_and_compensate(record, index, result)
                return
            with record.lock:
                record.completed_results.append((plan, result))
                record.completed_messages.append(result.message)
                record.completed_steps = index
                record.message = (
                    f"Completed step {index} of {len(record.steps)}. {result.message}"
                )
                self._save(record)
            self._notify_progress(record)

        with record.lock:
            record.status = AgentTaskStatus.COMPLETED
            record.current_tool = None
            record.message = " ".join(record.completed_messages)
        self._finish(record)

    def _cancel_running(self, record: _TaskRecord) -> None:
        self._interrupt_and_compensate(
            record,
            AgentTaskStatus.CANCELLED,
            f"Cancelled safely after {record.completed_steps} of {len(record.steps)} steps.",
        )

    def _interrupt_and_compensate(
        self,
        record: _TaskRecord,
        status: AgentTaskStatus,
        message: str,
    ) -> None:
        rolled_back, rollback_failed = self._compensate_completed(record)
        details = [message]
        if record.completed_messages:
            details.append(
                "Completed before interruption: " + " ".join(record.completed_messages)
            )
        if rolled_back:
            details.append(f"Safely rolled back: {' '.join(rolled_back)}")
        if rollback_failed:
            details.append(f"Rollback warning: {' '.join(rollback_failed)}")
        with record.lock:
            record.status = status
            record.current_tool = None
            record.message = " ".join(details)
        self._finish(record)

    def _fail_and_compensate(
        self,
        record: _TaskRecord,
        failed_step: int,
        failure: ToolResult,
    ) -> None:
        rolled_back, rollback_failed = self._compensate_completed(record)
        completed = " ".join(record.completed_messages)
        details = [
            f"Step {failed_step} failed, so the remaining steps were skipped.",
            failure.message,
        ]
        if completed:
            details.append(f"Completed before the failure: {completed}")
        if rolled_back:
            details.append(f"Safely rolled back: {' '.join(rolled_back)}")
        if rollback_failed:
            details.append(f"Rollback warning: {' '.join(rollback_failed)}")
        with record.lock:
            record.status = AgentTaskStatus.FAILED
            record.current_tool = None
            record.message = " ".join(details)
        self._finish(record)

    def _compensate_completed(
        self, record: _TaskRecord
    ) -> tuple[list[str], list[str]]:
        rolled_back: list[str] = []
        rollback_failed: list[str] = []
        for plan, result in reversed(tuple(record.completed_results)):
            if plan.tool_name is None:
                continue
            compensation = self.registry.compensate(plan.tool_name, result)
            if compensation is None:
                continue
            if compensation.status is ToolStatus.SUCCESS:
                rolled_back.append(compensation.message)
            else:
                rollback_failed.append(compensation.message)
        return rolled_back, rollback_failed

    def _finish(self, record: _TaskRecord) -> None:
        self._save(record)
        snapshot = record.snapshot()
        self._notifications.put(snapshot)
        self._notify_progress(record)
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:
                self._logger.exception("Agent task listener failed")

    def _notify_progress(self, record: _TaskRecord) -> None:
        snapshot = record.snapshot()
        with self._lock:
            listeners = tuple(self._progress_listeners)
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:
                self._logger.exception("Agent progress listener failed")

    def _save(self, record: _TaskRecord) -> None:
        if self.journal is None:
            return
        try:
            self.journal.save(record.snapshot(), record.steps, confirmed=record.confirmed)
        except Exception:
            self._logger.exception("Could not save agent task journal")

    def _recover(self) -> None:
        if self.journal is None:
            return
        try:
            entries = self.journal.load_recent(self.retained_tasks)
        except Exception:
            self._logger.exception("Could not load agent task journal")
            return
        for entry in entries:
            record = self._record_from_entry(entry)
            self._tasks[record.task_id] = record
            self._next_id = max(self._next_id, record.task_id + 1)
            if record.status in {AgentTaskStatus.QUEUED, AgentTaskStatus.RUNNING}:
                record.status = AgentTaskStatus.FAILED
                record.current_tool = None
                record.cancel_requested = False
                record.recovered = True
                record.message = (
                    "Ron restarted before this task finished. It was not resumed automatically; "
                    "ask me to run it again if you still want it."
                )
                self._save(record)

    @staticmethod
    def _record_from_entry(entry: JournalEntry) -> _TaskRecord:
        snapshot = entry.snapshot
        return _TaskRecord(
            task_id=snapshot.task_id,
            prompt=snapshot.prompt,
            steps=entry.steps,
            confirmed=entry.confirmed,
            status=snapshot.status,
            completed_steps=snapshot.completed_steps,
            current_tool=snapshot.current_tool,
            message=snapshot.message,
            cancel_requested=snapshot.cancel_requested,
            completed_messages=list(snapshot.completed_messages),
            recovered=snapshot.recovered,
            interaction=dict(snapshot.interaction),
        )

    def _trim_locked(self) -> None:
        completed_ids = [
            task_id
            for task_id, record in sorted(self._tasks.items())
            if record.snapshot().status in CLOSED_TASK_STATES
        ]
        for task_id in completed_ids[:-self.retained_tasks]:
            self._tasks.pop(task_id, None)
