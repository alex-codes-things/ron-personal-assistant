"""High-level memory API built on Ron's resilient storage manager."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ron.memory.catalog import MemoryCatalog
from ron.memory.core_store import CoreMemoryStore
from ron.memory.models import MemoryKind, MemoryRecord, RecalledMemory
from ron.memory.policy import MemoryCandidate, MemoryPolicy, contains_secret
from ron.storage import StorageManager

_CONTEXT_KINDS = (
    MemoryKind.KNOWLEDGE,
    MemoryKind.PERSON,
    MemoryKind.PROJECT,
    MemoryKind.EXPERIENCE,
)


class MemoryService:
    """Keep a small offline catalog while full records live on long-term storage."""

    def __init__(self, project_root: Path, storage: StorageManager) -> None:
        local_core = Path(project_root) / "runtime" / "memory" / "core"
        self.storage = storage
        self.catalog = MemoryCatalog(local_core / "memory_catalog.sqlite")
        self.core = CoreMemoryStore(local_core / "core_memory.sqlite")
        self.policy = MemoryPolicy()

    def remember(
        self,
        kind: MemoryKind,
        content: str,
        *,
        summary: str | None = None,
        project: str | None = None,
        importance: int = 50,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        clean = content.strip()
        if not clean:
            raise ValueError("Memory content cannot be empty")
        importance = min(max(int(importance), 0), 100)
        created = datetime.now(UTC)
        memory_id = uuid.uuid4().hex
        relative = self._memory_path(kind, created, memory_id, project)
        compact_summary = _compact(summary or clean, 600)
        payload = {
            "schema": 1,
            "memory_id": memory_id,
            "kind": kind.value,
            "created_utc": created.isoformat(),
            "project": project,
            "importance": importance,
            "summary": compact_summary,
            "content": clean,
            "metadata": metadata or {},
        }
        stored = self.storage.save_json(relative, payload)
        record = MemoryRecord(
            memory_id=memory_id,
            kind=kind,
            summary=compact_summary,
            relative_path=relative,
            created_utc=created.isoformat(),
            project=project,
            importance=importance,
            queued=stored.queued,
            sha256=stored.sha256,
            metadata=metadata or {},
        )
        self.catalog.upsert_memory(record)
        return record

    def remember_unique(
        self,
        kind: MemoryKind,
        content: str,
        *,
        summary: str | None = None,
        project: str | None = None,
        importance: int = 50,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[MemoryRecord, bool]:
        compact_summary = _compact(summary or content, 600)
        existing = self.catalog.find_exact_summary(compact_summary)
        if existing is not None:
            return existing, False
        return (
            self.remember(
                kind,
                content,
                summary=compact_summary,
                project=project,
                importance=importance,
                metadata=metadata,
            ),
            True,
        )

    def remember_explicit(self, content: str) -> tuple[MemoryRecord, bool]:
        clean = " ".join(content.strip().split())
        if not clean:
            raise ValueError("Tell me what you want me to remember.")
        if contains_secret(clean):
            raise ValueError(
                "I won't store passwords, PINs, API keys, access tokens, or similar "
                "secrets in long-term memory."
            )
        project = "Ron" if re.search(r"\bRon\b", clean, re.IGNORECASE) else None
        kind = MemoryKind.PROJECT if project else MemoryKind.KNOWLEDGE
        return self.remember_unique(
            kind,
            clean,
            project=project,
            importance=90,
            metadata={"source": "explicit"},
        )

    def consider_user_statement(self, user: str) -> MemoryRecord | None:
        """Conservatively learn a stable user fact without trusting model output."""
        candidate = self.policy.candidate_from_user(user)
        if candidate is None:
            return None
        record, created = self._remember_candidate(candidate)
        return record if created else None

    def _remember_candidate(self, candidate: MemoryCandidate) -> tuple[MemoryRecord, bool]:
        return self.remember_unique(
            candidate.kind,
            candidate.content,
            project=candidate.project,
            importance=candidate.importance,
            metadata=candidate.metadata,
        )

    def remember_conversation(
        self,
        user: str,
        assistant: str,
        *,
        project: str | None = None,
    ) -> MemoryRecord:
        """Legacy/manual episodic storage; normal turns are no longer saved automatically."""
        user_clean = user.strip()
        assistant_clean = assistant.strip()
        summary = f"User: {_compact(user_clean, 220)} | Ron: {_compact(assistant_clean, 300)}"
        content = f"USER\n{user_clean}\n\nRON\n{assistant_clean}"
        return self.remember(
            MemoryKind.CONVERSATION,
            content,
            summary=summary,
            project=project,
            importance=35,
            metadata={"source": "episodic"},
        )

    def remember_experience(
        self,
        problem: str,
        solution: str,
        *,
        project: str | None = None,
        visual_ids: tuple[str, ...] = (),
        outcome: str = "resolved",
        importance: int = 75,
    ) -> MemoryRecord:
        problem_clean = problem.strip()
        solution_clean = solution.strip()
        if not problem_clean or not solution_clean:
            raise ValueError("Experience memories need both a problem and a solution")
        summary = (
            f"Problem: {_compact(problem_clean, 260)} | "
            f"Solution: {_compact(solution_clean, 300)}"
        )
        content = f"PROBLEM\n{problem_clean}\n\nSOLUTION\n{solution_clean}"
        return self.remember(
            MemoryKind.EXPERIENCE,
            content,
            summary=summary,
            project=project,
            importance=importance,
            metadata={
                "source": "experience",
                "visual_ids": list(visual_ids),
                "outcome": outcome.strip() or "resolved",
            },
        )

    def load(self, memory_id: str) -> dict[str, Any]:
        record = self.catalog.get_memory(memory_id)
        if record is None:
            raise KeyError(f"Unknown memory: {memory_id}")
        payload = self.storage.read_bytes(record.relative_path)
        digest = hashlib.sha256(payload).hexdigest()
        if record.sha256 and digest != record.sha256:
            raise OSError(f"Memory checksum verification failed: {memory_id}")
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Memory record is invalid: {memory_id}")
        return value

    def recall(
        self,
        query: str,
        *,
        limit: int = 5,
        include_conversations: bool = False,
    ) -> tuple[RecalledMemory, ...]:
        clean = " ".join(query.strip().split())
        kinds = None if include_conversations else _CONTEXT_KINDS
        records = self.catalog.search(
            clean,
            limit=limit,
            kinds=kinds,
            fallback_recent=not bool(clean),
        )
        recalled: list[RecalledMemory] = []
        for record in records:
            try:
                payload = self.load(record.memory_id)
                content = str(payload.get("content", record.summary)).strip() or record.summary
                full = True
            except (FileNotFoundError, OSError, ValueError, KeyError):
                # The compact summary intentionally lives on the laptop so recall still
                # works in degraded mode when the external drive is disconnected.
                content = record.summary
                full = False
            recalled.append(RecalledMemory(record, content, full))
        return tuple(recalled)

    def recent_user_memories(self, limit: int = 8) -> tuple[MemoryRecord, ...]:
        return self.catalog.recent_memories(limit, kinds=_CONTEXT_KINDS)

    def find_for_forget(self, query: str, *, limit: int = 5) -> tuple[MemoryRecord, ...]:
        clean = query.strip()
        by_id = self.catalog.get_memory_by_prefix(clean)
        if by_id is not None:
            return (by_id,)
        return self.catalog.search(clean, limit=limit, fallback_recent=False)

    def forget(self, memory_id: str) -> MemoryRecord:
        record = self.catalog.get_memory(memory_id)
        if record is None:
            raise KeyError(f"Unknown memory: {memory_id}")
        # Storage deletion is made durable first. If the HDD is offline, a deletion
        # tombstone is queued locally and applied when the bound drive returns.
        self.storage.delete(record.relative_path)
        self.catalog.delete_memory(memory_id)
        return record

    def set_core(self, key: str, value: Any) -> None:
        self.core.set(key, value)

    def get_core(self, key: str, default: Any = None) -> Any:
        return self.core.get(key, default)

    def context_for_prompt(self, prompt: str, *, limit: int = 4) -> str:
        """Return only relevant, durable memories for bounded model context."""
        if len(_search_terms(prompt)) < 1:
            return ""
        records = self.catalog.search(
            prompt,
            limit=limit,
            kinds=_CONTEXT_KINDS,
            min_importance=55,
            fallback_recent=False,
        )
        if not records:
            return ""
        snippets = [
            f"- [{record.kind.value}] {record.summary}"
            for record in records
        ]
        return "Relevant saved memories (retrieved by Ron; may be incomplete):\n" + "\n".join(
            snippets
        )

    def refresh_queue_flags(self) -> None:
        for record in self.catalog.recent_memories(limit=500):
            pending = self.storage.is_pending(record.relative_path)
            if pending != record.queued:
                self.catalog.set_memory_queued(record.relative_path, pending)

    def status_label(self) -> str:
        memories, visuals = self.catalog.counts()
        return (
            f"memory: {memories} indexed, {visuals} visual, "
            f"auto-learn {self.policy.mode.value}"
        )

    @staticmethod
    def _memory_path(
        kind: MemoryKind,
        created: datetime,
        memory_id: str,
        project: str | None,
    ) -> str:
        year_month = f"{created:%Y}/{created:%m}"
        if kind is MemoryKind.CONVERSATION:
            base = "Memory/Conversations"
        elif kind is MemoryKind.KNOWLEDGE:
            base = "Memory/Knowledge"
        elif kind is MemoryKind.PERSON:
            base = "Memory/People"
        elif kind is MemoryKind.EXPERIENCE:
            base = "Memory/Experiences"
        else:
            name = _safe_component(project or "General")
            base = f"Memory/Projects/{name}"
        return f"{base}/{year_month}/{memory_id}.json"


def _safe_component(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._ -]+", "_", value.strip()).strip(" .")
    return clean[:80] or "General"


def _compact(value: str, limit: int) -> str:
    clean = " ".join(value.strip().split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def _search_terms(value: str) -> tuple[str, ...]:
    stop = {
        "the", "and", "that", "this", "what", "when", "where", "which", "with",
        "your", "you", "about", "remember", "have", "does", "did", "can", "could",
        "ron",
    }
    return tuple(
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_]{2,}", value.casefold())
        if token not in stop
    )
