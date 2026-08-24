from __future__ import annotations

import queue
import sys
import threading
import time
from array import array
from pathlib import Path
from types import SimpleNamespace

from ron.core import Coordinator, EventType
from ron.voice import SpeechOutputService, SpeechTextFormatter, VoiceSettings
from ron.voice.speech import (
    GroqSpeechSynthesizer,
    SoundDevicePlayer,
    SpeechOutputError,
)


class FakeSynthesizer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.texts: list[str] = []

    def synthesize(self, text: str):
        self.texts.append(text)
        if self.fail:
            raise SpeechOutputError("simulated synthesis failure")
        return [0.1, -0.2, 0.3, 0.0], 24_000


class FakePlayer:
    def __init__(self) -> None:
        self.calls = 0

    def play(self, samples, sample_rate, *, level_handler, stop_event) -> None:
        assert samples
        assert sample_rate == 24_000
        assert not stop_event.is_set()
        self.calls += 1
        level_handler(0.25)
        level_handler(0.75)


def settings(tmp_path: Path) -> VoiceSettings:
    return VoiceSettings(enabled=True, project_root=tmp_path, tts_enabled=True)


def test_formatter_removes_display_markup_and_bounds_long_speech() -> None:
    formatter = SpeechTextFormatter(maximum_characters=180)
    text = (
        """## Fix\n- Try this:\n```python\nprint('hello')\n```\n"""
        "See [the docs](https://example.com/docs). " + "More detail. " * 30
    )

    spoken = formatter.prepare(text)

    assert "```" not in spoken
    assert "https://" not in spoken
    assert "code in the terminal" in spoken.casefold()
    assert "rest" in spoken.casefold()
    assert len(spoken) <= 180


def test_formatter_returns_opening_sentence_as_first_audio_chunk() -> None:
    formatter = SpeechTextFormatter(maximum_characters=500)

    chunks = formatter.prepare_chunks(
        "Right away. I found the setting and changed it safely. Everything is ready.",
        80,
    )

    assert chunks[0] == "Right away."
    assert " ".join(chunks).endswith("Everything is ready.")


def test_cloud_formatter_never_ends_a_limited_reply_mid_sentence() -> None:
    formatter = SpeechTextFormatter(maximum_characters=700)
    text = (
        "The first result is ready. "
        + "This additional explanation contains useful context for the terminal. " * 20
    )

    chunks = formatter.prepare_cloud_chunks(
        text,
        target_characters=180,
        maximum_chunks=4,
    )

    assert len(chunks) == 4
    assert all(len(chunk) <= 180 for chunk in chunks)
    assert chunks[-1].endswith("terminal.")


def test_speech_synthesizes_sentence_first_in_multiple_chunks(tmp_path: Path) -> None:
    synth = FakeSynthesizer()
    player = FakePlayer()
    cfg = VoiceSettings(
        enabled=True,
        project_root=tmp_path,
        tts_enabled=True,
        tts_chunk_characters=80,
    )
    service = SpeechOutputService(Coordinator(), cfg, synthesizer=synth, player=player)
    service.start()

    assert (
        service.speak("Right away. I found the setting and changed it safely. Everything is ready.")
        is True
    )

    assert synth.texts[0] == "Right away."
    assert len(synth.texts) >= 2
    assert player.calls == len(synth.texts)


def test_streaming_speech_starts_from_complete_model_sentence(tmp_path: Path) -> None:
    synth = FakeSynthesizer()
    player = FakePlayer()
    first_audio = threading.Event()
    service = SpeechOutputService(
        Coordinator(), settings(tmp_path), synthesizer=synth, player=player
    )
    service.start()

    stream = service.open_stream(on_first_audio=first_audio.set)
    stream.feed("Right away. I am still working")
    stream.feed(" on the rest.")

    assert stream.finish(wait=True) is True
    assert first_audio.is_set()
    assert synth.texts[0] == "Right away."
    assert "working on the rest" in synth.texts[1]


def test_cloud_stream_uses_one_bounded_request_for_the_useful_opening(
    tmp_path: Path,
) -> None:
    synth = FakeSynthesizer()
    player = FakePlayer()
    cfg = VoiceSettings(
        enabled=True,
        project_root=tmp_path,
        groq_api_key="gsk_" + "x" * 32,
        tts_provider="groq",
        groq_tts_max_requests_per_turn=1,
        tts_chunk_characters=180,
    )
    service = SpeechOutputService(
        Coordinator(), cfg, synthesizer=synth, player=player
    )
    service.start()

    stream = service.open_stream()
    stream.feed("Right away. I found the cause and changed the voice path. ")
    stream.feed("The complete technical detail remains visible in the terminal.")

    assert stream.finish(wait=True) is True
    assert len(synth.texts) == 1
    assert synth.texts[0].startswith("Right away. I found the cause")
    assert len(synth.texts[0]) <= 200


def test_cloud_stream_speaks_four_safe_parts_instead_of_cutting_off(
    tmp_path: Path,
) -> None:
    synth = FakeSynthesizer()
    cfg = VoiceSettings(
        enabled=True,
        project_root=tmp_path,
        groq_api_key="gsk_" + "x" * 32,
        tts_provider="groq",
        groq_tts_max_requests_per_turn=4,
        tts_max_characters=700,
        tts_chunk_characters=180,
    )
    service = SpeechOutputService(
        Coordinator(), cfg, synthesizer=synth, player=FakePlayer()
    )
    service.start()
    stream = service.open_stream()
    stream.feed(
        "First, I found the cause. Second, I repaired the speech queue. "
        "Third, I kept the same voice for every cue. "
        + "This final explanation contains more useful detail for Alex. " * 20
    )

    assert stream.finish(wait=True) is True
    assert len(synth.texts) == 4
    assert all(len(text) <= 200 for text in synth.texts)
    assert synth.texts[-1].endswith("terminal.")


def test_cloud_audio_bytes_stream_into_playback_before_response_end(tmp_path: Path) -> None:
    events: list[str] = []

    class LiveSynthesizer(FakeSynthesizer):
        def stream_synthesize(self, text: str, *, stop_event):
            del text
            assert not stop_event.is_set()
            events.append("first network audio")
            yield [0.1, -0.1], 24_000
            events.append("later network audio")
            yield [0.2, -0.2], 24_000

        def synthesize(self, text: str):
            raise AssertionError(f"full synthesis should not run: {text}")

    class OrderedPlayer(FakePlayer):
        def play(self, samples, sample_rate, *, level_handler, stop_event) -> None:
            del samples, sample_rate, level_handler
            assert not stop_event.is_set()
            events.append("play")

    cfg = VoiceSettings(
        enabled=True,
        project_root=tmp_path,
        groq_api_key="gsk_" + "x" * 32,
        tts_provider="groq",
    )
    service = SpeechOutputService(
        Coordinator(), cfg, synthesizer=LiveSynthesizer(), player=OrderedPlayer()
    )
    service.start()
    stream = service.open_stream(
        on_first_audio_byte=lambda: events.append("first byte"),
        on_first_audio=lambda: events.append("first audio"),
    )
    stream.feed("The answer is ready.")

    assert stream.finish(wait=True) is True
    assert events == [
        "first network audio",
        "first byte",
        "first audio",
        "play",
        "later network audio",
        "play",
    ]


def test_later_groq_part_is_prefetched_during_current_playback(tmp_path: Path) -> None:
    second_ready = threading.Event()
    requested: list[str] = []

    class PrefetchSynthesizer(FakeSynthesizer):
        def synthesize(self, text: str):
            requested.append(text)
            second_ready.set()
            return [0.2, -0.2], 24_000

    service = SpeechOutputService(
        Coordinator(),
        VoiceSettings(enabled=True, project_root=tmp_path),
        synthesizer=PrefetchSynthesizer(),
        player=FakePlayer(),
    )
    text_queue: queue.Queue[str | None] = queue.Queue()
    text_queue.put("Second sentence.")
    text_queue.put(None)

    def current_provider():
        assert second_ready.wait(0.5)
        yield [0.1, -0.1], 24_000

    sequence = tuple(
        service._live_provider_sequence(
            text_queue,
            ([0.0], 24_000),
            current_provider(),
            threading.Event(),
        )
    )

    assert requested == ["Second sentence."]
    assert len(sequence) == 3


def test_cloud_non_streaming_speech_is_one_request_and_mentions_terminal(
    tmp_path: Path,
) -> None:
    synth = FakeSynthesizer()
    cfg = VoiceSettings(
        enabled=True,
        project_root=tmp_path,
        groq_api_key="gsk_" + "x" * 32,
        tts_provider="groq",
        groq_tts_max_requests_per_turn=1,
        tts_chunk_characters=180,
    )
    service = SpeechOutputService(
        Coordinator(), cfg, synthesizer=synth, player=FakePlayer()
    )
    service.start()

    assert service.speak("Useful detail. " * 60) is True

    assert len(synth.texts) == 1
    assert len(synth.texts[0]) <= 200
    assert "terminal" in synth.texts[0].casefold()


def test_empty_stream_does_not_block_cached_action_cue(tmp_path: Path) -> None:
    service = SpeechOutputService(
        Coordinator(),
        settings(tmp_path),
        synthesizer=FakeSynthesizer(),
        player=FakePlayer(),
    )
    service.start()
    service.prewarm(("Opening it now.",))
    stream = service.open_stream()
    # Give the stream worker time to reach its empty first-sentence wait. The
    # regression held the global speech lock at this point indefinitely.
    time.sleep(0.05)
    completed = threading.Event()
    thread = threading.Thread(
        target=lambda: (service.speak_cached("Opening it now."), completed.set())
    )
    thread.start()

    assert completed.wait(0.5)
    stream.cancel()
    assert stream.completion_event.wait(1.0)
    thread.join(1.0)


def test_stream_finish_can_handoff_before_playback_ends(tmp_path: Path) -> None:
    class BlockingPlayer(FakePlayer):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        def play(self, samples, sample_rate, *, level_handler, stop_event) -> None:
            del samples, sample_rate, level_handler
            self.started.set()
            while not stop_event.is_set() and not self.release.wait(0.01):
                pass

    player = BlockingPlayer()
    service = SpeechOutputService(
        Coordinator(),
        settings(tmp_path),
        synthesizer=FakeSynthesizer(),
        player=player,
    )
    service.start()
    stream = service.open_stream()
    stream.feed("The answer is ready.")

    assert stream.finish(wait=False) is True
    assert player.started.wait(0.5)
    assert not stream.completion_event.is_set()
    player.release.set()
    assert stream.completion_event.wait(1.0)


def test_sound_player_keeps_one_device_stream_across_chunks(tmp_path: Path, monkeypatch) -> None:
    streams = []

    class FakeStream:
        def __init__(self, **options) -> None:
            self.options = options
            self.writes = 0
            streams.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            del args

        def write(self, chunk) -> None:
            assert len(chunk)
            self.writes += 1

    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(OutputStream=FakeStream),
    )
    player = SoundDevicePlayer(settings(tmp_path))

    player.play_sequence(
        (
            (array("f", [0.1] * 100), 24_000),
            (array("f", [0.2] * 100), 24_000),
        ),
        level_handler=lambda level: None,
        stop_event=threading.Event(),
    )

    assert len(streams) == 1
    assert streams[0].writes == 2


def test_speech_service_publishes_real_level_lifecycle(tmp_path: Path) -> None:
    coordinator = Coordinator()
    events: list[tuple[EventType, float | None]] = []
    for event_type in (
        EventType.SPEECH_STARTED,
        EventType.SPEECH_LEVEL,
        EventType.SPEECH_ENDED,
    ):
        coordinator.subscribe(
            event_type,
            lambda event, kind=event_type: events.append(
                (kind, float(event.payload["level"]) if "level" in event.payload else None)
            ),
        )
    synth = FakeSynthesizer()
    player = FakePlayer()
    service = SpeechOutputService(
        coordinator,
        settings(tmp_path),
        synthesizer=synth,
        player=player,
    )
    service.start()

    assert service.speak("Hello **Alex**.") is True

    assert synth.texts == ["Hello Alex."]
    assert player.calls == 1
    assert (EventType.SPEECH_STARTED, None) in events
    assert (EventType.SPEECH_LEVEL, 0.25) in events
    assert (EventType.SPEECH_LEVEL, 0.75) in events
    assert events[-2][0] is EventType.SPEECH_ENDED or events[-1][0] is EventType.SPEECH_ENDED


def test_speech_failure_never_breaks_voice_or_chat(tmp_path: Path) -> None:
    notices: list[str] = []
    service = SpeechOutputService(
        Coordinator(),
        settings(tmp_path),
        notice_handler=notices.append,
        synthesizer=FakeSynthesizer(fail=True),
        player=FakePlayer(),
    )
    service.start()

    assert service.speak("This still has a terminal response.") is False
    assert service.speak("And a second one.") is False

    assert len(notices) == 1
    assert "still working" in notices[0].casefold()


def test_disabled_speech_is_a_noop(tmp_path: Path) -> None:
    cfg = VoiceSettings(enabled=True, project_root=tmp_path, tts_enabled=False)
    synth = FakeSynthesizer()
    service = SpeechOutputService(Coordinator(), cfg, synthesizer=synth, player=FakePlayer())
    service.start()

    assert service.speak("No audio please") is False
    assert synth.texts == []


def test_formatter_makes_common_technical_terms_speakable() -> None:
    formatter = SpeechTextFormatter(maximum_characters=500)

    spoken = formatter.prepare(
        r"The API is in C:\Development\Ron\app.py and PySide6 talks to the USB device in VS Code."
    )

    assert "A P I" in spoken
    assert "PySide six" in spoken
    assert "U S B" in spoken
    assert "V S Code" in spoken
    assert "path shown in the terminal" in spoken
    assert "C:\\" not in spoken


def test_short_acknowledgement_audio_is_cached_for_fast_reuse(tmp_path: Path) -> None:
    synth = FakeSynthesizer()
    service = SpeechOutputService(
        Coordinator(),
        settings(tmp_path),
        synthesizer=synth,
        player=FakePlayer(),
    )
    service.start()

    service.prewarm(("Yes?",))
    assert service.speak("Yes?") is True

    assert synth.texts == ["Yes?"]


def test_groq_cues_use_groq_and_ignore_old_local_voice_cache(tmp_path: Path) -> None:
    groq_settings = VoiceSettings(
        enabled=True,
        project_root=tmp_path,
        groq_api_key="gsk_" + "x" * 32,
        tts_provider="groq",
        groq_tts_voice="daniel",
        tts_voice="bm_george",
    )
    groq_service = SpeechOutputService(
        Coordinator(),
        groq_settings,
        synthesizer=FakeSynthesizer(),
        player=FakePlayer(),
    )
    local_service = SpeechOutputService(
        Coordinator(),
        VoiceSettings(enabled=True, project_root=tmp_path, tts_provider="local"),
        synthesizer=FakeSynthesizer(),
        player=FakePlayer(),
    )

    assert isinstance(groq_service._cue_synthesizer, GroqSpeechSynthesizer)
    assert groq_service._persistent_audio_path("Yes?") != local_service._persistent_audio_path(
        "Yes?"
    )


def test_acknowledgement_cache_survives_restart(tmp_path: Path) -> None:
    first_synth = FakeSynthesizer()
    first = SpeechOutputService(
        Coordinator(),
        settings(tmp_path),
        synthesizer=first_synth,
        player=FakePlayer(),
    )
    first.start()
    first.prewarm(("Yes?",))
    assert first_synth.texts == ["Yes?"]

    second_synth = FakeSynthesizer()
    second = SpeechOutputService(
        Coordinator(),
        settings(tmp_path),
        synthesizer=second_synth,
        player=FakePlayer(),
    )
    second.start()
    second.prewarm(("Yes?",))

    assert second_synth.texts == []
    assert "Yes?" in second._audio_cache
    assert tuple((tmp_path / "runtime" / "cache" / "voice_ack").glob("*.npz"))
