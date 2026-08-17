from unittest.mock import patch

from ron.ai.settings import LocalAISettings, SettingsError


def test_default_ai_settings_are_local_and_bounded() -> None:
    with patch.dict("os.environ", {}, clear=True):
        settings = LocalAISettings.from_environment()

    assert settings.model == "qwen3.5:4b"
    assert settings.base_url == "http://127.0.0.1:11434"
    assert settings.keep_alive == -1
    assert settings.context_size == 8_192


def test_remote_ollama_url_is_rejected() -> None:
    try:
        LocalAISettings(base_url="http://192.168.1.20:11434")
    except SettingsError:
        return
    raise AssertionError("A remote inference server should not be enabled accidentally")


def test_invalid_context_environment_value_is_rejected() -> None:
    with patch.dict("os.environ", {"RON_MODEL_CONTEXT": "huge"}, clear=True):
        try:
            LocalAISettings.from_environment()
        except SettingsError:
            return
    raise AssertionError("A non-numeric model context should not be accepted")


def test_invalid_port_is_reported_as_a_settings_error() -> None:
    try:
        LocalAISettings(base_url="http://127.0.0.1:99999")
    except SettingsError:
        return
    raise AssertionError("An invalid local port should not be accepted")
