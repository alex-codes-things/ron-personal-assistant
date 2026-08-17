from array import array
from pathlib import Path

from ron.core import Coordinator
from ron.voice import TranscriptionResult, VoiceReply, VoiceService, VoiceSettings


class FakeTranscriber:
    def __init__(self, result: TranscriptionResult) -> None:
        self.result = result

    def transcribe(self, samples):
        assert samples
        return self.result


class FakeAudio:
    overflow_count = 0

    def __init__(self, stop) -> None:
        self.frames = [array("f", [0.2] * 512), array("f", [0.2] * 512)]
        self.stop = stop

    def read(self, timeout):
        del timeout
        if self.frames:
            return self.frames.pop(0)
        self.stop()
        return None


class ShortWake:
    def __init__(self) -> None:
        self.calls = 0

    def feed(self, samples) -> bool:
        assert samples
        self.calls += 1
        return self.calls == 1


class LateVad:
    """Endpoint after KWS fires, without being active in the same block."""

    speech_detected = False

    def __init__(self) -> None:
        self.calls = 0

    def feed(self, samples):
        self.calls += 1
        return () if self.calls == 1 else (array("f", samples),)


def settings() -> VoiceSettings:
    return VoiceSettings(enabled=True, project_root=Path("."))


def test_service_routes_one_corrected_transcript_once() -> None:
    received = []
    notices = []
    service = VoiceService(
        Coordinator(),
        settings(),
        lambda item: received.append(item) or VoiceReply("done"),
        notice_handler=notices.append,
    )
    transcriber = FakeTranscriber(
        TranscriptionResult("Hey Ron open spot the fi", 0.9, 0.3)
    )

    outcome = service._process_segment(
        array("f", [0.2, 0.1]),
        transcriber,
        require_wake=True,
        wake_detected=True,
    )

    assert outcome == "handled"
    assert len(received) == 1
    assert received[0].text == "open Spotify"


def test_service_silently_rejects_unverified_false_wake() -> None:
    received = []
    notices = []
    service = VoiceService(
        Coordinator(),
        settings(),
        lambda item: received.append(item) or VoiceReply("unexpected"),
        notice_handler=notices.append,
    )

    outcome = service._process_segment(
        array("f", [0.2, 0.1]),
        FakeTranscriber(TranscriptionResult("Family conversation", 0.9, 0.2)),
        require_wake=True,
        wake_detected=True,
    )

    assert outcome == "rejected"
    assert received == []
    assert notices == []
    assert service.diagnostics.snapshot().rejected_activations == 1


def test_low_confidence_executes_nothing_and_requests_repeat() -> None:
    received = []
    notices = []
    service = VoiceService(
        Coordinator(),
        settings(),
        lambda item: received.append(item) or VoiceReply("unexpected"),
        notice_handler=notices.append,
    )

    outcome = service._process_segment(
        array("f", [0.2, 0.1]),
        FakeTranscriber(TranscriptionResult("Hey Ron delete everything", 0.01, 0.2)),
        require_wake=True,
        wake_detected=True,
    )

    assert outcome == "unclear"
    assert received == []
    assert any("Nothing" not in item and "repeat" in item.casefold() for item in notices)


def test_short_wake_is_not_vetoed_by_vad_timing() -> None:
    received = []
    notices = []
    service = VoiceService(
        Coordinator(),
        settings(),
        lambda item: received.append(item) or VoiceReply("done"),
        notice_handler=notices.append,
    )
    original_process = service._process_segment

    def process_and_stop(*args, **kwargs):
        result = original_process(*args, **kwargs)
        service._stop.set()
        return result

    service._process_segment = process_and_stop
    service._listen(
        FakeAudio(service._stop.set),
        ShortWake(),
        LateVad(),
        FakeTranscriber(TranscriptionResult("Hey Ron status", 0.9, 0.2)),
    )

    assert [item.text for item in received] == ["status"]
    assert any("detected" in message.casefold() for message in notices)
