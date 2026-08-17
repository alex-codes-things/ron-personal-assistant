from array import array
from pathlib import Path

from ron.core import Coordinator
from ron.voice import TranscriptionResult, VoiceReply, VoiceService, VoiceSettings


class OneTranscript:
    def transcribe(self, samples):
        assert samples
        return TranscriptionResult("How are you?", 0.9, 0.2)


def test_continuous_voice_turn_does_not_require_wake_phrase() -> None:
    received = []
    service = VoiceService(
        Coordinator(),
        VoiceSettings(enabled=True, project_root=Path(".")),
        lambda item: received.append(item.text) or VoiceReply("Good!", True),
    )

    outcome = service._process_segment(
        array("f", [0.1, 0.2]),
        OneTranscript(),
        require_wake=False,
        wake_detected=False,
    )

    assert outcome == "continuous"
    assert received == ["How are you?"]
