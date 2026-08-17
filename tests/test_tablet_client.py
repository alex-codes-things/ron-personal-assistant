import json
import socket
from pathlib import Path
from tempfile import TemporaryDirectory

from ron.core import FaceExpression
from ron.display.tablet_client import (
    ConnectionStatus,
    FaceConnectionUpdate,
    TabletClientConfig,
    TabletFaceClient,
)
from ron.display.quick_actions import QuickActionResult


def create_client(directory: Path) -> TabletFaceClient:
    return TabletFaceClient(
        TabletClientConfig(
            token_file=directory / "pairing_token",
            serial_file=directory / "tablet_serial.json",
        )
    )


def test_pairing_token_is_created_once_and_reused() -> None:
    with TemporaryDirectory() as directory_name:
        directory = Path(directory_name)
        first = create_client(directory)
        second = create_client(directory)

        assert first._pairing_token == second._pairing_token
        assert len(first._pairing_token) >= 32


def test_snapshot_tracks_speech_lifecycle() -> None:
    with TemporaryDirectory() as directory_name:
        client = create_client(Path(directory_name))

        client.set_expression(FaceExpression.HAPPY)
        assert client.snapshot().expression is FaceExpression.HAPPY

        client.speech_started()
        speaking = client.snapshot()
        assert speaking.expression is FaceExpression.SPEAKING
        assert speaking.speech_active is True

        client.set_speech_level(5.0)
        assert client.snapshot().speech_level == 1.0

        client.speech_ended()
        ended = client.snapshot()
        assert ended.expression is FaceExpression.IDLE
        assert ended.speech_active is False
        assert ended.speech_level == 0.0


def test_tablet_wake_updates_future_snapshot_state() -> None:
    with TemporaryDirectory() as directory_name:
        client = create_client(Path(directory_name))
        client.set_expression(FaceExpression.SLEEPING)

        client._handle_face_wake()

        snapshot = client.snapshot()
        assert snapshot.expression is FaceExpression.IDLE
        assert snapshot.speech_active is False
        assert snapshot.speech_level == 0.0


def test_tablet_quick_action_returns_a_correlated_result() -> None:
    with TemporaryDirectory() as directory_name:
        requested: list[str] = []

        def handle(action: str) -> QuickActionResult:
            requested.append(action)
            return QuickActionResult(True, "Opening Spotify")

        client = TabletFaceClient(
            TabletClientConfig(
                token_file=Path(directory_name) / "pairing_token",
                serial_file=Path(directory_name) / "tablet_serial.json",
            ),
            quick_action_handler=handle,
        )
        sender, receiver = socket.socketpair()
        try:
            client._handle_quick_action(
                sender,
                {
                    "type": "quick_action",
                    "request_id": 42,
                    "action": "open_spotify",
                },
            )
            response = json.loads(receiver.recv(4096).decode("utf-8"))
        finally:
            sender.close()
            receiver.close()

        assert requested == ["open_spotify"]
        assert response == {
            "type": "quick_action_result",
            "request_id": 42,
            "status": "success",
            "message": "Opening Spotify",
        }


def test_connection_update_restores_face_warning_compatibility() -> None:
    with TemporaryDirectory() as directory_name:
        client = create_client(Path(directory_name))
        updates: list[FaceConnectionUpdate] = []
        client.add_status_listener(updates.append)

        client._set_status(ConnectionStatus.WAITING_FOR_DEVICE)
        client._set_status(ConnectionStatus.RETRYING)
        client._set_status(ConnectionStatus.RETRYING)
        client._set_status(ConnectionStatus.READY)

        assert [update.status for update in updates] == [
            ConnectionStatus.WAITING_FOR_DEVICE,
            ConnectionStatus.RETRYING,
            ConnectionStatus.READY,
        ]
        assert updates[-1].previous_status is ConnectionStatus.RETRYING


def test_faulty_status_listener_does_not_break_other_listeners() -> None:
    with TemporaryDirectory() as directory_name:
        client = create_client(Path(directory_name))
        received: list[ConnectionStatus] = []

        def fail(update: FaceConnectionUpdate) -> None:
            del update
            raise RuntimeError("simulated display listener failure")

        client.add_status_listener(fail)
        client.add_status_listener(lambda update: received.append(update.status))
        client._set_status(ConnectionStatus.RETRYING)

        assert received == [ConnectionStatus.RETRYING]
