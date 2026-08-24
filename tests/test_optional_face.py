from io import StringIO
from pathlib import Path

from ron.core import Coordinator
from ron.display.face import TabletFaceDisplay
from ron.display.tablet_client import ConnectionStatus, FaceConnectionUpdate
from ron.terminal import TerminalChat


class FakeFaceClient:
    def __init__(self, *, fail_start: bool = False) -> None:
        self.status = ConnectionStatus.STOPPED
        self.fail_start = fail_start
        self.listeners = []

    def add_status_listener(self, listener) -> None:
        self.listeners.append(listener)

    def start(self) -> None:
        if self.fail_start:
            raise RuntimeError("simulated face startup failure")

    def stop(self) -> None:
        self.status = ConnectionStatus.STOPPED

    def emit(self, status: ConnectionStatus, detail: str = "") -> None:
        self.status = status
        update = FaceConnectionUpdate(status, detail)
        for listener in tuple(self.listeners):
            listener(update)

    def set_expression(self, expression) -> None:
        del expression

    def speech_started(self) -> None:
        pass

    def set_speech_level(self, level: float) -> None:
        del level

    def speech_ended(self) -> None:
        pass


def _display(client: FakeFaceClient) -> TabletFaceDisplay:
    return TabletFaceDisplay(
        Coordinator(),
        Path("."),
        client=client,
        reminder_interval_seconds=10_000.0,
    )


def test_face_offline_warning_is_restrained_and_reconnect_is_announced() -> None:
    client = FakeFaceClient()
    display = _display(client)
    notices: list[str] = []
    display.add_notice_listener(notices.append)

    client.emit(ConnectionStatus.RETRYING, "tablet missing")
    client.emit(ConnectionStatus.RETRYING, "tablet missing")
    client.emit(ConnectionStatus.READY)
    client.emit(ConnectionStatus.RETRYING, "cable removed")

    assert len(notices) == 3
    assert notices[0].startswith("[FACE OFFLINE]")
    assert notices[1].startswith("[FACE CONNECTED]")
    assert notices[2].startswith("[FACE OFFLINE]")


def test_face_permission_change_does_not_repeat_notice_during_same_outage() -> None:
    client = FakeFaceClient()
    display = _display(client)
    notices: list[str] = []
    display.add_notice_listener(notices.append)

    client.emit(ConnectionStatus.RETRYING, "tablet missing")
    client.emit(ConnectionStatus.UNAUTHORIZED, "approve debugging")
    client.emit(ConnectionStatus.UNAUTHORIZED, "approve debugging")

    assert len(notices) == 1
    assert notices[0].startswith("[FACE OFFLINE]")


def test_face_startup_failure_does_not_stop_ron() -> None:
    display = _display(FakeFaceClient(fail_start=True))
    notices: list[str] = []
    display.add_notice_listener(notices.append)

    display.start()

    assert notices
    assert "still fully working" in notices[0]


def test_zero_face_reminder_interval_never_repeats_offline_notice() -> None:
    client = FakeFaceClient()
    display = TabletFaceDisplay(
        Coordinator(),
        Path("."),
        client=client,
        reminder_interval_seconds=0.0,
    )
    notices: list[str] = []
    display.add_notice_listener(notices.append)

    display._announce_offline(force=False)
    display._last_offline_notice = -1_000_000.0
    display._announce_offline(force=False)

    assert len(notices) == 1


def test_terminal_prints_queued_face_notice_without_live_console() -> None:
    class FakeChat:
        continuous = False

    class FakeAssistant:
        chat = FakeChat()
        agent = None

    output = StringIO()
    terminal = TerminalChat(
        FakeAssistant(),
        input_reader=lambda prompt: "/quit",
        output=output,
    )
    terminal.post_system_notice("[FACE OFFLINE] Ron is still fully working.")

    assert terminal.run() == 0
    text = output.getvalue()
    assert "Ready. Type a message" in text
    assert "FACE OFFLINE  ·  Ron is still fully working." in text
