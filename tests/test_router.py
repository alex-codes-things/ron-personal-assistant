from ron.ai import (
    InferenceMetrics,
    InferenceResult,
    OllamaConnectionError,
)
from ron.assistant import RonAssistant
from ron.chat import ChatService, ChatSettings
from ron.core import Coordinator
from ron.routing import PromptRouter, RouteDestination, RouteSource


def inference(text: str) -> InferenceResult:
    return InferenceResult(
        model="test-model",
        text=text,
        done_reason="stop",
        metrics=InferenceMetrics(
            first_token_seconds=0.1,
            elapsed_seconds=0.2,
            total_duration_seconds=0.2,
            load_duration_seconds=0.0,
            prompt_tokens=10,
            output_tokens=1,
            tokens_per_second=20.0,
        ),
    )


class NeverCalledClient:
    def stream_chat(self, *args, **kwargs):
        del args, kwargs
        raise AssertionError("An obvious route should not call the local model")


class ClassifierClient:
    def __init__(self, label: str) -> None:
        self.label = label
        self.calls = 0

    def stream_chat(self, *args, **kwargs):
        del args, kwargs
        self.calls += 1
        return inference(self.label)


class FailingClassifierClient:
    def stream_chat(self, *args, **kwargs):
        del args, kwargs
        raise OllamaConnectionError("offline")


class CountingChatClient:
    def __init__(self) -> None:
        self.calls = 0

    def stream_chat(self, *args, **kwargs):
        del args, kwargs
        self.calls += 1
        return inference("chat response")


def test_time_is_immediately_routed_to_agent() -> None:
    decision = PromptRouter(NeverCalledClient()).route("What is the time right now?")
    assert decision.destination is RouteDestination.AGENT
    assert decision.source is RouteSource.DETERMINISTIC


def test_natural_time_variants_are_all_agent_work() -> None:
    router = PromptRouter(NeverCalledClient())
    prompts = (
        "Whats the time?",
        "What's the time?",
        "What’s the current time?",
        "What is the time right now?",
        "What time is it?",
        "Tell me the time",
        "Current time",
    )
    for prompt in prompts:
        decision = router.route(prompt)
        assert decision.destination is RouteDestination.AGENT, prompt
        assert decision.source is RouteSource.DETERMINISTIC, prompt


def test_natural_date_variants_are_all_agent_work() -> None:
    router = PromptRouter(NeverCalledClient())
    prompts = (
        "Whats the date?",
        "What's today's date?",
        "What’s todays date?",
        "What is the current date?",
        "What day is it?",
        "Tell me today's date",
        "Current date",
    )
    for prompt in prompts:
        decision = router.route(prompt)
        assert decision.destination is RouteDestination.AGENT, prompt


def test_direct_application_action_is_immediately_routed_to_agent() -> None:
    decision = PromptRouter(NeverCalledClient()).route("Open Spotify for me")
    assert decision.destination is RouteDestination.AGENT
    assert decision.requires_confirmation is False


def test_destructive_action_is_marked_for_confirmation() -> None:
    decision = PromptRouter(NeverCalledClient()).route("Delete the downloads folder")
    assert decision.destination is RouteDestination.AGENT
    assert decision.requires_confirmation is True


def test_polite_destructive_action_is_also_marked_for_confirmation() -> None:
    decision = PromptRouter(NeverCalledClient()).route(
        "Could you delete the downloads folder?"
    )
    assert decision.destination is RouteDestination.AGENT
    assert decision.requires_confirmation is True


def test_explanation_about_an_action_remains_chat() -> None:
    decision = PromptRouter(NeverCalledClient()).route("How can I open a Python file?")
    assert decision.destination is RouteDestination.CHAT


def test_obvious_conversation_avoids_classifier_latency() -> None:
    decision = PromptRouter(NeverCalledClient()).route("How are you today?")
    assert decision.destination is RouteDestination.CHAT
    assert decision.confidence >= 0.9


def test_live_battery_level_is_agent_work() -> None:
    decision = PromptRouter(NeverCalledClient()).route(
        "What is my battery percentage?"
    )
    assert decision.destination is RouteDestination.AGENT


def test_listing_files_is_agent_work() -> None:
    decision = PromptRouter(NeverCalledClient()).route(
        "What files are in my downloads folder?"
    )
    assert decision.destination is RouteDestination.AGENT


def test_task_status_and_cancellation_are_immediately_routed_to_agent() -> None:
    router = PromptRouter(NeverCalledClient())
    for prompt in ("How is task 1 going?", "Cancel task 1", "Show my tasks"):
        decision = router.route(prompt)
        assert decision.destination is RouteDestination.AGENT, prompt
        assert decision.source is RouteSource.DETERMINISTIC, prompt


def test_ambiguous_request_uses_local_classifier() -> None:
    client = ClassifierClient("AGENT")
    decision = PromptRouter(client).route("Could you find the file I downloaded?")
    assert client.calls == 1
    assert decision.destination is RouteDestination.AGENT
    assert decision.source is RouteSource.LOCAL_MODEL


def test_classifier_failure_chooses_non_executing_chat_path() -> None:
    decision = PromptRouter(FailingClassifierClient()).route(
        "Could you find something useful?"
    )
    assert decision.destination is RouteDestination.CHAT
    assert decision.source is RouteSource.SAFE_FALLBACK


def test_agent_placeholder_does_not_call_chat_model() -> None:
    coordinator = Coordinator()
    chat_client = CountingChatClient()
    chat = ChatService(coordinator, client=chat_client, settings=ChatSettings())
    assistant = RonAssistant(coordinator, chat, PromptRouter(NeverCalledClient()))
    chunks: list[str] = []

    response = assistant.respond("Open Spotify", on_token=chunks.append)

    assert response.route.destination is RouteDestination.AGENT
    assert chat_client.calls == 0
    assert "safe tools aren't connected" in response.text
    assert chunks == [response.text]
