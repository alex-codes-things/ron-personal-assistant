from pathlib import Path

import pytest

from ron.voice.settings import VoiceSettings, VoiceSettingsError


def test_voice_defaults_are_offline_local_and_bounded(monkeypatch, tmp_path: Path) -> None:
    for name in tuple(key for key in __import__("os").environ if key.startswith("RON_VOICE")):
        monkeypatch.delenv(name, raising=False)
    settings = VoiceSettings.from_environment(tmp_path)

    assert settings.enabled is True
    assert settings.wake_phrase == "hey ron"
    assert settings.wake_threshold == 0.35
    assert settings.kws_directory.is_absolute()
    assert settings.whisper_download_root.is_absolute()
    assert settings.maximum_speech_seconds == 15.0
    assert len(settings.hotwords) <= 20


def test_invalid_threshold_fails_before_microphone_start(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RON_WAKE_THRESHOLD", "2")

    with pytest.raises(VoiceSettingsError, match="RON_WAKE_THRESHOLD"):
        VoiceSettings.from_environment(tmp_path)


def test_blank_path_overrides_use_private_project_defaults(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RON_KWS_MODEL_DIR", "")
    monkeypatch.setenv("RON_VAD_MODEL", "")
    monkeypatch.setenv("RON_WHISPER_DOWNLOAD_ROOT", "")

    settings = VoiceSettings.from_environment(tmp_path)

    assert tmp_path.resolve() in settings.kws_directory.parents
    assert tmp_path.resolve() in settings.vad_model.parents
    assert tmp_path.resolve() in settings.whisper_download_root.parents
