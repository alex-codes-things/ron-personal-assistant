"""One entry point that routes every prompt while chat remains user-facing."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock

from ron.agent import AgentResponse, AgentService, AgentTaskSnapshot, ToolStatus
from ron.ai import InferenceCancelled, InferenceResult
from ron.chat import ChatService
from ron.core import Coordinator, EventType, FaceExpression, RonEvent
from ron.memory import MemoryIntelligence, MemoryService
from ron.routing import (
    PromptRouter,
    RouteDestination,
    RouteSource,
    RoutingDecision,
)

type TokenHandler = Callable[[str], None]
type ProgressListener = Callable[[str], None]


class AssistantTurnCancelled(RuntimeError):
    """A voice turn was intentionally replaced before it completed."""


@dataclass(frozen=True, slots=True)
class AssistantResponse:
    text: str
    route: RoutingDecision
    inference: InferenceResult | None = None
    agent: AgentResponse | None = None


class RonAssistant:
    """Route prompts, delegate work, and preserve one Ron personality."""

    def __init__(
        self,
        coordinator: Coordinator,
        chat: ChatService,
        router: PromptRouter,
        agent: AgentService | None = None,
        memory: MemoryService | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.chat = chat
        self.router = router
        self.agent = agent
        self.memory = memory
        self.memory_intelligence = MemoryIntelligence(memory) if memory is not None else None
        self._logger = logging.getLogger(__name__)
        self.last_route: RoutingDecision | None = None
        self._turn_lock = RLock()
        self._progress_lock = RLock()
        self._progress_listeners: list[ProgressListener] = []
        self._cancel_lock = RLock()
        self._current_cancel: threading.Event | None = None
        if self.agent is not None:
            self.agent.add_task_listener(self._record_task_completion)
            if self.agent.reminders is not None:
                self.agent.reminders.add_listener(self._record_reminder)

    def decide(self, prompt: str) -> RoutingDecision:
        with self._turn_lock:
            return self._decide_locked(prompt)

    def add_progress_listener(self, listener: ProgressListener) -> None:
        with self._progress_lock:
            if listener not in self._progress_listeners:
                self._progress_listeners.append(listener)

    def _decide_locked(self, prompt: str) -> RoutingDecision:
        if (
            self.memory_intelligence is not None
            and self.memory_intelligence.claims_interaction(prompt)
        ):
            decision = RoutingDecision(
                destination=RouteDestination.CHAT,
                confidence=1.0,
                reason="The prompt is an explicit long-term memory interaction.",
                source=RouteSource.DETERMINISTIC,
            )
        elif self.agent is not None and self.agent.claims_interaction(prompt):
            decision = RoutingDecision(
                destination=RouteDestination.AGENT,
                confidence=1.0,
                reason="The prompt answers Ron's pending confirmation or clarification.",
                source=RouteSource.DETERMINISTIC,
            )
        else:
            decision = self.router.route(prompt)
        self.last_route = decision
        return decision

    def respond(
        self,
        prompt: str,
        on_token: TokenHandler | None = None,
        *,
        spoken: bool = False,
        on_progress: ProgressListener | None = None,
    ) -> AssistantResponse:
        # Terminal and microphone threads share one bounded turn at a time. This
        # prevents Whisper, routing, memory interactions and agent state from racing.
        cancel_event = threading.Event()
        if spoken:
            with self._cancel_lock:
                self._current_cancel = cancel_event
        try:
            with self._turn_lock:
                return self._respond_locked(
                    prompt,
                    on_token,
                    spoken=spoken,
                    on_progress=on_progress,
                    cancel_event=cancel_event,
                )
        finally:
            with self._cancel_lock:
                if self._current_cancel is cancel_event:
                    self._current_cancel = None

    def cancel_current_turn(self) -> bool:
        """Request cancellation without waiting on the active turn lock."""
        with self._cancel_lock:
            cancel_event = self._current_cancel
            if cancel_event is None:
                return False
            cancel_event.set()
            return True

    def _respond_locked(
        self,
        prompt: str,
        on_token: TokenHandler | None = None,
        *,
        spoken: bool = False,
        on_progress: ProgressListener | None = None,
        cancel_event: threading.Event,
    ) -> AssistantResponse:
        if cancel_event.is_set():
            raise AssistantTurnCancelled("The assistant turn was replaced")
        self._report_progress("Understanding your request…", on_progress)
        if self.memory_intelligence is not None:
            memory_reply = self.memory_intelligence.handle(prompt)
            if memory_reply is not None:
                decision = RoutingDecision(
                    destination=RouteDestination.CHAT,
                    confidence=1.0,
                    reason="The prompt was handled directly by Ron's memory system.",
                    source=RouteSource.DETERMINISTIC,
                )
                self.last_route = decision
                self._show_expression(
                    FaceExpression.THINKING if spoken else FaceExpression.SPEAKING
                )
                try:
                    if on_token is not None:
                        on_token(memory_reply)
                    self.chat.history.record(prompt.strip(), memory_reply)
                finally:
                    self._show_expression(FaceExpression.IDLE)
                return AssistantResponse(text=memory_reply, route=decision)

        decision = self._decide_locked(prompt)
        if decision.destination is RouteDestination.CHAT:
            self._report_progress("Thinking about the reply…", on_progress)
            try:
                inference = self.chat.respond(
                    prompt,
                    on_token=on_token,
                    spoken=spoken,
                    cancel_event=cancel_event,
                )
            except InferenceCancelled as error:
                raise AssistantTurnCancelled(str(error)) from error
            self._consider_user_memory(prompt)
            return AssistantResponse(
                text=inference.text,
                route=decision,
                inference=inference,
            )

        self._show_expression(FaceExpression.THINKING)
        try:
            if self.agent is None:
                self._report_progress("Checking available computer controls…", on_progress)
                message = (
                    "That needs access to your computer or live information, so I've "
                    "routed it to my agent side. My safe tools aren't connected, so I "
                    "haven't changed anything."
                )
                agent_response = None
            else:
                def report_agent_progress(message: str) -> None:
                    if cancel_event.is_set():
                        raise AssistantTurnCancelled("The assistant turn was replaced")
                    self._report_progress(message, on_progress)
                    if cancel_event.is_set():
                        raise AssistantTurnCancelled("The assistant turn was replaced")

                agent_response = self.agent.respond(
                    prompt,
                    on_progress=report_agent_progress,
                )
                message = agent_response.text
                # Never tear through a tool's side-effect. Honour replacement only
                # after the tool returns to this safe boundary.
                if cancel_event.is_set():
                    raise AssistantTurnCancelled("The assistant turn was replaced")
                if (
                    agent_response.tool_result is not None
                    and agent_response.tool_result.status
                    in {ToolStatus.FAILED, ToolStatus.TIMED_OUT}
                ):
                    self._show_expression(FaceExpression.ERROR)
                else:
                    self._show_expression(
                        FaceExpression.THINKING if spoken else FaceExpression.SPEAKING
                    )
            if on_token is not None:
                on_token(message)
            if agent_response is None or agent_response.task is None:
                self.chat.history.record(prompt.strip(), message)
            self._consider_user_memory(prompt)
        finally:
            self._show_expression(FaceExpression.IDLE)
        return AssistantResponse(text=message, route=decision, agent=agent_response)

    def _show_expression(self, expression: FaceExpression) -> None:
        self.coordinator.publish(
            RonEvent(EventType.FACE_EXPRESSION, {"expression": expression.value})
        )

    def _report_progress(
        self,
        message: str,
        turn_listener: ProgressListener | None = None,
    ) -> None:
        with self._progress_lock:
            listeners = tuple(self._progress_listeners)
        for listener in listeners:
            try:
                listener(message)
            except Exception:
                self._logger.debug("Assistant progress listener failed", exc_info=True)
        if turn_listener is not None and turn_listener not in listeners:
            try:
                turn_listener(message)
            except Exception:
                self._logger.debug("Voice progress listener failed", exc_info=True)

    def _record_task_completion(self, snapshot: AgentTaskSnapshot) -> None:
        result = f"Task {snapshot.task_id} {snapshot.status.value}: {snapshot.message}"
        self.chat.history.record(snapshot.prompt, result)
        self._consider_user_memory(snapshot.prompt)

    def _record_reminder(self, reminder: object) -> None:
        reminder_id = getattr(reminder, "reminder_id", "?")
        message = getattr(reminder, "message", "Reminder finished")
        prompt = f"[Reminder {reminder_id}]"
        self.chat.history.record(prompt, str(message))

    def _consider_user_memory(self, user: str) -> None:
        if self.memory is None:
            return
        try:
            self.memory.consider_user_statement(user)
        except Exception:
            # Memory must never turn a successful user action into a failed response.
            self._logger.warning("Long-term memory learning failed safely", exc_info=True)
