import threading
import time
from array import array
from pathlib import Path

from ron.core import Coordinator
from ron.voice import (
    TranscriptionResult,
    VoiceInput,
    VoiceReply,
    VoiceService,
    VoiceSettings,
    VoiceState,
)


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
        self.discard_calls = 0
        self.muted_states = []

    def read(self, timeout):
        del timeout
        if self.frames:
            return self.frames.pop(0)
        self.stop()
        return None

    def set_capture_muted(self, muted):
        self.muted_states.append(bool(muted))

    def discard_pending(self):
        self.discard_calls += 1
        return 0


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
    transcriber = FakeTranscriber(TranscriptionResult("Hey Ron open spot the fi", 0.9, 0.3))

    outcome = service._process_segment(
        array("f", [0.2, 0.1]),
        transcriber,
        require_wake=True,
        wake_detected=True,
    )

    assert outcome == "handled"
    assert len(received) == 1
    assert received[0].text == "open Spotify"


def test_live_response_dispatch_does_not_block_microphone_thread() -> None:
    started = threading.Event()
    release = threading.Event()

    def handler(item):
        assert item.text == "status"
        started.set()
        release.wait(2.0)
        return VoiceReply("done")

    service = VoiceService(Coordinator(), settings(), handler)
    outcome = service._process_segment(
        array("f", [0.2, 0.1]),
        FakeTranscriber(TranscriptionResult("Hey Ron status", 0.9, 0.2)),
        require_wake=True,
        wake_detected=True,
        dispatch_response=True,
    )

    assert outcome == "dispatched"
    assert started.wait(1.0)
    assert service.response_active
    release.set()
    assert service._response_thread is not None
    service._response_thread.join(2.0)
    assert not service.response_active


def test_wake_gated_stop_interrupts_current_spoken_reply() -> None:
    interrupted: list[str] = []
    service = VoiceService(
        Coordinator(),
        settings(),
        lambda item: VoiceReply("unexpected"),
        interrupt_handler=lambda command: interrupted.append(command) or True,
    )
    service._response_active.set()

    outcome = service._process_segment(
        array("f", [0.2, 0.1]),
        FakeTranscriber(TranscriptionResult("Hey Ron stop", 0.95, 0.2)),
        require_wake=True,
        wake_detected=True,
    )

    assert outcome == "handled"
    assert interrupted == ["stop"]


def test_wake_gated_new_request_replaces_current_reply() -> None:
    interrupted: list[str] = []
    service = VoiceService(
        Coordinator(),
        settings(),
        lambda item: VoiceReply("unexpected"),
        interrupt_handler=lambda command: interrupted.append(command) or True,
    )
    service._response_active.set()

    outcome = service._process_segment(
        array("f", [0.2, 0.1]),
        FakeTranscriber(TranscriptionResult("Hey Ron open Spotify", 0.95, 0.2)),
        require_wake=True,
        wake_detected=True,
    )

    assert outcome == "queued"
    assert interrupted == ["new_request"]
    assert service._pending_prompt is not None
    assert service._pending_prompt[0].text == "open Spotify"


def test_voice_state_tracks_playback_before_mic_ready() -> None:
    completion = threading.Event()
    service = VoiceService(
        Coordinator(),
        settings(),
        lambda item: VoiceReply("done", speech_completion=completion),
    )
    voice_input = service.normalizer.normalize(
        "Hey Ron status", require_wake=True, wake_detected=True
    )
    assert voice_input.accepted
    service._dispatch_prompt(
        VoiceInput(
            raw_text=voice_input.raw_text,
            text=voice_input.text,
            confidence=0.95,
            wake_phrase=voice_input.wake_phrase,
        ),
        0.2,
    )
    deadline = time.monotonic() + 1.0
    while (
        service.diagnostics.snapshot().state is not VoiceState.SPEAKING
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)

    assert service.diagnostics.snapshot().state is VoiceState.SPEAKING
    assert service.response_active
    completion.set()
    assert service._response_thread is not None
    service._response_thread.join(1.0)
    assert service.diagnostics.snapshot().state is VoiceState.READY
    assert not service.response_active


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
    audio = FakeAudio(service._stop.set)
    service._listen(
        audio,
        ShortWake(),
        LateVad(),
        FakeTranscriber(TranscriptionResult("Hey Ron status", 0.9, 0.2)),
    )

    assert [item.text for item in received] == ["status"]
    assert audio.discard_calls == 1
    assert any("detected" in message.casefold() for message in notices)


def test_voice_can_mute_microphone_around_speech_output() -> None:
    service = VoiceService(Coordinator(), settings(), lambda item: VoiceReply("done"))
    audio = FakeAudio(lambda: None)
    service._audio = audio

    service.suspend_input_for_speech()
    service.resume_input_after_speech(speech_played=False)

    assert audio.muted_states == [True, False]
    assert audio.discard_calls == 2


def test_live_service_passes_personal_corrections_to_same_prompt_handler() -> None:
    received = []
    tuned = VoiceSettings(
        enabled=True,
        project_root=Path("."),
        wake_kws_aliases=("peron", "heyron"),
    )
    service = VoiceService(
        Coordinator(),
        tuned,
        lambda item: received.append(item) or VoiceReply("done"),
    )

    outcome = service._process_segment(
        array("f", [0.2, 0.1]),
        FakeTranscriber(TranscriptionResult("Peron, set volume to 20%.", 0.9, 0.2)),
        require_wake=True,
        wake_detected=True,
    )

    assert outcome == "handled"
    assert received[0].text == "set volume to 20%."
    assert received[0].correction_notes


def test_continuous_chat_still_uses_recognition_normalizer() -> None:
    received = []
    service = VoiceService(
        Coordinator(),
        settings(),
        lambda item: received.append(item) or VoiceReply("done", continue_listening=True),
    )

    outcome = service._process_segment(
        array("f", [0.2, 0.1]),
        FakeTranscriber(TranscriptionResult("open video studio code", 0.9, 0.2)),
        require_wake=False,
        wake_detected=False,
    )

    assert outcome == "continuous"
    assert received[0].text == "open Visual Studio Code"
    assert received[0].correction_notes


def test_recognition_profile_reports_live_model_and_normalizer() -> None:
    service = VoiceService(Coordinator(), settings(), lambda item: VoiceReply("done"))

    label = service.recognition_profile_label()

    assert "distil-large-v3" in label
    assert "beam 5" in label
    assert "normalizer active" in label


def test_verified_wake_only_speaks_acknowledgement_and_waits() -> None:
    received = []
    acknowledgements = []
    service = VoiceService(
        Coordinator(),
        settings(),
        lambda item: received.append(item) or VoiceReply("unexpected"),
        wake_acknowledgement_handler=lambda phrase: acknowledgements.append(phrase) or True,
    )

    outcome = service._process_segment(
        array("f", [0.2, 0.1]),
        FakeTranscriber(TranscriptionResult("Hey Ron", 0.95, 0.2)),
        require_wake=True,
        wake_detected=True,
    )

    assert outcome == "waiting"
    assert received == []
    assert acknowledgements == ["Yes?"]


def test_wake_with_command_does_not_interrupt_with_acknowledgement() -> None:
    received = []
    acknowledgements = []
    service = VoiceService(
        Coordinator(),
        settings(),
        lambda item: received.append(item.text) or VoiceReply("done"),
        wake_acknowledgement_handler=lambda phrase: acknowledgements.append(phrase) or True,
    )

    outcome = service._process_segment(
        array("f", [0.2, 0.1]),
        FakeTranscriber(TranscriptionResult("Hey Ron, open Spotify", 0.95, 0.3)),
        require_wake=True,
        wake_detected=True,
    )

    assert outcome == "handled"
    assert received == ["open Spotify"]
    assert acknowledgements == []


def test_acknowledgements_rotate_without_ai_generation() -> None:
    acknowledgements = []
    service = VoiceService(
        Coordinator(),
        settings(),
        lambda item: VoiceReply("unexpected"),
        wake_acknowledgement_handler=lambda phrase: acknowledgements.append(phrase) or True,
    )
    transcriber = FakeTranscriber(TranscriptionResult("Hey Ron", 0.95, 0.2))

    for _ in range(4):
        assert (
            service._process_segment(
                array("f", [0.2, 0.1]),
                transcriber,
                require_wake=True,
                wake_detected=True,
            )
            == "waiting"
        )

    assert acknowledgements == ["Yes?", "I'm listening.", "Go ahead.", "Yes?"]


def test_acknowledgement_failure_never_breaks_followup_listening() -> None:
    def fail_ack(phrase: str) -> bool:
        del phrase
        raise RuntimeError("speaker unavailable")

    service = VoiceService(
        Coordinator(),
        settings(),
        lambda item: VoiceReply("unexpected"),
        wake_acknowledgement_handler=fail_ack,
    )

    outcome = service._process_segment(
        array("f", [0.2, 0.1]),
        FakeTranscriber(TranscriptionResult("Hey Ron", 0.95, 0.2)),
        require_wake=True,
        wake_detected=True,
    )

    assert outcome == "waiting"


def test_low_confidence_wake_does_not_speak_acknowledgement() -> None:
    acknowledgements = []
    service = VoiceService(
        Coordinator(),
        settings(),
        lambda item: VoiceReply("unexpected"),
        wake_acknowledgement_handler=lambda phrase: acknowledgements.append(phrase) or True,
    )

    outcome = service._process_segment(
        array("f", [0.2, 0.1]),
        FakeTranscriber(TranscriptionResult("Hey Ron", 0.01, 0.2)),
        require_wake=True,
        wake_detected=True,
    )

    assert outcome == "unclear"
    assert acknowledgements == []


def test_interaction_profile_reports_wake_reply_and_followup_window() -> None:
    service = VoiceService(Coordinator(), settings(), lambda item: VoiceReply("done"))

    label = service.interaction_profile_label()

    assert "wake acknowledgement on" in label
    assert "8s follow-up window" in label
    assert "fast handoff" in label


def test_live_listen_loop_hands_wake_only_into_followup_command() -> None:
    class EveryVad:
        def feed(self, samples):
            return (array("f", samples),)

    class SequenceTranscriber:
        def __init__(self) -> None:
            self.results = [
                TranscriptionResult("Hey Ron", 0.95, 0.2),
                TranscriptionResult("Open Spotify", 0.95, 0.2),
            ]

        def transcribe(self, samples):
            assert samples
            return self.results.pop(0)

    received = []
    acknowledgements = []
    service = VoiceService(
        Coordinator(),
        settings(),
        lambda item: received.append(item.text) or VoiceReply("done"),
        wake_acknowledgement_handler=lambda phrase: acknowledgements.append(phrase) or True,
    )
    original_process = service._process_segment
    processed = 0

    def process_and_stop(*args, **kwargs):
        nonlocal processed
        outcome = original_process(*args, **kwargs)
        processed += 1
        if processed == 2:
            service._stop.set()
        return outcome

    service._process_segment = process_and_stop
    audio = FakeAudio(service._stop.set)
    service._listen(audio, ShortWake(), EveryVad(), SequenceTranscriber())

    assert acknowledgements == ["Yes?"]
    assert received == ["Open Spotify"]


def test_fast_live_wake_handoff_skips_large_transcriber() -> None:
    class WakeOnlyVad:
        def __init__(self) -> None:
            self.calls = 0

        def feed(self, samples):
            self.calls += 1
            if self.calls == 1:
                return ()
            # A realistic short wake-only utterance (~0.5 s at 16 kHz).
            return (array("f", [0.2] * 8_000),)

    class MustNotTranscribe:
        def transcribe(self, samples):
            del samples
            raise AssertionError("full Whisper should not run before fast wake acknowledgement")

    acknowledgements = []
    service = VoiceService(
        Coordinator(),
        settings(),
        lambda item: VoiceReply("unexpected"),
        wake_acknowledgement_handler=lambda phrase: acknowledgements.append(phrase) or True,
    )
    audio = FakeAudio(service._stop.set)
    service._listen(audio, ShortWake(), WakeOnlyVad(), MustNotTranscribe())

    assert acknowledgements == ["Yes?"]


def test_acknowledgement_can_use_shorter_echo_guard() -> None:
    service = VoiceService(Coordinator(), settings(), lambda item: VoiceReply("done"))
    audio = FakeAudio(lambda: None)
    service._audio = audio
    waits = []
    service._stop.wait = lambda seconds: waits.append(seconds) or False  # type: ignore[method-assign]

    service.suspend_input_for_speech()
    service.resume_input_after_speech(speech_played=True, guard_seconds=0.06)

    assert waits == [0.06]
    assert audio.muted_states == [True, False]


def test_uncertain_fast_transcript_uses_accuracy_retry_once() -> None:
    class AdaptiveTranscriber:
        def __init__(self) -> None:
            self.fast_calls = 0
            self.retry_calls = 0

        def transcribe(self, samples):
            assert samples
            self.fast_calls += 1
            return TranscriptionResult("Hey Ron open spot the fi", 0.30, 0.10)

        def retry(self, samples):
            assert samples
            self.retry_calls += 1
            return TranscriptionResult("Hey Ron open Spotify", 0.92, 0.25, decode_mode="accurate")

    received = []
    transcriber = AdaptiveTranscriber()
    service = VoiceService(
        Coordinator(),
        settings(),
        lambda item: received.append(item.text) or VoiceReply("done"),
    )

    outcome = service._process_segment(
        array("f", [0.2] * 3_200),
        transcriber,
        require_wake=True,
        wake_detected=True,
    )

    assert outcome == "handled"
    assert received == ["open Spotify"]
    assert transcriber.fast_calls == 1
    assert transcriber.retry_calls == 1
    assert service.diagnostics.snapshot().last_transcription_seconds == 0.35


def test_high_confidence_transcript_skips_accuracy_retry() -> None:
    class ConfidentTranscriber:
        def __init__(self) -> None:
            self.retry_calls = 0

        def transcribe(self, samples):
            assert samples
            return TranscriptionResult("Hey Ron open Spotify", 0.92, 0.10)

        def retry(self, samples):
            del samples
            self.retry_calls += 1
            return TranscriptionResult("unexpected", 1.0, 1.0)

    transcriber = ConfidentTranscriber()
    service = VoiceService(Coordinator(), settings(), lambda item: VoiceReply("done"))

    assert (
        service._process_segment(
            array("f", [0.2] * 3_200),
            transcriber,
            require_wake=True,
            wake_detected=True,
        )
        == "handled"
    )
    assert transcriber.retry_calls == 0


def test_short_wake_plus_command_is_not_swallowed_by_fast_handoff() -> None:
    class CommandVad:
        def __init__(self) -> None:
            self.calls = 0

        def feed(self, samples):
            self.calls += 1
            if self.calls == 1:
                return ()
            return (array("f", [0.2] * 12_000),)

    class CommandAudio(FakeAudio):
        def __init__(self, stop) -> None:
            super().__init__(stop)
            self.frames = [
                array("f", [0.2] * 512),
                array("f", [0.2] * 11_000),
            ]

    received = []
    service = VoiceService(
        Coordinator(),
        settings(),
        lambda item: received.append(item.text) or VoiceReply("done"),
    )
    original_process = service._process_segment

    def process_and_stop(*args, **kwargs):
        outcome = original_process(*args, **kwargs)
        service._stop.set()
        return outcome

    service._process_segment = process_and_stop
    service._listen(
        CommandAudio(service._stop.set),
        ShortWake(),
        CommandVad(),
        FakeTranscriber(TranscriptionResult("Hey Ron status", 0.95, 0.2)),
    )

    assert received == ["status"]


def test_automatic_followup_accepts_next_turn_without_second_wake() -> None:
    class EveryVad:
        def feed(self, samples):
            return (array("f", samples),)

    class SequenceTranscriber:
        def __init__(self) -> None:
            self.results = [
                TranscriptionResult("Hey Ron status", 0.95, 0.1),
                TranscriptionResult("What time is it", 0.95, 0.1),
            ]

        def transcribe(self, samples):
            assert samples
            return self.results.pop(0)

    received = []
    service = VoiceService(
        Coordinator(),
        VoiceSettings(
            enabled=True,
            project_root=Path("."),
            interaction_mode="followup",
            automatic_followup=True,
            tts_echo_guard_seconds=0.0,
        ),
        lambda item: received.append(item.text) or VoiceReply("done"),
    )
    original_process = service._process_segment

    def process_and_stop(*args, **kwargs):
        outcome = original_process(*args, **kwargs)
        if len(received) == 2:
            service._stop.set()
        return outcome

    service._process_segment = process_and_stop
    service._listen(
        FakeAudio(service._stop.set),
        ShortWake(),
        EveryVad(),
        SequenceTranscriber(),
    )

    assert received == ["status", "What time is it"]


def test_strict_interaction_returns_to_wake_gate_after_every_reply() -> None:
    settings = VoiceSettings(enabled=True, project_root=Path("."), interaction_mode="strict")

    assert settings.followup_enabled is False
    assert settings.continuous_enabled is False


def test_configured_continuous_mode_can_be_ended_explicitly() -> None:
    settings = VoiceSettings(
        enabled=True,
        project_root=Path("."),
        interaction_mode="continuous",
    )
    service = VoiceService(Coordinator(), settings, lambda item: VoiceReply(item.text))

    assert service._continuous_mode_active() is True
    service.deactivate_configured_continuous_mode()
    assert service._continuous_mode_active() is False
