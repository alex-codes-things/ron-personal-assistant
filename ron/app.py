"""Application assembly: create Ron's systems and connect them together."""

from __future__ import annotations

import logging
import os
import signal
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ron.agent.core_runtime import build_agent_core
from ron.ai import (
    InferencePriority,
    InferenceScheduler,
    OllamaClient,
    ScheduledOllamaClient,
)
from ron.assistant import RonAssistant
from ron.chat import ChatService
from ron.core import Coordinator, EventType, FaceExpression, RonEvent
from ron.display import TabletFaceDisplay
from ron.network import NetworkService
from ron.reminders import ReminderManager
from ron.routing_core import AgentCoreRouter
from ron.terminal import TerminalChat
from ron.voice import VoiceInput, VoiceReply, VoiceService, VoiceSettings
from ron.voice.settings import VoiceSettingsError


class RonApplication:
    """Own Ron's lifecycle while individual systems remain independent."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = project_root or Path(__file__).resolve().parents[1]
        self.coordinator = Coordinator()
        self.network = NetworkService(self.coordinator)
        self.face = TabletFaceDisplay(
            self.coordinator,
            self.project_root,
            network_service=self.network,
        )
        self.ai_client = OllamaClient()
        self.inference_scheduler = InferenceScheduler()
        self.chat_ai = ScheduledOllamaClient(
            self.ai_client, self.inference_scheduler, InferencePriority.CONVERSATION
        )
        self.routing_ai = ScheduledOllamaClient(
            self.ai_client, self.inference_scheduler, InferencePriority.ROUTING
        )
        self.planning_ai = ScheduledOllamaClient(
            self.ai_client, self.inference_scheduler, InferencePriority.PLANNING
        )
        reminder_path = self.project_root / "runtime" / "data" / "reminders.sqlite"
        self.reminders = ReminderManager(reminder_path)
        self.chat = ChatService(self.coordinator, client=self.chat_ai)
        self.router = AgentCoreRouter(self.routing_ai)
        self.agent_core = build_agent_core(
            self.project_root,
            self.planning_ai,
            self.reminders,
            self.network,
        )
        self.tools = self.agent_core.registry
        self.agent_planner = self.agent_core.planner
        self.agent = self.agent_core.service
        self.assistant = RonAssistant(
            self.coordinator, self.chat, self.router, agent=self.agent
        )
        self.terminal = TerminalChat(self.assistant, status_provider=self._runtime_status)
        self.face.add_notice_listener(self.terminal.post_system_notice)
        self._voice_configuration_error: str | None = None
        try:
            voice_settings = VoiceSettings.from_environment(self.project_root)
        except VoiceSettingsError as error:
            self._voice_configuration_error = str(error)
            voice_settings = VoiceSettings(enabled=False, project_root=self.project_root)
        self.voice = VoiceService(
            self.coordinator,
            voice_settings,
            self._handle_voice_input,
            notice_handler=self.terminal.post_system_notice,
            continuous_getter=lambda: self.chat.continuous,
            continuous_timeout_handler=self.chat.end_continuous_chat,
            transcription_runner=lambda operation: self.inference_scheduler.run(
                InferencePriority.TRANSCRIPTION, operation
            ),
            warm_runner=lambda operation: self.inference_scheduler.run(
                InferencePriority.BACKGROUND, operation
            ),
        )
        self._shutdown = threading.Event()
        self._started = False
        self._logger = logging.getLogger(__name__)

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
        self.voice.start()
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
        self.voice.stop()
        self.face.stop()
        try:
            self.network.stop()
        except Exception:
            self._logger.debug("Ron Network did not stop cleanly", exc_info=True)
        self.agent.stop()
        self.reminders.stop()
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
        network_state = self.network.status_label()
        return (
            f"Local AI: {ai_health}; scheduler: {ai_state} ({queued} queued); tablet face: "
            f"{face_state}; network: {network_state}; voice: {voice_state}; "
            f"{self.agent.capability_status()}"
        )

    def _handle_voice_input(self, voice_input: VoiceInput) -> VoiceReply:
        """Handle voice through the same chat, router, agent and safety gates."""
        prompt = voice_input.text.strip()
        self.terminal.post_system_notice(f"[VOICE HEARD] {prompt}")
        command = prompt.casefold().strip(" .!?")
        if command == "start a chat":
            self.chat.start_continuous_chat()
            message = "Continuous chat started. You don't need to repeat the wake word."
        elif command in {"end chat", "stop chat", "go to sleep", "that's all"}:
            self.chat.end_continuous_chat()
            message = 'Continuous chat ended. Say "Hey Ron" when you need me.'
        else:
            try:
                message = self.assistant.respond(prompt).text
            except Exception as error:
                self._logger.exception("Voice prompt failed safely")
                message = (
                    f"I couldn't complete that voice request safely ({type(error).__name__}). "
                    "You can repeat it or type it in the terminal."
                )
        self.terminal.post_system_notice(f"[VOICE RESPONSE] {message}")
        return VoiceReply(message, continue_listening=self.chat.continuous)

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
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        )
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
