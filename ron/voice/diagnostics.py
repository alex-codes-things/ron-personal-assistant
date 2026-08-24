"""Thread-safe, privacy-preserving voice health counters."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from ron.voice.models import VoiceState


@dataclass(frozen=True, slots=True)
class VoiceDiagnosticSnapshot:
    state: VoiceState
    device: str
    accepted_commands: int
    rejected_activations: int
    microphone_overflows: int
    restarts: int
    last_error: str | None
    last_transcription_seconds: float | None


class VoiceDiagnostics:
    """Store metrics and errors, never room audio or complete transcripts."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = VoiceState.STOPPED
        self._device = "not selected"
        self._accepted_commands = 0
        self._rejected_activations = 0
        self._microphone_overflows = 0
        self._restarts = 0
        self._last_error: str | None = None
        self._last_transcription_seconds: float | None = None

    def set_state(self, state: VoiceState, error: str | None = None) -> None:
        with self._lock:
            self._state = state
            if error is not None:
                self._last_error = error[:300]
            elif state in {VoiceState.READY, VoiceState.LISTENING}:
                self._last_error = None

    def set_device(self, label: str) -> None:
        with self._lock:
            self._device = label[:200]

    def record_command(self, transcription_seconds: float) -> None:
        with self._lock:
            self._accepted_commands += 1
            self._last_transcription_seconds = max(0.0, transcription_seconds)

    def record_rejection(self) -> None:
        with self._lock:
            self._rejected_activations += 1

    def update_overflows(self, count: int) -> None:
        with self._lock:
            self._microphone_overflows = max(self._microphone_overflows, count)

    def record_restart(self) -> None:
        with self._lock:
            self._restarts += 1

    def snapshot(self) -> VoiceDiagnosticSnapshot:
        with self._lock:
            return VoiceDiagnosticSnapshot(
                self._state,
                self._device,
                self._accepted_commands,
                self._rejected_activations,
                self._microphone_overflows,
                self._restarts,
                self._last_error,
                self._last_transcription_seconds,
            )

    def status_label(self) -> str:
        snapshot = self.snapshot()
        if snapshot.state is VoiceState.DISABLED:
            return "disabled"
        if snapshot.state in {VoiceState.OFFLINE, VoiceState.RETRYING}:
            detail = f" ({snapshot.last_error})" if snapshot.last_error else ""
            return f"offline{detail}"
        if snapshot.state is VoiceState.STOPPED:
            return "stopped"
        latency = (
            f"; last ASR {snapshot.last_transcription_seconds:.2f}s"
            if snapshot.last_transcription_seconds is not None
            else ""
        )
        return f"{snapshot.state.value} on {snapshot.device}{latency}"
