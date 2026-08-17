"""Sherpa-ONNX open-vocabulary detector for Ron's local wake phrase."""

from __future__ import annotations

from array import array
from pathlib import Path

from ron.voice.audio import VoiceDependencyError
from ron.voice.settings import VoiceSettings


class WakeWordModelError(RuntimeError):
    """Raised when the configured wake model is missing or invalid."""


class SherpaWakeWordDetector:
    """One-thread int8 wake detector; full speech recognition is not run here."""

    def __init__(self, settings: VoiceSettings) -> None:
        self.settings = settings
        self._spotter: object | None = None
        self._stream: object | None = None

    def load(self) -> None:
        files = (
            self.settings.kws_encoder,
            self.settings.kws_decoder,
            self.settings.kws_joiner,
            self.settings.kws_tokens,
            self.settings.kws_keywords,
        )
        missing = tuple(path for path in files if not Path(path).is_file())
        if missing:
            raise WakeWordModelError(
                "Wake-word model files are missing. Run scripts/setup_voice.ps1."
            )
        try:
            import sherpa_onnx
        except ImportError as error:
            raise VoiceDependencyError(
                "sherpa-onnx is missing. Run scripts/setup_voice.ps1."
            ) from error
        try:
            self._spotter = sherpa_onnx.KeywordSpotter(
                tokens=str(self.settings.kws_tokens),
                encoder=str(self.settings.kws_encoder),
                decoder=str(self.settings.kws_decoder),
                joiner=str(self.settings.kws_joiner),
                keywords_file=str(self.settings.kws_keywords),
                num_threads=1,
                max_active_paths=4,
                keywords_score=self.settings.wake_score,
                keywords_threshold=self.settings.wake_threshold,
                num_trailing_blanks=2,
                provider="cpu",
            )
            self._stream = self._spotter.create_stream()
        except Exception as error:
            raise WakeWordModelError(f"The wake-word model could not start: {error}") from error

    def feed(self, samples: array[float]) -> bool:
        if self._spotter is None or self._stream is None:
            raise WakeWordModelError("The wake-word detector is not loaded")
        try:
            import numpy as np

            values = np.asarray(samples, dtype=np.float32)
            self._stream.accept_waveform(self.settings.sample_rate, values)
            detected = False
            while self._spotter.is_ready(self._stream):
                self._spotter.decode_stream(self._stream)
                result = str(self._spotter.get_result(self._stream) or "")
                if result:
                    detected = self._matches(result)
                    self._stream = self._spotter.create_stream()
                    break
            return detected
        except WakeWordModelError:
            raise
        except Exception as error:
            raise WakeWordModelError(f"Wake-word decoding failed: {error}") from error

    def reset(self) -> None:
        if self._spotter is not None:
            self._stream = self._spotter.create_stream()

    def _matches(self, value: str) -> bool:
        clean = " ".join(value.replace("_", " ").casefold().split())
        return clean == self.settings.wake_phrase
