"""One entry point that routes every prompt while chat remains user-facing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock

from ron.agent import AgentResponse, AgentService, AgentTaskSnapshot, ToolStatus
from ron.ai import InferenceResult
from ron.chat import ChatService
from ron.core import Coordinator, EventType, FaceExpression, RonEvent
from ron.routing import (
    PromptRouter,
    RouteDestination,
    RouteSource,
    RoutingDecision,
)

TokenHandler = Callable[[str], None]


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
    ) -> None:
        self.coordinator = coordinator
        self.chat = chat
        self.router = router
        self.agent = agent
        self.last_route: RoutingDecision | None = None
        self._turn_lock = RLock()
        if self.agent is not None:
            self.agent.add_task_listener(self._record_task_completion)
            if self.agent.reminders is not None:
                self.agent.reminders.add_listener(self._record_reminder)

    def decide(self, prompt: str) -> RoutingDecision:
        with self._turn_lock:
            return self._decide_locked(prompt)

    def _decide_locked(self, prompt: str) -> RoutingDecision:
        if self.agent is not None and self.agent.claims_interaction(prompt):
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
        self, prompt: str, on_token: TokenHandler | None = None
    ) -> AssistantResponse:
        # Terminal and microphone threads share one bounded turn at a time. This
        # prevents Whisper, routing and agent state from racing one another.
        with self._turn_lock:
            return self._respond_locked(prompt, on_token)

    def _respond_locked(
        self, prompt: str, on_token: TokenHandler | None = None
    ) -> AssistantResponse:
        decision = self._decide_locked(prompt)
        if decision.destination is RouteDestination.CHAT:
            inference = self.chat.respond(prompt, on_token=on_token)
            return AssistantResponse(
                text=inference.text,
                route=decision,
                inference=inference,
            )

        self._show_expression(FaceExpression.THINKING)
        try:
            if self.agent is None:
                message = (
                    "That needs access to your computer or live information, so I've "
                    "routed it to my agent side. My safe tools aren't connected, so I "
                    "haven't changed anything."
                )
                agent_response = None
            else:
                agent_response = self.agent.respond(prompt)
                message = agent_response.text
                if (
                    agent_response.tool_result is not None
                    and agent_response.tool_result.status
                    in {ToolStatus.FAILED, ToolStatus.TIMED_OUT}
                ):
                    self._show_expression(FaceExpression.ERROR)
                else:
                    self._show_expression(FaceExpression.SPEAKING)
            if on_token is not None:
                on_token(message)
            if agent_response is None or agent_response.task is None:
                self.chat.history.record(prompt.strip(), message)
        finally:
            self._show_expression(FaceExpression.IDLE)
        return AssistantResponse(text=message, route=decision, agent=agent_response)

    def _show_expression(self, expression: FaceExpression) -> None:
        self.coordinator.publish(
            RonEvent(EventType.FACE_EXPRESSION, {"expression": expression.value})
        )

    def _record_task_completion(self, snapshot: AgentTaskSnapshot) -> None:
        self.chat.history.record(
            snapshot.prompt,
            f"Task {snapshot.task_id} {snapshot.status.value}: {snapshot.message}",
        )

    def _record_reminder(self, reminder: object) -> None:
        reminder_id = getattr(reminder, "reminder_id", "?")
        message = getattr(reminder, "message", "Reminder finished")
        self.chat.history.record(
            f"[Reminder {reminder_id}]",
            str(message),
        )
