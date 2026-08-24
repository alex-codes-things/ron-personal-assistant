"""Tiny local core-memory store for facts Ron needs without the external drive."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class CoreMemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialise()

    def set(self, key: str, value: Any) -> None:
        clean_key = key.strip()
        if not clean_key:
            raise ValueError("Core memory keys cannot be empty")
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO core_memory(key, value_json, updated_utc)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value_json=excluded.value_json,
                    updated_utc=CURRENT_TIMESTAMP
                """,
                (clean_key, payload),
            )

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT value_json FROM core_memory WHERE key=?", (key.strip(),)
            ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(str(row[0]))
        except (ValueError, TypeError):
            return default

    def delete(self, key: str) -> bool:
        with self._lock, self._connection() as connection:
            changed = connection.execute(
                "DELETE FROM core_memory WHERE key=?", (key.strip(),)
            ).rowcount
        return bool(changed)

    def _initialise(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS core_memory (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
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
