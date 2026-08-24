import json
import threading
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from ron.ai import (
    AIProviderSettings,
    GroqAISettings,
    GroqAuthenticationError,
    GroqClient,
    GroqConnectionError,
    HybridAIClient,
    SettingsError,
    build_ai_client,
)


class StreamResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines

    def __enter__(self):
        return iter(self.lines)

    def __exit__(self, *args) -> None:
        del args


def _event(payload: dict[str, object]) -> bytes:
    return b"data: " + json.dumps(payload).encode("utf-8") + b"\n"


def test_groq_streams_only_final_text_with_low_reasoning_effort() -> None:
    response = StreamResponse(
        [
            _event(
                {
                    "id": "chatcmpl_test",
                    "model": "openai/gpt-oss-120b",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "Hello "},
                            "finish_reason": None,
                        }
                    ],
                }
            ),
            _event(
                {
                    "id": "chatcmpl_test",
                    "model": "openai/gpt-oss-120b",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "Alex."},
                            "finish_reason": "stop",
                        }
                    ],
                    "x_groq": {
                        "usage": {"prompt_tokens": 8, "completion_tokens": 3}
                    },
                }
            ),
            b"data: [DONE]\n",
        ]
    )
    settings = GroqAISettings(api_key="gsk_test_abcdefghijklmnopqrstuvwxyz")
    client = GroqClient(settings)
    tokens: list[str] = []

    with patch("ron.ai.groq_client.urlopen", return_value=response) as mocked:
        result = client.stream_chat(
            [{"role": "user", "content": "Hello"}], on_token=tokens.append
        )

    request = mocked.call_args.args[0]
    payload = json.loads(request.data)
    assert request.full_url == "https://api.groq.com/openai/v1/chat/completions"
    assert request.get_header("Authorization").startswith("Bearer gsk_test_")
    assert payload["stream"] is True
    assert payload["reasoning_effort"] == "low"
    assert payload["include_reasoning"] is False
    assert payload["max_completion_tokens"] == 128
    assert tokens == ["Hello ", "Alex."]
    assert result.text == "Hello Alex."
    assert result.done_reason == "stop"
    assert result.metrics.prompt_tokens == 8
    assert result.metrics.output_tokens == 3


def test_groq_zero_temperature_is_made_api_compatible() -> None:
    response = StreamResponse(
        [
            _event(
                {
                    "choices": [
                        {"delta": {"content": "OK"}, "finish_reason": "stop"}
                    ]
                }
            ),
            b"data: [DONE]\n",
        ]
    )
    client = GroqClient(GroqAISettings(api_key="gsk_test_abcdefghijklmnopqrstuvwxyz"))

    with patch("ron.ai.groq_client.urlopen", return_value=response) as mocked:
        client.stream_chat([{"role": "user", "content": "Test"}], temperature=0)

    payload = json.loads(mocked.call_args.args[0].data)
    assert payload["temperature"] == 1e-8


def test_groq_key_is_redacted_from_settings_repr() -> None:
    secret = "gsk_test_abcdefghijklmnopqrstuvwxyz"
    settings = GroqAISettings(api_key=secret)

    assert secret not in repr(settings)


def test_groq_reports_rejected_key_without_echoing_it() -> None:
    settings = GroqAISettings(api_key="gsk_test_abcdefghijklmnopqrstuvwxyz")
    client = GroqClient(settings)
    error = HTTPError(
        "https://api.groq.com/openai/v1/chat/completions",
        401,
        "Unauthorized",
        {},
        BytesIO(b'{"error":{"message":"bad key"}}'),
    )

    with patch("ron.ai.groq_client.urlopen", side_effect=error):
        try:
            client.stream_chat([{"role": "user", "content": "Hello"}])
        except GroqAuthenticationError as caught:
            assert "GROQ_API_KEY" in str(caught)
            assert settings.api_key not in str(caught)
            return
    raise AssertionError("A rejected key should produce a safe authentication error")


def test_groq_rate_limit_can_trigger_safe_hybrid_fallback() -> None:
    settings = GroqAISettings(api_key="gsk_test_abcdefghijklmnopqrstuvwxyz")
    client = GroqClient(settings)
    error = HTTPError(
        "https://api.groq.com/openai/v1/chat/completions",
        429,
        "Too Many Requests",
        {},
        BytesIO(b'{"error":{"message":"rate limit exceeded"}}'),
    )

    with patch("ron.ai.groq_client.urlopen", side_effect=error):
        try:
            client.stream_chat([{"role": "user", "content": "Hello"}])
        except GroqConnectionError as caught:
            assert "free-plan rate limit" in str(caught)
            return
    raise AssertionError("A rate limit should be treated as a recoverable cloud outage")


def test_provider_auto_prefers_groq_when_both_cloud_keys_exist() -> None:
    environment = {
        "GROQ_API_KEY": "gsk_test_abcdefghijklmnopqrstuvwxyz",
        "OPENAI_API_KEY": "sk-test-abcdefghijklmnopqrstuvwxyz",
    }
    with patch.dict("os.environ", environment, clear=True):
        client = build_ai_client(AIProviderSettings("auto", False))

    assert isinstance(client, GroqClient)


def test_provider_groq_can_keep_a_cold_local_fallback() -> None:
    with patch.dict(
        "os.environ",
        {"GROQ_API_KEY": "gsk_test_abcdefghijklmnopqrstuvwxyz"},
        clear=True,
    ):
        client = build_ai_client(AIProviderSettings("groq", True))

    assert isinstance(client, HybridAIClient)
    assert isinstance(client.primary, GroqClient)


def test_provider_groq_requires_a_key() -> None:
    with patch.dict("os.environ", {}, clear=True):
        try:
            build_ai_client(AIProviderSettings("groq", False))
        except SettingsError:
            return
    raise AssertionError("Explicit Groq mode must require an API key")


def test_groq_cancellation_is_checked_before_network_access() -> None:
    cancelled = threading.Event()
    cancelled.set()
    client = GroqClient(GroqAISettings(api_key="gsk_test_abcdefghijklmnopqrstuvwxyz"))

    with patch("ron.ai.groq_client.urlopen") as mocked:
        try:
            client.stream_chat(
                [{"role": "user", "content": "Hello"}], cancel_event=cancelled
            )
        except Exception as error:
            assert type(error).__name__ == "InferenceCancelled"
        else:
            raise AssertionError("A cancelled request should stop before the network")
    mocked.assert_not_called()
