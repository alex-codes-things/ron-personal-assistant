"""Privacy-safe runtime health reporting and external performance retention."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from ron.latency import LatencyTracker
from ron.storage import StorageManager, StorageState
from ron.voice.models import VoiceState


class PerformanceArchive:
    """Write timing-only turn records to resilient RON_STORAGE in the background."""

    def __init__(self, storage: StorageManager, maximum_pending: int = 128) -> None:
        self.storage = storage
        self._queue: queue.Queue[dict[str, object] | None] = queue.Queue(
            maxsize=maximum_pending
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._archived = 0
        self._dropped = 0
        self._logger = logging.getLogger(__name__)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="ron-performance-archive",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(None)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        self._thread = None

    def record(self, snapshot: dict[str, object]) -> None:
        try:
            self._queue.put_nowait(dict(snapshot))
        except queue.Full:
            with self._lock:
                self._dropped += 1

    def status_label(self) -> str:
        with self._lock:
            archived = self._archived
            dropped = self._dropped
        suffix = f", {dropped} dropped" if dropped else ""
        return f"{archived} performance record(s) archived{suffix}"

    def _run(self) -> None:
        while True:
            try:
                snapshot = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if snapshot is None:
                return
            now = datetime.now(UTC)
            turn_id = int(snapshot.get("turn_id", 0))
            path = (
                f"Diagnostics/Performance/{now:%Y-%m-%d}/"
                f"{now:%H%M%S-%f}-turn-{turn_id:06d}.json"
            )
            record = {
                "schema": 1,
                "recorded_utc": now.isoformat(),
                **snapshot,
            }
            try:
                self.storage.save_json(path, record)
            except Exception:
                self._logger.debug("Performance archive write failed safely", exc_info=True)
            else:
                with self._lock:
                    self._archived += 1


class HealthMonitor:
    """Combine live component state without performing slow network probes."""

    def __init__(
        self,
        *,
        ai_label: Callable[[], str],
        voice: object,
        speech: object,
        face: object,
        storage: StorageManager,
        agent: object,
        latency: LatencyTracker,
        archive: PerformanceArchive,
    ) -> None:
        self.ai_label = ai_label
        self.voice = voice
        self.speech = speech
        self.face = face
        self.storage = storage
        self.agent = agent
        self.latency = latency
        self.archive = archive

    def report(self) -> str:
        warnings: list[str] = []
        notes: list[str] = []
        voice_snapshot = self.voice.diagnostics.snapshot()
        if voice_snapshot.state in {VoiceState.OFFLINE, VoiceState.RETRYING}:
            warnings.append(f"voice is {voice_snapshot.state.value}")
        if voice_snapshot.microphone_overflows:
            warnings.append(
                f"microphone has {voice_snapshot.microphone_overflows} overflow(s)"
            )

        speech_label = self.speech.status_label()
        if "unavailable" in speech_label or "offline" in speech_label:
            warnings.append("speech output is unavailable")

        storage_health = self.storage.health()
        if storage_health.state is not StorageState.ONLINE:
            warnings.append(
                f"external storage is {storage_health.state.value}; "
                f"{storage_health.pending_items} item(s) queued"
            )

        capability_report = self.agent.registry.capability_report()
        ready_tools = sum(1 for _name, available, _reason in capability_report if available)
        unavailable_tools = len(capability_report) - ready_tools
        latency_label = self.latency.health_summary()
        if "over " in latency_label:
            warnings.append(latency_label)

        face_label = self.face.connection_label()
        if "offline" in face_label.casefold() or "waiting" in face_label.casefold():
            notes.append("tablet face disconnected (optional)")

        state = "DEGRADED" if warnings else "READY"
        warning_text = "; warnings: " + "; ".join(warnings) if warnings else ""
        note_text = "; notes: " + "; ".join(notes) if notes else ""
        return (
            f"Health {state}: AI {self.ai_label()}; voice {voice_snapshot.state.value} "
            f"on {voice_snapshot.device}; {speech_label}; tools {ready_tools}/"
            f"{len(capability_report)} ready ({unavailable_tools} unavailable); "
            f"storage {storage_health.state.value}; {self.archive.status_label()}"
            f"{warning_text}{note_text}."
        )
