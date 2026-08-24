"""Local text-to-speech output with failure isolation and live mouth levels."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import queue
import re
import struct
import subprocess
import sys
import threading
import uuid
import wave
from array import array
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ron import __version__
from ron.core import Coordinator, EventType, FaceExpression, RonEvent
from ron.voice.settings import VoiceSettings


class SpeechOutputError(RuntimeError):
    """Raised when local speech output cannot safely produce or play audio."""


class SpeechDependencyError(SpeechOutputError):
    """Raised when an optional local TTS dependency is unavailable."""


class SpeechModelError(SpeechOutputError):
    """Raised when the configured local speech model is unavailable or invalid."""


MAX_GROQ_SPEECH_BYTES = 16 * 1024 * 1024
MAX_GROQ_TTS_CHARACTERS = 200
MAX_GROQ_ERROR_BYTES = 64 * 1024
MAX_GROQ_WAV_HEADER_BYTES = 64 * 1024
GROQ_STREAM_READ_BYTES = 4 * 1024
GROQ_ORPHEUS_TERMS_URL = (
    "https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english"
)


class GroqSpeechSynthesizer:
    """Convert one bounded reply segment to an in-memory Orpheus WAV."""

    _url = "https://api.groq.com/openai/v1/audio/speech"

    def __init__(self, settings: VoiceSettings) -> None:
        self.settings = settings

    @property
    def provider_label(self) -> str:
        return f"Groq Orpheus ({self.settings.groq_tts_voice})"

    def synthesize(self, text: str) -> tuple[array[float], int]:
        request = self._request(text)
        try:
            with urlopen(
                request, timeout=self.settings.groq_tts_timeout_seconds
            ) as response:
                payload = response.read(MAX_GROQ_SPEECH_BYTES + 1)
        except HTTPError as error:
            raise self._http_error(error) from error
        except (TimeoutError, URLError, OSError) as error:
            raise SpeechOutputError(
                "Groq speech could not be reached; check the internet connection"
            ) from error
        if len(payload) > MAX_GROQ_SPEECH_BYTES:
            raise SpeechOutputError("Groq returned oversized speech audio")
        return self._decode_wav(payload)

    def stream_synthesize(
        self,
        text: str,
        *,
        stop_event: threading.Event | None = None,
    ) -> Iterator[tuple[array[float], int]]:
        """Yield bounded PCM blocks as Orpheus sends its streaming WAV body."""
        request = self._request(text)
        try:
            with urlopen(
                request, timeout=self.settings.groq_tts_timeout_seconds
            ) as response:
                yield from self._stream_wav(response, stop_event=stop_event)
        except HTTPError as error:
            raise self._http_error(error) from error
        except SpeechOutputError:
            raise
        except (TimeoutError, URLError, OSError) as error:
            raise SpeechOutputError(
                "Groq speech streaming was interrupted; check the internet connection"
            ) from error

    def _request(self, text: str) -> Request:
        spoken = " ".join(text.split())
        if not spoken:
            raise SpeechOutputError("Groq speech received empty text")
        if len(spoken) > MAX_GROQ_TTS_CHARACTERS:
            raise SpeechOutputError("Groq Orpheus speech is limited to 200 characters per request")
        body = json.dumps(
            {
                "model": self.settings.groq_tts_model,
                "input": spoken,
                "voice": self.settings.groq_tts_voice,
                "response_format": "wav",
            },
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return Request(
            self._url,
            data=body,
            headers={
                "Accept": "audio/wav",
                "Authorization": f"Bearer {self.settings.groq_api_key}",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "User-Agent": f"Ron/{__version__}",
            },
            method="POST",
        )

    @staticmethod
    def _decode_wav(payload: bytes) -> tuple[array[float], int]:
        try:
            with wave.open(io.BytesIO(payload), "rb") as wav_file:
                channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                sample_rate = wav_file.getframerate()
                declared_frame_count = wav_file.getnframes()
                compression = wav_file.getcomptype()
                frames = wav_file.readframes(declared_frame_count)
        except (EOFError, wave.Error) as error:
            raise SpeechOutputError("Groq returned invalid WAV speech audio") from error
        if channels != 1 or compression != "NONE":
            raise SpeechOutputError("Groq returned an unsupported WAV layout")
        if sample_width not in {1, 2, 3, 4} or not 8_000 <= sample_rate <= 96_000:
            raise SpeechOutputError("Groq returned invalid WAV audio settings")

        # Groq serves Orpheus as a completed HTTP body with a streaming WAV
        # header: RIFF and data sizes are 0xFFFFFFFF because the producer did
        # not know the length when it emitted the header. Python's wave module
        # consequently reports 2,147,483,647 frames even though readframes()
        # correctly stops at the end of the downloaded payload. Validate the
        # real PCM bytes rather than trusting that sentinel declaration.
        frame_width = channels * sample_width
        if not frames or len(frames) % frame_width:
            raise SpeechOutputError("Groq returned incomplete WAV speech audio")
        frame_count = len(frames) // frame_width
        if frame_count <= 0 or frame_count > sample_rate * 120:
            raise SpeechOutputError("Groq returned invalid speech audio length")

        samples = GroqSpeechSynthesizer._decode_pcm(frames, sample_width)
        if not samples:
            raise SpeechOutputError("Groq returned empty speech audio")
        return samples, sample_rate

    @staticmethod
    def _decode_pcm(frames: bytes, sample_width: int) -> array[float]:
        if sample_width == 1:
            samples = array("f", ((value - 128) / 128.0 for value in frames))
        elif sample_width == 2:
            values = array("h")
            values.frombytes(frames)
            if sys.byteorder != "little":
                values.byteswap()
            samples = array("f", (value / 32_768.0 for value in values))
        elif sample_width == 4:
            values = array("i")
            values.frombytes(frames)
            if sys.byteorder != "little":
                values.byteswap()
            samples = array("f", (value / 2_147_483_648.0 for value in values))
        else:
            samples = array(
                "f",
                (
                    int.from_bytes(frames[index : index + 3], "little", signed=True)
                    / 8_388_608.0
                    for index in range(0, len(frames), 3)
                ),
            )
        return samples

    @classmethod
    def _stream_wav(
        cls,
        response: object,
        *,
        stop_event: threading.Event | None,
    ) -> Iterator[tuple[array[float], int]]:
        buffer = bytearray()
        total_bytes = 0
        ended = False

        def fill(minimum: int) -> bool:
            nonlocal total_bytes, ended
            while len(buffer) < minimum and not ended:
                if stop_event is not None and stop_event.is_set():
                    return False
                chunk = response.read(GROQ_STREAM_READ_BYTES)
                if not chunk:
                    ended = True
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_GROQ_SPEECH_BYTES:
                    raise SpeechOutputError("Groq returned oversized speech audio")
                buffer.extend(chunk)
            return len(buffer) >= minimum

        if not fill(12):
            if stop_event is not None and stop_event.is_set():
                return
            raise SpeechOutputError("Groq returned an incomplete WAV header")
        if bytes(buffer[:4]) != b"RIFF" or bytes(buffer[8:12]) != b"WAVE":
            raise SpeechOutputError("Groq returned invalid streaming WAV audio")
        del buffer[:12]
        header_bytes = 12
        sample_rate = 0
        sample_width = 0
        frame_width = 0
        data_size: int | None = None

        while data_size is None:
            if not fill(8):
                if stop_event is not None and stop_event.is_set():
                    return
                raise SpeechOutputError("Groq returned an incomplete WAV chunk header")
            chunk_name = bytes(buffer[:4])
            declared_size = struct.unpack_from("<I", buffer, 4)[0]
            del buffer[:8]
            header_bytes += 8
            if chunk_name == b"data":
                if frame_width == 0 or sample_rate == 0:
                    raise SpeechOutputError("Groq returned WAV data before its format")
                data_size = None if declared_size == 0xFFFFFFFF else declared_size
                break
            if declared_size > MAX_GROQ_WAV_HEADER_BYTES:
                raise SpeechOutputError("Groq returned an oversized WAV header chunk")
            padded_size = declared_size + (declared_size & 1)
            if not fill(padded_size):
                if stop_event is not None and stop_event.is_set():
                    return
                raise SpeechOutputError("Groq returned an incomplete WAV header chunk")
            chunk_payload = bytes(buffer[:declared_size])
            del buffer[:padded_size]
            header_bytes += padded_size
            if header_bytes > MAX_GROQ_WAV_HEADER_BYTES:
                raise SpeechOutputError("Groq returned an oversized WAV header")
            if chunk_name != b"fmt ":
                continue
            if len(chunk_payload) < 16:
                raise SpeechOutputError("Groq returned an incomplete WAV format")
            audio_format, channels, sample_rate, _byte_rate, block_align, bits = (
                struct.unpack_from("<HHIIHH", chunk_payload)
            )
            if audio_format != 1 or channels != 1:
                raise SpeechOutputError("Groq returned an unsupported WAV layout")
            if bits not in {8, 16, 24, 32} or not 8_000 <= sample_rate <= 96_000:
                raise SpeechOutputError("Groq returned invalid WAV audio settings")
            sample_width = bits // 8
            frame_width = channels * sample_width
            if block_align != frame_width:
                raise SpeechOutputError("Groq returned invalid WAV frame alignment")

        remaining = data_size
        carry = bytearray()
        total_frames = 0
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            if buffer:
                chunk = bytes(buffer)
                buffer.clear()
            else:
                chunk = response.read(GROQ_STREAM_READ_BYTES)
                if chunk:
                    total_bytes += len(chunk)
                    if total_bytes > MAX_GROQ_SPEECH_BYTES:
                        raise SpeechOutputError("Groq returned oversized speech audio")
            if not chunk:
                break
            if remaining is not None:
                chunk = chunk[:remaining]
                remaining -= len(chunk)
            carry.extend(chunk)
            complete = len(carry) - (len(carry) % frame_width)
            if complete:
                pcm = bytes(carry[:complete])
                del carry[:complete]
                total_frames += complete // frame_width
                if total_frames > sample_rate * 120:
                    raise SpeechOutputError("Groq returned invalid speech audio length")
                yield cls._decode_pcm(pcm, sample_width), sample_rate
            if remaining == 0:
                break
        if carry or (remaining not in {None, 0}):
            raise SpeechOutputError("Groq returned incomplete WAV speech audio")
        if total_frames == 0:
            raise SpeechOutputError("Groq returned empty speech audio")

    def _http_error(self, error: HTTPError) -> SpeechOutputError:
        error_code, detail = self._safe_error_detail(error)
        if error_code == "model_terms_required":
            return SpeechOutputError(
                "Groq Orpheus model terms must be accepted once by the Groq "
                f"organization admin; open {GROQ_ORPHEUS_TERMS_URL}, accept the "
                "terms, then run python .\\scripts\\check_groq_voice.py"
            )
        if error.code == 401:
            return SpeechOutputError(
                "Groq rejected the API key; check GROQ_API_KEY in .env"
            )
        if error.code == 403:
            suffix = f": {detail}" if detail else ""
            return SpeechOutputError(f"Groq denied access to speech{suffix}")
        if error.code == 429:
            return SpeechOutputError("Groq's free text-to-speech limit was reached")
        if error.code >= 500:
            return SpeechOutputError("Groq speech is temporarily unavailable")
        if detail:
            return SpeechOutputError(
                f"Groq rejected the speech request (HTTP {error.code}): {detail}"
            )
        return SpeechOutputError(f"Groq speech returned HTTP {error.code}")

    def _safe_error_detail(self, error: HTTPError) -> tuple[str, str]:
        """Extract Groq's bounded JSON reason without leaking credentials."""
        try:
            raw = error.read(MAX_GROQ_ERROR_BYTES + 1)
        except (OSError, ValueError):
            return "", ""
        if not raw or len(raw) > MAX_GROQ_ERROR_BYTES:
            return "", ""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return "", ""
        error_payload = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error_payload, dict):
            return "", ""

        raw_code = error_payload.get("code", "")
        error_code = (
            raw_code
            if isinstance(raw_code, str)
            and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", raw_code)
            else ""
        )
        raw_message = error_payload.get("message", "")
        if not isinstance(raw_message, str):
            return error_code, ""
        detail = " ".join(raw_message.split())
        if self.settings.groq_api_key:
            detail = detail.replace(self.settings.groq_api_key, "[redacted]")
        detail = re.sub(r"gsk_[A-Za-z0-9_-]{12,}", "[redacted]", detail)
        return error_code, detail[:500]


type OperationRunner[T] = Callable[[Callable[[], T]], T]
type NoticeHandler = Callable[[str], None]
type LevelHandler = Callable[[float], None]
type FirstAudioHandler = Callable[[], None]
type FirstAudioByteHandler = Callable[[], None]


class StreamingSpeechSession:
    """Accept model tokens and play complete speech chunks as they arrive."""

    _sentence_end = re.compile(r"(?<=[.!?])\s+")

    def __init__(
        self,
        service: SpeechOutputService,
        *,
        on_first_audio: FirstAudioHandler | None = None,
        on_first_audio_byte: FirstAudioByteHandler | None = None,
    ) -> None:
        self._service = service
        self._on_first_audio = on_first_audio
        self._on_first_audio_byte = on_first_audio_byte
        self._chunks: queue.Queue[str | None] = queue.Queue(maxsize=12)
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._lock = threading.RLock()
        self._pending = ""
        self._characters = 0
        self._chunks_emitted = 0
        self._closed = False
        self._played = False
        self._has_content = False
        self._thread = threading.Thread(
            target=self._run,
            name="ron-streaming-speech",
            daemon=True,
        )
        self._thread.start()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def played(self) -> bool:
        return self._played

    @property
    def has_content(self) -> bool:
        return self._has_content

    @property
    def completion_event(self) -> threading.Event:
        return self._done

    def feed(self, token: str) -> None:
        if not token or self._cancel.is_set():
            return
        with self._lock:
            if self._closed:
                return
            cloud_limit = self._service.cloud_request_limit
            if cloud_limit is not None:
                self._pending += token
                maximum_pending = max(
                    1_000,
                    self._service.settings.tts_max_characters * 2,
                )
                self._pending = self._pending[:maximum_pending]
                self._drain_cloud_sentences()
                return
            self._pending += token
            parts = self._sentence_end.split(self._pending)
            self._pending = parts.pop() if parts else ""
            for part in parts:
                self._emit(part)
            target = self._service.settings.tts_chunk_characters
            if len(self._pending) >= target:
                cut = self._pending.rfind(" ", 0, target + 1)
                if cut < max(40, target // 2):
                    cut = target
                ready, self._pending = self._pending[:cut], self._pending[cut:]
                self._emit(ready)

    def finish(self, *, wait: bool = True, timeout: float | None = None) -> bool:
        with self._lock:
            if not self._closed:
                if self._service.cloud_request_limit is not None:
                    self._drain_cloud_sentences()
                    self._emit_cloud_remainder()
                else:
                    self._emit(self._pending)
                self._pending = ""
                self._closed = True
                self._put(None)
        if wait:
            self._done.wait(timeout)
        return self._has_content and not self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            if not self._closed:
                self._closed = True
                self._pending = ""
                self._put(None)

    def _emit(self, text: str) -> None:
        if self._cancel.is_set():
            return
        cloud_limit = self._service.cloud_request_limit
        if cloud_limit is not None and self._chunks_emitted >= cloud_limit:
            return
        remaining = self._service.settings.tts_max_characters - self._characters
        if remaining <= 0:
            return
        hard_limit = remaining
        if cloud_limit is not None:
            hard_limit = min(hard_limit, self._service.cloud_chunk_characters)
        spoken = self._service.formatter.prepare(text)
        if len(spoken) > hard_limit:
            if hard_limit < 120:
                return
            spoken = SpeechTextFormatter(hard_limit).prepare(spoken)
        if cloud_limit is not None:
            spoken = spoken[:MAX_GROQ_TTS_CHARACTERS].rstrip()
        if not spoken:
            return
        self._has_content = True
        self._characters += len(spoken)
        self._chunks_emitted += 1
        self._put(spoken)

    def _drain_cloud_sentences(self) -> None:
        """Stream complete early sentences while reserving a safe final request."""
        cloud_limit = self._service.cloud_request_limit
        if cloud_limit is None:
            return
        early_limit = max(0, cloud_limit - 1)
        target = self._service.cloud_chunk_characters
        while self._chunks_emitted < early_limit:
            boundary = self._sentence_end.search(self._pending)
            if boundary is None:
                return
            sentence = self._pending[: boundary.start()].strip()
            remainder = self._pending[boundary.end() :]
            if not sentence:
                self._pending = remainder
                continue
            available = early_limit - self._chunks_emitted
            prepared = self._service.formatter.prepare(sentence)
            required = max(1, (len(prepared) + target - 1) // target)
            if required > available:
                return
            chunks = self._service.formatter.prepare_cloud_chunks(
                sentence,
                target_characters=target,
                maximum_chunks=available,
            )
            for chunk in chunks:
                self._emit(chunk)
            self._pending = remainder

    def _emit_cloud_remainder(self) -> None:
        cloud_limit = self._service.cloud_request_limit
        if cloud_limit is None or not self._pending.strip():
            return
        available = cloud_limit - self._chunks_emitted
        if available <= 0:
            return
        remaining_characters = (
            self._service.settings.tts_max_characters - self._characters
        )
        if remaining_characters < 120:
            return
        formatter = SpeechTextFormatter(
            min(
                remaining_characters,
                available * self._service.cloud_chunk_characters,
            )
        )
        chunks = formatter.prepare_cloud_chunks(
            self._pending,
            target_characters=self._service.cloud_chunk_characters,
            maximum_chunks=available,
        )
        for chunk in chunks:
            self._emit(chunk)

    def _put(self, item: str | None) -> None:
        while not self._service._stop.is_set():
            try:
                self._chunks.put(item, timeout=0.1)
                return
            except queue.Full:
                if self._cancel.is_set():
                    return

    def _run(self) -> None:
        try:
            self._played = self._service._play_stream(
                self._chunks,
                self._cancel,
                on_first_audio=self._on_first_audio,
                on_first_audio_byte=self._on_first_audio_byte,
            )
        finally:
            self._done.set()
            self._service._stream_finished(self)


class SpeechTextFormatter:
    """Turn display-oriented text into something comfortable to hear aloud."""

    _code_fence = re.compile(r"```.*?```", re.DOTALL)
    _markdown_link = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
    _url = re.compile(r"https?://\S+")
    _heading = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
    _bullet = re.compile(r"(?m)^\s*[-*+]\s+")
    _numbered = re.compile(r"(?m)^\s*\d+[.)]\s+")
    _emphasis = re.compile(r"[*_~`]+")
    _whitespace = re.compile(r"\s+")
    _windows_path = re.compile(r"\b[A-Za-z]:\\[^\s]+")
    _pronunciations = (
        (re.compile(r"\bAPI\b", re.IGNORECASE), "A P I"),
        (re.compile(r"\bCLI\b", re.IGNORECASE), "C L I"),
        (re.compile(r"\bUSB\b", re.IGNORECASE), "U S B"),
        (re.compile(r"\bSSD\b", re.IGNORECASE), "S S D"),
        (re.compile(r"\bHDD\b", re.IGNORECASE), "H D D"),
        (re.compile(r"\bURL\b", re.IGNORECASE), "U R L"),
        (re.compile(r"\bPySide6\b", re.IGNORECASE), "PySide six"),
        (re.compile(r"\bVS Code\b", re.IGNORECASE), "V S Code"),
    )

    def __init__(self, maximum_characters: int = 700) -> None:
        if not 120 <= maximum_characters <= 4_000:
            raise ValueError("Spoken reply character limit must be between 120 and 4000")
        self.maximum_characters = maximum_characters

    def prepare(self, text: str) -> str:
        value = text.strip()
        if not value:
            return ""

        had_code = bool(self._code_fence.search(value))
        value = self._code_fence.sub(" I've put the code in the terminal. ", value)
        value = self._markdown_link.sub(r"\1", value)
        value = self._url.sub("the link shown in the terminal", value)
        value = self._windows_path.sub("the path shown in the terminal", value)
        value = self._heading.sub("", value)
        value = self._bullet.sub("", value)
        value = self._numbered.sub("", value)
        value = self._emphasis.sub("", value)
        for pattern, replacement in self._pronunciations:
            value = pattern.sub(replacement, value)
        value = value.replace("|", ", ")
        value = self._whitespace.sub(" ", value).strip()

        if len(value) <= self.maximum_characters:
            return value

        suffix = " I've put the rest in the terminal."
        if had_code:
            suffix = " The rest is in the terminal."
        content_limit = max(1, self.maximum_characters - len(suffix) - 1)
        clipped = value[:content_limit].rstrip()
        sentence_end = max(clipped.rfind(". "), clipped.rfind("? "), clipped.rfind("! "))
        if sentence_end >= int(content_limit * 0.55):
            clipped = clipped[: sentence_end + 1].rstrip()
        else:
            clipped = clipped.rsplit(" ", 1)[0].rstrip(" ,;:-") + "."
        return (clipped + suffix).strip()

    def prepare_chunks(self, text: str, target_characters: int) -> tuple[str, ...]:
        """Create sentence-led chunks so short audio can start immediately."""
        if not 80 <= target_characters <= 500:
            raise ValueError("Speech chunk target must be between 80 and 500 characters")
        spoken = self.prepare(text)
        if not spoken:
            return ()
        sentences = tuple(
            item.strip() for item in re.split(r"(?<=[.!?])\s+", spoken) if item.strip()
        )
        if not sentences:
            return (spoken,)

        chunks: list[str] = []
        # The opening sentence stands alone. Kokoro can synthesize it while it is
        # still short, which removes the long silent wait before the first word.
        chunks.extend(self._split_long_chunk(sentences[0], target_characters))
        pending = ""
        for sentence in sentences[1:]:
            if len(sentence) > target_characters:
                if pending:
                    chunks.append(pending)
                    pending = ""
                chunks.extend(self._split_long_chunk(sentence, target_characters))
                continue
            candidate = f"{pending} {sentence}".strip()
            if pending and len(candidate) > target_characters:
                chunks.append(pending)
                pending = sentence
            else:
                pending = candidate
        if pending:
            chunks.append(pending)
        return tuple(item for item in chunks if item)

    def prepare_cloud_chunks(
        self,
        text: str,
        *,
        target_characters: int,
        maximum_chunks: int,
    ) -> tuple[str, ...]:
        """Return bounded Groq inputs whose final part always ends deliberately."""
        if not 120 <= target_characters <= MAX_GROQ_TTS_CHARACTERS:
            raise ValueError("Groq speech target must be between 120 and 200 characters")
        if not 1 <= maximum_chunks <= 6:
            raise ValueError("Groq speech allows between 1 and 6 chunks")
        budget = min(
            self.maximum_characters,
            target_characters * maximum_chunks,
        )
        formatter = self if budget == self.maximum_characters else SpeechTextFormatter(budget)
        pending = formatter.prepare(text)
        if not pending:
            return ()

        chunks: list[str] = []
        while pending and len(chunks) < maximum_chunks:
            slots_left = maximum_chunks - len(chunks)
            if len(pending) <= target_characters:
                chunks.append(pending)
                break
            if slots_left == 1:
                chunks.append(SpeechTextFormatter(target_characters).prepare(pending))
                break

            window = pending[: target_characters + 1]
            sentence_cut = max(
                window.rfind(". "),
                window.rfind("? "),
                window.rfind("! "),
            )
            clause_cut = max(window.rfind(", "), window.rfind("; "), window.rfind(": "))
            word_cut = window.rfind(" ")
            cut = sentence_cut + 1 if sentence_cut >= target_characters // 2 else clause_cut + 1
            if cut < target_characters // 2:
                cut = word_cut
            if cut <= 0:
                cut = target_characters
            chunk = pending[:cut].strip()
            if not chunk:
                break
            chunks.append(chunk)
            pending = pending[cut:].strip()
        return tuple(chunks)

    @staticmethod
    def _split_long_chunk(text: str, target: int) -> tuple[str, ...]:
        words = text.split()
        if len(text) <= target or not words:
            return (text,)
        chunks: list[str] = []
        pending: list[str] = []
        for word in words:
            if len(word) > target:
                if pending:
                    chunks.append(" ".join(pending))
                    pending = []
                chunks.extend(
                    word[index : index + target]
                    for index in range(0, len(word), target)
                )
                continue
            candidate = " ".join((*pending, word))
            if pending and len(candidate) > target:
                chunks.append(" ".join(pending))
                pending = [word]
            else:
                pending.append(word)
        if pending:
            chunks.append(" ".join(pending))
        return tuple(chunks)


class KokoroSynthesizer:
    """Small local neural TTS adapter loaded only when speech output is used."""

    def __init__(self, settings: VoiceSettings) -> None:
        self.settings = settings
        self._engine: object | None = None
        self._lock = threading.RLock()

    def load(self) -> None:
        with self._lock:
            if self._engine is not None:
                return
            # Kokoro and Ollama may overlap for the opening sentence. Keep ONNX's
            # native worker pool deliberately small so speech starts quickly without
            # starving the local chat model on Ron's laptop CPU.
            os.environ["OMP_NUM_THREADS"] = str(self.settings.tts_cpu_threads)
            os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
            model = self.settings.tts_model
            voices = self.settings.tts_voices
            if not model.is_file():
                raise SpeechModelError(
                    f"Kokoro model is missing: {model}. Run scripts/setup_voice.ps1."
                )
            if not voices.is_file():
                raise SpeechModelError(
                    f"Kokoro voice data is missing: {voices}. Run scripts/setup_voice.ps1."
                )
            try:
                from kokoro_onnx import Kokoro
            except ImportError as error:
                raise SpeechDependencyError(
                    "kokoro-onnx is not installed. Run scripts/setup_voice.ps1."
                ) from error
            try:
                self._engine = Kokoro(str(model), str(voices))
            except Exception as error:
                raise SpeechModelError(f"Kokoro could not load its local model: {error}") from error

    def synthesize(self, text: str):
        self.load()
        assert self._engine is not None
        try:
            samples, sample_rate = self._engine.create(
                text,
                voice=self.settings.tts_voice,
                speed=self.settings.tts_speed,
                lang=self.settings.tts_language,
            )
        except Exception as error:
            raise SpeechOutputError(f"Kokoro synthesis failed: {error}") from error
        if samples is None or len(samples) == 0:
            raise SpeechOutputError("Kokoro returned empty speech audio")
        if not 8_000 <= int(sample_rate) <= 96_000:
            raise SpeechOutputError("Kokoro returned an invalid sample rate")
        return samples, int(sample_rate)


class WindowsSpeechSynthesizer:
    """Use Windows' built-in SAPI voice as a quick, dependency-free fallback."""

    def __init__(self, settings: VoiceSettings) -> None:
        self.settings = settings
        self._cache_directory = (
            settings.project_root / "runtime" / "cache" / "system_speech"
        )

    @property
    def provider_label(self) -> str:
        return "Windows system speech"

    @property
    def available(self) -> bool:
        return os.name == "nt"

    def synthesize(self, text: str) -> tuple[array[float], int]:
        spoken = " ".join(text.split())[:500]
        if not spoken:
            raise SpeechOutputError("Windows speech received empty text")
        if not self.available:
            raise SpeechDependencyError("Windows system speech is unavailable")
        self._cache_directory.mkdir(parents=True, exist_ok=True)
        output_path = self._cache_directory / f"sapi-{uuid.uuid4().hex}.wav"
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$voice.SetOutputToWaveFile($args[0]); "
            "$text = [Console]::In.ReadToEnd(); "
            "$voice.Speak($text); $voice.Dispose()"
        )
        command = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
            str(output_path),
        ]
        try:
            completed = subprocess.run(
                command,
                input=spoken,
                capture_output=True,
                text=True,
                timeout=8.0,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode != 0 or not output_path.is_file():
                raise SpeechOutputError("Windows system speech failed")
            with output_path.open("rb") as handle:
                payload = handle.read(MAX_GROQ_SPEECH_BYTES + 1)
            if len(payload) > MAX_GROQ_SPEECH_BYTES:
                raise SpeechOutputError("Windows returned oversized speech audio")
            return GroqSpeechSynthesizer._decode_wav(payload)
        except subprocess.TimeoutExpired as error:
            raise SpeechOutputError("Windows system speech timed out") from error
        except OSError as error:
            raise SpeechOutputError("Windows system speech could not start") from error
        finally:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass


class HybridSpeechSynthesizer:
    """Prefer Orpheus, then quick system speech, then optional local Kokoro."""

    def __init__(self, settings: VoiceSettings) -> None:
        self.settings = settings
        self.primary = GroqSpeechSynthesizer(settings)
        self._fast_fallback = (
            WindowsSpeechSynthesizer(settings)
            if settings.tts_fast_fallback and os.name == "nt"
            else None
        )
        self._fallback: KokoroSynthesizer | None = None
        self._lock = threading.Lock()
        self._logger = logging.getLogger(__name__)

    @property
    def provider_label(self) -> str:
        fallbacks: list[str] = []
        if self._fast_fallback is not None:
            fallbacks.append("fast Windows fallback")
        if self.settings.tts_fallback_local:
            fallbacks.append("cold local fallback")
        suffix = f" + {' + '.join(fallbacks)}" if fallbacks else ""
        return f"{self.primary.provider_label}{suffix}"

    def synthesize(self, text: str):
        try:
            return self.primary.synthesize(text)
        except SpeechOutputError as cloud_error:
            return self._fallback_audio(text, cloud_error)

    def stream_synthesize(
        self,
        text: str,
        *,
        stop_event: threading.Event | None = None,
    ) -> Iterator[tuple[array[float], int]]:
        yielded = False
        try:
            for item in self.primary.stream_synthesize(text, stop_event=stop_event):
                yielded = True
                yield item
        except SpeechOutputError as cloud_error:
            if yielded:
                raise SpeechOutputError(
                    f"Groq speech stopped after playback began ({cloud_error})"
                ) from cloud_error
            yield self._fallback_audio(text, cloud_error)

    def _fallback_audio(
        self, text: str, cloud_error: SpeechOutputError
    ) -> tuple[object, int]:
        failures = [f"Groq speech was unavailable ({cloud_error})"]
        if self._fast_fallback is not None:
            self._logger.warning(
                "Groq TTS failed; trying the fast Windows fallback: %s", cloud_error
            )
            try:
                return self._fast_fallback.synthesize(text)
            except SpeechOutputError as fast_error:
                failures.append(f"Windows speech failed ({fast_error})")
        if self.settings.tts_fallback_local:
            self._logger.warning("Trying the cold local speech fallback")
            try:
                return self._local().synthesize(text)
            except SpeechOutputError as local_error:
                failures.append(f"local speech failed ({local_error})")
        raise SpeechOutputError("; ".join(failures))

    def _local(self) -> KokoroSynthesizer:
        with self._lock:
            if self._fallback is None:
                self._fallback = KokoroSynthesizer(self.settings)
            return self._fallback


def build_speech_synthesizer(settings: VoiceSettings) -> object:
    """Build the chosen TTS provider without loading a model or using the network."""
    if settings.effective_tts_provider == "local":
        return KokoroSynthesizer(settings)
    if settings.tts_fallback_local or (
        settings.tts_fast_fallback and os.name == "nt"
    ):
        return HybridSpeechSynthesizer(settings)
    return GroqSpeechSynthesizer(settings)


class SoundDevicePlayer:
    """Play mono float audio in short chunks so the face can follow real amplitude."""

    def __init__(self, settings: VoiceSettings) -> None:
        self.settings = settings

    def play(
        self,
        samples: object,
        sample_rate: int,
        *,
        level_handler: LevelHandler,
        stop_event: threading.Event,
    ) -> None:
        self.play_sequence(
            ((samples, sample_rate),),
            level_handler=level_handler,
            stop_event=stop_event,
        )

    def play_sequence(
        self,
        items: Iterable[tuple[object, int]],
        *,
        level_handler: LevelHandler,
        stop_event: threading.Event,
    ) -> None:
        """Keep one output stream open across lazily supplied speech chunks."""
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError as error:
            raise SpeechDependencyError(
                "Speech playback dependencies are missing. Run scripts/setup_voice.ps1."
            ) from error

        iterator = iter(items)
        try:
            try:
                first_samples, sample_rate = next(iterator)
            except StopIteration:
                raise SpeechOutputError("Speech playback received no audio") from None
            audio = self._playback_audio(first_samples, np)
            chunk_samples = max(
                64,
                int(sample_rate * self.settings.tts_level_interval_ms / 1000),
            )
            with sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                device=self.settings.tts_output_device,
                blocksize=chunk_samples,
            ) as stream:
                self._write_audio(
                    stream,
                    audio,
                    chunk_samples,
                    np,
                    level_handler,
                    stop_event,
                )
                for next_samples, next_rate in iterator:
                    if int(next_rate) != int(sample_rate):
                        raise SpeechOutputError("Speech chunks returned inconsistent sample rates")
                    self._write_audio(
                        stream,
                        self._playback_audio(next_samples, np),
                        chunk_samples,
                        np,
                        level_handler,
                        stop_event,
                    )
        except Exception as error:
            raise SpeechOutputError(f"Audio playback failed: {error}") from error
        finally:
            level_handler(0.0)

    def _playback_audio(self, samples: object, np: object):
        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            raise SpeechOutputError("Speech playback received no audio")
        peak = float(np.max(np.abs(audio)))
        if not math.isfinite(peak):
            raise SpeechOutputError("Speech playback received invalid audio")
        if peak > 1.0:
            audio = audio / peak
        if self.settings.tts_gain != 1.0:
            audio = np.clip(audio * self.settings.tts_gain, -1.0, 1.0)
        return audio

    @staticmethod
    def _write_audio(
        stream: object,
        audio: object,
        chunk_samples: int,
        np: object,
        level_handler: LevelHandler,
        stop_event: threading.Event,
    ) -> None:
        for start in range(0, audio.size, chunk_samples):
            if stop_event.is_set():
                break
            chunk = audio[start : start + chunk_samples]
            rms = float(np.sqrt(np.mean(np.square(chunk), dtype=np.float64)))
            level_handler(max(0.0, min(1.0, rms * 5.5)))
            stream.write(chunk.reshape(-1, 1))


class SpeechOutputService:
    """Synthesize and speak replies without making TTS a dependency of Ron itself."""

    def __init__(
        self,
        coordinator: Coordinator,
        settings: VoiceSettings,
        *,
        notice_handler: NoticeHandler | None = None,
        synthesis_runner: OperationRunner | None = None,
        synthesizer: object | None = None,
        player: object | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.settings = settings
        self.notice_handler = notice_handler or (lambda message: None)
        self.synthesis_runner = synthesis_runner or (lambda operation: operation())
        self.synthesizer = synthesizer or build_speech_synthesizer(settings)
        self.player = player or SoundDevicePlayer(settings)
        # Normal acknowledgements and progress cues must use the exact same
        # configured Groq voice as full replies. Emergency fallbacks remain
        # available only when the live cloud request itself fails.
        self._cue_synthesizer = (
            GroqSpeechSynthesizer(settings)
            if settings.effective_tts_provider == "groq"
            else None
        )
        formatter_limit = settings.tts_max_characters
        if settings.effective_tts_provider == "groq":
            formatter_limit = min(
                formatter_limit,
                max(120, min(190, settings.tts_chunk_characters))
                * settings.groq_tts_max_requests_per_turn,
            )
        self.formatter = SpeechTextFormatter(formatter_limit)
        self._stop = threading.Event()
        self._logger = logging.getLogger(__name__)
        self._failure_notified = False
        self._audio_cache: dict[str, tuple[object, int]] = {}
        self._persistent_cache_directory = (
            self.settings.project_root / "runtime" / "cache" / "voice_ack"
        )
        self._lock = threading.RLock()
        self._session_lock = threading.RLock()
        self._current_session: StreamingSpeechSession | None = None
        self._active_stop_event: threading.Event | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.tts_enabled

    @property
    def cloud_request_limit(self) -> int | None:
        if self.settings.effective_tts_provider != "groq":
            return None
        return self.settings.groq_tts_max_requests_per_turn

    @property
    def cloud_chunk_characters(self) -> int:
        return max(120, min(190, self.settings.tts_chunk_characters))

    def status_label(self) -> str:
        if not self.settings.tts_enabled:
            return "speech output disabled"
        if self._failure_notified:
            return "speech output unavailable"
        label = getattr(self.synthesizer, "provider_label", None)
        if isinstance(label, str) and label:
            return f"speech output configured ({label})"
        voice = (
            self.settings.groq_tts_voice
            if self.settings.effective_tts_provider == "groq"
            else self.settings.tts_voice
        )
        return f"speech output configured ({voice})"

    def _chunks_for(self, text: str) -> tuple[str, ...]:
        if self.cloud_request_limit is None:
            return self.formatter.prepare_chunks(
                text, self.settings.tts_chunk_characters
            )
        return self.formatter.prepare_cloud_chunks(
            text,
            target_characters=self.cloud_chunk_characters,
            maximum_chunks=self.cloud_request_limit,
        )

    def is_cached(self, text: str) -> bool:
        """Return true only when speaking this text needs no model inference."""
        chunks = self._chunks_for(text)
        if not chunks:
            return False
        with self._lock:
            return all(chunk in self._audio_cache for chunk in chunks)

    def speak_cached(self, text: str) -> bool:
        """Play only already-generated feedback; never add synthesis to a tool path."""
        if not self.is_cached(text):
            return False
        return self.speak(text)

    def start(self) -> None:
        self._stop.clear()

    def stop(self) -> None:
        self._stop.set()
        self.cancel_current()
        self._publish_level(0.0)
        self.coordinator.publish(RonEvent(EventType.SPEECH_ENDED))

    def open_stream(
        self,
        *,
        on_first_audio: FirstAudioHandler | None = None,
        on_first_audio_byte: FirstAudioByteHandler | None = None,
    ) -> StreamingSpeechSession:
        """Open a token-fed speech turn, replacing any older unfinished turn."""
        with self._session_lock:
            previous = self._current_session
            if previous is not None:
                previous.cancel()
            session = StreamingSpeechSession(
                self,
                on_first_audio=on_first_audio,
                on_first_audio_byte=on_first_audio_byte,
            )
            self._current_session = session
            return session

    def cancel_current(self) -> bool:
        with self._session_lock:
            session = self._current_session
            active_stop = self._active_stop_event
        interrupted = False
        if session is not None and not session.cancelled:
            session.cancel()
            interrupted = True
        if active_stop is not None and not active_stop.is_set():
            active_stop.set()
            interrupted = True
        return interrupted

    def _stream_finished(self, session: StreamingSpeechSession) -> None:
        with self._session_lock:
            if self._current_session is session:
                self._current_session = None

    def speak(self, text: str) -> bool:
        if not self.settings.tts_enabled or self._stop.is_set():
            return False
        chunks = self._chunks_for(text)
        if not chunks:
            return False

        with self._lock:
            stop_event = threading.Event()
            with self._session_lock:
                self._active_stop_event = stop_event
            try:
                self._publish_expression(FaceExpression.THINKING)
                played = self._play_chunks(chunks, stop_event)
                self._failure_notified = False
                return played
            except SpeechOutputError as error:
                self._logger.warning("Speech output failed safely: %s", error)
                self._notify_failure(str(error))
                return False
            except Exception as error:
                self._logger.exception("Unexpected speech output failure")
                self._notify_failure(f"Unexpected {type(error).__name__}")
                return False
            finally:
                with self._session_lock:
                    if self._active_stop_event is stop_event:
                        self._active_stop_event = None
                self._publish_level(0.0)
                self.coordinator.publish(RonEvent(EventType.SPEECH_ENDED))
                self._publish_expression(FaceExpression.IDLE)

    def _play_chunks(
        self,
        chunks: tuple[str, ...],
        stop_event: threading.Event,
    ) -> bool:
        """Pipeline next-chunk synthesis underneath current-chunk playback."""
        current = self._audio_for(chunks[0])
        if self._stop.is_set():
            return False
        self.coordinator.publish(RonEvent(EventType.SPEECH_STARTED))
        played = False
        audio_items = self._audio_sequence(chunks, current)
        sequence_player = getattr(self.player, "play_sequence", None)
        if callable(sequence_player):
            sequence_player(
                audio_items,
                level_handler=self._publish_level,
                stop_event=stop_event,
            )
            return not self._stop.is_set() and not stop_event.is_set()

        for samples, sample_rate in audio_items:
            if self._stop.is_set() or stop_event.is_set():
                break
            self.player.play(
                samples,
                sample_rate,
                level_handler=self._publish_level,
                stop_event=stop_event,
            )
            played = True
        return played

    def _play_stream(
        self,
        chunks: queue.Queue[str | None],
        cancel_event: threading.Event,
        *,
        on_first_audio: FirstAudioHandler | None,
        on_first_audio_byte: FirstAudioByteHandler | None,
    ) -> bool:
        """Keep one output stream open while the language model supplies sentences."""
        if not self.settings.tts_enabled or self._stop.is_set():
            return False
        # Do not own the speech lock while the model is still producing its first
        # sentence. Cached progress cues must remain able to play during this wait.
        first_text = self._next_stream_text(chunks, cancel_event)
        if first_text is None:
            return False
        with self._lock:
            with self._session_lock:
                self._active_stop_event = cancel_event
            try:
                if cancel_event.is_set():
                    return False
                live_streamer = getattr(self.synthesizer, "stream_synthesize", None)
                live_iterator: Iterator[tuple[object, int]] | None = None
                if self.settings.groq_tts_streaming and callable(live_streamer):
                    live_iterator = iter(
                        live_streamer(first_text, stop_event=cancel_event)
                    )
                    try:
                        first = next(live_iterator)
                    except StopIteration:
                        return False
                    if on_first_audio_byte is not None:
                        on_first_audio_byte()
                else:
                    first = self._audio_for(first_text)
                    if on_first_audio_byte is not None:
                        on_first_audio_byte()
                if cancel_event.is_set() or self._stop.is_set():
                    return False
                self.coordinator.publish(RonEvent(EventType.SPEECH_STARTED))
                self._publish_expression(FaceExpression.SPEAKING)
                if on_first_audio is not None:
                    on_first_audio()
                sequence = (
                    self._live_provider_sequence(
                        chunks,
                        first,
                        live_iterator,
                        cancel_event,
                    )
                    if live_iterator is not None
                    else self._stream_audio_sequence(chunks, first, cancel_event)
                )
                sequence_player = getattr(self.player, "play_sequence", None)
                if callable(sequence_player):
                    sequence_player(
                        sequence,
                        level_handler=self._publish_level,
                        stop_event=cancel_event,
                    )
                else:
                    for samples, sample_rate in sequence:
                        if cancel_event.is_set():
                            break
                        self.player.play(
                            samples,
                            sample_rate,
                            level_handler=self._publish_level,
                            stop_event=cancel_event,
                        )
                self._failure_notified = False
                return not cancel_event.is_set()
            except SpeechOutputError as error:
                self._logger.warning("Streaming speech failed safely: %s", error)
                self._notify_failure(str(error))
                return False
            except Exception as error:
                self._logger.exception("Unexpected streaming speech failure")
                self._notify_failure(f"Unexpected {type(error).__name__}")
                return False
            finally:
                with self._session_lock:
                    if self._active_stop_event is cancel_event:
                        self._active_stop_event = None
                self._publish_level(0.0)
                self.coordinator.publish(RonEvent(EventType.SPEECH_ENDED))
                self._publish_expression(FaceExpression.IDLE)

    def _live_provider_sequence(
        self,
        chunks: queue.Queue[str | None],
        first: tuple[object, int],
        provider: Iterator[tuple[object, int]],
        cancel_event: threading.Event,
    ) -> Iterator[tuple[object, int]]:
        """Prefetch each later Groq part underneath current streamed playback."""
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="ron-tts") as executor:
            pending = executor.submit(self._next_stream_audio, chunks, cancel_event)
            yield first
            for item in provider:
                if cancel_event.is_set() or self._stop.is_set():
                    return
                yield item
            while not cancel_event.is_set() and not self._stop.is_set():
                following = pending.result()
                if following is None:
                    return
                pending = executor.submit(
                    self._next_stream_audio,
                    chunks,
                    cancel_event,
                )
                yield following

    def _stream_audio_sequence(
        self,
        chunks: queue.Queue[str | None],
        first: tuple[object, int],
        cancel_event: threading.Event,
    ) -> Iterator[tuple[object, int]]:
        """Synthesize the next streamed sentence underneath current playback."""
        current = first
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="ron-tts") as executor:
            while not cancel_event.is_set() and not self._stop.is_set():
                pending = executor.submit(self._next_stream_audio, chunks, cancel_event)
                yield current
                following = pending.result()
                if following is None:
                    return
                current = following

    def _next_stream_audio(
        self,
        chunks: queue.Queue[str | None],
        cancel_event: threading.Event,
    ) -> tuple[object, int] | None:
        while not cancel_event.is_set() and not self._stop.is_set():
            try:
                text = chunks.get(timeout=0.1)
            except queue.Empty:
                continue
            return None if text is None else self._audio_for(text)
        return None

    def _next_stream_text(
        self,
        chunks: queue.Queue[str | None],
        cancel_event: threading.Event,
    ) -> str | None:
        while not cancel_event.is_set() and not self._stop.is_set():
            try:
                return chunks.get(timeout=0.1)
            except queue.Empty:
                continue
        return None

    def _audio_sequence(
        self,
        chunks: tuple[str, ...],
        first: tuple[object, int],
    ) -> Iterator[tuple[object, int]]:
        """Yield audio in order while preparing at most one future chunk."""
        if not self.settings.tts_prefetch_chunks or len(chunks) == 1:
            yield first
            for chunk in chunks[1:]:
                yield self._audio_for(chunk)
            return

        current = first
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="ron-tts") as executor:
            for index in range(len(chunks)):
                pending: Future[tuple[object, int]] | None = None
                if index + 1 < len(chunks):
                    pending = executor.submit(self._audio_for, chunks[index + 1])
                yield current
                if pending is not None:
                    current = pending.result()

    def prewarm(self, phrases: tuple[str, ...]) -> None:
        """Cache tiny acknowledgement phrases so wake replies feel immediate."""
        if not self.settings.tts_enabled or self._stop.is_set():
            return
        for phrase in phrases:
            if self._stop.is_set():
                return
            spoken = self.formatter.prepare(phrase)
            if not spoken:
                continue
            try:
                if self.cloud_request_limit is not None:
                    with self._lock:
                        cached = self._audio_cache.get(spoken)
                        if cached is None:
                            cached = self._load_persistent_audio(spoken)
                            if cached is not None:
                                self._audio_cache[spoken] = cached
                    if cached is not None or self._cue_synthesizer is None:
                        continue

                    # Never hold the playback lock across a background network
                    # request. A real wake/response can therefore take priority.
                    samples, sample_rate = self._cue_synthesizer.synthesize(spoken)
                    with self._lock:
                        self._audio_cache.setdefault(spoken, (samples, sample_rate))
                        self._store_persistent_audio(spoken, samples, sample_rate)
                    # Normal Groq responses already take several seconds; this
                    # cancellable gap keeps one-time cue generation conservatively paced.
                    if self._stop.wait(1.0):
                        return
                    continue
                with self._lock:
                    self._audio_for(spoken, persistent=True)
            except Exception:
                # Runtime speak() will surface a single friendly failure if TTS is unavailable.
                self._logger.debug("Speech prewarm skipped a phrase", exc_info=True)
                if self.cloud_request_limit is None:
                    return

    def _audio_for(self, spoken: str, *, persistent: bool = False) -> tuple[object, int]:
        cached = self._audio_cache.get(spoken)
        if cached is not None:
            return cached

        if persistent and len(spoken) <= 80:
            disk_cached = self._load_persistent_audio(spoken)
            if disk_cached is not None:
                self._audio_cache[spoken] = disk_cached
                return disk_cached

        samples, sample_rate = self.synthesis_runner(lambda: self.synthesizer.synthesize(spoken))
        result = (samples, sample_rate)
        # Keep only short, reusable prompts in memory. Long answers remain one-shot.
        if len(spoken) <= 80:
            self._audio_cache[spoken] = result
            if persistent:
                self._store_persistent_audio(spoken, samples, sample_rate)
        return result

    def _persistent_audio_path(self, spoken: str) -> Path:
        if self.settings.effective_tts_provider == "groq":
            model_marker = (
                f"groq:{self.settings.groq_tts_model}:"
                f"{self.settings.groq_tts_voice}:unified-v1"
            )
            voice_marker = self.settings.groq_tts_voice
            speed_marker = "provider-default"
            language_marker = "en"
        else:
            model_marker = f"local:{self.settings.tts_model.name}"
            try:
                stat = self.settings.tts_model.stat()
                model_marker += f":{stat.st_size}:{stat.st_mtime_ns}"
            except OSError:
                pass
            voice_marker = self.settings.tts_voice
            speed_marker = f"{self.settings.tts_speed:.4f}"
            language_marker = self.settings.tts_language
        key = "|".join(
            (
                model_marker,
                voice_marker,
                speed_marker,
                language_marker,
                spoken,
            )
        )
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        return self._persistent_cache_directory / f"{digest}.npz"

    def _load_persistent_audio(self, spoken: str) -> tuple[object, int] | None:
        path = self._persistent_audio_path(spoken)
        if not path.is_file():
            return None
        try:
            import numpy as np

            with np.load(path, allow_pickle=False) as data:
                samples = np.asarray(data["samples"], dtype=np.float32).reshape(-1)
                sample_rate = int(np.asarray(data["sample_rate"]).reshape(-1)[0])
            if samples.size == 0 or not 8_000 <= sample_rate <= 96_000:
                raise ValueError("invalid cached acknowledgement")
            return samples, sample_rate
        except Exception:
            self._logger.debug("Ignoring damaged acknowledgement cache", exc_info=True)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None

    def _store_persistent_audio(self, spoken: str, samples: object, sample_rate: int) -> None:
        path = self._persistent_audio_path(spoken)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            import numpy as np

            path.parent.mkdir(parents=True, exist_ok=True)
            audio = np.asarray(samples, dtype=np.float32).reshape(-1)
            if audio.size == 0:
                return
            with temporary.open("wb") as handle:
                np.savez(handle, samples=audio, sample_rate=np.asarray([sample_rate]))
            os.replace(temporary, path)
        except Exception:
            self._logger.debug("Could not persist acknowledgement cache", exc_info=True)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _notify_failure(self, detail: str) -> None:
        if self._failure_notified:
            return
        self._failure_notified = True
        try:
            self.notice_handler(
                f"[SPEECH OUTPUT OFFLINE] {detail} Voice input and terminal chat are still working."
            )
        except Exception:
            self._logger.debug("Speech output notice failed", exc_info=True)

    def _publish_level(self, level: float) -> None:
        self.coordinator.publish(
            RonEvent(EventType.SPEECH_LEVEL, {"level": max(0.0, min(1.0, float(level)))})
        )

    def _publish_expression(self, expression: FaceExpression) -> None:
        self.coordinator.publish(
            RonEvent(EventType.FACE_EXPRESSION, {"expression": expression.value})
        )
