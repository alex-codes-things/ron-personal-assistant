"""Validated environment configuration for Ron's optional voice subsystem."""

from __future__ import annotations

import os
import re
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
            item.strip().casefold() for item in os.getenv(name, default).split("|") if item.strip()
        )
    )
    if not values or len(values) > maximum:
        raise VoiceSettingsError(f"{name} must contain between 1 and {maximum} phrases")
    if any(len(item) > 80 or any(char in item for char in "\r\n\0") for item in values):
        raise VoiceSettingsError(f"{name} contains an invalid phrase")
    return values


def _optional_phrases(name: str, default: str, *, maximum: int) -> tuple[str, ...]:
    values = tuple(
        dict.fromkeys(
            item.strip().casefold() for item in os.getenv(name, default).split("|") if item.strip()
        )
    )
    if len(values) > maximum:
        raise VoiceSettingsError(f"{name} allows at most {maximum} phrases")
    if any(len(item) > 80 or any(char in item for char in "\r\n\0") for item in values):
        raise VoiceSettingsError(f"{name} contains an invalid phrase")
    return values


def _spoken_phrases(name: str, default: str, *, maximum: int) -> tuple[str, ...]:
    values = tuple(
        dict.fromkeys(item.strip() for item in os.getenv(name, default).split("|") if item.strip())
    )
    if not values or len(values) > maximum:
        raise VoiceSettingsError(f"{name} must contain between 1 and {maximum} phrases")
    if any(len(item) > 80 or any(char in item for char in "\r\n\0") for item in values):
        raise VoiceSettingsError(f"{name} contains an invalid phrase")
    return values


def _hotwords() -> tuple[str, ...]:
    raw = os.getenv(
        "RON_VOICE_HOTWORDS",
        "Ron,Spotify,Notepad,Calculator,File Explorer,Visual Studio Code,VS Code,Brave,"
        "YouTube,Galway Girl,volume,brightness,Nexus 7",
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
    wake_aliases: tuple[str, ...] = ("hey ron", "hey run", "hey wrong", "hey ryan")
    wake_fuzzy_threshold: float = 0.84
    wake_kws_aliases: tuple[str, ...] = ("peron", "here on", "tehran", "aaron", "heyron")
    microphone_device: str | None = None
    sample_rate: int = 16_000
    audio_block_ms: int = 32
    ring_buffer_seconds: float = 2.0
    audio_queue_frames: int = 64
    wake_threshold: float = 0.35
    wake_score: float = 1.5
    wake_sensitivity: str = "high"
    wake_cooldown_seconds: float = 2.0
    vad_threshold: float = 0.5
    end_silence_seconds: float = 0.38
    minimum_speech_seconds: float = 0.20
    maximum_speech_seconds: float = 15.0
    command_start_timeout_seconds: float = 2.5
    wake_ack_enabled: bool = True
    wake_acknowledgements: tuple[str, ...] = ("Yes?", "I'm listening.", "Go ahead.")
    wake_followup_timeout_seconds: float = 8.0
    wake_ack_echo_guard_seconds: float = 0.06
    wake_fast_handoff: bool = True
    wake_fast_segment_seconds: float = 0.90
    wake_fast_post_seconds: float = 0.68
    interaction_mode: str = "strict"
    automatic_followup: bool = False
    automatic_followup_seconds: float = 6.0
    barge_in_enabled: bool = True
    accept_new_turn_during_reply: bool = True
    interrupt_phrases: tuple[str, ...] = (
        "stop",
        "wait",
        "hold on",
        "quiet",
        "cancel that",
        "never mind",
        "that's enough",
    )
    thinking_cue_enabled: bool = True
    action_cues_enabled: bool = True
    thinking_cue_delay_seconds: float = 0.9
    thinking_cues: tuple[str, ...] = ("On it.", "I'm checking that now.")
    continuous_timeout_seconds: float = 25.0
    minimum_transcript_confidence: float = 0.18
    asr_provider: str = "auto"
    asr_fallback_local: bool = True
    groq_api_key: str = field(default="", repr=False)
    groq_asr_model: str = "whisper-large-v3-turbo"
    groq_asr_retry_model: str = "whisper-large-v3"
    groq_asr_timeout_seconds: float = 15.0
    asr_model: str = "distil-large-v3"
    asr_compute_type: str = "int8"
    asr_threads: int = 4
    asr_beam_size: int = 1
    asr_retry_enabled: bool = True
    asr_retry_beam_size: int = 5
    asr_retry_confidence: float = 0.52
    asr_patience: float = 1.0
    asr_initial_prompt: str = (
        "Hey Ron. Open Spotify. Open Notepad. Open Calculator. Open File Explorer. "
        "Open Visual Studio Code. Set volume to twenty percent. Set brightness to fifty "
        "percent. Play Galway Girl on Spotify. What time is it?"
    )
    asr_preload: bool = True
    require_wake_in_transcript: bool = True
    retry_min_seconds: float = 2.0
    retry_max_seconds: float = 60.0
    reminder_seconds: float = 1_200.0
    hotwords: tuple[str, ...] = ()
    kws_directory: Path = field(default_factory=Path)
    vad_model: Path = field(default_factory=Path)
    whisper_download_root: Path = field(default_factory=Path)
    tts_enabled: bool = True
    tts_provider: str = "auto"
    tts_fallback_local: bool = True
    groq_tts_model: str = "canopylabs/orpheus-v1-english"
    groq_tts_voice: str = "daniel"
    groq_tts_timeout_seconds: float = 15.0
    groq_tts_max_requests_per_turn: int = 4
    groq_tts_streaming: bool = True
    tts_fast_fallback: bool = True
    tts_voice: str = "bm_george"
    tts_speed: float = 0.94
    tts_language: str = "en-gb"
    tts_gain: float = 1.0
    tts_max_characters: int = 700
    tts_level_interval_ms: int = 40
    tts_chunk_characters: int = 180
    tts_prefetch_chunks: bool = True
    tts_streaming: bool = True
    tts_concurrent_synthesis: bool = True
    tts_cpu_threads: int = 2
    tts_echo_guard_seconds: float = 0.12
    tts_output_device: str | None = None
    tts_model: Path = field(default_factory=Path)
    tts_voices: Path = field(default_factory=Path)

    def __post_init__(self) -> None:
        root = self.project_root.resolve()
        object.__setattr__(self, "project_root", root)
        wake = " ".join(self.wake_phrase.strip().casefold().split())
        if not wake or len(wake) > 40:
            raise VoiceSettingsError("RON_WAKE_PHRASE is invalid")
        object.__setattr__(self, "wake_phrase", wake)
        asr_provider = self.asr_provider.strip().casefold()
        if asr_provider not in {"auto", "groq", "local"}:
            raise VoiceSettingsError("RON_ASR_PROVIDER must be auto, groq, or local")
        object.__setattr__(self, "asr_provider", asr_provider)
        tts_provider = self.tts_provider.strip().casefold()
        if tts_provider not in {"auto", "groq", "local"}:
            raise VoiceSettingsError("RON_TTS_PROVIDER must be auto, groq, or local")
        object.__setattr__(self, "tts_provider", tts_provider)
        interaction_mode = self.interaction_mode.strip().casefold()
        if interaction_mode not in {"strict", "followup", "continuous"}:
            raise VoiceSettingsError(
                "RON_INTERACTION_MODE must be strict, followup, or continuous"
            )
        object.__setattr__(self, "interaction_mode", interaction_mode)
        api_key = self.groq_api_key.strip()
        if api_key and (len(api_key) < 20 or any(char.isspace() for char in api_key)):
            raise VoiceSettingsError("GROQ_API_KEY is invalid")
        if (asr_provider == "groq" or tts_provider == "groq") and not api_key:
            raise VoiceSettingsError(
                "GROQ_API_KEY is required when Groq voice is selected"
            )
        object.__setattr__(self, "groq_api_key", api_key)
        model_pattern = r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}"
        if not re.fullmatch(model_pattern, self.groq_asr_model.strip()):
            raise VoiceSettingsError("RON_GROQ_ASR_MODEL is invalid")
        if not re.fullmatch(model_pattern, self.groq_asr_retry_model.strip()):
            raise VoiceSettingsError("RON_GROQ_ASR_RETRY_MODEL is invalid")
        if not re.fullmatch(model_pattern, self.groq_tts_model.strip()):
            raise VoiceSettingsError("RON_GROQ_TTS_MODEL is invalid")
        object.__setattr__(self, "groq_asr_model", self.groq_asr_model.strip())
        object.__setattr__(self, "groq_asr_retry_model", self.groq_asr_retry_model.strip())
        object.__setattr__(self, "groq_tts_model", self.groq_tts_model.strip())
        groq_voice = self.groq_tts_voice.strip().casefold()
        if groq_voice not in {"autumn", "diana", "hannah", "austin", "daniel", "troy"}:
            raise VoiceSettingsError(
                "RON_GROQ_TTS_VOICE must be autumn, diana, hannah, austin, daniel, or troy"
            )
        object.__setattr__(self, "groq_tts_voice", groq_voice)
        if not 1.0 <= self.groq_asr_timeout_seconds <= 60.0:
            raise VoiceSettingsError("RON_GROQ_ASR_TIMEOUT must be between 1 and 60")
        if not 1.0 <= self.groq_tts_timeout_seconds <= 60.0:
            raise VoiceSettingsError("RON_GROQ_TTS_TIMEOUT must be between 1 and 60")
        if not 1 <= self.groq_tts_max_requests_per_turn <= 6:
            raise VoiceSettingsError(
                "RON_GROQ_TTS_MAX_REQUESTS_PER_TURN must be between 1 and 6"
            )
        if self.sample_rate not in {8_000, 16_000}:
            raise VoiceSettingsError("Voice sample rate must be 8000 or 16000")
        if not 10 <= self.audio_block_ms <= 100:
            raise VoiceSettingsError("RON_AUDIO_BLOCK_MS must be between 10 and 100")
        if not self.asr_model.strip() or len(self.asr_model) > 120:
            raise VoiceSettingsError("RON_WHISPER_MODEL is invalid")
        if self.asr_compute_type not in {"int8", "int8_float16", "float16", "float32"}:
            raise VoiceSettingsError("RON_WHISPER_COMPUTE_TYPE is invalid")
        if not 1 <= self.asr_threads <= 16:
            raise VoiceSettingsError("RON_WHISPER_THREADS must be between 1 and 16")
        if not 1 <= self.asr_beam_size <= 10:
            raise VoiceSettingsError("RON_WHISPER_FAST_BEAM_SIZE must be between 1 and 10")
        if not 1 <= self.asr_retry_beam_size <= 10:
            raise VoiceSettingsError("RON_WHISPER_RETRY_BEAM_SIZE must be between 1 and 10")
        if self.asr_retry_beam_size < self.asr_beam_size:
            raise VoiceSettingsError(
                "RON_WHISPER_RETRY_BEAM_SIZE cannot be lower than the fast beam size"
            )
        if not 0.0 <= self.asr_retry_confidence <= 0.95:
            raise VoiceSettingsError("RON_WHISPER_RETRY_CONFIDENCE must be between 0 and 0.95")
        if not 0.5 <= self.asr_patience <= 2.0:
            raise VoiceSettingsError("RON_WHISPER_PATIENCE must be between 0.5 and 2.0")
        if len(self.asr_initial_prompt) > 1_500 or any(
            char in self.asr_initial_prompt for char in "\r\n\0"
        ):
            raise VoiceSettingsError("RON_WHISPER_INITIAL_PROMPT is invalid")
        if self.wake_sensitivity not in {"high", "balanced", "strict"}:
            raise VoiceSettingsError("RON_WAKE_SENSITIVITY must be high, balanced, or strict")
        if not 0.55 <= self.wake_fuzzy_threshold <= 0.95:
            raise VoiceSettingsError("RON_WAKE_FUZZY_THRESHOLD must be between 0.55 and 0.95")
        if not 1.0 <= self.wake_followup_timeout_seconds <= 20.0:
            raise VoiceSettingsError("RON_WAKE_FOLLOWUP_WAIT must be between 1 and 20")
        if not 0.0 <= self.wake_ack_echo_guard_seconds <= 0.5:
            raise VoiceSettingsError("RON_WAKE_ACK_ECHO_GUARD must be between 0 and 0.5")
        if not 0.55 <= self.wake_fast_segment_seconds <= 1.5:
            raise VoiceSettingsError("RON_WAKE_FAST_SEGMENT_SECONDS must be between 0.55 and 1.5")
        if not 0.2 <= self.wake_fast_post_seconds <= 1.2:
            raise VoiceSettingsError("RON_WAKE_FAST_POST_SECONDS must be between 0.2 and 1.2")
        if not 2.0 <= self.automatic_followup_seconds <= 20.0:
            raise VoiceSettingsError("RON_VOICE_AUTO_FOLLOWUP_WAIT must be between 2 and 20")
        if not self.interrupt_phrases or len(self.interrupt_phrases) > 16:
            raise VoiceSettingsError(
                "RON_VOICE_INTERRUPT_PHRASES must contain between 1 and 16 phrases"
            )
        if not 0.8 <= self.thinking_cue_delay_seconds <= 5.0:
            raise VoiceSettingsError("RON_VOICE_THINKING_CUE_DELAY must be between 0.8 and 5")
        if not self.thinking_cues or len(self.thinking_cues) > 6:
            raise VoiceSettingsError("RON_VOICE_THINKING_CUES must contain between 1 and 6 phrases")
        if not self.wake_acknowledgements or len(self.wake_acknowledgements) > 8:
            raise VoiceSettingsError("RON_WAKE_ACK_PHRASES must contain between 1 and 8 phrases")
        if any(
            not item.strip() or len(item) > 80 or any(char in item for char in "\r\n\0")
            for item in self.wake_acknowledgements
        ):
            raise VoiceSettingsError("RON_WAKE_ACK_PHRASES contains an invalid phrase")
        if not self.tts_voice.strip() or len(self.tts_voice) > 80:
            raise VoiceSettingsError("RON_TTS_VOICE is invalid")
        if self.tts_language not in {"en-gb", "en-us"}:
            raise VoiceSettingsError("RON_TTS_LANGUAGE must be en-gb or en-us")
        if not 0.6 <= self.tts_speed <= 1.5:
            raise VoiceSettingsError("RON_TTS_SPEED must be between 0.6 and 1.5")
        if not 0.1 <= self.tts_gain <= 2.0:
            raise VoiceSettingsError("RON_TTS_GAIN must be between 0.1 and 2.0")
        if not 120 <= self.tts_max_characters <= 4_000:
            raise VoiceSettingsError("RON_TTS_MAX_CHARACTERS must be between 120 and 4000")
        if not 20 <= self.tts_level_interval_ms <= 100:
            raise VoiceSettingsError("RON_TTS_LEVEL_MS must be between 20 and 100")
        if not 80 <= self.tts_chunk_characters <= 500:
            raise VoiceSettingsError("RON_TTS_CHUNK_CHARACTERS must be between 80 and 500")
        if not 0.0 <= self.tts_echo_guard_seconds <= 1.5:
            raise VoiceSettingsError("RON_TTS_ECHO_GUARD must be between 0 and 1.5")
        if not 1 <= self.tts_cpu_threads <= 8:
            raise VoiceSettingsError("RON_TTS_CPU_THREADS must be between 1 and 8")

    @property
    def kws_encoder(self) -> Path:
        return self.kws_directory / "encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx"

    @property
    def effective_asr_provider(self) -> str:
        """Resolve auto without contacting a provider or exposing the secret key."""
        if self.asr_provider == "auto":
            return "groq" if self.groq_api_key else "local"
        return self.asr_provider

    @property
    def effective_tts_provider(self) -> str:
        """Resolve auto without loading either speech engine."""
        if self.tts_provider == "auto":
            return "groq" if self.groq_api_key else "local"
        return self.tts_provider

    @property
    def followup_enabled(self) -> bool:
        """Return whether completed replies may accept a wake-free next turn."""
        return self.interaction_mode == "followup"

    @property
    def continuous_enabled(self) -> bool:
        """Return whether voice starts in explicit continuous-listening mode."""
        return self.interaction_mode == "continuous"

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
        cpu_count = os.cpu_count() or 4
        cpu_default = min(4, max(1, cpu_count // 2))
        tts_cpu_default = 1 if cpu_count <= 4 else 2
        microphone = os.getenv("RON_MICROPHONE", "").strip() or None
        speaker = os.getenv("RON_TTS_OUTPUT_DEVICE", "").strip() or None
        tts_models = voice_models / "tts"
        # v0.8 used RON_WHISPER_BEAM_SIZE=5 for every utterance. Keep that
        # value as the deliberate accuracy retry while the new first pass uses
        # a one-beam decode. Existing .env files therefore gain the fast path
        # without silently losing their accuracy preference.
        legacy_beam = _integer("RON_WHISPER_BEAM_SIZE", 5, 1, 10)
        legacy_automatic_followup = _boolean("RON_VOICE_AUTO_FOLLOWUP", False)
        interaction_default = "followup" if legacy_automatic_followup else "strict"
        return cls(
            enabled=_boolean("RON_VOICE_ENABLED", True),
            project_root=root,
            wake_phrase=os.getenv("RON_WAKE_PHRASE", "Hey Ron"),
            wake_aliases=_phrases(
                "RON_WAKE_ALIASES",
                "hey ron|hey run|hey wrong|hey ryan",
                maximum=12,
            ),
            wake_fuzzy_threshold=_number("RON_WAKE_FUZZY_THRESHOLD", 0.84, 0.55, 0.95),
            wake_kws_aliases=_optional_phrases(
                "RON_WAKE_KWS_ALIASES",
                "peron|here on|tehran|aaron|heyron",
                maximum=12,
            ),
            microphone_device=microphone,
            audio_block_ms=_integer("RON_AUDIO_BLOCK_MS", 32, 10, 100),
            ring_buffer_seconds=_number("RON_VOICE_RING_SECONDS", 2.0, 0.5, 5.0),
            audio_queue_frames=_integer("RON_VOICE_QUEUE_FRAMES", 64, 8, 256),
            wake_threshold=_number("RON_WAKE_THRESHOLD", 0.35, 0.05, 0.99),
            wake_score=_number("RON_WAKE_SCORE", 1.5, 0.1, 5.0),
            wake_sensitivity=os.getenv("RON_WAKE_SENSITIVITY", "high").strip().casefold(),
            wake_cooldown_seconds=_number("RON_WAKE_COOLDOWN", 2.0, 0.5, 10.0),
            vad_threshold=_number("RON_VAD_THRESHOLD", 0.5, 0.1, 0.95),
            # A new variable intentionally avoids carrying the old 550 ms
            # endpoint into upgraded installations that already have a .env.
            end_silence_seconds=_number("RON_VOICE_RESPONSIVE_END_SILENCE", 0.38, 0.2, 2.0),
            minimum_speech_seconds=_number("RON_VOICE_MIN_SPEECH", 0.20, 0.05, 2.0),
            maximum_speech_seconds=_number("RON_VOICE_MAX_SPEECH", 15.0, 2.0, 60.0),
            command_start_timeout_seconds=_number("RON_VOICE_COMMAND_WAIT", 2.5, 0.5, 10.0),
            wake_ack_enabled=_boolean("RON_WAKE_ACK_ENABLED", True),
            wake_acknowledgements=_spoken_phrases(
                "RON_WAKE_ACK_PHRASES",
                "Yes?|I'm listening.|Go ahead.",
                maximum=8,
            ),
            wake_followup_timeout_seconds=_number("RON_WAKE_FOLLOWUP_WAIT", 8.0, 1.0, 20.0),
            wake_ack_echo_guard_seconds=_number("RON_WAKE_ACK_ECHO_GUARD", 0.06, 0.0, 0.5),
            wake_fast_handoff=_boolean("RON_WAKE_FAST_HANDOFF", True),
            wake_fast_segment_seconds=_number("RON_WAKE_FAST_SEGMENT_SECONDS", 0.90, 0.55, 1.5),
            wake_fast_post_seconds=_number("RON_WAKE_FAST_POST_SECONDS", 0.68, 0.2, 1.2),
            interaction_mode=os.getenv("RON_INTERACTION_MODE", interaction_default),
            automatic_followup=legacy_automatic_followup,
            automatic_followup_seconds=_number("RON_VOICE_AUTO_FOLLOWUP_WAIT", 6.0, 2.0, 20.0),
            barge_in_enabled=_boolean("RON_VOICE_BARGE_IN", True),
            accept_new_turn_during_reply=_boolean(
                "RON_VOICE_ACCEPT_NEW_TURN", True
            ),
            interrupt_phrases=_phrases(
                "RON_VOICE_INTERRUPT_PHRASES",
                "stop|wait|hold on|quiet|cancel that|never mind|that's enough",
                maximum=16,
            ),
            thinking_cue_enabled=_boolean("RON_VOICE_THINKING_CUE", True),
            action_cues_enabled=_boolean("RON_VOICE_ACTION_CUES", True),
            thinking_cue_delay_seconds=_number("RON_VOICE_THINKING_CUE_DELAY", 0.9, 0.8, 5.0),
            thinking_cues=_spoken_phrases(
                "RON_VOICE_THINKING_CUES",
                "On it.|I'm checking that now.",
                maximum=6,
            ),
            continuous_timeout_seconds=_number("RON_VOICE_CHAT_TIMEOUT", 25.0, 5.0, 120.0),
            minimum_transcript_confidence=_number("RON_VOICE_MIN_CONFIDENCE", 0.18, 0.0, 0.95),
            asr_provider=os.getenv("RON_ASR_PROVIDER", "auto"),
            asr_fallback_local=_boolean("RON_ASR_FALLBACK_LOCAL", True),
            groq_api_key=os.getenv("GROQ_API_KEY", ""),
            groq_asr_model=os.getenv(
                "RON_GROQ_ASR_MODEL", "whisper-large-v3-turbo"
            ),
            groq_asr_retry_model=os.getenv(
                "RON_GROQ_ASR_RETRY_MODEL", "whisper-large-v3"
            ),
            groq_asr_timeout_seconds=_number(
                "RON_GROQ_ASR_TIMEOUT", 15.0, 1.0, 60.0
            ),
            asr_model=os.getenv("RON_WHISPER_MODEL", "distil-large-v3").strip(),
            asr_compute_type=os.getenv("RON_WHISPER_COMPUTE_TYPE", "int8").strip(),
            asr_threads=_integer("RON_WHISPER_THREADS", cpu_default, 1, 16),
            asr_beam_size=_integer("RON_WHISPER_FAST_BEAM_SIZE", 1, 1, 10),
            asr_retry_enabled=_boolean("RON_WHISPER_RETRY_ENABLED", True),
            asr_retry_beam_size=_integer("RON_WHISPER_RETRY_BEAM_SIZE", legacy_beam, 1, 10),
            asr_retry_confidence=_number("RON_WHISPER_RETRY_CONFIDENCE", 0.52, 0.0, 0.95),
            asr_patience=_number("RON_WHISPER_PATIENCE", 1.0, 0.5, 2.0),
            asr_initial_prompt=os.getenv(
                "RON_WHISPER_INITIAL_PROMPT",
                "Hey Ron. Open Spotify. Open Notepad. Open Calculator. Open File Explorer. "
                "Open Visual Studio Code. Set volume to twenty percent. Set brightness to "
                "fifty percent. Play Galway Girl on Spotify. What time is it?",
            ).strip(),
            asr_preload=_boolean("RON_WHISPER_PRELOAD", True),
            require_wake_in_transcript=_boolean("RON_WAKE_VERIFY_TRANSCRIPT", True),
            retry_min_seconds=_number("RON_VOICE_RETRY_MIN", 2.0, 0.5, 60.0),
            retry_max_seconds=_number("RON_VOICE_RETRY_MAX", 60.0, 2.0, 600.0),
            reminder_seconds=60.0 * _number("RON_VOICE_REMINDER_MINUTES", 20.0, 5.0, 240.0),
            hotwords=_hotwords(),
            kws_directory=_path(
                "RON_KWS_MODEL_DIR",
                voice_models / "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20",
            ),
            vad_model=_path("RON_VAD_MODEL", voice_models / "silero_vad.onnx"),
            whisper_download_root=_path("RON_WHISPER_DOWNLOAD_ROOT", voice_models / "whisper"),
            tts_enabled=_boolean("RON_TTS_ENABLED", True),
            tts_provider=os.getenv("RON_TTS_PROVIDER", "auto"),
            tts_fallback_local=_boolean("RON_TTS_FALLBACK_LOCAL", True),
            groq_tts_model=os.getenv(
                "RON_GROQ_TTS_MODEL", "canopylabs/orpheus-v1-english"
            ),
            groq_tts_voice=os.getenv("RON_GROQ_TTS_VOICE", "daniel"),
            groq_tts_timeout_seconds=_number(
                "RON_GROQ_TTS_TIMEOUT", 15.0, 1.0, 60.0
            ),
            groq_tts_max_requests_per_turn=_integer(
                "RON_GROQ_TTS_MAX_REQUESTS_PER_TURN", 4, 1, 6
            ),
            groq_tts_streaming=_boolean("RON_GROQ_TTS_STREAMING", True),
            tts_fast_fallback=_boolean("RON_TTS_FAST_FALLBACK", True),
            tts_voice=os.getenv("RON_TTS_VOICE", "bm_george").strip(),
            tts_speed=_number("RON_TTS_SPEED", 0.94, 0.6, 1.5),
            tts_language=os.getenv("RON_TTS_LANGUAGE", "en-gb").strip().casefold(),
            tts_gain=_number("RON_TTS_GAIN", 1.0, 0.1, 2.0),
            tts_max_characters=_integer("RON_TTS_MAX_CHARACTERS", 700, 120, 4_000),
            tts_level_interval_ms=_integer("RON_TTS_LEVEL_MS", 40, 20, 100),
            tts_chunk_characters=_integer("RON_TTS_CHUNK_CHARACTERS", 180, 80, 500),
            tts_prefetch_chunks=_boolean("RON_TTS_PREFETCH_CHUNKS", True),
            tts_streaming=_boolean("RON_TTS_STREAMING", True),
            tts_concurrent_synthesis=_boolean("RON_TTS_CONCURRENT_SYNTHESIS", True),
            tts_cpu_threads=_integer("RON_TTS_CPU_THREADS", tts_cpu_default, 1, 8),
            tts_echo_guard_seconds=_number("RON_TTS_ECHO_GUARD", 0.12, 0.0, 1.5),
            tts_output_device=speaker,
            tts_model=_path("RON_TTS_MODEL", tts_models / "kokoro-v1.0.int8.onnx"),
            tts_voices=_path("RON_TTS_VOICES", tts_models / "voices-v1.0.bin"),
        )
