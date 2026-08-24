import os
from pathlib import Path

from ron.config import load_project_environment
from ron.voice.settings import VoiceSettings


def test_project_env_is_loaded_and_used_by_voice_settings(tmp_path: Path, monkeypatch) -> None:
    for key in ("RON_WHISPER_MODEL", "RON_WHISPER_BEAM_SIZE", "RON_WAKE_KWS_ALIASES"):
        monkeypatch.delenv(key, raising=False)

    (tmp_path / ".env").write_text(
        "RON_WHISPER_MODEL=distil-large-v3\n"
        "RON_WHISPER_BEAM_SIZE=5\n"
        "RON_WAKE_KWS_ALIASES=peron|here on|tehran|aaron|heyron\n",
        encoding="utf-8",
    )

    result = load_project_environment(tmp_path)
    settings = VoiceSettings.from_environment(tmp_path)

    assert result.found is True
    assert result.loaded_count == 3
    assert settings.asr_model == "distil-large-v3"
    assert settings.asr_beam_size == 1
    assert settings.asr_retry_beam_size == 5
    assert "heyron" in settings.wake_kws_aliases


def test_explicit_process_environment_wins_over_dotenv(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RON_TTS_VOICE", "bm_fable")
    (tmp_path / ".env").write_text("RON_TTS_VOICE=bm_george\n", encoding="utf-8")

    result = load_project_environment(tmp_path)

    assert os.environ["RON_TTS_VOICE"] == "bm_fable"
    assert result.loaded_count == 0
    assert result.skipped_keys == ("RON_TTS_VOICE",)


def test_dotenv_parser_never_returns_secret_values(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("RON_TEST_SECRET", raising=False)
    (tmp_path / ".env").write_text('RON_TEST_SECRET="very-secret"\n', encoding="utf-8")

    result = load_project_environment(tmp_path)

    assert os.environ["RON_TEST_SECRET"] == "very-secret"
    rendered = repr(result)
    assert "very-secret" not in rendered
    assert "RON_TEST_SECRET" in rendered
