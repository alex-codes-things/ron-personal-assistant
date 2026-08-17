"""Immediate streaming terminal conversation for Ron."""

from __future__ import annotations

import queue
import sys
import threading
from collections.abc import Callable
from contextlib import nullcontext
from typing import TextIO

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout
except ImportError:  # Safe fallback for a partially installed environment.
    PromptSession = None
    patch_stdout = None

from ron.ai import OllamaConnectionError, OllamaError
from ron.assistant import RonAssistant
from ron.routing import RouteDestination

InputReader = Callable[[str], str]
StopCheck = Callable[[], bool]
StatusProvider = Callable[[], str]


class TerminalChat:
    """Own terminal commands and formatting, but no model or personality logic."""

    def __init__(
        self,
        assistant: RonAssistant,
        *,
        input_reader: InputReader = input,
        output: TextIO | None = None,
        status_provider: StatusProvider | None = None,
    ) -> None:
        self.assistant = assistant
        self.chat = assistant.chat
        self._input = input_reader
        self._output = output or sys.stdout
        self._status_provider = status_provider
        self._system_notices: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._notice_lock = threading.RLock()
        self._running = False
        self._live = False
        self._session = None
        if self._can_use_live_terminal(input_reader, output):
            try:
                self._session = PromptSession() if PromptSession is not None else None
            except Exception:
                # Some Windows hosts report a TTY but expose no console buffer.
                self._session = None
            self._live = self._session is not None
        self._last_progress: dict[int, tuple[object, ...]] = {}
        if self.assistant.agent is not None:
            self.assistant.agent.add_progress_listener(self._on_task_progress)
            if self.assistant.agent.reminders is not None:
                self.assistant.agent.reminders.add_listener(self._on_reminder)

    def run(self, should_stop: StopCheck = lambda: False) -> int:
        self._running = True
        try:
            context = (
                patch_stdout(raw=True)
                if self._live and patch_stdout is not None
                else nullcontext()
            )
        except Exception:
            # Keep Ron usable if an unusual terminal loses its console handle.
            self._live = False
            self._session = None
            context = nullcontext()
        try:
            with context:
                return self._run_loop(should_stop)
        finally:
            self._running = False

    def post_system_notice(self, message: str) -> None:
        clean = " ".join(message.strip().split())
        if not clean:
            return
        clean = clean[:500]
        with self._notice_lock:
            if self._live and self._running:
                print(f"Ron > {clean}", flush=True)
                return
            self._system_notices.put(clean)

    @staticmethod
    def _can_use_live_terminal(
        input_reader: InputReader,
        output: TextIO | None,
    ) -> bool:
        if input_reader is not input or output is not None or PromptSession is None:
            return False
        try:
            return bool(sys.stdin.isatty() and sys.stdout.isatty())
        except (AttributeError, OSError):
            return False

    def _run_loop(self, should_stop: StopCheck) -> int:
        self._line("Ron is ready. Type a message immediately, or /help for commands.")
        while not should_stop():
            self._drain_system_notices()
            self._drain_task_notifications()
            try:
                prompt = (
                    self._session.prompt("You > ")
                    if self._session is not None
                    else self._input("You > ")
                )
            except EOFError:
                self._line("\nRon > Terminal input closed. Shutting down safely.")
                return 0
            except KeyboardInterrupt:
                self._line("\nRon > Okay, shutting down safely.")
                return 130

            clean_prompt = prompt.strip()
            if not clean_prompt:
                continue
            command_result = self._handle_command(clean_prompt)
            if command_result is not None:
                if command_result == "quit":
                    return 0
                continue

            self._write("Ron > ")
            try:
                self.assistant.respond(clean_prompt, on_token=self._stream_token)
            except OllamaConnectionError:
                self._line(
                    "I can't reach my local AI right now. Open Ollama and try again."
                )
            except OllamaError as error:
                self._line(f"My local model returned an error: {error}")
            except ValueError as error:
                self._line(str(error))
            except KeyboardInterrupt:
                self._line("\nI stopped that response. Your previous chat is still safe.")
            except Exception as error:
                self._line(f"I hit an unexpected local error: {error}")
            else:
                self._line("")
        return 0

    def _drain_system_notices(self) -> None:
        while True:
            try:
                notice = self._system_notices.get_nowait()
            except queue.Empty:
                return
            self._line(f"Ron > {notice}")

    def _handle_command(self, prompt: str) -> str | None:
        command = prompt.casefold()
        if command == "/quit":
            self._line("Ron > See you soon!")
            return "quit"
        if command == "/clear":
            self.chat.clear_history()
            self._line("Ron > Conversation history cleared.")
            return "handled"
        if command == "/help":
            self._line(
                "Ron > Commands: /help, /clear, /status, /tools, /tasks, "
                "/task ID, /cancel ID, /diagnose ID, /reminders, "
                "/cancel-reminder ID, /route PROMPT, /quit. "
                "You can also say 'Start a chat' or 'End chat'."
            )
            return "handled"
        if command == "/tools":
            if self.assistant.agent is None:
                self._line("Ron > No agent tools are connected.")
            else:
                names = ", ".join(self.assistant.agent.registry.names())
                self._line(f"Ron > Approved tools: {names}.")
            return "handled"
        if command == "/status":
            mode = "continuous chat" if self.chat.continuous else "ready"
            last_route = self.assistant.last_route
            route_text = (
                f"; last route: {last_route.destination.value}"
                if last_route is not None
                else ""
            )
            base = (
                f"Ron > Mode: {mode}; remembered turns: "
                f"{self.chat.history.turn_count}{route_text}."
            )
            details = self._status_provider() if self._status_provider is not None else ""
            self._line(f"{base} {details}".rstrip())
            return "handled"
        if command == "/tasks":
            if self.assistant.agent is None:
                self._line("Ron > No agent task manager is connected.")
            else:
                snapshots = self.assistant.agent.task_snapshots()
                self._line(
                    f"Ron > {self.assistant.agent.describe_tasks(snapshots)}"
                )
            return "handled"
        if command.startswith("/task "):
            task_id = self._parse_task_id(prompt[6:])
            if task_id is None:
                self._line("Ron > Use /task followed by a positive task number.")
            elif self.assistant.agent is None:
                self._line("Ron > No agent task manager is connected.")
            else:
                snapshot = self.assistant.agent.task_snapshot(task_id)
                message = (
                    f"I couldn't find task {task_id}."
                    if snapshot is None
                    else self.assistant.agent.describe_task(snapshot)
                )
                self._line(f"Ron > {message}")
            return "handled"
        if command.startswith("/cancel "):
            task_id = self._parse_task_id(prompt[8:])
            if task_id is None:
                self._line("Ron > Use /cancel followed by a positive task number.")
            elif self.assistant.agent is None:
                self._line("Ron > No agent task manager is connected.")
            else:
                snapshot = self.assistant.agent.cancel_task(task_id)
                message = (
                    f"I couldn't find task {task_id}."
                    if snapshot is None
                    else self.assistant.agent.describe_task(snapshot)
                )
                self._line(f"Ron > {message}")
            return "handled"
        if command.startswith("/diagnose "):
            task_id = self._parse_task_id(prompt[10:])
            if task_id is None:
                self._line("Ron > Use /diagnose followed by a positive task number.")
            elif self.assistant.agent is None:
                self._line("Ron > No agent diagnostics are connected.")
            else:
                self._line(f"Ron > {self.assistant.agent.diagnose(task_id)}")
            return "handled"
        if command == "/reminders":
            if self.assistant.agent is None:
                self._line("Ron > No reminder system is connected.")
            else:
                reminders = self.assistant.agent.reminder_snapshots()
                if not reminders:
                    self._line("Ron > There are no saved reminders.")
                else:
                    summary = " ".join(
                        f"Reminder {item.reminder_id} is {item.status}, due "
                        f"{item.due_local}: {item.message}."
                        for item in reminders
                    )
                    self._line(f"Ron > {summary}")
            return "handled"
        if command.startswith("/cancel-reminder "):
            reminder_id = self._parse_task_id(prompt[17:])
            if reminder_id is None:
                self._line("Ron > Add a positive reminder number.")
            elif self.assistant.agent is None:
                self._line("Ron > No reminder system is connected.")
            else:
                reminder = self.assistant.agent.cancel_reminder(reminder_id)
                message = (
                    f"I couldn't find reminder {reminder_id}."
                    if reminder is None
                    else f"Reminder {reminder_id} is {reminder.status}."
                )
                self._line(f"Ron > {message}")
            return "handled"
        if command.startswith("/route "):
            candidate = prompt[7:].strip()
            if not candidate:
                self._line("Ron > Add a prompt after /route.")
                return "handled"
            decision = self.assistant.decide(candidate)
            confirmation = (
                "; confirmation required"
                if decision.requires_confirmation
                and decision.destination is RouteDestination.AGENT
                else ""
            )
            self._line(
                f"Ron > {decision.destination.value.upper()} "
                f"({decision.confidence:.0%}, {decision.source.value}{confirmation}): "
                f"{decision.reason}"
            )
            return "handled"
        if command == "start a chat":
            self.chat.start_continuous_chat()
            self._line(
                "Ron > Continuous chat started. I'll keep more of our conversation in mind."
            )
            return "handled"
        if command in {"end chat", "stop chat"}:
            self.chat.end_continuous_chat()
            self._line("Ron > Continuous chat ended. You can still message me normally.")
            return "handled"
        return None

    def _stream_token(self, token: str) -> None:
        self._write(token)

    def _drain_task_notifications(self) -> None:
        if self.assistant.agent is None:
            return
        for snapshot in self.assistant.agent.drain_notifications():
            if not self._live:
                self._line(f"Ron > {self.assistant.agent.describe_task(snapshot)}")
        for reminder in self.assistant.agent.drain_reminders():
            if not self._live:
                self._line(f"Ron > Reminder {reminder.reminder_id}: {reminder.message}")

    def _on_task_progress(self, snapshot: object) -> None:
        if not self._live or self.assistant.agent is None:
            return
        task_id = getattr(snapshot, "task_id", None)
        if not isinstance(task_id, int):
            return
        marker = (
            getattr(snapshot, "status", None),
            getattr(snapshot, "completed_steps", None),
            getattr(snapshot, "current_tool", None),
            getattr(snapshot, "message", None),
        )
        if self._last_progress.get(task_id) == marker:
            return
        self._last_progress[task_id] = marker
        print(f"Ron > {self.assistant.agent.describe_task(snapshot)}", flush=True)

    def _on_reminder(self, reminder: object) -> None:
        if self._live:
            reminder_id = getattr(reminder, "reminder_id", "?")
            message = getattr(reminder, "message", "Reminder finished")
            print(f"Ron > Reminder {reminder_id}: {message}", flush=True)

    @staticmethod
    def _parse_task_id(value: str) -> int | None:
        clean = value.strip()
        if not clean.isdecimal():
            return None
        task_id = int(clean)
        return task_id if task_id > 0 else None

    def _write(self, text: str) -> None:
        safe_text = "".join(
            character
            for character in text
            if character in {"\n", "\t"} or 32 <= ord(character) != 127
        )
        self._output.write(safe_text)
        self._output.flush()

    def _line(self, text: str) -> None:
        self._write(f"{text}\n")
