import threading

from ron.assistant import AssistantTurnCancelled, RonAssistant
from ron.chat import ChatService, ChatSettings
from ron.core import Coordinator
from ron.routing import RouteDestination, RouteSource, RoutingDecision


class UnusedClient:
    def stream_chat(self, *args, **kwargs):
        del args, kwargs
        raise AssertionError("Cancellation should happen before chat inference")


class AgentAtSafeBoundary:
    reminders = None

    def __init__(self) -> None:
        self.executed = False

    def add_task_listener(self, listener) -> None:
        del listener

    def claims_interaction(self, prompt: str) -> bool:
        del prompt
        return False

    def respond(self, prompt: str, *, on_progress):
        del prompt
        on_progress("Running: opening the application…")
        self.executed = True
        raise AssertionError("A cancelled action must not start past the safe boundary")


class AgentRouter:
    def route(self, prompt: str) -> RoutingDecision:
        del prompt
        return RoutingDecision(
            RouteDestination.AGENT,
            1.0,
            "Test routes directly to the agent.",
            RouteSource.DETERMINISTIC,
        )


def test_replacement_cancels_before_tool_side_effect_starts() -> None:
    coordinator = Coordinator()
    agent = AgentAtSafeBoundary()
    assistant = RonAssistant(
        coordinator,
        ChatService(coordinator, client=UnusedClient(), settings=ChatSettings()),
        AgentRouter(),  # type: ignore[arg-type]
        agent=agent,  # type: ignore[arg-type]
    )
    reached_boundary = threading.Event()
    release_boundary = threading.Event()

    def pause_at_boundary(message: str) -> None:
        if "Running:" in message:
            reached_boundary.set()
            release_boundary.wait(1.0)

    assistant.add_progress_listener(pause_at_boundary)
    errors: list[BaseException] = []
    thread = threading.Thread(
        target=lambda: _capture_error(
            lambda: assistant.respond("open Spotify", spoken=True), errors
        )
    )
    thread.start()

    assert reached_boundary.wait(1.0)
    assert assistant.cancel_current_turn()
    release_boundary.set()
    thread.join(1.0)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], AssistantTurnCancelled)
    assert not agent.executed


def _capture_error(operation, errors: list[BaseException]) -> None:
    try:
        operation()
    except BaseException as error:
        errors.append(error)
