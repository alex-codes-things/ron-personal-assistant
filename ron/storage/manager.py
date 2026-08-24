"""Drive-aware, queue-backed storage for Ron's long-term data."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from ron.storage.atomic import atomic_write_bytes, atomic_write_json, sha256_file
from ron.storage.locator import (
    DEFAULT_VOLUME_LABEL,
    IDENTITY_FILENAME,
    locate_storage_root,
    read_identity,
)
from ron.storage.models import (
    DeletedObject,
    StorageHealth,
    StorageIdentityError,
    StorageQueueFullError,
    StorageState,
    StoredObject,
)

type StorageLocator = Callable[[], Path | None]
type NoticeHandler = Callable[[str], None]

_STORAGE_SCHEMA = 1


class StorageManager:
    """Own external-drive discovery, safe writes, fallback queue and recovery."""

    def __init__(
        self,
        project_root: Path,
        *,
        locator: StorageLocator | None = None,
        notice_handler: NoticeHandler | None = None,
        queue_limit_bytes: int | None = None,
        check_interval_seconds: float | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.local_root = self.project_root / "runtime" / "memory"
        self.core_root = self.local_root / "core"
        self.queue_root = self.local_root / "storage_queue"
        self.queue_objects = self.queue_root / "objects"
        self.queue_db = self.queue_root / "queue.sqlite"
        self.binding_path = self.core_root / "storage_binding.json"
        self._locator = locator
        self._notice_handler = notice_handler
        self.queue_limit_bytes = queue_limit_bytes or _env_int(
            "RON_STORAGE_QUEUE_LIMIT_MB", 512, 32, 16384
        ) * 1024 * 1024
        self.external_reserve_bytes = (
            _env_int("RON_STORAGE_MIN_FREE_GB", 5, 1, 256) * 1024 * 1024 * 1024
        )
        self.local_reserve_bytes = (
            _env_int("RON_LOCAL_MIN_FREE_MB", 1024, 256, 16384) * 1024 * 1024
        )
        self.check_interval_seconds = check_interval_seconds or _env_float(
            "RON_STORAGE_CHECK_SECONDS", 10.0, 1.0, 300.0
        )
        self._lock = threading.RLock()
        self._queue_lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = StorageState.DEGRADED
        self._external_root: Path | None = None
        self._detail = "External memory has not been checked yet."
        self._last_notice_state: StorageState | None = None
        self._logger = logging.getLogger(__name__)
        self.core_root.mkdir(parents=True, exist_ok=True)
        self.queue_objects.mkdir(parents=True, exist_ok=True)
        self._initialise_queue()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            # Discovery is quick; potentially large queue recovery happens in the
            # background so a reconnected HDD never blocks Ron's startup.
            self.refresh_once(sync=False)
            self._thread = threading.Thread(
                target=self._monitor,
                name="ron-storage-monitor",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=min(self.check_interval_seconds + 1.0, 6.0))
        self._thread = None

    @property
    def state(self) -> StorageState:
        with self._lock:
            return self._state

    @property
    def external_root(self) -> Path | None:
        with self._lock:
            return self._external_root

    def health(self) -> StorageHealth:
        count, size = self.pending_stats()
        with self._lock:
            return StorageHealth(
                state=self._state,
                external_root=self._external_root,
                pending_items=count,
                pending_bytes=size,
                queue_limit_bytes=self.queue_limit_bytes,
                detail=self._detail,
            )

    def status_label(self) -> str:
        health = self.health()
        pending = f", {health.pending_items} queued" if health.pending_items else ""
        return f"{health.state.value}{pending}"

    def refresh_once(self, *, sync: bool = True) -> StorageHealth:
        """Check the drive immediately; safe to call from tests or diagnostics."""
        try:
            candidate = self._discover_candidate()
        except Exception as error:
            self._set_state(StorageState.ERROR, None, f"Drive discovery failed: {error}")
            return self.health()

        if candidate is None:
            self._set_state(
                StorageState.DEGRADED,
                None,
                "External memory is disconnected; writes are queued on the laptop.",
            )
            return self.health()

        candidate = Path(candidate)
        with self._lock:
            previous = self._state
            previous_root = self._external_root
        try:
            candidate = candidate.resolve()
            # Capacity is checked before first-time identity/binding so Ron never
            # adopts a drive that cannot safely hold the long-term store.
            self._require_external_space(candidate, 0)
            storage_id = self._verify_or_initialise_identity(candidate)
            if previous is not StorageState.ONLINE or previous_root != candidate:
                self._ensure_external_layout(candidate)
                self._probe_write_access(candidate)
            self._bind_storage_if_needed(storage_id)
        except StorageIdentityError as error:
            self._set_state(StorageState.ERROR, None, str(error))
            return self.health()
        except OSError as error:
            self._set_state(
                StorageState.ERROR, None, f"External memory is unavailable: {error}"
            )
            return self.health()

        pending_count, _pending_bytes = self.pending_stats()
        needs_recovery = pending_count > 0
        if needs_recovery:
            self._set_state(
                StorageState.RECOVERING,
                candidate,
                "External memory is healthy; queued writes are waiting to sync.",
            )
        else:
            self._set_state(StorageState.ONLINE, candidate, "External memory is healthy.")

        if not sync or not needs_recovery:
            return self.health()
        try:
            self.sync_pending()
        except Exception as error:
            self._logger.exception("Queued storage synchronization failed")
            self._set_state(StorageState.ERROR, candidate, f"Recovery sync failed: {error}")
            return self.health()
        self._set_state(StorageState.ONLINE, candidate, "External memory is healthy.")
        return self.health()

    def save_bytes(self, relative_path: str | Path, data: bytes) -> StoredObject:
        relative = _normalise_relative_path(relative_path)
        self._cancel_pending_deletion(relative)
        digest = hashlib.sha256(data).hexdigest()
        with self._lock:
            root = self._external_root if self._state is StorageState.ONLINE else None
        if root is not None:
            destination = root / Path(*PurePosixPath(relative).parts)
            try:
                self._require_external_space(root, len(data))
                atomic_write_bytes(destination, data)
                if sha256_file(destination) != digest:
                    raise OSError("checksum verification failed after write")
                return StoredObject(relative, digest, len(data), False, destination)
            except OSError as error:
                self._logger.warning(
                    "External write failed; switching to fallback queue", exc_info=True
                )
                self._set_state(
                    StorageState.DEGRADED,
                    None,
                    f"External write failed ({error}); new data is queued locally.",
                )
        return self._queue_bytes(relative, data, digest)

    def save_json(self, relative_path: str | Path, value: object) -> StoredObject:
        data = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")
        return self.save_bytes(relative_path, data)

    def read_bytes(self, relative_path: str | Path) -> bytes:
        relative = _normalise_relative_path(relative_path)
        if self._deletion_is_pending(relative):
            raise FileNotFoundError(f"Memory is pending deletion: {relative}")
        queued = self._queued_object_path(relative)
        if queued is not None and queued.exists():
            return queued.read_bytes()
        with self._lock:
            root = self._external_root
            online = self._state is StorageState.ONLINE
        if not online or root is None:
            raise FileNotFoundError(f"External memory is offline: {relative}")
        return (root / Path(*PurePosixPath(relative).parts)).read_bytes()

    def delete(self, relative_path: str | Path) -> DeletedObject:
        """Delete now when possible, otherwise queue a durable deletion tombstone."""
        relative = _normalise_relative_path(relative_path)
        with self._queue_lock:
            self._remove_queued_write(relative)
            with self._lock:
                root = self._external_root if self._state is StorageState.ONLINE else None
            if root is not None:
                destination = root / Path(*PurePosixPath(relative).parts)
                try:
                    destination.unlink(missing_ok=True)
                    if destination.exists():
                        raise OSError("file still exists after deletion")
                    self._cancel_pending_deletion(relative)
                    return DeletedObject(relative, False)
                except OSError as error:
                    self._logger.warning(
                        "External deletion failed; queueing deletion tombstone",
                        exc_info=True,
                    )
                    self._set_state(
                        StorageState.DEGRADED,
                        None,
                        f"External deletion failed ({error}); deletion is queued locally.",
                    )
            self._queue_deletion(relative)
            return DeletedObject(relative, True)

    def pending_stats(self) -> tuple[int, int]:
        with self._connection() as connection:
            write_row = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM storage_queue"
            ).fetchone()
            delete_count = int(
                connection.execute("SELECT COUNT(*) FROM storage_deletions").fetchone()[0]
            )
        return int(write_row[0]) + delete_count, int(write_row[1])

    def is_pending(self, relative_path: str | Path) -> bool:
        relative = _normalise_relative_path(relative_path)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM storage_queue WHERE relative_path=?
                UNION ALL
                SELECT 1 FROM storage_deletions WHERE relative_path=?
                LIMIT 1
                """,
                (relative, relative),
            ).fetchone()
        return row is not None

    def sync_pending(self) -> int:
        with self._queue_lock:
            return self._sync_pending_locked()

    def _sync_pending_locked(self) -> int:
        with self._lock:
            root = self._external_root
        if root is None:
            return 0
        synced = self._sync_deletions(root)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT queue_id, relative_path, local_path, sha256, size_bytes
                FROM storage_queue ORDER BY queue_id ASC
                """
            ).fetchall()
        for queue_id, relative, local_path, digest, _size in rows:
            source = Path(str(local_path))
            if not source.exists():
                self._logger.error("Queued object disappeared: %s", source)
                continue
            destination = root / Path(*PurePosixPath(str(relative)).parts)
            payload = source.read_bytes()
            if hashlib.sha256(payload).hexdigest() != str(digest):
                self._logger.error("Queued object checksum mismatch: %s", source)
                continue
            atomic_write_bytes(destination, payload)
            if sha256_file(destination) != str(digest):
                raise OSError(f"Verification failed while syncing {relative}")
            with self._connection() as connection:
                connection.execute("DELETE FROM storage_queue WHERE queue_id=?", (queue_id,))
            source.unlink(missing_ok=True)
            self._remove_empty_queue_parents(source.parent)
            synced += 1
        if synced:
            suffix = "s" if synced != 1 else ""
            self._notify(
                f"[MEMORY RECOVERED] Synced {synced} queued item{suffix} "
                "to external storage."
            )
        return synced

    def _queue_bytes(self, relative: str, data: bytes, digest: str) -> StoredObject:
        with self._queue_lock:
            return self._queue_bytes_locked(relative, data, digest)

    def _queue_bytes_locked(self, relative: str, data: bytes, digest: str) -> StoredObject:
        pending_count, pending_bytes = self.pending_stats()
        del pending_count
        existing_size = self._queued_size(relative)
        projected = pending_bytes - existing_size + len(data)
        if projected > self.queue_limit_bytes:
            raise StorageQueueFullError(
                "Ron's local storage queue is full. Reconnect RON_STORAGE before "
                "saving more long-term data."
            )
        self._require_local_space(len(data))
        local_path = self.queue_objects / Path(*PurePosixPath(relative).parts)
        atomic_write_bytes(local_path, data)
        if sha256_file(local_path) != digest:
            local_path.unlink(missing_ok=True)
            raise OSError("Fallback queue checksum verification failed")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO storage_queue(
                    relative_path, local_path, sha256, size_bytes, created_utc
                )
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(relative_path) DO UPDATE SET
                    local_path=excluded.local_path,
                    sha256=excluded.sha256,
                    size_bytes=excluded.size_bytes,
                    created_utc=CURRENT_TIMESTAMP
                """,
                (relative, str(local_path), digest, len(data)),
            )
        return StoredObject(relative, digest, len(data), True, None)

    def _queue_deletion(self, relative: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO storage_deletions(relative_path, created_utc)
                VALUES (?, CURRENT_TIMESTAMP)
                ON CONFLICT(relative_path) DO UPDATE SET created_utc=CURRENT_TIMESTAMP
                """,
                (relative,),
            )

    def _cancel_pending_deletion(self, relative: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM storage_deletions WHERE relative_path=?", (relative,)
            )

    def _deletion_is_pending(self, relative: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM storage_deletions WHERE relative_path=?", (relative,)
            ).fetchone()
        return row is not None

    def _remove_queued_write(self, relative: str) -> None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT local_path FROM storage_queue WHERE relative_path=?", (relative,)
            ).fetchone()
            connection.execute(
                "DELETE FROM storage_queue WHERE relative_path=?", (relative,)
            )
        if row is None:
            return
        local_path = Path(str(row[0]))
        local_path.unlink(missing_ok=True)
        self._remove_empty_queue_parents(local_path.parent)

    def _sync_deletions(self, root: Path) -> int:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT deletion_id, relative_path
                FROM storage_deletions ORDER BY deletion_id ASC
                """
            ).fetchall()
        synced = 0
        for deletion_id, relative in rows:
            destination = root / Path(*PurePosixPath(str(relative)).parts)
            destination.unlink(missing_ok=True)
            if destination.exists():
                raise OSError(f"Verification failed while deleting {relative}")
            with self._connection() as connection:
                connection.execute(
                    "DELETE FROM storage_deletions WHERE deletion_id=?", (deletion_id,)
                )
            synced += 1
        return synced

    def _queued_object_path(self, relative: str) -> Path | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT local_path FROM storage_queue WHERE relative_path=?", (relative,)
            ).fetchone()
        return Path(str(row[0])) if row else None

    def _queued_size(self, relative: str) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT size_bytes FROM storage_queue WHERE relative_path=?", (relative,)
            ).fetchone()
        return int(row[0]) if row else 0

    def _monitor(self) -> None:
        self.refresh_once(sync=True)
        while not self._stop.wait(self.check_interval_seconds):
            self.refresh_once(sync=True)

    def _set_state(self, state: StorageState, root: Path | None, detail: str) -> None:
        with self._lock:
            changed = state is not self._state
            self._state = state
            self._external_root = root
            self._detail = detail
        if changed or self._last_notice_state is None:
            self._emit_state_notice(state, detail)
        self._last_notice_state = state

    def _emit_state_notice(self, state: StorageState, detail: str) -> None:
        if state is StorageState.DEGRADED:
            self._notify(
                "[MEMORY DEGRADED] External storage is offline. Ron will keep working "
                "and queue long-term writes locally."
            )
        elif state is StorageState.RECOVERING:
            self._notify(
                "[MEMORY RECOVERING] External storage returned. Ron is verifying and "
                "synchronizing queued memory."
            )
        elif state is StorageState.ERROR:
            self._notify(f"[MEMORY ERROR] {detail}")
        elif state is StorageState.ONLINE and self._last_notice_state in {
            StorageState.DEGRADED,
            StorageState.RECOVERING,
            StorageState.ERROR,
        }:
            self._notify("[MEMORY ONLINE] External long-term memory is healthy again.")

    def _notify(self, message: str) -> None:
        if self._notice_handler is not None:
            try:
                self._notice_handler(message)
            except Exception:
                self._logger.debug("Storage notice handler failed", exc_info=True)

    def _discover_candidate(self) -> Path | None:
        if self._locator is not None:
            return self._locator()
        binding = self._read_binding()
        expected_id = str(binding["storage_id"]) if binding else None
        return locate_storage_root(expected_storage_id=expected_id)

    def _verify_or_initialise_identity(self, root: Path) -> str:
        if not root.exists() or not root.is_dir():
            raise OSError(f"Storage root does not exist: {root}")
        binding = self._read_binding()
        expected_id = str(binding.get("storage_id", "")) if binding else ""
        identity_path = root / IDENTITY_FILENAME
        identity = read_identity(root)

        if identity_path.exists() and identity is None:
            raise StorageIdentityError(
                "RON_STORAGE has a corrupt identity file. Ron refused to overwrite it."
            )
        if identity is None and expected_id:
            raise StorageIdentityError(
                "The bound Ron storage identity is missing from this drive. "
                "Ron refused to adopt the drive automatically."
            )
        if identity is None:
            storage_id = str(uuid.uuid4())
            identity = {
                "schema": _STORAGE_SCHEMA,
                "storage_id": storage_id,
                "display_name": DEFAULT_VOLUME_LABEL,
                "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            atomic_write_json(identity_path, identity)

        self._validate_identity(identity)
        storage_id = str(identity["storage_id"])
        if expected_id and storage_id != expected_id:
            raise StorageIdentityError(
                "A different drive is presenting itself as RON_STORAGE. "
                "Ron refused to write to it."
            )
        return storage_id

    def _bind_storage_if_needed(self, storage_id: str) -> None:
        if self._read_binding() is not None:
            return
        atomic_write_json(
            self.binding_path,
            {"schema": _STORAGE_SCHEMA, "storage_id": storage_id},
        )

    @staticmethod
    def _validate_identity(identity: dict[str, object]) -> None:
        if identity.get("schema") != _STORAGE_SCHEMA:
            raise StorageIdentityError("RON_STORAGE uses an unsupported identity schema.")
        storage_id = str(identity.get("storage_id", ""))
        try:
            uuid.UUID(storage_id)
        except ValueError as error:
            raise StorageIdentityError("RON_STORAGE has an invalid storage ID.") from error
        if str(identity.get("display_name", "")) != DEFAULT_VOLUME_LABEL:
            raise StorageIdentityError("RON_STORAGE has an invalid identity name.")

    def _read_binding(self) -> dict[str, object] | None:
        if not self.binding_path.exists():
            return None
        try:
            value = json.loads(self.binding_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as error:
            raise StorageIdentityError(
                "Ron's local storage binding is corrupt; automatic rebinding is disabled."
            ) from error
        if not isinstance(value, dict) or value.get("schema") != _STORAGE_SCHEMA:
            raise StorageIdentityError(
                "Ron's local storage binding is invalid; automatic rebinding is disabled."
            )
        storage_id = str(value.get("storage_id", ""))
        try:
            uuid.UUID(storage_id)
        except ValueError as error:
            raise StorageIdentityError("Ron's bound storage ID is invalid.") from error
        return value

    def _require_external_space(self, root: Path, incoming_bytes: int) -> None:
        free = shutil.disk_usage(root).free
        if free - incoming_bytes < self.external_reserve_bytes:
            raise OSError(
                "external memory is below Ron's reserved free-space threshold"
            )

    def _require_local_space(self, incoming_bytes: int) -> None:
        free = shutil.disk_usage(self.local_root).free
        if free - incoming_bytes < self.local_reserve_bytes:
            raise StorageQueueFullError(
                "The laptop is low on free space. Ron refused to grow the fallback queue."
            )

    @staticmethod
    def _ensure_external_layout(root: Path) -> None:
        directories = (
            "Memory/Conversations",
            "Memory/Knowledge",
            "Memory/People",
            "Memory/Projects",
            "Memory/Experiences",
            "Memory/Archives",
            "Visual_Memory/Screenshots/Coding",
            "Visual_Memory/Screenshots/Applications",
            "Visual_Memory/Screenshots/Errors",
            "Visual_Memory/Screenshots/General",
            "Visual_Memory/Thumbnails",
            "Visual_Memory/Analysis",
            "AI/Models",
            "AI/Embeddings",
            "AI/Voice",
            "AI/Vision",
            "Devices/Nexus_7",
            "Devices/Router",
            "Devices/Future",
            "Logs",
            "Backups/Ron",
            "Backups/Memory",
            "Backups/Configuration",
            "System/integrity",
            "System/manifests",
        )
        for directory in directories:
            (root / directory).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _probe_write_access(root: Path) -> None:
        probe = root / "System" / ".ron-write-probe"
        atomic_write_bytes(probe, b"ok")
        probe.unlink(missing_ok=True)

    def _initialise_queue(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS storage_queue (
                    queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    relative_path TEXT NOT NULL UNIQUE,
                    local_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    created_utc TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS storage_deletions (
                    deletion_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    relative_path TEXT NOT NULL UNIQUE,
                    created_utc TEXT NOT NULL
                )
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.queue_root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.queue_db, timeout=2.0)
        try:
            connection.execute("PRAGMA journal_mode=WAL").close()
            connection.execute("PRAGMA synchronous=NORMAL").close()
            with connection:
                yield connection
        finally:
            connection.close()

    def _remove_empty_queue_parents(self, directory: Path) -> None:
        while directory != self.queue_objects:
            try:
                directory.rmdir()
            except OSError:
                return
            directory = directory.parent


def _normalise_relative_path(value: str | Path) -> str:
    raw = str(value).replace("\\", "/").strip()
    if raw.startswith("/") or (len(raw) >= 2 and raw[1] == ":"):
        raise ValueError("Storage paths must be safe relative paths")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError("Storage paths must be safe relative paths")
    return path.as_posix()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)
