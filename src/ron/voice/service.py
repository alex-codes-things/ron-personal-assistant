"""Failure-isolated voice state machine feeding Ron's existing assistant."""

from __future__ import annotations

import logging
import threading
import time
from array import array
from collections.abc import Callable
from typing import TypeVar

from ron.core import Coordinator, EventType, FaceExpression, RonEvent
from ron.voice.audio import (
    MicrophoneError,
    MicrophoneStream,
    SampleRingBuffer,
    VoiceDependencyError,
)
from ron.voice.diagnostics import VoiceDiagnostics
from ron.voice.models import VoiceInput, VoiceReply, VoiceState
from ron.voice.normalizer import VoiceNormalizer
from ron.voice.settings import VoiceSettings
from ron.voice.transcriber import FasterWhisperTranscriber, TranscriptionError
from ron.voice.vad import SileroEndpointDetector, VadModelError
from ron.voice.wake_word import SherpaWakeWordDetector, WakeWordModelError

T = TypeVar("T")
PromptHandler = Callable[[VoiceInput], VoiceReply]
NoticeHandler = Callable[[str], None]
ContinuousGetter = Callable[[], bool]
OperationRunner = Callable[[Callable[[], T]], T]
Factory = Callable[[VoiceSettings], object]


class VoiceService:
    """Listen locally without ever making terminal input or the face mandatory."""

    def __init__(
        self,
        coordinator: Coordinator,
        settings: VoiceSettings,
        prompt_handler: PromptHandler,
        *,
        notice_handler: NoticeHandler | None = None,
        continuous_getter: ContinuousGetter | None = None,
        continuous_timeout_handler: Callable[[], None] | None = None,
        transcription_runner: OperationRunner | None = None,
        warm_runner: OperationRunner | None = None,
        audio_factory: Factory = lambda settings: MicrophoneStream(
            target_sample_rate=settings.sample_rate,
            device=settings.microphone_device,
            queue_frames=settings.audio_queue_frames,
        ),
        wake_factory: Factory = SherpaWakeWordDetector,
        vad_factory: Factory = SileroEndpointDetector,
        transcriber_factory: Factory = FasterWhisperTranscriber,
    ) -> None:
        self.coordinator = coordinator
        self.settings = settings
        self.prompt_handler = prompt_handler
        self.notice_handler = notice_handler or (lambda message: None)
        self.continuous_getter = continuous_getter or (lambda: False)
        self.continuous_timeout_handler = continuous_timeout_handler or (lambda: None)
        self.transcription_runner = transcription_runner or (lambda operation: operation())
        self.warm_runner = warm_runner or (lambda operation: operation())
        self.audio_factory = audio_factory
        self.wake_factory = wake_factory
        self.vad_factory = vad_factory
        self.transcriber_factory = transcriber_factory
        self.normalizer = VoiceNormalizer(settings)
        self.diagnostics = VoiceDiagnostics()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._audio: object | None = None
        self._logger = logging.getLogger(__name__)
        self._notice_times: dict[str, float] = {}
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            if not self.settings.enabled:
                self.diagnostics.set_state(VoiceState.DISABLED)
                return
            self._thread = threading.Thread(
                target=self._run,
                name="ron-voice",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        audio = self._audio
        if audio is not None:
            try:
                audio.stop()
            except Exception:
                self._logger.debug("Voice microphone stop failed", exc_info=True)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10.0)
        with self._lock:
            self._thread = None
            self._audio = None
        self.diagnostics.set_state(VoiceState.STOPPED)

    def status_label(self) -> str:
        return self.diagnostics.status_label()

    def _run(self) -> None:
        backoff = self.settings.retry_min_seconds
        while not self._stop.is_set():
            try:
                self._run_once()
                return
            except (VoiceDependencyError, WakeWordModelError, VadModelError) as error:
                message = str(error)
                self.diagnostics.set_state(VoiceState.OFFLINE, message)
                self._notify(
                    "permanent",
                    f"[VOICE OFFLINE] {message} Terminal chat is still fully working.",
                    force=True,
                )
                return
            except TranscriptionError as error:
                message = str(error)
                self.diagnostics.set_state(VoiceState.OFFLINE, message)
                self._notify(
                    "transcriber",
                    f"[VOICE OFFLINE] {message} Terminal chat is still fully working.",
                    force=True,
                )
                return
            except (MicrophoneError, OSError, RuntimeError) as error:
                if self._stop.is_set():
                    return
                message = str(error) or type(error).__name__
                self.diagnostics.set_state(VoiceState.RETRYING, message)
                self.diagnostics.record_restart()
                self._notify(
                    "microphone",
                    f"[VOICE OFFLINE] {message} I'll retry quietly; terminal chat still works.",
                )
                self._publish_expression(FaceExpression.IDLE)
                if self._stop.wait(backoff):
                    return
                backoff = min(self.settings.retry_max_seconds, backoff * 2)
            except Exception as error:
                if self._stop.is_set():
                    return
                self._logger.exception("Unexpected voice failure; restarting safely")
                message = f"Unexpected {type(error).__name__}"
                self.diagnostics.set_state(VoiceState.RETRYING, message)
                self.diagnostics.record_restart()
                self._notify(
                    "unexpected",
                    "[VOICE OFFLINE] Voice input hit an unexpected local error. "
                    "I'll retry quietly; terminal chat still works.",
                )
                if self._stop.wait(backoff):
                    return
                backoff = min(self.settings.retry_max_seconds, backoff * 2)

    def _run_once(self) -> None:
        self.diagnostics.set_state(VoiceState.STARTING)
        wake = self.wake_factory(self.settings)
        vad = self.vad_factory(self.settings)
        transcriber = self.transcriber_factory(self.settings)
        wake.load()
        vad.load()
        if self.settings.asr_preload:
            self.warm_runner(transcriber.warm)
        else:
            transcriber.load()

        audio = self.audio_factory(self.settings)
        self._audio = audio
        try:
            audio.start()
            self.diagnostics.set_device(audio.device_label)
            self.diagnostics.set_state(VoiceState.READY)
            self._notify(
                "ready",
                f"[VOICE READY] Listening for \"{self.settings.wake_phrase.title()}\" "
                f"on {audio.device_label}.",
                force=True,
            )
            self._publish_expression(FaceExpression.IDLE)
            self._listen(audio, wake, vad, transcriber)
        finally:
            try:
                audio.stop()
            finally:
                self._audio = None

    def _listen(self, audio: object, wake: object, vad: object, transcriber: object) -> None:
        wake_pending = False
        wake_capture: array[float] | None = None
        awaiting_command_until = 0.0
        continuous_until = 0.0
        cooldown_until = 0.0
        previous_continuous = False
        ring = SampleRingBuffer(
            int(self.settings.sample_rate * self.settings.ring_buffer_seconds)
        )

        while not self._stop.is_set():
            now = time.monotonic()
            continuous = bool(self.continuous_getter())
            if continuous and not previous_continuous:
                continuous_until = now + self.settings.continuous_timeout_seconds
                self.diagnostics.set_state(VoiceState.LISTENING)
                self._publish_expression(FaceExpression.LISTENING)
            if not continuous:
                continuous_until = 0.0
            previous_continuous = continuous

            if continuous and continuous_until and now >= continuous_until:
                self.continuous_timeout_handler()
                continuous_until = 0.0
                previous_continuous = False
                self._notify(
                    "chat-timeout",
                    "[VOICE] Continuous chat timed out. Say \"Hey Ron\" when you need me.",
                    force=True,
                )
                self.diagnostics.set_state(VoiceState.READY)
                self._publish_expression(FaceExpression.IDLE)

            if awaiting_command_until and now >= awaiting_command_until:
                awaiting_command_until = 0.0
                wake_pending = False
                self.diagnostics.set_state(VoiceState.READY)
                self._publish_expression(FaceExpression.IDLE)

            samples = audio.read(timeout=0.25)
            self.diagnostics.update_overflows(audio.overflow_count)
            if samples is None:
                continue

            ring.append(samples)
            if wake_pending and wake_capture is not None:
                wake_capture.extend(samples)
                maximum_capture = int(
                    self.settings.sample_rate * (self.settings.maximum_speech_seconds + 1.5)
                )
                if len(wake_capture) > maximum_capture:
                    wake_capture = array("f", wake_capture[-maximum_capture:])

            detected = False
            if now >= cooldown_until:
                detected = bool(wake.feed(samples))
            segments = vad.feed(samples)

            # KWS is the wake gate. Requiring VAD to agree in this exact audio
            # block can discard short phrases, especially on quiet microphones.
            # VAD still endpoints the utterance and Whisper must independently
            # verify "Hey Ron" before any command is allowed to execute.
            if detected:
                wake_pending = True
                wake_capture = ring.snapshot(
                    int(self.settings.sample_rate * min(1.25, self.settings.ring_buffer_seconds))
                )
                awaiting_command_until = 0.0
                self.diagnostics.set_state(VoiceState.LISTENING)
                self._notify(
                    "wake-detected",
                    "[VOICE] Hey Ron detected. Listening...",
                    force=True,
                )
                self._publish_expression(FaceExpression.LISTENING)

            for segment in segments:
                now = time.monotonic()
                continuous_active = bool(self.continuous_getter()) and (
                    continuous_until == 0.0 or now < continuous_until
                )
                awaiting = awaiting_command_until > now
                if not wake_pending and not awaiting and not continuous_active:
                    continue

                require_wake = wake_pending
                wake_pending = False
                captured = wake_capture
                wake_capture = None
                outcome = self._process_segment(
                    captured if require_wake and captured else array("f", segment),
                    transcriber,
                    require_wake=require_wake,
                    wake_detected=require_wake,
                )
                if outcome == "rejected":
                    cooldown_until = now + self.settings.wake_cooldown_seconds
                    awaiting_command_until = 0.0
                elif outcome in {"waiting", "clarification", "unclear"}:
                    awaiting_command_until = (
                        now + self.settings.command_start_timeout_seconds
                    )
                elif outcome == "continuous":
                    continuous_until = now + self.settings.continuous_timeout_seconds
                    awaiting_command_until = 0.0
                else:
                    cooldown_until = now + self.settings.wake_cooldown_seconds
                    awaiting_command_until = 0.0

    def _process_segment(
        self,
        samples: array[float],
        transcriber: object,
        *,
        require_wake: bool,
        wake_detected: bool,
    ) -> str:
        self.diagnostics.set_state(VoiceState.TRANSCRIBING)
        self._publish_expression(FaceExpression.THINKING)
        try:
            transcript = self.transcription_runner(lambda: transcriber.transcribe(samples))
        except TranscriptionError as error:
            self._notify(
                "transcription-error",
                f"[VOICE UNCLEAR] {error} Nothing was executed.",
                force=True,
            )
            self._publish_expression(FaceExpression.CONFUSED)
            return "unclear"

        normalized = self.normalizer.normalize(
            transcript.text,
            require_wake=require_wake,
            wake_detected=wake_detected,
        )
        if not normalized.accepted:
            if require_wake:
                self.diagnostics.record_rejection()
            self.diagnostics.set_state(VoiceState.READY)
            self._publish_expression(FaceExpression.IDLE)
            return "rejected"
        if normalized.waiting_for_command:
            self.diagnostics.set_state(VoiceState.LISTENING)
            self._publish_expression(FaceExpression.LISTENING)
            return "waiting"
        if (
            transcript.confidence < self.settings.minimum_transcript_confidence
            or transcript.no_speech_probability >= 0.90
        ):
            self._notify(
                "unclear",
                "[VOICE UNCLEAR] I wasn't confident enough to act. Please repeat that.",
                force=True,
            )
            self._publish_expression(FaceExpression.CONFUSED)
            return "unclear"
        if normalized.clarification is not None:
            self._notify("clarification", f"[VOICE] {normalized.clarification}", force=True)
            self._publish_expression(FaceExpression.CONFUSED)
            return "clarification"

        voice_input = VoiceInput(
            raw_text=normalized.raw_text,
            text=normalized.text,
            confidence=transcript.confidence,
            wake_phrase=normalized.wake_phrase,
        )
        self.diagnostics.set_state(VoiceState.PROCESSING)
        try:
            reply = self.prompt_handler(voice_input)
        except Exception as error:
            self._logger.exception("Voice prompt handler failed")
            self._notify(
                "handler",
                f"[VOICE ERROR] Ron couldn't process that safely ({type(error).__name__}). "
                "Nothing else was executed.",
                force=True,
            )
            self._publish_expression(FaceExpression.ERROR)
            return "handled"

        self.diagnostics.record_command(transcript.duration_seconds)
        if reply.continue_listening:
            self.diagnostics.set_state(VoiceState.LISTENING)
            self._publish_expression(FaceExpression.LISTENING)
            return "continuous"
        self.diagnostics.set_state(VoiceState.READY)
        self._publish_expression(FaceExpression.IDLE)
        return "handled"

    def _notify(self, key: str, message: str, *, force: bool = False) -> None:
        now = time.monotonic()
        previous = self._notice_times.get(key)
        if not force and previous is not None and now - previous < self.settings.reminder_seconds:
            return
        self._notice_times[key] = now
        try:
            self.notice_handler(message)
        except Exception:
            self._logger.exception("Voice notice listener failed")

    def _publish_expression(self, expression: FaceExpression) -> None:
        self.coordinator.publish(
            RonEvent(EventType.FACE_EXPRESSION, {"expression": expression.value})
        )
