import threading
import time
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from ron.agent import (
    AgentPlan,
    AgentPlanner,
    AgentPlanSource,
    AgentService,
    AgentTaskManager,
    AgentTaskSnapshot,
    AgentTaskStatus,
    ToolArgument,
    ToolArgumentKind,
    ToolRegistry,
    ToolResult,
    ToolRisk,
    ToolSpec,
    ToolStatus,
)
from ron.agent.journal import AgentTaskJournal
from ron.agent.models import AgentTaskPlan
from ron.agent.tools.spotify import build_spotify_tool
from ron.ai import InferencePriority, InferenceScheduler
from ron.integrations.spotify import SpotifyTrack
from ron.reminders import ReminderManager
from ron.terminal import TerminalChat


class NeverCalledClient:
    def stream_chat(self, *args, **kwargs):
        del args, kwargs
        raise AssertionError("Deterministic planning should not call the model")


def _plan(tool: str, arguments: dict[str, object]) -> AgentPlan:
    return AgentPlan(tool, arguments, "test", AgentPlanSource.DETERMINISTIC)


def _wait_for_task(
    manager: AgentTaskManager,
    task_id: int,
    states: set[AgentTaskStatus],
    timeout: float = 2.0,
) -> AgentTaskSnapshot:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = manager.snapshot(task_id)
        if snapshot is not None and snapshot.status in states:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"Task {task_id} did not reach {states}")


def test_planner_builds_three_natural_steps_and_number_words() -> None:
    planner = AgentPlanner(NeverCalledClient(), ToolRegistry())

    task = planner.plan_steps(
        "Open Spotify, put the volume around twenty, then play Galway Girl."
    )

    assert [(step.tool_name, step.arguments) for step in task.steps] == [
        ("open_application", {"application": "spotify"}),
        ("control_volume", {"action": "set", "level": 20}),
        ("spotify_play_track", {"query": "Galway Girl"}),
    ]


def test_planner_preserves_timer_and_reminder_messages() -> None:
    planner = AgentPlanner(NeverCalledClient(), ToolRegistry())

    timer = planner.plan_steps("Set a timer for five minutes for tea").steps[0]
    reminder = planner.plan_steps(
        "Remind me in two hours to practice guitar"
    ).steps[0]

    assert timer.arguments == {"seconds": 300, "message": "tea"}
    assert reminder.arguments == {"seconds": 7_200, "message": "practice guitar"}


def test_registry_enforces_deadline_cancellation_and_output_limit() -> None:
    registry = ToolRegistry()

    def slow(
        arguments: dict[str, str | int], context: object
    ) -> ToolResult:
        del arguments
        time.sleep(0.11)
        context.checkpoint()
        return ToolResult("slow", ToolStatus.SUCCESS, "unexpected")

    registry.register(
        ToolSpec(
            "slow",
            "Cooperatively exceed a test deadline.",
            {},
            ToolRisk.READ_ONLY,
            slow,
            timeout_seconds=0.1,
        )
    )
    registry.register(
        ToolSpec(
            "large",
            "Return an oversized structured result.",
            {},
            ToolRisk.READ_ONLY,
            lambda arguments: ToolResult(
                "large", ToolStatus.SUCCESS, "x" * 2_000
            ),
            max_output_bytes=1_024,
        )
    )
    cancelled = threading.Event()
    cancelled.set()

    assert registry.execute("slow", {}).status is ToolStatus.TIMED_OUT
    assert (
        registry.execute("slow", {}, cancel_event=cancelled).status
        is ToolStatus.CANCELLED
    )
    assert registry.execute("large", {}).status is ToolStatus.FAILED


def test_task_timeout_is_distinct_and_completed_steps_roll_back() -> None:
    calls: list[str] = []
    registry = ToolRegistry()

    def first(arguments: dict[str, str | int]) -> ToolResult:
        del arguments
        calls.append("first")
        return ToolResult("first", ToolStatus.SUCCESS, "first done")

    def rollback(result: ToolResult) -> ToolResult:
        del result
        calls.append("rollback")
        return ToolResult("first", ToolStatus.SUCCESS, "first restored")

    def timeout(
        arguments: dict[str, str | int], context: object
    ) -> ToolResult:
        del arguments
        time.sleep(0.11)
        context.checkpoint()
        return ToolResult("timeout", ToolStatus.SUCCESS, "unexpected")

    registry.register(
        ToolSpec(
            "first",
            "Run a reversible first step.",
            {},
            ToolRisk.REVERSIBLE,
            first,
            compensator=rollback,
        )
    )
    registry.register(
        ToolSpec(
            "timeout",
            "Cooperatively exceed a deadline.",
            {},
            ToolRisk.READ_ONLY,
            timeout,
            timeout_seconds=0.1,
        )
    )
    manager = AgentTaskManager(registry)
    submitted = manager.submit(
        "test timeout",
        (_plan("first", {}), _plan("timeout", {})),
    )

    final = _wait_for_task(
        manager,
        submitted.task_id,
        {AgentTaskStatus.TIMED_OUT},
    )
    manager.stop()

    assert final.status is AgentTaskStatus.TIMED_OUT
    assert "Safely rolled back" in final.message
    assert calls == ["first", "rollback"]


def test_restart_marks_interrupted_task_failed_without_resuming() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "tasks.sqlite"
        journal = AgentTaskJournal(path)
        snapshot = AgentTaskSnapshot(
            task_id=7,
            prompt="open something",
            status=AgentTaskStatus.RUNNING,
            total_steps=1,
            completed_steps=0,
            current_tool="safe",
            message="running",
        )
        journal.save(snapshot, (_plan("safe", {}),), confirmed=False)

        manager = AgentTaskManager(ToolRegistry(), journal=journal)
        recovered = manager.snapshot(7)

        assert recovered is not None
        assert recovered.status is AgentTaskStatus.FAILED
        assert recovered.recovered is True
        assert "not resumed automatically" in recovered.message


def test_confirmation_is_exact_and_runs_only_after_confirm() -> None:
    calls: list[str] = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "sensitive",
            "Run a confirmation-gated test action.",
            {},
            ToolRisk.DESTRUCTIVE,
            lambda arguments: (
                calls.append("ran")
                or ToolResult("sensitive", ToolStatus.SUCCESS, "done")
            ),
            requires_confirmation=True,
        )
    )

    class FixedPlanner:
        def plan_steps(self, prompt: str) -> AgentTaskPlan:
            del prompt
            return AgentTaskPlan((_plan("sensitive", {}),), "test")

    service = AgentService(FixedPlanner(), registry)
    pending = service.respond("do the sensitive thing")

    assert pending.tool_result is not None
    assert pending.tool_result.status is ToolStatus.CONFIRMATION_REQUIRED
    assert service.claims_interaction("yes please") is False
    assert service.claims_interaction("confirm") is True
    assert service.respond("confirm").text == "done"
    assert calls == ["ran"]


def test_repeat_supports_a_volume_override() -> None:
    levels: list[int] = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "control_volume",
            "Set an exact test volume.",
            {
                "action": ToolArgument(ToolArgumentKind.ENUM, choices=("set",)),
                "level": ToolArgument(
                    ToolArgumentKind.INTEGER,
                    minimum=0,
                    maximum=100,
                ),
            },
            ToolRisk.REVERSIBLE,
            lambda arguments: (
                levels.append(int(arguments["level"]))
                or ToolResult("control_volume", ToolStatus.SUCCESS, "set")
            ),
        )
    )
    service = AgentService(
        AgentPlanner(NeverCalledClient(), registry),
        registry,
    )

    service.respond("Set the volume to 20 percent")
    service.respond("Do that again, but at 30%")

    assert levels == [20, 30]


def test_spotify_ambiguity_waits_then_resolves_to_selected_track() -> None:
    played: list[str] = []

    class FakeSpotify:
        def availability(self):
            return True, "ready"

        def search_tracks(self, query: str, context: object):
            del query, context
            return (
                SpotifyTrack("Hello", ("Adele",), "spotify:track:one"),
                SpotifyTrack("Hello", ("Lionel Richie",), "spotify:track:two"),
            )

        def play_track(self, track: SpotifyTrack, context: object):
            del context
            played.append(track.uri)

    registry = ToolRegistry()
    registry.register(build_spotify_tool(lambda: FakeSpotify()))
    service = AgentService(AgentPlanner(NeverCalledClient(), registry), registry)

    first = service.respond("Play Hello")
    assert first.task is not None
    waiting = _wait_for_task(
        service.tasks,
        first.task.task_id,
        {AgentTaskStatus.WAITING},
    )
    assert service.claims_interaction("2") is True

    second = service.respond("2")
    assert second.task is not None
    resolved = service.task_snapshot(waiting.task_id)
    final = _wait_for_task(
        service.tasks,
        second.task.task_id,
        {AgentTaskStatus.COMPLETED},
    )
    service.stop()

    assert resolved is not None and resolved.status is AgentTaskStatus.RESOLVED
    assert final.status is AgentTaskStatus.COMPLETED
    assert played == ["spotify:track:two"]


def test_reminders_persist_cancel_and_fire_without_a_model() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "reminders.sqlite"
        manager = ReminderManager(path)
        cancelled = manager.create(60, "do not fire")
        manager.cancel(cancelled.reminder_id)
        manager.start()
        due = manager.create(1, "tea")

        deadline = time.monotonic() + 2.5
        fired = ()
        while time.monotonic() < deadline and not fired:
            fired = manager.drain_notifications()
            time.sleep(0.02)
        manager.stop()

        restored = ReminderManager(path).list_recent()
        states = {item.reminder_id: item.status for item in restored}
        assert states[cancelled.reminder_id] == "cancelled"
        assert states[due.reminder_id] == "fired"
        assert fired and fired[0].message == "tea"


def test_inference_scheduler_prioritises_waiting_conversation() -> None:
    scheduler = InferenceScheduler()
    blocker_started = threading.Event()
    release_blocker = threading.Event()
    order: list[str] = []

    def blocker() -> None:
        blocker_started.set()
        assert release_blocker.wait(2.0)

    background = threading.Thread(
        target=lambda: scheduler.run(InferencePriority.BACKGROUND, blocker)
    )
    background.start()
    assert blocker_started.wait(1.0)

    workers = [
        threading.Thread(
            target=lambda priority=priority, label=label: scheduler.run(
                priority, lambda: order.append(label)
            )
        )
        for priority, label in (
            (InferencePriority.PLANNING, "planning"),
            (InferencePriority.ROUTING, "routing"),
            (InferencePriority.CONVERSATION, "conversation"),
        )
    ]
    for worker in workers:
        worker.start()
    deadline = time.monotonic() + 1.0
    while scheduler.status()[1] < 3 and time.monotonic() < deadline:
        time.sleep(0.005)
    release_blocker.set()
    background.join(2.0)
    for worker in workers:
        worker.join(2.0)

    assert order == ["conversation", "routing", "planning"]


def test_terminal_falls_back_when_windows_console_buffer_is_missing() -> None:
    import ron.terminal as terminal_module

    class FakeTTY(StringIO):
        def isatty(self) -> bool:
            return True

    class BrokenPromptSession:
        def __init__(self) -> None:
            raise RuntimeError("No Windows console screen buffer")

    class FakeAssistant:
        chat = object()
        agent = None

    original_session = terminal_module.PromptSession
    original_stdin = terminal_module.sys.stdin
    original_stdout = terminal_module.sys.stdout
    terminal_module.PromptSession = BrokenPromptSession
    terminal_module.sys.stdin = FakeTTY()
    terminal_module.sys.stdout = FakeTTY()
    try:
        terminal = TerminalChat(FakeAssistant())
    finally:
        terminal_module.PromptSession = original_session
        terminal_module.sys.stdin = original_stdin
        terminal_module.sys.stdout = original_stdout

    assert terminal._live is False
    assert terminal._session is None
