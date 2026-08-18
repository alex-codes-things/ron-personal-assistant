import threading
import time
from ron.agent import (
    AgentPlan,
    AgentPlanSource,
    AgentPlanner,
    AgentService,
    AgentTaskManager,
    AgentTaskStatus,
    ToolArgument,
    ToolArgumentKind,
    ToolRegistry,
    ToolResult,
    ToolRisk,
    ToolSpec,
    ToolStatus,
)
from ron.agent.tools.spotify import build_spotify_tool
from ron.integrations.spotify import SpotifyClient, SpotifyTrack


class NeverCalledClient:
    def stream_chat(self, *args, **kwargs):
        del args, kwargs
        raise AssertionError("Deterministic planning should not call the model")


def _plan(tool: str, arguments: dict[str, object]) -> AgentPlan:
    return AgentPlan(tool, arguments, "test", AgentPlanSource.DETERMINISTIC)


def test_multi_step_preflight_rejects_everything_before_first_action() -> None:
    calls: list[str] = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "control_volume",
            "Set a test volume.",
            {
                "action": ToolArgument(ToolArgumentKind.ENUM, choices=("set",)),
                "level": ToolArgument(ToolArgumentKind.INTEGER, minimum=0, maximum=100),
            },
            ToolRisk.REVERSIBLE,
            lambda arguments: (
                calls.append("volume")
                or ToolResult("control_volume", ToolStatus.SUCCESS, str(arguments))
            ),
        )
    )
    service = AgentService(AgentPlanner(NeverCalledClient(), registry), registry)

    response = service.respond(
        "Play Galway Girl by Ed Sheeran and set volume to 0 percent"
    )

    assert "No step was run" in response.text
    assert response.tool_result is not None
    assert response.tool_result.status is ToolStatus.UNSUPPORTED
    assert calls == []


def test_valid_multi_step_task_reports_completion() -> None:
    calls: list[str] = []
    registry = ToolRegistry()
    for name in ("first", "second"):
        registry.register(
            ToolSpec(
                name,
                f"Run {name} test step.",
                {},
                ToolRisk.REVERSIBLE,
                lambda arguments, step=name: (
                    calls.append(step)
                    or ToolResult(step, ToolStatus.SUCCESS, f"{step} done.")
                ),
            )
        )
    manager = AgentTaskManager(registry)
    finished = threading.Event()
    manager.add_listener(lambda snapshot: finished.set())
    snapshot = manager.submit("do both", (_plan("first", {}), _plan("second", {})))

    assert finished.wait(2.0)
    final = manager.snapshot(snapshot.task_id)
    manager.stop()

    assert final is not None
    assert final.status is AgentTaskStatus.COMPLETED
    assert final.completed_steps == 2
    assert calls == ["first", "second"]


def test_running_task_cancels_before_next_step() -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    finished = threading.Event()
    calls: list[str] = []

    def first(arguments):
        del arguments
        calls.append("first")
        first_started.set()
        assert release_first.wait(2.0)
        return ToolResult("first", ToolStatus.SUCCESS, "first done")

    registry = ToolRegistry()
    registry.register(ToolSpec("first", "First step.", {}, ToolRisk.REVERSIBLE, first))
    registry.register(
        ToolSpec(
            "second",
            "Second step.",
            {},
            ToolRisk.REVERSIBLE,
            lambda arguments: (
                calls.append("second")
                or ToolResult("second", ToolStatus.SUCCESS, "second done")
            ),
        )
    )
    manager = AgentTaskManager(registry)
    manager.add_listener(lambda snapshot: finished.set())
    submitted = manager.submit("cancel me", (_plan("first", {}), _plan("second", {})))
    assert first_started.wait(2.0)
    cancelled = manager.cancel(submitted.task_id)
    assert cancelled is not None and cancelled.cancel_requested
    release_first.set()
    assert finished.wait(2.0)
    final = manager.snapshot(submitted.task_id)
    manager.stop()

    assert final is not None and final.status is AgentTaskStatus.CANCELLED
    assert calls == ["first"]


def test_spotify_query_uses_track_and_artist_filters() -> None:
    assert SpotifyClient._structured_query("Galway Girl by Ed Sheeran") == (
        "track:Galway Girl artist:Ed Sheeran"
    )


def test_spotify_tool_validates_query_and_reports_exact_track() -> None:
    class FakeSpotify:
        def availability(self):
            return True, "ready"

        def search_and_play(self, query: str):
            assert query == "Galway Girl by Ed Sheeran"
            return SpotifyTrack(
                "Galway Girl", ("Ed Sheeran",), "spotify:track:allowed"
            )

    registry = ToolRegistry()
    registry.register(build_spotify_tool(lambda: FakeSpotify()))
    result = registry.execute(
        "spotify_play_track", {"query": "Galway Girl by Ed Sheeran"}
    )
    rejected = registry.execute("spotify_play_track", {"query": "x" * 201})

    assert result.status is ToolStatus.SUCCESS
    assert result.message == "Playing Galway Girl by Ed Sheeran on Spotify."
    assert rejected.status is ToolStatus.FAILED


def test_named_spotify_request_runs_as_background_task() -> None:
    class FakeSpotify:
        def availability(self):
            return True, "ready"

        def search_and_play(self, query: str):
            return SpotifyTrack(query, ("Artist",), "spotify:track:allowed")

    registry = ToolRegistry()
    registry.register(build_spotify_tool(lambda: FakeSpotify()))
    service = AgentService(AgentPlanner(NeverCalledClient(), registry), registry)

    response = service.respond("Play A Test Song")
    assert response.task is not None
    assert "queued" in response.text
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        final = service.task_snapshot(response.task.task_id)
        if final is not None and final.status is AgentTaskStatus.COMPLETED:
            break
        time.sleep(0.01)
    else:
        raise AssertionError("Spotify task did not finish")
    service.stop()
