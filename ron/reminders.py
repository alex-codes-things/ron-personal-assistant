"""Persistent local timers and reminders with no model or network dependency."""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Reminder:
    reminder_id: int
    message: str
    due_at: float
    status: str

    @property
    def due_local(self) -> str:
        return datetime.fromtimestamp(self.due_at).astimezone().strftime("%d %b %Y at %I:%M %p")


type ReminderListener = Callable[[Reminder], None]


class ReminderManager:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._condition = threading.Condition()
        self._notifications: queue.SimpleQueue[Reminder] = queue.SimpleQueue()
        self._listeners: list[ReminderListener] = []
        self._stop = False
        self._change_version = 0
        self._worker: threading.Thread | None = None
        self._logger = logging.getLogger(__name__)
        self._initialise()

    def start(self) -> None:
        with self._condition:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stop = False
            self._worker = threading.Thread(
                target=self._run,
                name="ron-reminders",
                daemon=True,
            )
            self._worker.start()

    def stop(self) -> None:
        with self._condition:
            self._stop = True
            self._condition.notify_all()
            worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=2.0)

    def add_listener(self, listener: ReminderListener) -> None:
        with self._condition:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def create(self, seconds: int, message: str) -> Reminder:
        if not 1 <= seconds <= 31_536_000:
            raise ValueError("Reminder duration must be between 1 second and 1 year")
        clean_message = message.strip()
        if not clean_message or len(clean_message) > 240:
            raise ValueError("Reminder message is invalid")
        due_at = time.time() + seconds
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO reminders (message, due_at, status) VALUES (?, ?, 'pending')",
                (clean_message, due_at),
            )
            reminder_id = int(cursor.lastrowid)
        reminder = Reminder(reminder_id, clean_message, due_at, "pending")
        with self._condition:
            self._change_version += 1
            self._condition.notify_all()
        return reminder

    def cancel(self, reminder_id: int) -> Reminder | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT reminder_id, message, due_at, status FROM reminders WHERE reminder_id=?",
                (reminder_id,),
            ).fetchone()
            if row is None:
                return None
            if row[3] == "pending":
                connection.execute(
                    "UPDATE reminders SET status='cancelled' WHERE reminder_id=?",
                    (reminder_id,),
                )
                row = (row[0], row[1], row[2], "cancelled")
        with self._condition:
            self._change_version += 1
            self._condition.notify_all()
        return Reminder(int(row[0]), str(row[1]), float(row[2]), str(row[3]))

    def list_recent(self, limit: int = 20) -> tuple[Reminder, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT reminder_id, message, due_at, status FROM reminders
                ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, due_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(
            Reminder(int(row[0]), str(row[1]), float(row[2]), str(row[3])) for row in rows
        )

    def drain_notifications(self) -> tuple[Reminder, ...]:
        result: list[Reminder] = []
        while True:
            try:
                result.append(self._notifications.get_nowait())
            except queue.Empty:
                return tuple(result)

    def _run(self) -> None:
        while True:
            with self._condition:
                if self._stop:
                    return
                observed_version = self._change_version
            reminder = self._next_pending()
            if reminder is None:
                with self._condition:
                    if self._change_version == observed_version and not self._stop:
                        self._condition.wait(timeout=30.0)
                continue
            delay = reminder.due_at - time.time()
            if delay > 0:
                with self._condition:
                    if self._change_version == observed_version and not self._stop:
                        self._condition.wait(timeout=min(delay, 30.0))
                continue
            with self._connection() as connection:
                changed = connection.execute(
                    """
                    UPDATE reminders SET status='fired'
                    WHERE reminder_id=? AND status='pending'
                    """,
                    (reminder.reminder_id,),
                ).rowcount
            if not changed:
                continue
            fired = Reminder(reminder.reminder_id, reminder.message, reminder.due_at, "fired")
            self._notifications.put(fired)
            with self._condition:
                listeners = tuple(self._listeners)
            for listener in listeners:
                try:
                    listener(fired)
                except Exception:
                    self._logger.exception("Reminder listener failed")

    def _next_pending(self) -> Reminder | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT reminder_id, message, due_at, status FROM reminders
                WHERE status='pending' ORDER BY due_at ASC LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return Reminder(int(row[0]), str(row[1]), float(row[2]), str(row[3]))

    def _initialise(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message TEXT NOT NULL,
                    due_at REAL NOT NULL,
                    status TEXT NOT NULL
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
