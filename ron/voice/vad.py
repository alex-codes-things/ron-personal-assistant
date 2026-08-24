"""Silero voice-activity endpointing through sherpa-onnx."""

from __future__ import annotations

from array import array
from pathlib import Path

from ron.voice.audio import VoiceDependencyError
from ron.voice.settings import VoiceSettings


class VadModelError(RuntimeError):
    """Raised when local speech endpointing cannot be started."""


class SileroEndpointDetector:
    """Return only complete speech segments after configured trailing silence."""

    def __init__(self, settings: VoiceSettings) -> None:
        self.settings = settings
        self._vad: object | None = None
        self._window_size = 512
        self._pending = array("f")

    def load(self) -> None:
        if not Path(self.settings.vad_model).is_file():
            raise VadModelError("Silero VAD model is missing. Run scripts/setup_voice.ps1.")
        try:
            import sherpa_onnx
        except ImportError as error:
            raise VoiceDependencyError(
                "sherpa-onnx is missing. Run scripts/setup_voice.ps1."
            ) from error
        try:
            config = sherpa_onnx.VadModelConfig()
            config.silero_vad.model = str(self.settings.vad_model)
            config.silero_vad.threshold = self.settings.vad_threshold
            config.silero_vad.min_silence_duration = self.settings.end_silence_seconds
            config.silero_vad.min_speech_duration = self.settings.minimum_speech_seconds
            config.silero_vad.max_speech_duration = self.settings.maximum_speech_seconds
            config.sample_rate = self.settings.sample_rate
            self._window_size = int(config.silero_vad.window_size)
            self._vad = sherpa_onnx.VoiceActivityDetector(
                config,
                buffer_size_in_seconds=max(20, int(self.settings.maximum_speech_seconds + 5)),
            )
        except Exception as error:
            raise VadModelError(f"Silero VAD could not start: {error}") from error

    @property
    def speech_detected(self) -> bool:
        if self._vad is None:
            return False
        return bool(self._vad.is_speech_detected())

    def feed(self, samples: array[float]) -> tuple[array[float], ...]:
        if self._vad is None:
            raise VadModelError("Silero VAD is not loaded")
        self._pending.extend(samples)
        while len(self._pending) >= self._window_size:
            window = array("f", self._pending[: self._window_size])
            del self._pending[: self._window_size]
            self._vad.accept_waveform(window)

        completed: list[array[float]] = []
        while not self._vad.empty():
            segment = array("f", self._vad.front.samples)
            self._vad.pop()
            if segment:
                completed.append(segment)
        return tuple(completed)

    def reset(self) -> None:
        """Discard speaker-tail state before opening a no-wake follow-up window."""
        self._pending = array("f")
        resetter = getattr(self._vad, "reset", None)
        if callable(resetter):
            resetter()
