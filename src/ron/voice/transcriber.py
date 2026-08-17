"""Single-pass, warm local faster-whisper transcription."""

from __future__ import annotations

import math
import threading
import time
from array import array

from ron.voice.audio import VoiceDependencyError
from ron.voice.models import TranscriptionResult
from ron.voice.settings import VoiceSettings


class TranscriptionError(RuntimeError):
    """Raised when local speech recognition cannot safely return text."""


class FasterWhisperTranscriber:
    """Keep one CPU-int8 model warm and transcribe one complete utterance once."""

    def __init__(self, settings: VoiceSettings) -> None:
        self.settings = settings
        self._model: object | None = None
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            try:
                from faster_whisper import WhisperModel
            except ImportError as error:
                raise VoiceDependencyError(
                    "faster-whisper is missing. Run scripts/setup_voice.ps1."
                ) from error
            self.settings.whisper_download_root.mkdir(parents=True, exist_ok=True)
            arguments = {
                "device": "cpu",
                "compute_type": self.settings.asr_compute_type,
                "cpu_threads": self.settings.asr_threads,
                "download_root": str(self.settings.whisper_download_root),
                "local_files_only": True,
            }
            try:
                self._model = WhisperModel(self.settings.asr_model, **arguments)
            except Exception as error:
                raise TranscriptionError(
                    "The local Whisper model is unavailable. Run scripts/setup_voice.ps1."
                ) from error

    def warm(self) -> None:
        """Pay the first inference cost before the first real voice command."""
        self.load()
        silence = array("f", [0.0]) * int(self.settings.sample_rate * 0.25)
        try:
            self.transcribe(silence, allow_empty=True)
        except TranscriptionError:
            # Silence can be rejected by model versions; successful load is enough.
            return

    def transcribe(
        self, samples: array[float], *, allow_empty: bool = False
    ) -> TranscriptionResult:
        self.load()
        if self._model is None:
            raise TranscriptionError("Whisper is not loaded")
        maximum_samples = int(
            self.settings.sample_rate * self.settings.maximum_speech_seconds * 1.2
        )
        if len(samples) > maximum_samples:
            raise TranscriptionError("The captured speech exceeded Ron's safety limit")
        if len(samples) < int(self.settings.sample_rate * 0.05):
            if allow_empty:
                return TranscriptionResult("", 0.0, 0.0, 1.0)
            raise TranscriptionError("The captured speech was too short")

        try:
            import numpy as np

            audio = np.asarray(samples, dtype=np.float32)
            started = time.perf_counter()
            segments, _ = self._model.transcribe(
                audio,
                language="en",
                task="transcribe",
                beam_size=1,
                best_of=1,
                temperature=0.0,
                condition_on_previous_text=False,
                word_timestamps=False,
                vad_filter=False,
                hotwords=", ".join(self.settings.hotwords) or None,
            )
            items = list(segments)
            duration = time.perf_counter() - started
        except Exception as error:
            raise TranscriptionError(f"Local transcription failed: {error}") from error

        text = " ".join(str(item.text).strip() for item in items if str(item.text).strip())
        text = " ".join(text.split())[:6_000]
        if not items:
            confidence = 0.0
            no_speech = 1.0
        else:
            weights = [max(0.01, float(item.end) - float(item.start)) for item in items]
            total_weight = sum(weights)
            average_log_probability = sum(
                float(getattr(item, "avg_logprob", -2.0)) * weight
                for item, weight in zip(items, weights, strict=True)
            ) / total_weight
            confidence = max(0.0, min(1.0, math.exp(average_log_probability)))
            no_speech = max(
                0.0,
                min(1.0, max(float(getattr(item, "no_speech_prob", 0.0)) for item in items)),
            )
        if not text and not allow_empty:
            return TranscriptionResult("", confidence, duration, no_speech)
        return TranscriptionResult(text, confidence, duration, no_speech)
