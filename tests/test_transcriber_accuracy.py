from array import array
from pathlib import Path

from ron.voice.settings import VoiceSettings
from ron.voice.transcriber import FasterWhisperTranscriber


class _Segment:
    text = " Hey Ron open Spotify "
    start = 0.0
    end = 1.0
    avg_logprob = -0.1
    no_speech_prob = 0.01


class _FakeModel:
    def __init__(self) -> None:
        self.options: dict[str, object] = {}

    def transcribe(self, audio: object, **kwargs: object):
        del audio
        self.options = kwargs
        return iter((_Segment(),)), object()


def test_fast_decoding_is_passed_to_faster_whisper() -> None:
    settings = VoiceSettings(
        enabled=True, project_root=Path("."), hotwords=("Ron", "Spotify", "volume")
    )
    transcriber = FasterWhisperTranscriber(settings)
    model = _FakeModel()
    transcriber._model = model

    result = transcriber.transcribe(array("f", [0.1]) * 3_200)

    assert result.text == "Hey Ron open Spotify"
    assert model.options["language"] == "en"
    assert model.options["beam_size"] == 1
    assert model.options["best_of"] == 1
    assert model.options["without_timestamps"] is True
    assert model.options["patience"] == 1.0
    assert model.options["initial_prompt"] == settings.asr_initial_prompt
    assert "Spotify" in str(model.options["hotwords"])


def test_retry_uses_accuracy_beam_without_loading_a_second_model() -> None:
    settings = VoiceSettings(enabled=True, project_root=Path("."))
    transcriber = FasterWhisperTranscriber(settings)
    model = _FakeModel()
    transcriber._model = model

    result = transcriber.retry(array("f", [0.1]) * 3_200)

    assert result.decode_mode == "accurate"
    assert model.options["beam_size"] == 5
