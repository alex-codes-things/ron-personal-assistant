from __future__ import annotations

import io
import json
import wave
from array import array
from pathlib import Path

import pytest

from ron.voice.settings import VoiceSettings, VoiceSettingsError
from ron.voice.speech import (
    GroqSpeechSynthesizer,
    HybridSpeechSynthesizer,
    SpeechOutputError,
    build_speech_synthesizer,
)
from ron.voice.transcriber import (
    GroqTranscriber,
    HybridTranscriber,
    TranscriptionError,
    build_transcriber,
)

API_KEY = "gsk_" + "x" * 32


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        del args

    def read(self, maximum: int = -1) -> bytes:
        return self.payload if maximum < 0 else self.payload[:maximum]


class _StreamingResponse(_Response):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.offset = 0
        self.read_calls = 0

    def read(self, maximum: int = -1) -> bytes:
        self.read_calls += 1
        if self.offset >= len(self.payload):
            return b""
        end = len(self.payload) if maximum < 0 else self.offset + maximum
        chunk = self.payload[self.offset : end]
        self.offset += len(chunk)
        return chunk


def _settings(tmp_path: Path, **changes: object) -> VoiceSettings:
    values: dict[str, object] = {
        "enabled": True,
        "project_root": tmp_path,
        "groq_api_key": API_KEY,
        "asr_provider": "groq",
        "tts_provider": "groq",
    }
    values.update(changes)
    return VoiceSettings(**values)


def _wav_payload() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24_000)
        wav_file.writeframes(array("h", [0, 1_000, -1_000, 0]).tobytes())
    return output.getvalue()


def _streaming_wav_payload() -> bytes:
    """Match Groq's unknown-length RIFF and data chunk declarations."""
    payload = bytearray(_wav_payload())
    payload[4:8] = b"\xff\xff\xff\xff"
    data_chunk = payload.find(b"data", 12)
    assert data_chunk >= 0
    payload[data_chunk + 4 : data_chunk + 8] = b"\xff\xff\xff\xff"
    return bytes(payload)


def test_groq_transcriber_uploads_bounded_wav_and_parses_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    seen = {}
    payload = json.dumps(
        {
            "text": " Hey Ron, open Spotify ",
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.2,
                    "avg_logprob": -0.1,
                    "no_speech_prob": 0.01,
                }
            ],
        }
    ).encode()

    def fake_urlopen(request, timeout):
        seen["request"] = request
        seen["timeout"] = timeout
        return _Response(payload)

    monkeypatch.setattr("ron.voice.transcriber.urlopen", fake_urlopen)
    settings = _settings(tmp_path, asr_fallback_local=False)
    result = GroqTranscriber(settings).transcribe(array("f", [0.1]) * 3_200)

    request = seen["request"]
    assert request.full_url.endswith("/audio/transcriptions")
    assert request.get_header("Authorization") == f"Bearer {API_KEY}"
    assert seen["timeout"] == 15.0
    assert b"whisper-large-v3-turbo" in request.data
    assert b"ron-command.wav" in request.data
    assert b"RIFF" in request.data
    assert result.text == "Hey Ron, open Spotify"
    assert result.confidence > 0.8
    assert result.no_speech_probability == 0.01
    assert result.decode_mode == "groq-fast"


def test_groq_transcriber_retry_uses_accuracy_model(tmp_path: Path, monkeypatch) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        del timeout
        requests.append(request)
        return _Response(json.dumps({"text": "status"}).encode())

    monkeypatch.setattr("ron.voice.transcriber.urlopen", fake_urlopen)
    result = GroqTranscriber(_settings(tmp_path)).retry(array("f", [0.1]) * 1_600)

    assert b"\r\nwhisper-large-v3\r\n" in requests[0].data
    assert result.decode_mode == "groq-accurate"
    assert result.confidence == 0.85


def test_groq_tts_posts_orpheus_request_and_decodes_wav(
    tmp_path: Path, monkeypatch
) -> None:
    seen = {}

    def fake_urlopen(request, timeout):
        seen["request"] = request
        seen["timeout"] = timeout
        return _Response(_wav_payload())

    monkeypatch.setattr("ron.voice.speech.urlopen", fake_urlopen)
    samples, sample_rate = GroqSpeechSynthesizer(
        _settings(tmp_path, tts_fallback_local=False)
    ).synthesize("Ready, Alex.")

    request = seen["request"]
    body = json.loads(request.data)
    assert request.full_url.endswith("/audio/speech")
    assert request.get_header("Authorization") == f"Bearer {API_KEY}"
    assert body == {
        "model": "canopylabs/orpheus-v1-english",
        "input": "Ready, Alex.",
        "voice": "daniel",
        "response_format": "wav",
    }
    assert seen["timeout"] == 15.0
    assert sample_rate == 24_000
    assert len(samples) == 4
    assert samples[1] > 0


def test_groq_tts_decodes_unknown_length_streaming_wav(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "ron.voice.speech.urlopen",
        lambda *args, **kwargs: _Response(_streaming_wav_payload()),
    )

    samples, sample_rate = GroqSpeechSynthesizer(_settings(tmp_path)).synthesize(
        "Ron cloud speech is ready."
    )

    assert sample_rate == 24_000
    assert len(samples) == 4
    assert samples[1] > 0


def test_groq_tts_yields_pcm_before_the_stream_closes(
    tmp_path: Path, monkeypatch
) -> None:
    response = _StreamingResponse(_streaming_wav_payload())
    monkeypatch.setattr("ron.voice.speech.urlopen", lambda *args, **kwargs: response)

    chunks = list(
        GroqSpeechSynthesizer(_settings(tmp_path)).stream_synthesize("Ready, Alex.")
    )

    assert sum(len(samples) for samples, _rate in chunks) == 4
    assert {rate for _samples, rate in chunks} == {24_000}
    assert response.read_calls >= 2


def test_groq_tts_rejects_oversized_text_before_network(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "ron.voice.speech.urlopen",
        lambda *args, **kwargs: pytest.fail("network should not be called"),
    )

    with pytest.raises(SpeechOutputError, match="200 characters"):
        GroqSpeechSynthesizer(_settings(tmp_path)).synthesize("x" * 201)


def test_groq_tts_explains_required_model_terms(tmp_path: Path, monkeypatch) -> None:
    from urllib.error import HTTPError

    payload = json.dumps(
        {
            "error": {
                "message": "The model requires terms acceptance.",
                "type": "invalid_request_error",
                "code": "model_terms_required",
            }
        }
    ).encode()

    def fake_urlopen(*args, **kwargs):
        del args, kwargs
        raise HTTPError("https://api.groq.com", 400, "bad request", {}, io.BytesIO(payload))

    monkeypatch.setattr("ron.voice.speech.urlopen", fake_urlopen)
    with pytest.raises(SpeechOutputError, match="terms must be accepted once") as raised:
        GroqSpeechSynthesizer(_settings(tmp_path)).synthesize("Ready.")

    assert "check_groq_voice.py" in str(raised.value)
    assert API_KEY not in str(raised.value)


def test_groq_tts_shows_safe_bounded_server_reason(tmp_path: Path, monkeypatch) -> None:
    from urllib.error import HTTPError

    payload = json.dumps(
        {
            "error": {
                "message": f"Invalid voice for secret {API_KEY}",
                "type": "invalid_request_error",
            }
        }
    ).encode()

    def fake_urlopen(*args, **kwargs):
        del args, kwargs
        raise HTTPError("https://api.groq.com", 400, "bad request", {}, io.BytesIO(payload))

    monkeypatch.setattr("ron.voice.speech.urlopen", fake_urlopen)
    with pytest.raises(SpeechOutputError, match="Invalid voice") as raised:
        GroqSpeechSynthesizer(_settings(tmp_path)).synthesize("Ready.")

    assert "[redacted]" in str(raised.value)
    assert API_KEY not in str(raised.value)


def test_cloud_factories_keep_local_models_cold(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    transcriber = build_transcriber(settings)
    synthesizer = build_speech_synthesizer(settings)

    assert isinstance(transcriber, HybridTranscriber)
    assert transcriber._fallback is None
    assert isinstance(synthesizer, HybridSpeechSynthesizer)
    assert synthesizer._fallback is None


def test_explicit_groq_voice_requires_key_but_repr_never_contains_it(
    tmp_path: Path,
) -> None:
    with pytest.raises(VoiceSettingsError, match="GROQ_API_KEY"):
        VoiceSettings(
            enabled=True,
            project_root=tmp_path,
            asr_provider="groq",
        )

    settings = _settings(tmp_path)
    assert API_KEY not in repr(settings)


def test_cloud_errors_do_not_expose_api_key(tmp_path: Path, monkeypatch) -> None:
    from urllib.error import HTTPError

    error = HTTPError("https://api.groq.com", 401, "no", {}, io.BytesIO(b"{}"))
    monkeypatch.setattr(
        "ron.voice.transcriber.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(TranscriptionError) as raised:
        GroqTranscriber(_settings(tmp_path)).transcribe(array("f", [0.1]) * 1_600)

    assert API_KEY not in str(raised.value)
    assert "GROQ_API_KEY" in str(raised.value)
