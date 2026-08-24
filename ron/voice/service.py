"""Failure-isolated voice state machine feeding Ron's existing assistant."""

from __future__ import annotations

import logging
import threading
import time
from array import array
from collections.abc import Callable

from ron.core import Coordinator, EventType, FaceExpression, RonEvent
from ron.voice.audio import (
    MicrophoneError,
    MicrophoneStream,
    SampleRingBuffer,
    VoiceDependencyError,
)
from ron.voice.diagnostics import VoiceDiagnostics
from ron.voice.models import (
    NormalizationResult,
    TranscriptionResult,
    VoiceInput,
    VoiceReply,
    VoiceState,
)
from ron.voice.normalizer import VoiceNormalizer
from ron.voice.settings import VoiceSettings
from ron.voice.transcriber import TranscriptionError, build_transcriber
from ron.voice.vad import SileroEndpointDetector, VadModelError
from ron.voice.wake_word import SherpaWakeWordDetector, WakeWordModelError

type PromptHandler = Callable[[VoiceInput], VoiceReply]
type NoticeHandler = Callable[[str], None]
type WakeAcknowledgementHandler = Callable[[str], bool]
type InterruptHandler = Callable[[str], bool]
type ContinuousGetter = Callable[[], bool]
type OperationRunner[T] = Callable[[Callable[[], T]], T]
type Factory = Callable[[VoiceSettings], object]


class VoiceService:
    """Listen locally without ever making terminal input or the face mandatory."""

    def __init__(
        self,
        coordinator: Coordinator,
        settings: VoiceSettings,
        prompt_handler: PromptHandler,
        *,
        notice_handler: NoticeHandler | None = None,
        wake_acknowledgement_handler: WakeAcknowledgementHandler | None = None,
        interrupt_handler: InterruptHandler | None = None,
        continuous_getter: ContinuousGetter | None = None,
        continuous_timeout_handler: Callable[[], None] | None = None,
        transcription_runner: OperationRunner | None = None,
        warm_runner: OperationRunner | None = None,
        audio_factory: Factory = lambda settings: MicrophoneStream(
            target_sample_rate=settings.sample_rate,
            device=settings.microphone_device,
            queue_frames=settings.audio_queue_frames,
            block_ms=settings.audio_block_ms,
        ),
        wake_factory: Factory = SherpaWakeWordDetector,
        vad_factory: Factory = SileroEndpointDetector,
        transcriber_factory: Factory = build_transcriber,
    ) -> None:
        self.coordinator = coordinator
        self.settings = settings
        self.prompt_handler = prompt_handler
        self.notice_handler = notice_handler or (lambda message: None)
        self.wake_acknowledgement_handler = wake_acknowledgement_handler
        self.interrupt_handler = interrupt_handler
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
        self._acknowledgement_index = 0
        self._last_reply_allows_followup = False
        self._configured_continuous_active = settings.continuous_enabled
        self._response_active = threading.Event()
        self._readying_microphone = threading.Event()
        self._speech_completion: threading.Event | None = None
        self._pending_prompt: tuple[VoiceInput, float] | None = None
        self._response_thread: threading.Thread | None = None
        self._followup_until = 0.0
        self._ignore_audio_until = 0.0
        self._reset_detectors = threading.Event()
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._configured_continuous_active = self.settings.continuous_enabled
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
        response_thread = self._response_thread
        if response_thread is not None and response_thread is not threading.current_thread():
            response_thread.join(timeout=10.0)
        with self._lock:
            self._thread = None
            self._audio = None
        self.diagnostics.set_state(VoiceState.STOPPED)

    def status_label(self) -> str:
        return self.diagnostics.status_label()

    def recognition_profile_label(self) -> str:
        """Human-readable proof of the live recognition configuration."""
        alias_count = len(self.settings.wake_kws_aliases)
        if self.settings.effective_asr_provider == "groq":
            fallback = ", cold local fallback" if self.settings.asr_fallback_local else ""
            return (
                f"Groq {self.settings.groq_asr_model}{fallback}, normalizer active, "
                f"wake sensitivity {self.settings.wake_sensitivity}, "
                f"{alias_count} personal KWS alias(es)"
            )
        return (
            f"{self.settings.asr_model} adaptive beam {self.settings.asr_beam_size}, "
            f"retry beam {self.settings.asr_retry_beam_size}, "
            f"normalizer active, wake sensitivity {self.settings.wake_sensitivity}, "
            f"{alias_count} personal KWS alias(es)"
        )

    def interaction_profile_label(self) -> str:
        """Describe the low-latency conversational handoff used by live Ron."""
        if not self.settings.wake_ack_enabled:
            return "wake acknowledgement disabled"
        fast = "fast handoff" if self.settings.wake_fast_handoff else "verified handoff"
        followup = (
            f", automatic {self.settings.automatic_followup_seconds:g}s follow-up"
            if self.settings.followup_enabled
            else ""
        )
        interruption = ", wake-gated interruption" if self.settings.barge_in_enabled else ""
        replacement = (
            ", new-request handoff"
            if self.settings.accept_new_turn_during_reply
            else ""
        )
        return (
            f"{self.settings.interaction_mode} mode, {fast}, wake acknowledgement on "
            f"({len(self.settings.wake_acknowledgements)} phrases), "
            f"{self.settings.wake_followup_timeout_seconds:g}s follow-up window after wake"
            f"{followup}{interruption}{replacement}"
        )

    def _continuous_mode_active(self) -> bool:
        return self._configured_continuous_active or bool(self.continuous_getter())

    def deactivate_configured_continuous_mode(self) -> None:
        """Let an explicit spoken end-chat command override startup continuous mode."""
        self._configured_continuous_active = False

    @property
    def response_active(self) -> bool:
        return self._turn_is_active()

    def _turn_is_active(self) -> bool:
        with self._lock:
            completion = self._speech_completion
            return (
                self._response_active.is_set()
                or self._readying_microphone.is_set()
                or (completion is not None and not completion.is_set())
            )

    def suspend_input_for_speech(self) -> None:
        """Prevent Ron's own synthesized voice from entering the microphone queue."""
        audio = self._audio
        if audio is None:
            return
        setter = getattr(audio, "set_capture_muted", None)
        if callable(setter):
            setter(True)
        discard = getattr(audio, "discard_pending", None)
        if callable(discard):
            discard()

    def resume_input_after_speech(
        self, *, speech_played: bool, guard_seconds: float | None = None
    ) -> None:
        """Resume room listening after speaker tail/reverb has settled."""
        audio = self._audio
        if audio is None:
            return
        guard = (
            self.settings.tts_echo_guard_seconds
            if guard_seconds is None
            else max(0.0, guard_seconds)
        )
        if speech_played and guard > 0:
            self._stop.wait(guard)
        discard = getattr(audio, "discard_pending", None)
        if callable(discard):
            discard()
        setter = getattr(audio, "set_capture_muted", None)
        if callable(setter):
            setter(False)

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
                f'[VOICE READY] Listening for "{self.settings.wake_phrase.title()}" '
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
        post_wake_capture: array[float] | None = None
        awaiting_command_until = 0.0
        continuous_until = 0.0
        cooldown_until = 0.0
        previous_continuous = False
        ring = SampleRingBuffer(int(self.settings.sample_rate * self.settings.ring_buffer_seconds))

        while not self._stop.is_set():
            now = time.monotonic()
            if self._reset_detectors.is_set():
                self._reset_detectors.clear()
                for detector in (wake, vad):
                    resetter = getattr(detector, "reset", None)
                    if callable(resetter):
                        resetter()
                ring.clear()
                discard_pending = getattr(audio, "discard_pending", None)
                if callable(discard_pending):
                    discard_pending()
            with self._lock:
                if self._followup_until > awaiting_command_until:
                    awaiting_command_until = self._followup_until
                    self._followup_until = 0.0
            continuous = self._continuous_mode_active()
            if continuous and not previous_continuous:
                continuous_until = now + self.settings.continuous_timeout_seconds
                self.diagnostics.set_state(VoiceState.LISTENING)
                self._publish_expression(FaceExpression.LISTENING)
            if not continuous:
                continuous_until = 0.0
            previous_continuous = continuous

            if continuous and continuous_until and now >= continuous_until:
                self.continuous_timeout_handler()
                self._configured_continuous_active = False
                continuous_until = 0.0
                previous_continuous = False
                self._notify(
                    "chat-timeout",
                    '[VOICE] Continuous chat timed out. Say "Hey Ron" when you need me.',
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
            if now < self._ignore_audio_until:
                continue

            ring.append(samples)
            if wake_pending and wake_capture is not None:
                wake_capture.extend(samples)
                if post_wake_capture is not None:
                    post_wake_capture.extend(samples)
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
                post_wake_capture = array("f")
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
                continuous_active = self._continuous_mode_active() and (
                    continuous_until == 0.0 or now < continuous_until
                )
                if self._turn_is_active():
                    # While Ron is speaking, only a fresh acoustic wake phrase may
                    # reach ASR. This enables barge-in without treating speaker echo
                    # or room conversation as a follow-up command.
                    continuous_active = False
                awaiting = awaiting_command_until > now
                if self._turn_is_active() and not wake_pending:
                    continue
                if not wake_pending and not awaiting and not continuous_active:
                    continue

                require_wake = wake_pending
                wake_pending = False
                captured = wake_capture
                wake_capture = None
                captured_after_wake = post_wake_capture
                post_wake_capture = None
                segment_samples = array("f", segment)
                segment_seconds = len(segment_samples) / max(1, self.settings.sample_rate)
                post_wake_seconds = len(captured_after_wake or ()) / max(
                    1, self.settings.sample_rate
                )
                if (
                    require_wake
                    and not self._turn_is_active()
                    and self.settings.wake_fast_handoff
                    and self.settings.minimum_speech_seconds
                    <= segment_seconds
                    <= self.settings.wake_fast_segment_seconds
                    and post_wake_seconds <= self.settings.wake_fast_post_seconds
                ):
                    # KWS already recognized the dedicated wake phrase. For a short
                    # wake-only segment, do not make the much larger Whisper model
                    # prove "Hey Ron" again before saying "Yes?". This path cannot
                    # execute a tool; it only opens the follow-up listening window.
                    outcome = self._fast_wake_handoff()
                else:
                    outcome = self._process_segment(
                        captured if require_wake and captured else segment_samples,
                        transcriber,
                        require_wake=require_wake,
                        wake_detected=require_wake,
                        dispatch_response=True,
                    )
                # The microphone continues capturing while synchronous TTS plays.
                # Never feed Ron's own speaker audio back into wake detection/ASR.
                discard_pending = getattr(audio, "discard_pending", None)
                if callable(discard_pending) and outcome in {
                    "handled",
                    "continuous",
                    "dispatched",
                    "queued",
                }:
                    discard_pending()
                    ring.clear()
                completed_at = time.monotonic()
                if outcome == "rejected":
                    cooldown_until = completed_at + self.settings.wake_cooldown_seconds
                    awaiting_command_until = 0.0
                elif outcome == "waiting":
                    # Start the user's response window after "Yes?" has finished,
                    # not before TTS began. Otherwise a slow first synthesis quietly
                    # consumed most of the follow-up timeout.
                    awaiting_command_until = (
                        completed_at + self.settings.wake_followup_timeout_seconds
                    )
                    ring.clear()
                elif outcome in {"clarification", "unclear"}:
                    awaiting_command_until = (
                        completed_at + self.settings.command_start_timeout_seconds
                    )
                elif outcome == "continuous":
                    continuous_until = completed_at + self.settings.continuous_timeout_seconds
                    awaiting_command_until = 0.0
                elif outcome == "dispatched":
                    cooldown_until = 0.0
                    awaiting_command_until = 0.0
                    ring.clear()
                elif outcome == "queued":
                    cooldown_until = 0.0
                    awaiting_command_until = 0.0
                    ring.clear()
                elif (
                    outcome == "handled"
                    and self.settings.followup_enabled
                    and self._last_reply_allows_followup
                ):
                    # Alexa-style follow-up mode: the next natural reply does not
                    # require another wake phrase. The timer starts only after Ron
                    # has finished speaking because prompt_handler is synchronous.
                    awaiting_command_until = completed_at + self.settings.automatic_followup_seconds
                    cooldown_until = 0.0
                    self.diagnostics.set_state(VoiceState.LISTENING)
                    self._publish_expression(FaceExpression.LISTENING)
                else:
                    cooldown_until = completed_at + self.settings.wake_cooldown_seconds
                    awaiting_command_until = 0.0

    def _fast_wake_handoff(self) -> str:
        """Acknowledge a short KWS-confirmed wake without running full Whisper."""
        self.diagnostics.set_state(VoiceState.LISTENING)
        self._publish_expression(FaceExpression.LISTENING)
        self._acknowledge_wake()
        # Speech output temporarily owns the face. Return it to attentive listening
        # as soon as the cached acknowledgement has finished.
        self.diagnostics.set_state(VoiceState.LISTENING)
        self._publish_expression(FaceExpression.LISTENING)
        return "waiting"

    def _process_segment(
        self,
        samples: array[float],
        transcriber: object,
        *,
        require_wake: bool,
        wake_detected: bool,
        dispatch_response: bool = False,
    ) -> str:
        self._last_reply_allows_followup = False
        self.diagnostics.set_state(VoiceState.TRANSCRIBING)
        self._publish_expression(FaceExpression.THINKING)
        try:
            transcript, normalized = self._recognize(
                samples,
                transcriber,
                require_wake=require_wake,
                wake_detected=wake_detected,
            )
        except TranscriptionError as error:
            self._notify(
                "transcription-error",
                f"[VOICE UNCLEAR] {error} Nothing was executed.",
                force=True,
            )
            self._publish_expression(FaceExpression.CONFUSED)
            return "unclear"

        if not normalized.accepted:
            if require_wake:
                self.diagnostics.record_rejection()
            self.diagnostics.set_state(VoiceState.READY)
            self._publish_expression(FaceExpression.IDLE)
            return "rejected"
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
        if normalized.waiting_for_command:
            if self._turn_is_active():
                self._notify(
                    "interrupt-waiting",
                    "[VOICE] Say 'Hey Ron, stop' to interrupt this reply.",
                )
                return "handled"
            if require_wake:
                self._acknowledge_wake()
            # Speech output temporarily owns the face; restore attention afterwards.
            self.diagnostics.set_state(VoiceState.LISTENING)
            self._publish_expression(FaceExpression.LISTENING)
            return "waiting"
        if normalized.clarification is not None:
            self._notify("clarification", f"[VOICE] {normalized.clarification}", force=True)
            self._publish_expression(FaceExpression.CONFUSED)
            return "clarification"

        voice_input = VoiceInput(
            raw_text=normalized.raw_text,
            text=normalized.text,
            confidence=transcript.confidence,
            wake_phrase=normalized.wake_phrase,
            correction_notes=normalized.correction_notes,
            transcription_seconds=transcript.duration_seconds,
        )
        command = voice_input.text.casefold().strip(" .!?")
        turn_active = self._turn_is_active()
        if turn_active:
            if (
                self.settings.barge_in_enabled
                and require_wake
                and command in self.settings.interrupt_phrases
            ):
                interrupted = False
                if self.interrupt_handler is not None:
                    try:
                        interrupted = self.interrupt_handler(command)
                    except Exception:
                        self._logger.exception("Voice interruption handler failed safely")
                self._notify(
                    "interrupted",
                    "[VOICE] Stopped." if interrupted else "[VOICE] I heard you.",
                    force=True,
                )
                return "handled"
            if self.settings.accept_new_turn_during_reply and require_wake:
                with self._lock:
                    turn_active = self._turn_is_active()
                    if turn_active:
                        self._pending_prompt = (voice_input, transcript.duration_seconds)
                if not turn_active:
                    if dispatch_response:
                        self._dispatch_prompt(voice_input, transcript.duration_seconds)
                        return "dispatched"
                    return self._complete_prompt(voice_input, transcript.duration_seconds)
                interrupted = False
                if self.interrupt_handler is not None:
                    try:
                        interrupted = self.interrupt_handler("new_request")
                    except Exception:
                        self._logger.exception("Voice replacement handler failed safely")
                self._notify(
                    "request-queued",
                    "[VOICE] Got it — switching to that request."
                    if interrupted
                    else "[VOICE] Got it — I'll handle that next.",
                    force=True,
                )
                return "queued"
            self._notify(
                "response-busy",
                "[VOICE] I'm still finishing the current turn. Say 'Hey Ron, stop' to interrupt.",
            )
            return "handled"

        if dispatch_response:
            self._dispatch_prompt(voice_input, transcript.duration_seconds)
            return "dispatched"

        return self._complete_prompt(voice_input, transcript.duration_seconds)

    def _dispatch_prompt(self, voice_input: VoiceInput, transcription_seconds: float) -> None:
        """Keep live microphone capture running while the response is produced."""
        thread = threading.Thread(
            target=self._complete_prompt,
            args=(voice_input, transcription_seconds),
            name="ron-voice-response",
            daemon=True,
        )
        with self._lock:
            self._response_active.set()
            self._response_thread = thread
        thread.start()

    def _complete_prompt(self, voice_input: VoiceInput, transcription_seconds: float) -> str:
        self.diagnostics.set_state(VoiceState.PROCESSING)
        reply: VoiceReply | None = None
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
            outcome = "handled"
        else:
            self.diagnostics.record_command(transcription_seconds)
            self._last_reply_allows_followup = reply.allow_followup
        finally:
            completion = reply.speech_completion if reply is not None else None
            with self._lock:
                self._readying_microphone.set()
                self._speech_completion = completion
                self._response_active.clear()

        if completion is not None and not completion.is_set():
            self.diagnostics.set_state(VoiceState.SPEAKING)
            while not self._stop.is_set() and not completion.wait(0.1):
                pass

        self.diagnostics.set_state(VoiceState.READYING)
        guard_end = time.monotonic() + self.settings.tts_echo_guard_seconds
        with self._lock:
            self._speech_completion = None
            self._ignore_audio_until = guard_end
        self._reset_detectors.set()

        with self._lock:
            pending = self._pending_prompt
            self._pending_prompt = None
            if pending is not None and not self._stop.is_set():
                self._last_reply_allows_followup = False
                self._dispatch_prompt(*pending)
            self._readying_microphone.clear()
        if pending is not None and not self._stop.is_set():
            return "dispatched"

        if reply is not None and reply.continue_listening:
            self.diagnostics.set_state(VoiceState.LISTENING)
            self._publish_expression(FaceExpression.LISTENING)
            outcome = "continuous"
        else:
            self.diagnostics.set_state(VoiceState.READY)
            self._publish_expression(FaceExpression.IDLE)
            outcome = "handled"

        if (
            reply is not None
            and outcome == "handled"
            and self.settings.followup_enabled
            and reply.allow_followup
        ):
            with self._lock:
                self._followup_until = guard_end + self.settings.automatic_followup_seconds
        return outcome

    def _recognize(
        self,
        samples: array[float],
        transcriber: object,
        *,
        require_wake: bool,
        wake_detected: bool,
    ) -> tuple[TranscriptionResult, NormalizationResult]:
        """Decode quickly first, then spend accuracy work only on uncertainty."""
        fast = self.transcription_runner(lambda: transcriber.transcribe(samples))
        normalized_fast = self.normalizer.normalize(
            fast.text,
            require_wake=require_wake,
            wake_detected=wake_detected,
        )
        retry = getattr(transcriber, "retry", None)
        should_retry = (
            self.settings.asr_retry_enabled
            and callable(retry)
            and (
                not fast.text.strip()
                or fast.confidence < self.settings.asr_retry_confidence
                or fast.no_speech_probability >= 0.75
                or normalized_fast.clarification is not None
                or (require_wake and wake_detected and not normalized_fast.accepted)
            )
        )
        if not should_retry:
            return fast, normalized_fast

        try:
            accurate = self.transcription_runner(lambda: retry(samples))
        except TranscriptionError:
            self._logger.debug("Accurate ASR retry failed; keeping fast result", exc_info=True)
            return fast, normalized_fast

        normalized_accurate = self.normalizer.normalize(
            accurate.text,
            require_wake=require_wake,
            wake_detected=wake_detected,
        )
        fast_rank = self._recognition_rank(fast, normalized_fast)
        accurate_rank = self._recognition_rank(accurate, normalized_accurate)
        chosen, normalized = (
            (accurate, normalized_accurate)
            if accurate_rank > fast_rank
            else (fast, normalized_fast)
        )
        combined = TranscriptionResult(
            chosen.text,
            chosen.confidence,
            fast.duration_seconds + accurate.duration_seconds,
            chosen.no_speech_probability,
            chosen.decode_mode,
            2,
        )
        return combined, normalized

    @staticmethod
    def _recognition_rank(
        transcript: TranscriptionResult, normalized: NormalizationResult
    ) -> tuple[int, int, int, float, float]:
        """Prefer safe acceptance and complete intent before confidence alone."""
        return (
            int(normalized.accepted),
            int(normalized.clarification is None),
            int(bool(normalized.text) or normalized.waiting_for_command),
            transcript.confidence,
            -transcript.no_speech_probability,
        )

    def acknowledgement_phrases(self) -> tuple[str, ...]:
        """Return the configured short wake acknowledgements for optional prewarming."""
        return self.settings.wake_acknowledgements if self.settings.wake_ack_enabled else ()

    def _next_acknowledgement(self) -> str:
        phrases = self.settings.wake_acknowledgements
        with self._lock:
            phrase = phrases[self._acknowledgement_index % len(phrases)]
            self._acknowledgement_index += 1
        return phrase

    def _acknowledge_wake(self) -> None:
        """Speak a short acknowledgement after a verified wake-only utterance."""
        if not self.settings.wake_ack_enabled:
            return
        phrase = self._next_acknowledgement()
        handler = self.wake_acknowledgement_handler
        if handler is None:
            self._notify("wake-ack", f"[VOICE ACK] {phrase}", force=True)
            return
        try:
            handler(phrase)
        except Exception:
            # Acknowledgement is conversational polish, never a dependency of listening.
            self._logger.exception("Wake acknowledgement failed safely")

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
