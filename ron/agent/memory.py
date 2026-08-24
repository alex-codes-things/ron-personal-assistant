"""Small persistent working memory for short conversational references."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from ron.agent.models import AgentPlan, AgentPlanSource, ToolResult


class WorkingMemory:
    """Remember only recent task/workspace context, not an unbounded chat history."""

    VERSION = 1

    def __init__(self, path: Path, *, ttl_seconds: float = 6 * 60 * 60) -> None:
        if ttl_seconds < 60:
            raise ValueError("Working-memory TTL must be at least one minute")
        self.path = path
        self.ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._state: dict[str, object] = {
            "version": self.VERSION,
            "updated_at": 0.0,
            "last_task_id": None,
            "last_process_id": None,
            "workspace": None,
            "last_target": None,
            "last_plans": [],
        }
        self._load()

    @property
    def last_task_id(self) -> int | None:
        return self._integer("last_task_id")

    @property
    def last_process_id(self) -> int | None:
        return self._integer("last_process_id")

    @property
    def workspace(self) -> str | None:
        with self._lock:
            value = self._state.get("workspace")
        return value if isinstance(value, str) and value else None

    @property
    def last_target(self) -> str | None:
        with self._lock:
            value = self._state.get("last_target")
        return value if isinstance(value, str) and value else None

    def remember_workspace(self, workspace: str) -> None:
        clean = workspace.strip()
        if clean:
            self._set("workspace", clean)

    def remember_task(self, task_id: int) -> None:
        if task_id <= 0:
            return
        self._set("last_task_id", task_id)

    def remember_plans(self, plans: tuple[AgentPlan, ...]) -> None:
        serialised = []
        for plan in plans[:4]:
            if plan.tool_name is None:
                continue
            serialised.append(
                {
                    "tool": plan.tool_name,
                    "arguments": dict(plan.arguments),
                    "reason": plan.reason,
                    "source": plan.source.value,
                }
            )
        self._set("last_plans", serialised)

    def recalled_plans(self) -> tuple[AgentPlan, ...]:
        if self._expired():
            return ()
        with self._lock:
            payload = self._state.get("last_plans")
        if not isinstance(payload, list):
            return ()
        plans: list[AgentPlan] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            tool = item.get("tool")
            arguments = item.get("arguments")
            reason = item.get("reason")
            source = item.get("source")
            if not isinstance(tool, str) or not isinstance(arguments, dict):
                continue
            try:
                source_value = AgentPlanSource(str(source))
            except ValueError:
                source_value = AgentPlanSource.DETERMINISTIC
            plans.append(
                AgentPlan(
                    tool,
                    dict(arguments),
                    str(reason or "Recalled from recent working memory."),
                    source_value,
                )
            )
        return tuple(plans)

    def remember_result(self, result: ToolResult) -> None:
        data = result.data
        updates: dict[str, object] = {}
        workspace = data.get("workspace")
        if isinstance(workspace, str) and workspace:
            updates["workspace"] = workspace
        process_id = data.get("process_id")
        if isinstance(process_id, int) and process_id > 0:
            updates["last_process_id"] = process_id
        for key in ("folder", "application", "device_id"):
            value = data.get(key)
            if isinstance(value, str) and value:
                updates["last_target"] = value
                break
        if updates:
            self._update(updates)

    def status_label(self) -> str:
        if self._expired():
            return "working memory: idle"
        details = []
        if self.last_task_id is not None:
            details.append(f"task {self.last_task_id}")
        if self.workspace:
            details.append("workspace")
        if self.last_process_id is not None:
            details.append(f"process {self.last_process_id}")
        return "working memory: " + (", ".join(details) if details else "ready")

    def _integer(self, key: str) -> int | None:
        if self._expired():
            return None
        with self._lock:
            value = self._state.get(key)
        return value if isinstance(value, int) and value > 0 else None

    def _set(self, key: str, value: object) -> None:
        self._update({key: value})

    def _update(self, updates: dict[str, object]) -> None:
        with self._lock:
            self._state.update(updates)
            self._state["updated_at"] = time.time()
            self._save_locked()

    def _expired(self) -> bool:
        with self._lock:
            updated = self._state.get("updated_at")
        return (
            not isinstance(updated, (int, float))
            or time.time() - float(updated) > self.ttl_seconds
        )

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict) or payload.get("version") != self.VERSION:
            return
        updated = payload.get("updated_at")
        if not isinstance(updated, (int, float)):
            return
        if time.time() - float(updated) > self.ttl_seconds:
            return
        with self._lock:
            self._state.update(payload)

    def _save_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(self._state, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError:
            return
