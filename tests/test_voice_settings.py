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
    assert settings.wake_sensitivity == "high"
    assert settings.wake_ack_echo_guard_seconds == 0.06
    assert settings.wake_fast_handoff is True
    assert settings.kws_directory.is_absolute()
    assert settings.whisper_download_root.is_absolute()
    assert settings.maximum_speech_seconds == 15.0
    assert len(settings.hotwords) <= 20
    assert settings.tts_enabled is True
    assert settings.tts_voice == "bm_george"
    assert settings.tts_language == "en-gb"
    assert settings.tts_model.is_absolute()


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


def test_invalid_tts_speed_fails_before_runtime(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RON_TTS_SPEED", "3")

    with pytest.raises(VoiceSettingsError, match="RON_TTS_SPEED"):
        VoiceSettings.from_environment(tmp_path)


def test_interaction_profiles_are_explicit_and_strict_by_default(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("RON_INTERACTION_MODE", raising=False)
    monkeypatch.delenv("RON_VOICE_AUTO_FOLLOWUP", raising=False)
    strict = VoiceSettings.from_environment(tmp_path)
    assert strict.interaction_mode == "strict"
    assert strict.followup_enabled is False

    monkeypatch.setenv("RON_INTERACTION_MODE", "followup")
    followup = VoiceSettings.from_environment(tmp_path)
    assert followup.followup_enabled is True

    monkeypatch.setenv("RON_INTERACTION_MODE", "invalid")
    with pytest.raises(VoiceSettingsError, match="RON_INTERACTION_MODE"):
        VoiceSettings.from_environment(tmp_path)


def test_accuracy_first_asr_defaults(monkeypatch, tmp_path: Path) -> None:
    for name in (
        "RON_WHISPER_MODEL",
        "RON_WHISPER_BEAM_SIZE",
        "RON_WHISPER_FAST_BEAM_SIZE",
        "RON_WHISPER_RETRY_BEAM_SIZE",
        "RON_WHISPER_PATIENCE",
        "RON_WHISPER_INITIAL_PROMPT",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = VoiceSettings.from_environment(tmp_path)

    assert settings.asr_model == "distil-large-v3"
    assert settings.asr_beam_size == 1
    assert settings.asr_retry_beam_size == 5
    assert settings.asr_retry_enabled is True
    assert settings.asr_patience == 1.0
    assert "Spotify" in settings.asr_initial_prompt
    assert "tehran" in settings.wake_kws_aliases
    assert "aaron" in settings.wake_kws_aliases
    assert settings.interaction_mode == "strict"
    assert settings.automatic_followup is False
    assert settings.followup_enabled is False
    assert settings.end_silence_seconds == 0.38


def test_wake_acknowledgement_defaults_are_short_and_configurable(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("RON_WAKE_ACK_ENABLED", raising=False)
    monkeypatch.delenv("RON_WAKE_ACK_PHRASES", raising=False)
    monkeypatch.delenv("RON_WAKE_FOLLOWUP_WAIT", raising=False)

    defaults = VoiceSettings.from_environment(tmp_path)
    assert defaults.wake_ack_enabled is True
    assert defaults.wake_acknowledgements[0] == "Yes?"
    assert defaults.wake_followup_timeout_seconds == 8.0

    monkeypatch.setenv("RON_WAKE_ACK_PHRASES", "Ready.|Go ahead.")
    monkeypatch.setenv("RON_WAKE_FOLLOWUP_WAIT", "8")
    configured = VoiceSettings.from_environment(tmp_path)
    assert configured.wake_acknowledgements == ("Ready.", "Go ahead.")
    assert configured.wake_followup_timeout_seconds == 8.0
