"""Types shared by Ron's resilient storage layer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class StorageState(StrEnum):
    """Health states exposed to the rest of Ron."""

    ONLINE = "online"
    DEGRADED = "degraded"
    RECOVERING = "recovering"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class StorageHealth:
    state: StorageState
    external_root: Path | None
    pending_items: int
    pending_bytes: int
    queue_limit_bytes: int
    detail: str

    @property
    def available(self) -> bool:
        return self.state is StorageState.ONLINE and self.external_root is not None


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Result of a resilient storage write."""

    relative_path: str
    sha256: str
    size_bytes: int
    queued: bool
    external_path: Path | None


@dataclass(frozen=True, slots=True)
class DeletedObject:
    """Result of a resilient storage deletion."""

    relative_path: str
    queued: bool


class StorageError(RuntimeError):
    """Base class for storage failures Ron can surface safely."""


class StorageQueueFullError(StorageError):
    """Raised before fallback data can consume too much of the laptop SSD."""


class StorageIdentityError(StorageError):
    """Raised when an unexpected external drive is presented as Ron storage."""
