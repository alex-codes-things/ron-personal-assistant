from pathlib import Path
from tempfile import TemporaryDirectory

from ron.core import FaceExpression
from ron.display.tablet_client import TabletClientConfig, TabletFaceClient


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
