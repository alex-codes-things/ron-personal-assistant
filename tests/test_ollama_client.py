import json
from unittest.mock import patch

from ron.ai.benchmark import speed_rating
from ron.ai.ollama_client import OllamaClient, OllamaProtocolError
from ron.ai.settings import LocalAISettings


class FakeResponse:
    def __init__(self, lines: list[bytes] | None = None, payload: bytes = b"") -> None:
        self.lines = lines or []
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def __iter__(self):
        return iter(self.lines)

    def read(self, amount: int = -1) -> bytes:
        return self.payload[:amount] if amount >= 0 else self.payload


def json_line(value: dict[str, object]) -> bytes:
    return (json.dumps(value) + "\n").encode()


def test_stream_chat_combines_text_and_reads_metrics() -> None:
    response = FakeResponse(
        lines=[
            json_line({"model": "qwen3.5:4b", "message": {"content": "Hi"}, "done": False}),
            json_line({"model": "qwen3.5:4b", "message": {"content": "!"}, "done": False}),
            json_line(
                {
                    "model": "qwen3.5:4b",
                    "message": {"content": ""},
                    "done": True,
                    "done_reason": "stop",
                    "total_duration": 2_000_000_000,
                    "load_duration": 100_000_000,
                    "prompt_eval_count": 5,
                    "eval_count": 10,
                    "eval_duration": 500_000_000,
                }
            ),
        ]
    )
    tokens: list[str] = []
    with patch("ron.ai.ollama_client.urlopen", return_value=response):
        result = OllamaClient(LocalAISettings()).stream_chat(
            [{"role": "user", "content": "Hello"}], on_token=tokens.append
        )

    assert result.text == "Hi!"
    assert tokens == ["Hi", "!"]
    assert result.done_reason == "stop"
    assert result.metrics.output_tokens == 10
    assert result.metrics.tokens_per_second == 20.0
    assert result.metrics.first_token_seconds is not None


def test_stream_chat_rejects_missing_completion_record() -> None:
    response = FakeResponse(lines=[json_line({"message": {"content": "partial"}})])
    with patch("ron.ai.ollama_client.urlopen", return_value=response):
        try:
            OllamaClient(LocalAISettings()).stream_chat(
                [{"role": "user", "content": "Hello"}]
            )
        except OllamaProtocolError:
            return
    raise AssertionError("A truncated stream should not be accepted")


def test_stream_chat_reports_ollama_error_items() -> None:
    response = FakeResponse(lines=[json_line({"error": "model crashed"})])
    with patch("ron.ai.ollama_client.urlopen", return_value=response):
        try:
            OllamaClient(LocalAISettings()).stream_chat(
                [{"role": "user", "content": "Hello"}]
            )
        except OllamaProtocolError as error:
            assert "model crashed" in str(error)
            return
    raise AssertionError("An Ollama error stream item should not be ignored")


def test_model_list_supports_both_ollama_name_fields() -> None:
    response = FakeResponse(
        payload=json.dumps(
            {"models": [{"name": "qwen3.5:4b"}, {"model": "another:latest"}]}
        ).encode()
    )
    with patch("ron.ai.ollama_client.urlopen", return_value=response):
        names = OllamaClient(LocalAISettings()).model_names()
    assert names == {"qwen3.5:4b", "another:latest"}


def test_speed_rating_requires_low_latency_and_generation_speed() -> None:
    assert speed_rating(0.8, 25.0) == "excellent"
    assert speed_rating(2.0, 12.0) == "good"
    assert speed_rating(4.0, 6.0) == "usable"
    assert speed_rating(6.0, 20.0) == "slow"
