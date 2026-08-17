"""Private SQLite journal for task recovery and diagnostics."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ron.agent.models import AgentPlan, AgentPlanSource, AgentTaskSnapshot, AgentTaskStatus


@dataclass(frozen=True, slots=True)
class JournalEntry:
    snapshot: AgentTaskSnapshot
    steps: tuple[AgentPlan, ...]
    confirmed: bool


class AgentTaskJournal:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    def save(
        self,
        snapshot: AgentTaskSnapshot,
        steps: tuple[AgentPlan, ...],
        *,
        confirmed: bool,
    ) -> None:
        step_data = [
            {
                "tool_name": step.tool_name,
                "arguments": step.arguments,
                "reason": step.reason,
                "source": step.source.value,
            }
            for step in steps
        ]
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO agent_tasks (
                    task_id, prompt, status, steps_json, confirmed, total_steps,
                    completed_steps, current_tool, message, cancel_requested,
                    completed_messages_json, recovered, interaction_json, updated_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(task_id) DO UPDATE SET
                    prompt=excluded.prompt,
                    status=excluded.status,
                    steps_json=excluded.steps_json,
                    confirmed=excluded.confirmed,
                    total_steps=excluded.total_steps,
                    completed_steps=excluded.completed_steps,
                    current_tool=excluded.current_tool,
                    message=excluded.message,
                    cancel_requested=excluded.cancel_requested,
                    completed_messages_json=excluded.completed_messages_json,
                    recovered=excluded.recovered,
                    interaction_json=excluded.interaction_json,
                    updated_utc=CURRENT_TIMESTAMP
                """,
                (
                    snapshot.task_id,
                    snapshot.prompt,
                    snapshot.status.value,
                    json.dumps(step_data, separators=(",", ":"), allow_nan=False),
                    int(confirmed),
                    snapshot.total_steps,
                    snapshot.completed_steps,
                    snapshot.current_tool,
                    snapshot.message,
                    int(snapshot.cancel_requested),
                    json.dumps(snapshot.completed_messages, allow_nan=False),
                    int(snapshot.recovered),
                    json.dumps(snapshot.interaction, separators=(",", ":"), allow_nan=False),
                ),
            )

    def load_recent(self, limit: int = 30) -> tuple[JournalEntry, ...]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT task_id, prompt, status, steps_json, confirmed, total_steps,
                       completed_steps, current_tool, message, cancel_requested,
                       completed_messages_json, recovered, interaction_json
                FROM agent_tasks ORDER BY task_id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        entries: list[JournalEntry] = []
        for row in reversed(rows):
            try:
                status = AgentTaskStatus(str(row[2]))
                raw_steps = json.loads(str(row[3]))
                raw_messages = json.loads(str(row[10]))
                interaction = json.loads(str(row[12]))
                steps = tuple(
                    AgentPlan(
                        item.get("tool_name"),
                        dict(item.get("arguments", {})),
                        str(item.get("reason", "Recovered task step.")),
                        AgentPlanSource(str(item.get("source", "none"))),
                    )
                    for item in raw_steps
                    if isinstance(item, dict)
                )
                messages = tuple(
                    str(message) for message in raw_messages if isinstance(message, str)
                )
                interaction_data = interaction if isinstance(interaction, dict) else {}
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
            snapshot = AgentTaskSnapshot(
                task_id=int(row[0]),
                prompt=str(row[1]),
                status=status,
                total_steps=int(row[5]),
                completed_steps=int(row[6]),
                current_tool=str(row[7]) if row[7] is not None else None,
                message=str(row[8]),
                cancel_requested=bool(row[9]),
                completed_messages=messages,
                recovered=bool(row[11]),
                interaction=interaction_data,
            )
            entries.append(JournalEntry(snapshot, steps, bool(row[4])))
        return tuple(entries)

    def _initialise(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_tasks (
                    task_id INTEGER PRIMARY KEY,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    confirmed INTEGER NOT NULL,
                    total_steps INTEGER NOT NULL,
                    completed_steps INTEGER NOT NULL,
                    current_tool TEXT,
                    message TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL,
                    completed_messages_json TEXT NOT NULL,
                    recovered INTEGER NOT NULL,
                    interaction_json TEXT NOT NULL,
                    updated_utc TEXT NOT NULL
                )
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=2.0)
        try:
            connection.execute("PRAGMA journal_mode=WAL").close()
            connection.execute("PRAGMA synchronous=NORMAL").close()
            with connection:
                yield connection
        finally:
            connection.close()
