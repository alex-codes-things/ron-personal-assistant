"""Validated environment configuration for Ron's optional voice subsystem."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


class VoiceSettingsError(ValueError):
    """Raised before voice starts when a setting is invalid."""


def _boolean(name: str, default: bool) -> bool:
    value = os.getenv(name, "true" if default else "false").strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise VoiceSettingsError(f"{name} must be true or false")


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise VoiceSettingsError(f"{name} must be a whole number") from error
    if not minimum <= value <= maximum:
        raise VoiceSettingsError(f"{name} must be between {minimum} and {maximum}")
    return value


def _number(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as error:
        raise VoiceSettingsError(f"{name} must be a number") from error
    if not minimum <= value <= maximum:
        raise VoiceSettingsError(f"{name} must be between {minimum} and {maximum}")
    return value


def _phrases(name: str, default: str, *, maximum: int) -> tuple[str, ...]:
    values = tuple(
        dict.fromkeys(
            item.strip().casefold()
            for item in os.getenv(name, default).split("|")
            if item.strip()
        )
    )
    if not values or len(values) > maximum:
        raise VoiceSettingsError(f"{name} must contain between 1 and {maximum} phrases")
    if any(len(item) > 80 or any(char in item for char in "\r\n\0") for item in values):
        raise VoiceSettingsError(f"{name} contains an invalid phrase")
    return values


def _hotwords() -> tuple[str, ...]:
    raw = os.getenv(
        "RON_VOICE_HOTWORDS",
        "Ron,Spotify,Notepad,Calculator,File Explorer,Galway Girl,volume,brightness",
    )
    values = tuple(dict.fromkeys(item.strip() for item in raw.split(",") if item.strip()))
    if len(values) > 20 or any(len(item) > 60 for item in values):
        raise VoiceSettingsError("RON_VOICE_HOTWORDS allows at most 20 short terms")
    return values


def _path(name: str, default: Path) -> Path:
    value = os.getenv(name, "").strip()
    return Path(value).resolve() if value else default.resolve()


@dataclass(frozen=True, slots=True)
class VoiceSettings:
    """All voice limits are explicit so ambient audio cannot grow work unbounded."""

    enabled: bool
    project_root: Path
    wake_phrase: str = "hey ron"
    wake_aliases: tuple[str, ...] = ("hey ron", "hey run")
    microphone_device: str | None = None
    sample_rate: int = 16_000
    ring_buffer_seconds: float = 2.0
    audio_queue_frames: int = 64
    wake_threshold: float = 0.35
    wake_score: float = 1.5
    wake_cooldown_seconds: float = 2.0
    vad_threshold: float = 0.5
    end_silence_seconds: float = 0.55
    minimum_speech_seconds: float = 0.20
    maximum_speech_seconds: float = 15.0
    command_start_timeout_seconds: float = 2.5
    continuous_timeout_seconds: float = 25.0
    minimum_transcript_confidence: float = 0.18
    asr_model: str = "small.en"
    asr_compute_type: str = "int8"
    asr_threads: int = 4
    asr_preload: bool = True
    require_wake_in_transcript: bool = True
    retry_min_seconds: float = 2.0
    retry_max_seconds: float = 60.0
    reminder_seconds: float = 1_200.0
    hotwords: tuple[str, ...] = ()
    kws_directory: Path = field(default_factory=Path)
    vad_model: Path = field(default_factory=Path)
    whisper_download_root: Path = field(default_factory=Path)

    def __post_init__(self) -> None:
        root = self.project_root.resolve()
        object.__setattr__(self, "project_root", root)
        wake = " ".join(self.wake_phrase.strip().casefold().split())
        if not wake or len(wake) > 40:
            raise VoiceSettingsError("RON_WAKE_PHRASE is invalid")
        object.__setattr__(self, "wake_phrase", wake)
        if self.sample_rate not in {8_000, 16_000}:
            raise VoiceSettingsError("Voice sample rate must be 8000 or 16000")
        if not self.asr_model.strip() or len(self.asr_model) > 120:
            raise VoiceSettingsError("RON_WHISPER_MODEL is invalid")
        if self.asr_compute_type not in {"int8", "int8_float16", "float16", "float32"}:
            raise VoiceSettingsError("RON_WHISPER_COMPUTE_TYPE is invalid")
        if not 1 <= self.asr_threads <= 16:
            raise VoiceSettingsError("RON_WHISPER_THREADS must be between 1 and 16")

    @property
    def kws_encoder(self) -> Path:
        return self.kws_directory / "encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx"

    @property
    def kws_decoder(self) -> Path:
        return self.kws_directory / "decoder-epoch-13-avg-2-chunk-8-left-64.onnx"

    @property
    def kws_joiner(self) -> Path:
        return self.kws_directory / "joiner-epoch-13-avg-2-chunk-8-left-64.int8.onnx"

    @property
    def kws_tokens(self) -> Path:
        return self.kws_directory / "tokens.txt"

    @property
    def kws_keywords(self) -> Path:
        return self.kws_directory.parent / "keywords.txt"

    @classmethod
    def from_environment(cls, project_root: Path) -> VoiceSettings:
        root = project_root.resolve()
        voice_models = root / "runtime" / "models" / "voice"
        cpu_default = min(4, max(1, (os.cpu_count() or 4) // 2))
        microphone = os.getenv("RON_MICROPHONE", "").strip() or None
        return cls(
            enabled=_boolean("RON_VOICE_ENABLED", True),
            project_root=root,
            wake_phrase=os.getenv("RON_WAKE_PHRASE", "Hey Ron"),
            wake_aliases=_phrases("RON_WAKE_ALIASES", "hey ron|hey run", maximum=8),
            microphone_device=microphone,
            ring_buffer_seconds=_number("RON_VOICE_RING_SECONDS", 2.0, 0.5, 5.0),
            audio_queue_frames=_integer("RON_VOICE_QUEUE_FRAMES", 64, 8, 256),
            wake_threshold=_number("RON_WAKE_THRESHOLD", 0.35, 0.05, 0.99),
            wake_score=_number("RON_WAKE_SCORE", 1.5, 0.1, 5.0),
            wake_cooldown_seconds=_number("RON_WAKE_COOLDOWN", 2.0, 0.5, 10.0),
            vad_threshold=_number("RON_VAD_THRESHOLD", 0.5, 0.1, 0.95),
            end_silence_seconds=_number("RON_VOICE_END_SILENCE", 0.55, 0.2, 2.0),
            minimum_speech_seconds=_number("RON_VOICE_MIN_SPEECH", 0.20, 0.05, 2.0),
            maximum_speech_seconds=_number("RON_VOICE_MAX_SPEECH", 15.0, 2.0, 60.0),
            command_start_timeout_seconds=_number(
                "RON_VOICE_COMMAND_WAIT", 2.5, 0.5, 10.0
            ),
            continuous_timeout_seconds=_number(
                "RON_VOICE_CHAT_TIMEOUT", 25.0, 5.0, 120.0
            ),
            minimum_transcript_confidence=_number(
                "RON_VOICE_MIN_CONFIDENCE", 0.18, 0.0, 0.95
            ),
            asr_model=os.getenv("RON_WHISPER_MODEL", "small.en").strip(),
            asr_compute_type=os.getenv("RON_WHISPER_COMPUTE_TYPE", "int8").strip(),
            asr_threads=_integer("RON_WHISPER_THREADS", cpu_default, 1, 16),
            asr_preload=_boolean("RON_WHISPER_PRELOAD", True),
            require_wake_in_transcript=_boolean("RON_WAKE_VERIFY_TRANSCRIPT", True),
            retry_min_seconds=_number("RON_VOICE_RETRY_MIN", 2.0, 0.5, 60.0),
            retry_max_seconds=_number("RON_VOICE_RETRY_MAX", 60.0, 2.0, 600.0),
            reminder_seconds=60.0
            * _number("RON_VOICE_REMINDER_MINUTES", 20.0, 5.0, 240.0),
            hotwords=_hotwords(),
            kws_directory=_path(
                "RON_KWS_MODEL_DIR",
                voice_models / "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20",
            ),
            vad_model=_path("RON_VAD_MODEL", voice_models / "silero_vad.onnx"),
            whisper_download_root=_path(
                "RON_WHISPER_DOWNLOAD_ROOT", voice_models / "whisper"
            ),
        )
