"""Local metadata catalog that stays available when the HDD is disconnected."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ron.memory.models import MemoryKind, MemoryRecord, VisualCategory, VisualMemoryRecord

_SEARCH_STOP_WORDS = {
    "and", "are", "but", "for", "from", "have", "how", "into", "just", "not",
    "that", "the", "their", "then", "there", "they", "this", "was", "what",
    "when", "where", "which", "with", "you", "your", "about", "remember",
}


class MemoryCatalog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialise()

    def upsert_memory(self, record: MemoryRecord) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO memories (
                    memory_id, kind, summary, relative_path, created_utc,
                    project, importance, queued, sha256, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    kind=excluded.kind,
                    summary=excluded.summary,
                    relative_path=excluded.relative_path,
                    project=excluded.project,
                    importance=excluded.importance,
                    queued=excluded.queued,
                    sha256=excluded.sha256,
                    metadata_json=excluded.metadata_json
                """,
                (
                    record.memory_id,
                    record.kind.value,
                    record.summary,
                    record.relative_path,
                    record.created_utc,
                    record.project,
                    record.importance,
                    int(record.queued),
                    record.sha256,
                    json.dumps(record.metadata, ensure_ascii=False, allow_nan=False),
                ),
            )

    def upsert_visual(self, record: VisualMemoryRecord) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO visual_memories (
                    visual_id, category, image_path, analysis_path, created_utc,
                    summary, application, project, queued, image_sha256, analysis_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(visual_id) DO UPDATE SET
                    category=excluded.category,
                    image_path=excluded.image_path,
                    analysis_path=excluded.analysis_path,
                    summary=excluded.summary,
                    application=excluded.application,
                    project=excluded.project,
                    queued=excluded.queued,
                    image_sha256=excluded.image_sha256,
                    analysis_sha256=excluded.analysis_sha256
                """,
                (
                    record.visual_id,
                    record.category.value,
                    record.image_path,
                    record.analysis_path,
                    record.created_utc,
                    record.summary,
                    record.application,
                    record.project,
                    int(record.queued),
                    record.image_sha256,
                    record.analysis_sha256,
                ),
            )

    def delete_memory(self, memory_id: str) -> bool:
        with self._lock, self._connection() as connection:
            changed = connection.execute(
                "DELETE FROM memories WHERE memory_id=?", (memory_id,)
            ).rowcount
        return bool(changed)

    def set_memory_queued(self, relative_path: str, queued: bool) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                "UPDATE memories SET queued=? WHERE relative_path=?",
                (int(queued), relative_path),
            )

    def set_visual_queued(self, visual_id: str, queued: bool) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                "UPDATE visual_memories SET queued=? WHERE visual_id=?",
                (int(queued), visual_id),
            )

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT memory_id, kind, summary, relative_path, created_utc,
                       project, importance, queued, sha256, metadata_json
                FROM memories WHERE memory_id=?
                """,
                (memory_id,),
            ).fetchone()
        return self._memory_from_row(row) if row is not None else None

    def get_memory_by_prefix(self, prefix: str) -> MemoryRecord | None:
        clean = prefix.strip().casefold()
        if len(clean) < 6 or not re.fullmatch(r"[0-9a-f]+", clean):
            return None
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT memory_id, kind, summary, relative_path, created_utc,
                       project, importance, queued, sha256, metadata_json
                FROM memories WHERE LOWER(memory_id) LIKE ? LIMIT 2
                """,
                (f"{clean}%",),
            ).fetchall()
        if len(rows) != 1:
            return None
        return self._memory_from_row(rows[0])

    def find_exact_summary(self, summary: str) -> MemoryRecord | None:
        clean = " ".join(summary.strip().split()).casefold().strip(" .!?")
        if not clean:
            return None
        with self._lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT memory_id, kind, summary, relative_path, created_utc,
                       project, importance, queued, sha256, metadata_json
                FROM memories
                WHERE LOWER(TRIM(summary, ' .!?'))=?
                ORDER BY importance DESC LIMIT 1
                """,
                (clean,),
            ).fetchone()
        return self._memory_from_row(row) if row is not None else None

    def get_visual(self, visual_id: str) -> VisualMemoryRecord | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT visual_id, category, image_path, analysis_path, created_utc,
                       summary, application, project, queued, image_sha256,
                       analysis_sha256
                FROM visual_memories WHERE visual_id=?
                """,
                (visual_id,),
            ).fetchone()
        if row is None:
            return None
        return VisualMemoryRecord(
            visual_id=str(row[0]),
            category=VisualCategory(str(row[1])),
            image_path=str(row[2]),
            analysis_path=str(row[3]),
            created_utc=str(row[4]),
            summary=str(row[5]) if row[5] is not None else None,
            application=str(row[6]) if row[6] is not None else None,
            project=str(row[7]) if row[7] is not None else None,
            queued=bool(row[8]),
            image_sha256=str(row[9]),
            analysis_sha256=str(row[10]),
        )

    def recent_memories(
        self,
        limit: int = 20,
        *,
        kinds: tuple[MemoryKind, ...] | None = None,
        min_importance: int = 0,
    ) -> tuple[MemoryRecord, ...]:
        where, parameters = self._filters(kinds, min_importance)
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT memory_id, kind, summary, relative_path, created_utc,
                       project, importance, queued, sha256, metadata_json
                FROM memories {where}
                ORDER BY created_utc DESC LIMIT ?
                """,
                (*parameters, limit),
            ).fetchall()
        return tuple(self._memory_from_row(row) for row in rows)

    def search(
        self,
        query: str,
        limit: int = 6,
        *,
        kinds: tuple[MemoryKind, ...] | None = None,
        min_importance: int = 0,
        fallback_recent: bool = True,
    ) -> tuple[MemoryRecord, ...]:
        """Small offline lexical search; semantic embeddings can replace this later."""
        tokens = _search_tokens(query)
        if not tokens:
            if fallback_recent:
                return self.recent_memories(
                    limit, kinds=kinds, min_importance=min_importance
                )
            return ()

        text_match = " OR ".join("LOWER(summary) LIKE ?" for _ in tokens)
        text_parameters = tuple(f"%{token}%" for token in tokens)
        score_expression = " + ".join(
            "CASE WHEN LOWER(summary) LIKE ? THEN 1 ELSE 0 END" for _ in tokens
        )
        filter_where, filter_parameters = self._filters(kinds, min_importance)
        filter_clause = filter_where.removeprefix("WHERE ")
        where = f"WHERE ({text_match})"
        if filter_clause:
            where += f" AND {filter_clause}"

        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT memory_id, kind, summary, relative_path, created_utc,
                       project, importance, queued, sha256, metadata_json,
                       ({score_expression}) AS score
                FROM memories
                {where}
                ORDER BY score DESC, importance DESC, created_utc DESC
                LIMIT ?
                """,
                (*text_parameters, *text_parameters, *filter_parameters, limit),
            ).fetchall()
        return tuple(self._memory_from_row(row) for row in rows)

    def counts(self) -> tuple[int, int]:
        with self._lock, self._connection() as connection:
            memory_count = int(
                connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            )
            visual_count = int(
                connection.execute("SELECT COUNT(*) FROM visual_memories").fetchone()[0]
            )
        return memory_count, visual_count

    @staticmethod
    def _filters(
        kinds: tuple[MemoryKind, ...] | None,
        min_importance: int,
    ) -> tuple[str, tuple[object, ...]]:
        clauses: list[str] = []
        parameters: list[object] = []
        if kinds:
            placeholders = ", ".join("?" for _ in kinds)
            clauses.append(f"kind IN ({placeholders})")
            parameters.extend(kind.value for kind in kinds)
        if min_importance > 0:
            clauses.append("importance >= ?")
            parameters.append(int(min_importance))
        if not clauses:
            return "", ()
        return "WHERE " + " AND ".join(clauses), tuple(parameters)

    @staticmethod
    def _memory_from_row(row: tuple[object, ...]) -> MemoryRecord:
        try:
            metadata = json.loads(str(row[9]))
        except (ValueError, TypeError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return MemoryRecord(
            memory_id=str(row[0]),
            kind=MemoryKind(str(row[1])),
            summary=str(row[2]),
            relative_path=str(row[3]),
            created_utc=str(row[4]),
            project=str(row[5]) if row[5] is not None else None,
            importance=int(row[6]),
            queued=bool(row[7]),
            sha256=str(row[8]),
            metadata=metadata,
        )

    def _initialise(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE,
                    created_utc TEXT NOT NULL,
                    project TEXT,
                    importance INTEGER NOT NULL,
                    queued INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_memories_created ON memories(created_utc DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_memories_project ON memories(project)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS visual_memories (
                    visual_id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    image_path TEXT NOT NULL UNIQUE,
                    analysis_path TEXT NOT NULL UNIQUE,
                    created_utc TEXT NOT NULL,
                    summary TEXT,
                    application TEXT,
                    project TEXT,
                    queued INTEGER NOT NULL,
                    image_sha256 TEXT NOT NULL,
                    analysis_sha256 TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_visual_created ON visual_memories(created_utc DESC)"
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


def _search_tokens(query: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            token
            for token in re.findall(r"[a-z0-9][a-z0-9_]{2,}", query.casefold())
            if token not in _SEARCH_STOP_WORDS
        )
    )[:10]
