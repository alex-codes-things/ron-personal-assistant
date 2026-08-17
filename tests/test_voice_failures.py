import time
from pathlib import Path

from ron.core import Coordinator
from ron.voice import VoiceReply, VoiceService, VoiceSettings, VoiceState
from ron.voice.audio import VoiceDependencyError


class MissingWakeModel:
    def __init__(self, settings) -> None:
        del settings

    def load(self) -> None:
        raise VoiceDependencyError("simulated missing voice package")


def test_missing_voice_dependency_never_stops_main_application() -> None:
    notices = []
    service = VoiceService(
        Coordinator(),
        VoiceSettings(enabled=True, project_root=Path(".")),
        lambda item: VoiceReply("unused"),
        notice_handler=notices.append,
        wake_factory=MissingWakeModel,
    )

    service.start()
    deadline = time.monotonic() + 1.0
    while (
        service.diagnostics.snapshot().state is VoiceState.STARTING
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    service.stop()

    assert notices
    assert "Terminal chat is still fully working" in notices[0]
