"""Application assembly: create Ron's systems and connect them together."""

from __future__ import annotations

import logging
import os
import signal
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ron.agent import AgentPlanner, AgentService, build_default_registry
from ron.ai import (
    InferencePriority,
    InferenceScheduler,
    ScheduledAIClient,
    build_ai_client,
)
from ron.assistant import AssistantTurnCancelled, RonAssistant
from ron.chat import ChatService
from ron.config import load_project_environment
from ron.core import Coordinator, EventType, FaceExpression, RonEvent
from ron.display import TabletFaceDisplay
from ron.health import HealthMonitor, PerformanceArchive
from ron.latency import LatencyTracker, TurnTrace
from ron.memory import MemoryService, VisualMemoryService
from ron.network import NetworkService
from ron.reminders import ReminderManager
from ron.routing import PromptRouter
from ron.storage import StorageManager
from ron.terminal import TerminalChat
from ron.voice import (
    SpeechOutputService,
    VoiceInput,
    VoiceReply,
    VoiceService,
    VoiceSettings,
)
from ron.voice.settings import VoiceSettingsError

VOICE_PROGRESS_PHRASES = (
    "Opening it now.",
    "Adjusting playback.",
    "Finding that track.",
    "Searching now.",
    "Saving that reminder.",
    "Adjusting it now.",
    "Running that now.",
)
class RonApplication:
    """Own Ron's lifecycle while individual systems remain independent."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = (project_root or Path(__file__).resolve().parents[1]).resolve()
        # Load project-local configuration before constructing *any* service.
        # PowerShell/OS variables still take precedence over values in .env.
        self.environment = load_project_environment(self.project_root)
        self.coordinator = Coordinator()
        self.storage = StorageManager(self.project_root, notice_handler=self._post_memory_notice)
        self.memory = MemoryService(self.project_root, self.storage)
        self.visual_memory = VisualMemoryService(
            self.project_root, self.storage, self.memory.catalog
        )
        self.network = NetworkService(self.coordinator)
        self.face = TabletFaceDisplay(
            self.coordinator,
            self.project_root,
            network_service=self.network,
        )
        self.ai_client = build_ai_client()
        self.inference_scheduler = InferenceScheduler()
        self.chat_ai = ScheduledAIClient(
            self.ai_client, self.inference_scheduler, InferencePriority.CONVERSATION
        )
        self.routing_ai = ScheduledAIClient(
            self.ai_client, self.inference_scheduler, InferencePriority.ROUTING
        )
        self.planning_ai = ScheduledAIClient(
            self.ai_client, self.inference_scheduler, InferencePriority.PLANNING
        )
        reminder_path = self.project_root / "runtime" / "data" / "reminders.sqlite"
        self.reminders = ReminderManager(reminder_path)
        self.chat = ChatService(
            self.coordinator,
            client=self.chat_ai,
            memory_context_provider=self.memory.context_for_prompt,
        )
        self.tools = build_default_registry(self.project_root, self.reminders)
        self.agent_planner = AgentPlanner(self.planning_ai, self.tools)
        self.router = PromptRouter(
            self.routing_ai,
            action_resolver=self.agent_planner.can_handle,
        )
        self.agent = AgentService(
            self.agent_planner,
            self.tools,
            project_root=self.project_root,
            reminder_manager=self.reminders,
        )
        self.assistant = RonAssistant(
            self.coordinator,
            self.chat,
            self.router,
            agent=self.agent,
            memory=self.memory,
        )
        self.latency = LatencyTracker()
        self.performance_archive = PerformanceArchive(self.storage)
        self.latency.add_finish_listener(self.performance_archive.record)
        self.assistant.add_progress_listener(self.latency.on_progress)
        self.terminal = TerminalChat(
            self.assistant,
            status_provider=self._runtime_status,
            latency_provider=self.latency.report,
            health_provider=self._health_report,
        )
        self.face.add_notice_listener(self.terminal.post_system_notice)
        self._voice_configuration_error: str | None = None
        try:
            voice_settings = VoiceSettings.from_environment(self.project_root)
        except VoiceSettingsError as error:
            self._voice_configuration_error = str(error)
            voice_settings = VoiceSettings(
                enabled=False, project_root=self.project_root, tts_enabled=False
            )
        self.speech = SpeechOutputService(
            self.coordinator,
            voice_settings,
            notice_handler=self.terminal.post_system_notice,
            synthesis_runner=(
                None
                if voice_settings.tts_concurrent_synthesis or not self.ai_client.is_local
                else lambda operation: self.inference_scheduler.run(
                    InferencePriority.SPEECH, operation
                )
            ),
        )
        self.voice = VoiceService(
            self.coordinator,
            voice_settings,
            self._handle_voice_input,
            notice_handler=self.terminal.post_system_notice,
            wake_acknowledgement_handler=self._handle_wake_acknowledgement,
            interrupt_handler=self._handle_voice_interruption,
            continuous_getter=lambda: self.chat.continuous,
            continuous_timeout_handler=self.chat.end_continuous_chat,
            transcription_runner=(
                (lambda operation: operation())
                if not self.ai_client.is_local
                else lambda operation: self.inference_scheduler.run(
                    InferencePriority.TRANSCRIPTION, operation
                )
            ),
            warm_runner=(
                (lambda operation: operation())
                if not self.ai_client.is_local
                else lambda operation: self.inference_scheduler.run(
                    InferencePriority.BACKGROUND, operation
                )
            ),
        )
        self.health_monitor = HealthMonitor(
            ai_label=lambda: self.ai_client.provider_label,
            voice=self.voice,
            speech=self.speech,
            face=self.face,
            storage=self.storage,
            agent=self.agent,
            latency=self.latency,
            archive=self.performance_archive,
        )
        self._shutdown = threading.Event()
        self._started = False
        self._logger = logging.getLogger(__name__)
        self._thinking_cue_index = 0
        self._thinking_cue_lock = threading.Lock()
        self._speech_prewarm_thread: threading.Thread | None = None

        self.coordinator.subscribe(EventType.SHUTDOWN, self._handle_shutdown)

    def run(self) -> int:
        """Start Ron and block until Ctrl+C or a shutdown event."""
        self._configure_logging(self.project_root)
        self._install_signal_handlers()
        self.start()
        self._logger.info("Ron is running. Type /quit or press Ctrl+C to stop him safely.")

        try:
            return self.terminal.run(self._shutdown.is_set)
        except KeyboardInterrupt:
            self._logger.info("Keyboard interrupt received")
            return 130
        finally:
            self.stop()

    def start(self) -> None:
        """Start each independent system once."""
        if self._started:
            return
        self.terminal.post_system_notice(f"[CONFIG] {self.environment.status_label()}")
        self.terminal.post_system_notice(f"[AI READY] {self.ai_client.provider_label}")
        for warning in self.environment.warnings:
            self.terminal.post_system_notice(f"[CONFIG WARNING] {warning}")
        self.storage.start()
        self.performance_archive.start()
        self.reminders.start()
        self.agent.start()
        try:
            self.network.start()
        except Exception:
            self._logger.warning(
                "Ron Network could not start; local Ron is unaffected",
                exc_info=True,
            )
            self.terminal.post_system_notice(
                "[NETWORK OFFLINE] Ron is still fully working locally. "
                "Network devices will be unavailable until the network service recovers."
            )
        self.face.start()
        self.speech.start()
        voice_settings = getattr(self.voice, "settings", None)
        if voice_settings is not None and (
            voice_settings.effective_asr_provider == "groq"
            or voice_settings.effective_tts_provider == "groq"
        ):
            self.terminal.post_system_notice(
                "[CLOUD VOICE READY] Wake detection and silence detection stay local; "
                f"commands use {voice_settings.groq_asr_model} and replies use "
                f"Orpheus {voice_settings.groq_tts_voice}. Only finalized commands and "
                "speakable reply text leave the laptop."
            )
        acknowledgement_provider = getattr(self.voice, "acknowledgement_phrases", None)
        acknowledgement_phrases = (
            acknowledgement_provider() if callable(acknowledgement_provider) else ()
        )
        prewarm_phrases = acknowledgement_phrases
        live_voice_settings = getattr(self.voice, "settings", None)
        if live_voice_settings is not None and live_voice_settings.thinking_cue_enabled:
            prewarm_phrases = tuple(
                dict.fromkeys((*prewarm_phrases, *live_voice_settings.thinking_cues))
            )
        if (
            live_voice_settings is not None
            and live_voice_settings.action_cues_enabled
            and live_voice_settings.effective_tts_provider != "groq"
        ):
            prewarm_phrases = tuple(
                dict.fromkeys((*prewarm_phrases, *VOICE_PROGRESS_PHRASES))
            )
        self.voice.start()
        if voice_settings is None:
            pass
        elif voice_settings.interaction_mode == "strict":
            self.terminal.post_system_notice(
                '[READY] Strict voice mode active. Waiting for "Hey Ron" before every command.'
            )
        elif voice_settings.interaction_mode == "followup":
            self.terminal.post_system_notice(
                "[READY] Follow-up voice mode active. Completed replies briefly keep listening."
            )
        else:
            self.terminal.post_system_notice(
                "[READY] Continuous voice mode active until its safety timeout."
            )
        if prewarm_phrases and self.speech.enabled:
            # Cache optional phrases without blocking terminal startup or microphone
            # initialization. SpeechOutputService serializes access to Kokoro safely.
            self._speech_prewarm_thread = threading.Thread(
                target=self.speech.prewarm,
                args=(prewarm_phrases,),
                name="ron-speech-prewarm",
                daemon=True,
            )
            self._speech_prewarm_thread.start()
        if self._voice_configuration_error is not None:
            self.terminal.post_system_notice(
                "[VOICE OFFLINE] Voice configuration is invalid: "
                f"{self._voice_configuration_error}. Terminal chat is still fully working."
            )
        self.coordinator.publish(
            RonEvent(
                EventType.FACE_EXPRESSION,
                {"expression": FaceExpression.IDLE.value},
            )
        )
        self._started = True

    def stop(self) -> None:
        """Stop systems in reverse order and tolerate repeated calls."""
        if not self._started:
            return
        self.speech.stop()
        self.voice.stop()
        self.face.stop()
        try:
            self.network.stop()
        except Exception:
            self._logger.debug("Ron Network did not stop cleanly", exc_info=True)
        self.agent.stop()
        self.reminders.stop()
        self.performance_archive.stop()
        self.storage.stop()
        self._started = False
        self._logger.info("Ron stopped safely")

    def request_shutdown(self) -> None:
        self._shutdown.set()

    def _handle_shutdown(self, event: RonEvent) -> None:
        del event
        self.request_shutdown()

    def _runtime_status(self) -> str:
        active, queued = self.inference_scheduler.status()
        ai_state = "busy" if active else "idle"
        ai_health = self.inference_scheduler.health_label()
        face_state = self.face.connection_label()
        voice_state = self.voice.status_label()
        speech_state = self.speech.status_label()
        network_state = self.network.status_label()
        storage_state = self.storage.status_label()
        memory_state = self.memory.status_label()
        return (
            f"AI: {self.ai_client.provider_label}; health: {ai_health}; "
            f"scheduler: {ai_state} ({queued} queued); tablet face: "
            f"{face_state}; network: {network_state}; voice: {voice_state}; {speech_state}; "
            f"storage: {storage_state}; {memory_state}; "
            f"config: {self.environment.status_label()}; "
            f"recognition: {self.voice.recognition_profile_label()}; "
            f"interaction: {self.voice.interaction_profile_label()}; "
            f"latency: {self.latency.latest_summary()}; "
            f"performance: {self.performance_archive.status_label()}; "
            f"{self.agent.capability_status()}"
        )

    def _health_report(self) -> str:
        return self.health_monitor.report()

    def _post_memory_notice(self, message: str) -> None:
        terminal = getattr(self, "terminal", None)
        if terminal is not None:
            terminal.post_system_notice(message)

    def _handle_wake_acknowledgement(self, phrase: str) -> bool:
        """Acknowledge a verified wake phrase, then hand listening back immediately."""
        self.terminal.post_system_notice(f"[VOICE ACK] {phrase}")
        self.voice.suspend_input_for_speech()
        speech_played = False
        try:
            speech_played = self.speech.speak(phrase)
        finally:
            self.voice.resume_input_after_speech(
                speech_played=speech_played,
                guard_seconds=self.voice.settings.wake_ack_echo_guard_seconds,
            )
        return speech_played

    def _handle_voice_input(self, voice_input: VoiceInput) -> VoiceReply:
        """Handle voice through the same chat, router, agent and safety gates."""
        prompt = voice_input.text.strip()
        trace = self.latency.start("voice")
        trace.duration("asr", voice_input.transcription_seconds)
        self.terminal.post_status(f"Heard: {prompt}")
        if voice_input.correction_notes and voice_input.raw_text.strip() != prompt:
            raw = " ".join(voice_input.raw_text.strip().split())
            self.terminal.post_system_notice(f'[VOICE CORRECTED] "{raw}" -> "{prompt}"')
        command = prompt.casefold().strip(" .!?")
        allow_followup = True
        speech_stream = None
        speech_played = False
        speech_completion: threading.Event | None = None
        streamed_any = False
        if command == "start a chat":
            self.chat.start_continuous_chat()
            message = "Continuous chat started. You don't need to repeat the wake word."
        elif command in {"end chat", "stop chat", "go to sleep", "that's all"}:
            self.chat.end_continuous_chat()
            self.voice.deactivate_configured_continuous_mode()
            allow_followup = False
            message = 'Continuous chat ended. Say "Hey Ron" when you need me.'
        else:
            cue_timer, response_ready = self._schedule_thinking_cue()
            action_cue_started = threading.Event()
            if self.voice.settings.tts_streaming and self.speech.enabled:
                speech_stream = self.speech.open_stream(
                    on_first_audio_byte=lambda: trace.mark("first_audio_byte"),
                    on_first_audio=lambda: trace.mark("first_audio")
                )

            def stream_to_voice(token: str) -> None:
                nonlocal streamed_any
                if not token:
                    return
                streamed_any = True
                trace.mark("first_token")
                response_ready.set()
                if speech_stream is not None:
                    speech_stream.feed(token)

            def report_voice_progress(progress: str) -> None:
                unified_cue = (
                    self.voice.settings.thinking_cues[0]
                    if self.voice.settings.effective_tts_provider == "groq"
                    else None
                )
                phrase = self._voice_progress_phrase(progress, unified_cue=unified_cue)
                if (
                    phrase is None
                    or action_cue_started.is_set()
                    or not self.voice.settings.action_cues_enabled
                    or not self.speech.is_cached(phrase)
                ):
                    return
                action_cue_started.set()
                response_ready.set()
                threading.Thread(
                    target=self.speech.speak_cached,
                    args=(phrase,),
                    name="ron-action-cue",
                    daemon=True,
                ).start()

            try:
                with self.latency.activate(trace):
                    message = self.assistant.respond(
                        prompt,
                        spoken=True,
                        on_token=stream_to_voice if speech_stream is not None else None,
                        on_progress=report_voice_progress,
                    ).text
                trace.mark("assistant_complete")
            except AssistantTurnCancelled:
                if speech_stream is not None:
                    speech_stream.cancel()
                    speech_completion = speech_stream.completion_event
                if speech_completion is not None and not speech_completion.is_set():
                    threading.Thread(
                        target=self._finish_latency_after_speech,
                        args=(trace, speech_completion),
                        name="ron-latency-cancel",
                        daemon=True,
                    ).start()
                else:
                    self.latency.finish(trace)
                return VoiceReply(
                    "",
                    speech_played=False,
                    allow_followup=False,
                    speech_completion=speech_completion,
                )
            except Exception as error:
                self._logger.exception("Voice prompt failed safely")
                message = (
                    f"I couldn't complete that voice request safely ({type(error).__name__}). "
                    "You can repeat it or type it in the terminal."
                )
            finally:
                self._finish_thinking_cue(cue_timer, response_ready)
        self.terminal.post_system_notice(f"[VOICE RESPONSE] {message}")
        if speech_stream is not None:
            if not streamed_any:
                speech_stream.feed(message)
            speech_played = speech_stream.finish(wait=False)
            speech_completion = speech_stream.completion_event
        else:
            # Cached wake responses and the non-streaming compatibility mode still
            # use the echo guard. Live streamed turns stay wake-gated for barge-in.
            self.voice.suspend_input_for_speech()
            try:
                speech_played = self.speech.speak(message)
            finally:
                self.voice.resume_input_after_speech(speech_played=speech_played)
        if speech_completion is not None and not speech_completion.is_set():
            threading.Thread(
                target=self._finish_latency_after_speech,
                args=(trace, speech_completion),
                name="ron-latency-finish",
                daemon=True,
            ).start()
        else:
            self.latency.finish(trace)
        return VoiceReply(
            message,
            continue_listening=self.chat.continuous,
            speech_played=speech_played,
            allow_followup=allow_followup,
            speech_completion=speech_completion,
        )

    def _handle_voice_interruption(self, command: str) -> bool:
        """Stop current audio promptly; model/tool safety boundaries remain intact."""
        del command
        speech_interrupted = self.speech.cancel_current()
        turn_interrupted = self.assistant.cancel_current_turn()
        interrupted = speech_interrupted or turn_interrupted
        if interrupted:
            self.terminal.post_system_notice("[VOICE INTERRUPTED] Stopping my reply.")
        return interrupted

    def _finish_latency_after_speech(
        self,
        trace: TurnTrace,
        completion: threading.Event,
    ) -> None:
        completion.wait()
        self.latency.finish(trace)

    @staticmethod
    def _voice_progress_phrase(
        progress: str,
        *,
        unified_cue: str | None = None,
    ) -> str | None:
        clean = progress.casefold()
        if "running:" not in clean:
            return None
        if unified_cue:
            return unified_cue
        mappings = (
            ("opening the application", "Opening it now."),
            ("controlling the current media", "Adjusting playback."),
            ("controlling spotify playback", "Adjusting playback."),
            ("finding and playing", "Finding that track."),
            ("searching", "Searching now."),
            ("saving the reminder", "Saving that reminder."),
            ("adjusting", "Adjusting it now."),
        )
        for marker, phrase in mappings:
            if marker in clean:
                return phrase
        return "Running that now."

    def _schedule_thinking_cue(self) -> tuple[threading.Timer | None, threading.Event]:
        """Play a cached acknowledgement only when a response is genuinely slow."""
        response_ready = threading.Event()
        settings = self.voice.settings
        if not settings.thinking_cue_enabled or not self.speech.enabled:
            return None, response_ready
        with self._thinking_cue_lock:
            phrase = settings.thinking_cues[self._thinking_cue_index % len(settings.thinking_cues)]
            self._thinking_cue_index += 1

        def play_if_still_waiting() -> None:
            if response_ready.is_set() or not self.speech.is_cached(phrase):
                return
            self.voice.suspend_input_for_speech()
            speech_played = False
            try:
                if not response_ready.is_set():
                    speech_played = self.speech.speak(phrase)
            finally:
                self.voice.resume_input_after_speech(
                    speech_played=speech_played,
                    guard_seconds=settings.wake_ack_echo_guard_seconds,
                )

        timer = threading.Timer(settings.thinking_cue_delay_seconds, play_if_still_waiting)
        timer.name = "ron-thinking-cue"
        timer.daemon = True
        timer.start()
        return timer, response_ready

    @staticmethod
    def _finish_thinking_cue(
        timer: threading.Timer | None, response_ready: threading.Event
    ) -> None:
        response_ready.set()
        if timer is None:
            return
        timer.cancel()
        # If the timer already began playing, let the tiny cached phrase finish
        # before the actual answer starts so voices never overlap.
        if timer.is_alive() and timer is not threading.current_thread():
            timer.join(timeout=3.0)

    def _install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return

        def stop_from_signal(signum: int, frame: object) -> None:
            del signum, frame
            self.request_shutdown()
            raise KeyboardInterrupt

        for signal_name in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(signal_name, stop_from_signal)
            except (OSError, ValueError):
                continue

    @staticmethod
    def _configure_logging(project_root: Path) -> None:
        level_name = os.getenv("RON_LOG_LEVEL", "WARNING").upper()
        level = getattr(logging, level_name, logging.WARNING)
        formatter = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        if not any(getattr(handler, "_ron_console", False) for handler in root_logger.handlers):
            console = logging.StreamHandler()
            console._ron_console = True  # type: ignore[attr-defined]
            console.setLevel(level)
            console.setFormatter(formatter)
            root_logger.addHandler(console)
        log_directory = project_root / "runtime" / "logs"
        log_directory.mkdir(parents=True, exist_ok=True)
        log_path = (log_directory / "ron.log").resolve()
        has_private_log = any(
            isinstance(handler, RotatingFileHandler)
            and Path(handler.baseFilename).resolve() == log_path
            for handler in root_logger.handlers
        )
        if not has_private_log:
            private_log = RotatingFileHandler(
                log_path,
                maxBytes=1_048_576,
                backupCount=3,
                encoding="utf-8",
            )
            private_log.setLevel(logging.DEBUG)
            private_log.setFormatter(formatter)
            root_logger.addHandler(private_log)
