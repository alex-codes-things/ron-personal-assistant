import json
import threading
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from ron.ai import (
    AIConnectionError,
    AIProviderSettings,
    CloudAISettings,
    HybridAIClient,
    InferenceMetrics,
    InferenceResult,
    OllamaClient,
    OpenAIAuthenticationError,
    OpenAIClient,
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


def _result(text: str, model: str = "test") -> InferenceResult:
    return InferenceResult(
        model=model,
        text=text,
        done_reason="completed",
        metrics=InferenceMetrics(0.1, 0.2, None, None, 2, 2, 10.0),
    )


def _event(payload: dict[str, object]) -> bytes:
    return b"data: " + json.dumps(payload).encode("utf-8") + b"\n"


def test_openai_streams_visible_text_and_never_stores_the_response() -> None:
    final = {
        "id": "resp_test",
        "model": "gpt-5.4-mini",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "Hello Alex."}],
            }
        ],
        "usage": {"input_tokens": 8, "output_tokens": 3},
    }
    response = StreamResponse(
        [
            _event({"type": "response.output_text.delta", "delta": "Hello "}),
            b"\n",
            _event({"type": "response.output_text.delta", "delta": "Alex."}),
            b"\n",
            _event({"type": "response.completed", "response": final}),
            b"\n",
        ]
    )
    settings = CloudAISettings(api_key="sk-test-abcdefghijklmnopqrstuvwxyz")
    client = OpenAIClient(settings)
    tokens: list[str] = []

    with patch("ron.ai.openai_client.urlopen", return_value=response) as mocked:
        result = client.stream_chat(
            [{"role": "user", "content": "Hello"}], on_token=tokens.append
        )

    request = mocked.call_args.args[0]
    payload = json.loads(request.data)
    assert request.full_url == "https://api.openai.com/v1/responses"
    assert request.get_header("Authorization").startswith("Bearer sk-test-")
    assert payload["stream"] is True
    assert payload["store"] is False
    assert payload["reasoning"] == {"effort": "none"}
    assert tokens == ["Hello ", "Alex."]
    assert result.text == "Hello Alex."
    assert result.metrics.prompt_tokens == 8
    assert result.metrics.output_tokens == 3


def test_openai_key_is_redacted_from_settings_repr() -> None:
    secret = "sk-test-abcdefghijklmnopqrstuvwxyz"
    settings = CloudAISettings(api_key=secret)

    assert secret not in repr(settings)


def test_openai_reports_rejected_key_without_echoing_it() -> None:
    settings = CloudAISettings(api_key="sk-test-abcdefghijklmnopqrstuvwxyz")
    client = OpenAIClient(settings)
    error = HTTPError(
        "https://api.openai.com/v1/responses",
        401,
        "Unauthorized",
        {},
        BytesIO(b'{"error":{"message":"bad key"}}'),
    )

    with patch("ron.ai.openai_client.urlopen", side_effect=error):
        try:
            client.stream_chat([{"role": "user", "content": "Hello"}])
        except OpenAIAuthenticationError as caught:
            assert "OPENAI_API_KEY" in str(caught)
            assert settings.api_key not in str(caught)
            return
    raise AssertionError("A rejected key should produce a safe authentication error")


def test_provider_auto_uses_local_without_a_cloud_key() -> None:
    with patch.dict("os.environ", {}, clear=True):
        client = build_ai_client(AIProviderSettings("auto", True))

    assert isinstance(client, OllamaClient)


def test_provider_openai_requires_a_key() -> None:
    with patch.dict("os.environ", {}, clear=True):
        try:
            build_ai_client(AIProviderSettings("openai", False))
        except SettingsError:
            return
    raise AssertionError("Explicit cloud mode must require an API key")


class FakeClient:
    settings = object()

    def __init__(
        self,
        label: str,
        *,
        error: Exception | None = None,
        token_before_error: str = "",
    ) -> None:
        self.provider_label = label
        self.error = error
        self.token_before_error = token_before_error
        self.calls = 0
        self.is_local = label == "local"

    def stream_chat(self, messages, *, on_token=None, **options):
        del messages, options
        self.calls += 1
        if self.token_before_error and on_token is not None:
            on_token(self.token_before_error)
        if self.error is not None:
            raise self.error
        if on_token is not None:
            on_token(self.provider_label)
        return _result(self.provider_label)

    def version(self) -> str:
        return "test"

    def has_configured_model(self) -> bool:
        return True

    def preload(self) -> None:
        pass


def test_hybrid_falls_back_only_before_visible_cloud_output() -> None:
    cloud = FakeClient("cloud", error=AIConnectionError("offline"))
    local = FakeClient("local")
    client = HybridAIClient(cloud, local)
    tokens: list[str] = []

    result = client.stream_chat(
        [{"role": "user", "content": "Hello"}], on_token=tokens.append
    )

    assert result.text == "local"
    assert cloud.calls == 1
    assert local.calls == 1
    assert tokens == ["local"]
    assert "fallback in use" in client.provider_label


def test_hybrid_never_mixes_partial_cloud_text_with_local_text() -> None:
    cloud = FakeClient(
        "cloud",
        error=AIConnectionError("offline"),
        token_before_error="Partial",
    )
    local = FakeClient("local")
    client = HybridAIClient(cloud, local)
    tokens: list[str] = []

    try:
        client.stream_chat(
            [{"role": "user", "content": "Hello"}], on_token=tokens.append
        )
    except AIConnectionError:
        assert tokens == ["Partial"]
        assert local.calls == 0
        return
    raise AssertionError("A partial cloud response must never be followed by fallback text")


def test_cloud_cancellation_is_checked_before_network_access() -> None:
    cancelled = threading.Event()
    cancelled.set()
    client = OpenAIClient(CloudAISettings(api_key="sk-test-abcdefghijklmnopqrstuvwxyz"))

    with patch("ron.ai.openai_client.urlopen") as mocked:
        try:
            client.stream_chat(
                [{"role": "user", "content": "Hello"}], cancel_event=cancelled
            )
        except Exception as error:
            assert type(error).__name__ == "InferenceCancelled"
        else:
            raise AssertionError("A cancelled request should stop before the network")
    mocked.assert_not_called()
