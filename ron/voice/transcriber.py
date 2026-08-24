"""Fast Groq transcription with a cold, accent-tolerant local fallback."""

from __future__ import annotations

import io
import json
import logging
import math
import sys
import threading
import time
import uuid
import wave
from array import array
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ron import __version__
from ron.voice.audio import VoiceDependencyError
from ron.voice.models import TranscriptionResult
from ron.voice.settings import VoiceSettings


class TranscriptionError(RuntimeError):
    """Raised when speech recognition cannot safely return text."""


MAX_GROQ_TRANSCRIPTION_BYTES = 4 * 1024 * 1024


class GroqTranscriber:
    """Send only one finalized post-wake utterance to Groq Whisper."""

    _url = "https://api.groq.com/openai/v1/audio/transcriptions"

    def __init__(self, settings: VoiceSettings) -> None:
        self.settings = settings

    @property
    def provider_label(self) -> str:
        return f"Groq {self.settings.groq_asr_model}"

    def load(self) -> None:
        """Cloud ASR has no laptop-side model to load."""

    def warm(self) -> None:
        """Avoid a startup network request and preserve the free allowance."""

    def transcribe(
        self,
        samples: array[float],
        *,
        allow_empty: bool = False,
        accurate: bool = False,
    ) -> TranscriptionResult:
        model = (
            self.settings.groq_asr_retry_model
            if accurate
            else self.settings.groq_asr_model
        )
        maximum_samples = int(
            self.settings.sample_rate * self.settings.maximum_speech_seconds * 1.2
        )
        if len(samples) > maximum_samples:
            raise TranscriptionError("The captured speech exceeded Ron's safety limit")
        if len(samples) < int(self.settings.sample_rate * 0.05):
            if allow_empty:
                return TranscriptionResult(
                    "", 0.0, 0.0, 1.0, "groq-accurate" if accurate else "groq-fast"
                )
            raise TranscriptionError("The captured speech was too short")

        wav_audio = self._wav_bytes(samples)
        boundary = f"ron-{uuid.uuid4().hex}"
        body = self._multipart_body(boundary, wav_audio, model)
        request = Request(
            self._url,
            data=body,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.settings.groq_api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
                "User-Agent": f"Ron/{__version__}",
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urlopen(
                request, timeout=self.settings.groq_asr_timeout_seconds
            ) as response:
                raw = response.read(MAX_GROQ_TRANSCRIPTION_BYTES + 1)
        except HTTPError as error:
            raise self._http_error(error) from error
        except (TimeoutError, URLError, OSError) as error:
            raise TranscriptionError(
                "Groq speech recognition could not be reached; check the internet connection"
            ) from error
        elapsed = time.perf_counter() - started
        if len(raw) > MAX_GROQ_TRANSCRIPTION_BYTES:
            raise TranscriptionError("Groq returned an oversized transcription response")
        payload = self._decode_payload(raw)
        return self._result(payload, elapsed, accurate=accurate, allow_empty=allow_empty)

    def retry(self, samples: array[float]) -> TranscriptionResult:
        """Use Groq's accuracy model only when the turbo result was uncertain."""
        if not self.settings.asr_retry_enabled:
            raise TranscriptionError("The accurate transcription retry is disabled")
        return self.transcribe(samples, accurate=True)

    def _wav_bytes(self, samples: array[float]) -> bytes:
        pcm = array(
            "h",
            (
                int(
                    max(-1.0, min(1.0, float(sample) if math.isfinite(sample) else 0.0))
                    * 32_767
                )
                for sample in samples
            ),
        )
        if sys.byteorder != "little":
            pcm.byteswap()
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.settings.sample_rate)
            wav_file.writeframes(pcm.tobytes())
        return output.getvalue()

    def _multipart_body(self, boundary: str, wav_audio: bytes, model: str) -> bytes:
        marker = boundary.encode("ascii")
        parts: list[bytes] = []

        def add_field(name: str, value: str) -> None:
            parts.extend(
                (
                    b"--" + marker + b"\r\n",
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    value.encode("utf-8"),
                    b"\r\n",
                )
            )

        add_field("model", model)
        add_field("language", "en")
        add_field("response_format", "verbose_json")
        add_field("temperature", "0")
        prompt = self.settings.asr_initial_prompt.strip()[:800]
        if prompt:
            add_field("prompt", prompt)
        parts.extend(
            (
                b"--" + marker + b"\r\n",
                b'Content-Disposition: form-data; name="file"; '
                b'filename="ron-command.wav"\r\n',
                b"Content-Type: audio/wav\r\n\r\n",
                wav_audio,
                b"\r\n",
                b"--" + marker + b"--\r\n",
            )
        )
        return b"".join(parts)

    @staticmethod
    def _decode_payload(raw: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TranscriptionError("Groq returned malformed transcription data") from error
        if not isinstance(payload, dict):
            raise TranscriptionError("Groq returned an invalid transcription response")
        return payload

    @staticmethod
    def _result(
        payload: dict[str, Any],
        elapsed: float,
        *,
        accurate: bool,
        allow_empty: bool,
    ) -> TranscriptionResult:
        text_value = payload.get("text")
        text = " ".join(text_value.split())[:6_000] if isinstance(text_value, str) else ""
        raw_segments = payload.get("segments")
        segments = (
            tuple(item for item in raw_segments if isinstance(item, dict))
            if isinstance(raw_segments, list)
            else ()
        )
        if segments:
            try:
                weights = [
                    max(
                        0.01,
                        float(item.get("end", 0.0))
                        - float(item.get("start", 0.0)),
                    )
                    for item in segments
                ]
                total = sum(weights)
                average_log_probability = sum(
                    float(item.get("avg_logprob", -2.0)) * weight
                    for item, weight in zip(segments, weights, strict=True)
                ) / total
                if not math.isfinite(average_log_probability):
                    raise ValueError("non-finite log probability")
                average_log_probability = max(-20.0, min(0.0, average_log_probability))
                confidence = math.exp(average_log_probability)
                no_speech_values = [
                    float(item.get("no_speech_prob", 0.0)) for item in segments
                ]
                if not all(math.isfinite(value) for value in no_speech_values):
                    raise ValueError("non-finite no-speech probability")
                no_speech = max(0.0, min(1.0, max(no_speech_values)))
            except (TypeError, ValueError, OverflowError) as error:
                raise TranscriptionError(
                    "Groq returned invalid transcription metadata"
                ) from error
        elif text:
            confidence = 0.85
            no_speech = 0.0
        else:
            confidence = 0.0
            no_speech = 1.0
        if not text and not allow_empty:
            text = ""
        return TranscriptionResult(
            text,
            confidence,
            elapsed,
            no_speech,
            "groq-accurate" if accurate else "groq-fast",
        )

    @staticmethod
    def _http_error(error: HTTPError) -> TranscriptionError:
        if error.code in {401, 403}:
            return TranscriptionError(
                "Groq rejected the API key; check GROQ_API_KEY in .env"
            )
        if error.code == 429:
            return TranscriptionError(
                "Groq's free speech-recognition limit was reached"
            )
        if error.code >= 500:
            return TranscriptionError("Groq speech recognition is temporarily unavailable")
        return TranscriptionError(f"Groq speech recognition returned HTTP {error.code}")


class FasterWhisperTranscriber:
    """Keep one local model warm and decode short commands with accuracy-first settings."""

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
        self,
        samples: array[float],
        *,
        allow_empty: bool = False,
        accurate: bool = False,
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
                return TranscriptionResult(
                    "", 0.0, 0.0, 1.0, "accurate" if accurate else "fast"
                )
            raise TranscriptionError("The captured speech was too short")

        try:
            import numpy as np

            audio = self._condition_audio(np.asarray(samples, dtype=np.float32), np)
            beam_size = (
                self.settings.asr_retry_beam_size
                if accurate
                else self.settings.asr_beam_size
            )
            started = time.perf_counter()
            segments, _ = self._model.transcribe(
                audio,
                language="en",
                task="transcribe",
                beam_size=beam_size,
                # best_of applies to sampling; a deterministic beam pass does not
                # benefit from repeating the same search work here.
                best_of=1,
                patience=self.settings.asr_patience,
                temperature=0.0,
                condition_on_previous_text=False,
                initial_prompt=self.settings.asr_initial_prompt or None,
                word_timestamps=False,
                without_timestamps=True,
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
            return TranscriptionResult(
                "",
                confidence,
                duration,
                no_speech,
                "accurate" if accurate else "fast",
            )
        return TranscriptionResult(
            text,
            confidence,
            duration,
            no_speech,
            "accurate" if accurate else "fast",
        )

    def retry(self, samples: array[float]) -> TranscriptionResult:
        """Run the slower accuracy pass only after the fast pass was uncertain."""
        if not self.settings.asr_retry_enabled:
            raise TranscriptionError("The accurate transcription retry is disabled")
        return self.transcribe(samples, accurate=True)

    @staticmethod
    def _condition_audio(audio: object, np: object):
        """Clean malformed samples and gently lift a quiet microphone signal.

        Whisper is robust to normal volume variation, so this deliberately avoids
        aggressive automatic gain. It only removes DC offset and raises genuinely
        quiet speech by at most 3x; loud input is never boosted into clipping.
        """
        values = np.asarray(audio, dtype=np.float32).reshape(-1)
        if values.size == 0:
            return values
        values = np.nan_to_num(values, nan=0.0, posinf=1.0, neginf=-1.0)
        values = values - np.mean(values, dtype=np.float64)
        rms = float(np.sqrt(np.mean(np.square(values), dtype=np.float64)))
        if 0.0015 <= rms < 0.035:
            values = values * min(3.0, 0.045 / rms)
        return np.clip(values, -1.0, 1.0).astype(np.float32, copy=False)


class HybridTranscriber:
    """Prefer Groq and construct the heavy local model only after a failure."""

    def __init__(self, settings: VoiceSettings) -> None:
        self.settings = settings
        self.primary = GroqTranscriber(settings)
        self._fallback: FasterWhisperTranscriber | None = None
        self._lock = threading.Lock()
        self._logger = logging.getLogger(__name__)

    @property
    def provider_label(self) -> str:
        return f"{self.primary.provider_label} + cold local fallback"

    def load(self) -> None:
        """Leave local Whisper unloaded while Groq is the active provider."""

    def warm(self) -> None:
        """A cloud-first path deliberately performs no startup inference."""

    def transcribe(
        self,
        samples: array[float],
        *,
        allow_empty: bool = False,
        accurate: bool = False,
    ) -> TranscriptionResult:
        try:
            return self.primary.transcribe(
                samples, allow_empty=allow_empty, accurate=accurate
            )
        except TranscriptionError as cloud_error:
            self._logger.warning("Groq ASR failed; trying the cold local fallback: %s", cloud_error)
            try:
                return self._local().transcribe(
                    samples, allow_empty=allow_empty, accurate=accurate
                )
            except (TranscriptionError, VoiceDependencyError) as local_error:
                raise TranscriptionError(
                    f"Groq ASR was unavailable ({cloud_error}); "
                    f"the local fallback also failed ({local_error})"
                ) from local_error

    def retry(self, samples: array[float]) -> TranscriptionResult:
        if not self.settings.asr_retry_enabled:
            raise TranscriptionError("The accurate transcription retry is disabled")
        return self.transcribe(samples, accurate=True)

    def _local(self) -> FasterWhisperTranscriber:
        with self._lock:
            if self._fallback is None:
                self._fallback = FasterWhisperTranscriber(self.settings)
            return self._fallback


def build_transcriber(settings: VoiceSettings) -> object:
    """Build the selected provider without loading any model or using the network."""
    if settings.effective_asr_provider == "local":
        return FasterWhisperTranscriber(settings)
    if settings.asr_fallback_local:
        return HybridTranscriber(settings)
    return GroqTranscriber(settings)
