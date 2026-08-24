"""Track long-running local processes that Ron deliberately started."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProcessSnapshot:
    process_id: int
    key: str
    label: str
    pid: int
    status: str
    started_at: float
    exit_code: int | None
    log_path: str


@dataclass(slots=True)
class _ProcessRecord:
    process_id: int
    key: str
    label: str
    process: subprocess.Popen[bytes]
    started_at: float
    log_path: Path

    def snapshot(self) -> ProcessSnapshot:
        exit_code = self.process.poll()
        if exit_code is None:
            status = "running"
        elif exit_code == 0:
            status = "completed"
        else:
            status = "failed"
        return ProcessSnapshot(
            self.process_id,
            self.key,
            self.label,
            self.process.pid,
            status,
            self.started_at,
            exit_code,
            str(self.log_path),
        )


class ManagedProcessManager:
    """Start only caller-supplied fixed commands and remember their live state."""

    def __init__(self, log_directory: Path, *, maximum_records: int = 20) -> None:
        if not 1 <= maximum_records <= 100:
            raise ValueError("Managed-process record limit is invalid")
        self.log_directory = log_directory
        self.maximum_records = maximum_records
        self._lock = threading.RLock()
        self._records: dict[int, _ProcessRecord] = {}
        self._next_id = 1

    def start(
        self,
        *,
        key: str,
        label: str,
        command: tuple[str, ...],
        cwd: Path,
    ) -> tuple[ProcessSnapshot, bool]:
        if not key or not label or not command:
            raise ValueError("Managed process metadata is incomplete")
        if any(not isinstance(part, str) or not part for part in command):
            raise ValueError("Managed process command is invalid")
        with self._lock:
            for record in self._records.values():
                snapshot = record.snapshot()
                if record.key == key and snapshot.status == "running":
                    return snapshot, False

            self.log_directory.mkdir(parents=True, exist_ok=True)
            process_id = self._next_id
            self._next_id += 1
            log_path = self.log_directory / f"{process_id:03d}-{key}.log"
            flags = 0
            if os.name == "nt":
                flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            with log_path.open("ab") as log_file:
                process = subprocess.Popen(
                    list(command),
                    cwd=str(cwd),
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    close_fds=True,
                    creationflags=flags,
                )
            record = _ProcessRecord(
                process_id,
                key,
                label,
                process,
                time.time(),
                log_path,
            )
            self._records[process_id] = record
            self._trim_locked()
            return record.snapshot(), True

    def snapshot(self, process_id: int) -> ProcessSnapshot | None:
        with self._lock:
            record = self._records.get(process_id)
        return record.snapshot() if record is not None else None

    def latest(self) -> ProcessSnapshot | None:
        with self._lock:
            if not self._records:
                return None
            record = self._records[max(self._records)]
        return record.snapshot()

    def snapshots(self) -> tuple[ProcessSnapshot, ...]:
        with self._lock:
            records = tuple(self._records[key] for key in sorted(self._records))
        return tuple(record.snapshot() for record in records)

    def stop(self, process_id: int | None = None) -> ProcessSnapshot | None:
        with self._lock:
            if process_id is None:
                if not self._records:
                    return None
                process_id = max(self._records)
            record = self._records.get(process_id)
        if record is None:
            return None
        if record.process.poll() is None:
            try:
                record.process.terminate()
                record.process.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    record.process.kill()
                except OSError:
                    pass
        return record.snapshot()

    def status_label(self) -> str:
        snapshots = self.snapshots()
        running = sum(1 for item in snapshots if item.status == "running")
        return f"managed processes: {running} running, {len(snapshots)} tracked"

    def _trim_locked(self) -> None:
        if len(self._records) <= self.maximum_records:
            return
        removable = [
            process_id
            for process_id, record in self._records.items()
            if record.process.poll() is not None
        ]
        for process_id in sorted(removable):
            if len(self._records) <= self.maximum_records:
                break
            self._records.pop(process_id, None)
