"""Sherpa-ONNX open-vocabulary detector for Ron's local wake phrase."""

from __future__ import annotations

from array import array
from pathlib import Path

from ron.voice.audio import VoiceDependencyError
from ron.voice.keyword_file import prepare_keyword
from ron.voice.settings import VoiceSettings


class WakeWordModelError(RuntimeError):
    """Raised when the configured wake model is missing or invalid."""


class SherpaWakeWordDetector:
    """Tiny always-on wake detector; full speech recognition is never run here."""

    def __init__(self, settings: VoiceSettings) -> None:
        self.settings = settings
        self._spotter: object | None = None
        self._stream: object | None = None
        self._effective_threshold = settings.wake_threshold
        self._effective_score = settings.wake_score

    @property
    def effective_threshold(self) -> float:
        return self._effective_threshold

    @property
    def effective_score(self) -> float:
        return self._effective_score

    def _sensitivity_profile(self) -> tuple[float, float, int, int, int]:
        """Return threshold, score, paths, trailing blanks and threads."""
        mode = self.settings.wake_sensitivity
        if mode == "high":
            # Accent-friendly profile. A false KWS activation can only open the
            # conversational handoff; it cannot itself execute a tool.
            return (
                min(self.settings.wake_threshold, 0.20),
                max(self.settings.wake_score, 2.00),
                8,
                1,
                2,
            )
        if mode == "strict":
            return (
                max(self.settings.wake_threshold, 0.45),
                min(self.settings.wake_score, 1.00),
                4,
                2,
                1,
            )
        return (self.settings.wake_threshold, self.settings.wake_score, 4, 2, 1)

    def load(self) -> None:
        files = (
            self.settings.kws_encoder,
            self.settings.kws_decoder,
            self.settings.kws_joiner,
            self.settings.kws_tokens,
        )
        missing = tuple(path for path in files if not Path(path).is_file())
        if missing:
            raise WakeWordModelError(
                "Wake-word model files are missing. Run scripts/setup_voice.ps1."
            )

        # Refresh the tiny keyword file at startup. This upgrades older installs
        # in-place with the accent-friendly Ron vowel variant without touching
        # large model files or requiring setup to be rerun.
        try:
            prepare_keyword(self.settings.kws_tokens, self.settings.kws_keywords)
        except (OSError, RuntimeError) as error:
            raise WakeWordModelError(
                f"The Hey Ron keyword could not be prepared: {error}"
            ) from error

        try:
            import sherpa_onnx
        except ImportError as error:
            raise VoiceDependencyError(
                "sherpa-onnx is missing. Run scripts/setup_voice.ps1."
            ) from error

        threshold, score, paths, trailing_blanks, threads = self._sensitivity_profile()
        self._effective_threshold = threshold
        self._effective_score = score
        try:
            self._spotter = sherpa_onnx.KeywordSpotter(
                tokens=str(self.settings.kws_tokens),
                encoder=str(self.settings.kws_encoder),
                decoder=str(self.settings.kws_decoder),
                joiner=str(self.settings.kws_joiner),
                keywords_file=str(self.settings.kws_keywords),
                num_threads=threads,
                max_active_paths=paths,
                keywords_score=score,
                keywords_threshold=threshold,
                num_trailing_blanks=trailing_blanks,
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
        return clean in {self.settings.wake_phrase, "hey ron"}
