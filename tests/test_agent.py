from datetime import datetime, timedelta, timezone

from ron.agent import (
    AgentPlan,
    AgentPlanner,
    AgentPlanSource,
    AgentService,
    ToolArgument,
    ToolArgumentKind,
    ToolRegistry,
    ToolResult,
    ToolRisk,
    ToolSpec,
    ToolStatus,
)
from ron.agent.tools.clock import build_time_tool
from ron.agent.tools.media import build_media_tool
from ron.ai import InferenceMetrics, InferenceResult
from ron.assistant import RonAssistant
from ron.chat import ChatService, ChatSettings
from ron.core import Coordinator
from ron.routing import PromptRouter, RouteDestination


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
            output_tokens=5,
            tokens_per_second=20.0,
        ),
    )


class PlannerClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def stream_chat(self, *args, **kwargs):
        del args, kwargs
        self.calls += 1
        return inference(self.response)


class NeverCalledClient:
    def __init__(self) -> None:
        self.calls = 0

    def stream_chat(self, *args, **kwargs):
        del args, kwargs
        self.calls += 1
        raise AssertionError("The local model should not be needed")


def test_registry_rejects_unknown_and_out_of_range_arguments() -> None:
    calls: list[int] = []

    def handler(arguments):
        calls.append(int(arguments["level"]))
        return ToolResult("volume", ToolStatus.SUCCESS, "done")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="volume",
            description="Test bounded integer arguments.",
            arguments={"level": ToolArgument(ToolArgumentKind.INTEGER, minimum=0, maximum=100)},
            risk=ToolRisk.REVERSIBLE,
            handler=handler,
        )
    )

    assert registry.execute("volume", {"level": 101}).status is ToolStatus.FAILED
    assert registry.execute("volume", {"level": 20, "extra": 1}).status is ToolStatus.FAILED
    assert calls == []


def test_registry_confirmation_gate_runs_nothing_before_confirmation() -> None:
    calls: list[str] = []

    def handler(arguments):
        del arguments
        calls.append("ran")
        return ToolResult("dangerous", ToolStatus.SUCCESS, "done")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="dangerous",
            description="Test confirmation behavior.",
            arguments={},
            risk=ToolRisk.DESTRUCTIVE,
            handler=handler,
            requires_confirmation=True,
        )
    )

    blocked = registry.execute("dangerous", {})
    assert blocked.status is ToolStatus.CONFIRMATION_REQUIRED
    assert calls == []
    allowed = registry.execute("dangerous", {}, confirmed=True)
    assert allowed.status is ToolStatus.SUCCESS
    assert calls == ["ran"]


def test_time_request_uses_tool_without_calling_model() -> None:
    local_zone = timezone(timedelta(hours=2), "SAST")
    fixed_time = datetime(2026, 8, 14, 12, 30, tzinfo=local_zone)
    client = NeverCalledClient()
    registry = ToolRegistry()
    registry.register(build_time_tool(lambda: fixed_time))
    planner = AgentPlanner(client, registry)
    agent = AgentService(planner, registry)
    coordinator = Coordinator()
    chat = ChatService(coordinator, client=client, settings=ChatSettings())
    assistant = RonAssistant(coordinator, chat, PromptRouter(client), agent=agent)

    response = assistant.respond("Whats the time?")

    assert response.route.destination is RouteDestination.AGENT
    assert response.text == "It's 12:30 PM (SAST)."
    assert response.agent is not None
    assert response.agent.tool_result is not None
    assert response.agent.tool_result.status is ToolStatus.SUCCESS
    assert client.calls == 0


def test_unpause_song_uses_fast_media_action_without_background_queue() -> None:
    keys: list[int] = []
    client = NeverCalledClient()
    registry = ToolRegistry()
    registry.register(build_media_tool(keys.append))
    service = AgentService(AgentPlanner(client, registry), registry)
    progress: list[str] = []

    response = service.respond("Unpause the song", on_progress=progress.append)

    assert response.task is None
    assert response.tool_result is not None
    assert response.tool_result.status is ToolStatus.SUCCESS
    assert keys
    assert any("controlling the current media" in item for item in progress)
    assert any("Completed request" in item for item in progress)
    assert client.calls == 0


def test_recent_action_context_resolves_pause_it_to_same_provider() -> None:
    client = NeverCalledClient()
    registry = ToolRegistry()
    planner = AgentPlanner(client, registry)
    planner.record_success(
        (
            AgentPlan(
                "spotify_play_track",
                {"query": "Galway Girl"},
                "test",
                AgentPlanSource.DETERMINISTIC,
            ),
        )
    )

    plan = planner.plan("pause it")

    assert plan.tool_name == "spotify_control_playback"
    assert plan.arguments == {"action": "pause"}
    assert client.calls == 0


def test_context_finds_relevant_media_action_after_unrelated_success() -> None:
    client = NeverCalledClient()
    registry = ToolRegistry()
    planner = AgentPlanner(client, registry)
    planner.record_success(
        (
            AgentPlan(
                "spotify_play_track",
                {"query": "Galway Girl"},
                "test",
                AgentPlanSource.DETERMINISTIC,
            ),
        ),
        prompt="Play Galway Girl",
    )
    planner.record_success(
        (
            AgentPlan(
                "control_volume",
                {"action": "set", "level": 30},
                "test",
                AgentPlanSource.DETERMINISTIC,
            ),
        ),
        prompt="Set volume to 30",
    )

    plan = planner.plan("pause it")

    assert plan.tool_name == "spotify_control_playback"
    assert plan.arguments == {"action": "pause"}


def test_planner_schemas_report_live_tool_availability() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="offline_tool",
            description="A test capability that is currently unavailable.",
            arguments={},
            risk=ToolRisk.READ_ONLY,
            handler=lambda arguments: ToolResult(
                "offline_tool", ToolStatus.SUCCESS, str(arguments)
            ),
            availability=lambda: (False, "device disconnected"),
        )
    )

    schema = registry.planner_schemas()[0]

    assert schema["available"] is False
    assert schema["availability_reason"] == "device disconnected"


def test_model_planner_accepts_only_registered_structured_call() -> None:
    client = PlannerClient(
        'Here is the plan: {"tool":"open_application","arguments":{"application":"calculator"}}'
    )
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="open_application",
            description="Open a test application.",
            arguments={"application": ToolArgument(ToolArgumentKind.ENUM, choices=("calculator",))},
            risk=ToolRisk.REVERSIBLE,
            handler=lambda arguments: ToolResult(
                "open_application", ToolStatus.SUCCESS, str(arguments)
            ),
        )
    )

    plan = AgentPlanner(client, registry).plan("Could you launch the application I use for sums?")

    assert plan.tool_name == "open_application"
    assert plan.arguments == {"application": "calculator"}
    assert client.calls == 1


def test_model_planner_rejects_invented_tool() -> None:
    client = PlannerClient('{"tool":"run_shell","arguments":{"command":"anything"}}')
    registry = ToolRegistry()

    plan = AgentPlanner(client, registry).plan("Do a complicated computer task")

    assert plan.tool_name is None


def test_planner_refuses_to_partially_run_multiple_actions() -> None:
    client = NeverCalledClient()
    registry = ToolRegistry()

    plan = AgentPlanner(client, registry).plan("Open Spotify and then play Galway Girl")

    assert plan.tool_name is None
    assert "partially execute" in plan.reason
    assert client.calls == 0
