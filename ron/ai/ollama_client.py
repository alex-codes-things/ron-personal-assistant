"""Small dependency-free client for Ollama's local streaming API."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ron import __version__
from ron.ai.errors import AIConnectionError, AIError, AIProtocolError, InferenceCancelled
from ron.ai.settings import LocalAISettings

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_STREAM_LINE_BYTES = 1024 * 1024
type TokenHandler = Callable[[str], None]


class OllamaError(AIError):
    """Base error for failures talking to local Ollama."""


class OllamaConnectionError(OllamaError, AIConnectionError):
    """Raised when the local Ollama service cannot be reached."""


class OllamaProtocolError(OllamaError, AIProtocolError):
    """Raised when Ollama returns malformed or unexpected data."""


@dataclass(frozen=True, slots=True)
class InferenceMetrics:
    """Timing data for one streamed inference request."""

    first_token_seconds: float | None
    elapsed_seconds: float
    total_duration_seconds: float | None
    load_duration_seconds: float | None
    prompt_tokens: int
    output_tokens: int
    tokens_per_second: float | None

    def to_dict(self) -> dict[str, float | int | None]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class InferenceResult:
    """Complete text and metrics from a streamed response."""

    model: str
    text: str
    done_reason: str | None
    metrics: InferenceMetrics


def _nanoseconds_to_seconds(value: Any) -> float | None:
    if not isinstance(value, int) or value < 0:
        return None
    return value / 1_000_000_000


def _positive_int(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


class OllamaClient:
    """Access only the loopback Ollama server configured for Ron."""

    def __init__(self, settings: LocalAISettings | None = None) -> None:
        self.settings = settings or LocalAISettings.from_environment()

    is_local = True

    @property
    def provider_label(self) -> str:
        return f"Ollama local ({self.settings.model})"

    def version(self) -> str:
        """Return the running Ollama version."""
        payload = self._request_json("GET", "/api/version")
        version = payload.get("version")
        if not isinstance(version, str) or not version:
            raise OllamaProtocolError("Ollama did not return a valid version")
        return version

    def model_names(self) -> frozenset[str]:
        """Return installed model names without guessing aliases."""
        payload = self._request_json("GET", "/api/tags")
        models = payload.get("models")
        if not isinstance(models, list):
            raise OllamaProtocolError("Ollama did not return a model list")

        names: set[str] = set()
        for model in models:
            if not isinstance(model, dict):
                continue
            for key in ("name", "model"):
                name = model.get(key)
                if isinstance(name, str) and name:
                    names.add(name)
        return frozenset(names)

    def has_configured_model(self) -> bool:
        """Check both explicit and implicit latest-tag forms."""
        names = self.model_names()
        return self.settings.model in names or f"{self.settings.model}:latest" in names

    def preload(self) -> None:
        """Load the configured model into memory without generating text."""
        self._request_json(
            "POST",
            "/api/generate",
            {
                "model": self.settings.model,
                "prompt": "",
                "stream": False,
                "keep_alive": self.settings.keep_alive,
                "options": {"num_ctx": self.settings.context_size},
            },
        )

    def stream_chat(
        self,
        messages: Iterable[Mapping[str, str]],
        *,
        on_token: TokenHandler | None = None,
        think: bool = False,
        max_output_tokens: int = 128,
        temperature: float = 0.2,
        cancel_event: threading.Event | None = None,
    ) -> InferenceResult:
        """Stream one chat request and report true time to first visible token."""
        message_list = self._validate_messages(messages)
        if not 1 <= max_output_tokens <= 8_192:
            raise ValueError("max_output_tokens must be between 1 and 8192")
        if not 0.0 <= temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        if cancel_event is not None and cancel_event.is_set():
            raise InferenceCancelled("The inference was cancelled")

        body = {
            "model": self.settings.model,
            "messages": message_list,
            "stream": True,
            "think": think,
            "keep_alive": self.settings.keep_alive,
            "options": {
                "num_ctx": self.settings.context_size,
                "num_predict": max_output_tokens,
                "temperature": temperature,
            },
        }
        request = self._build_request("POST", "/api/chat", body)
        started = perf_counter()
        first_token_seconds: float | None = None
        chunks: list[str] = []
        final_payload: dict[str, Any] | None = None
        bytes_received = 0

        try:
            with urlopen(request, timeout=self.settings.request_timeout_seconds) as response:
                for raw_line in response:
                    if cancel_event is not None and cancel_event.is_set():
                        raise InferenceCancelled("The inference was cancelled")
                    bytes_received += len(raw_line)
                    if bytes_received > MAX_RESPONSE_BYTES:
                        raise OllamaProtocolError("Ollama's streamed response was too large")
                    if len(raw_line) > MAX_STREAM_LINE_BYTES:
                        raise OllamaProtocolError("Ollama returned an oversized stream item")
                    if not raw_line.strip():
                        continue
                    payload = self._decode_object(raw_line)
                    error_message = payload.get("error")
                    if isinstance(error_message, str) and error_message:
                        raise OllamaProtocolError(f"Ollama rejected the request: {error_message}")
                    message = payload.get("message")
                    if isinstance(message, dict):
                        content = message.get("content")
                        if isinstance(content, str) and content:
                            if first_token_seconds is None:
                                first_token_seconds = perf_counter() - started
                            chunks.append(content)
                            if on_token is not None:
                                on_token(content)
                    if payload.get("done") is True:
                        final_payload = payload
        except OllamaError:
            raise
        except HTTPError as error:
            raise self._http_error(error) from error
        except (TimeoutError, URLError, OSError) as error:
            raise OllamaConnectionError(
                f"Could not reach Ollama at {self.settings.base_url}: {error}"
            ) from error

        elapsed_seconds = perf_counter() - started
        if cancel_event is not None and cancel_event.is_set():
            raise InferenceCancelled("The inference was cancelled")
        if final_payload is None:
            raise OllamaProtocolError("Ollama ended the stream without a completion record")

        eval_count = _positive_int(final_payload.get("eval_count"))
        eval_duration = _nanoseconds_to_seconds(final_payload.get("eval_duration"))
        tokens_per_second = None
        if eval_count and eval_duration and eval_duration > 0:
            tokens_per_second = eval_count / eval_duration

        metrics = InferenceMetrics(
            first_token_seconds=first_token_seconds,
            elapsed_seconds=elapsed_seconds,
            total_duration_seconds=_nanoseconds_to_seconds(final_payload.get("total_duration")),
            load_duration_seconds=_nanoseconds_to_seconds(final_payload.get("load_duration")),
            prompt_tokens=_positive_int(final_payload.get("prompt_eval_count")),
            output_tokens=eval_count,
            tokens_per_second=tokens_per_second,
        )
        done_reason = final_payload.get("done_reason")
        return InferenceResult(
            model=str(final_payload.get("model", self.settings.model)),
            text="".join(chunks),
            done_reason=done_reason if isinstance(done_reason, str) else None,
            metrics=metrics,
        )

    def _validate_messages(
        self, messages: Iterable[Mapping[str, str]]
    ) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        character_count = 0
        allowed_roles = {"system", "user", "assistant"}
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role not in allowed_roles or not isinstance(content, str):
                raise ValueError("Each message needs a valid role and text content")
            character_count += len(content)
            if character_count > self.settings.max_prompt_characters:
                raise ValueError("The combined prompt is larger than Ron's safety limit")
            result.append({"role": str(role), "content": content})
        if not result:
            raise ValueError("At least one message is required")
        return result

    def _request_json(
        self, method: str, path: str, body: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        request = self._build_request(method, path, body)
        try:
            with urlopen(request, timeout=self.settings.request_timeout_seconds) as response:
                raw_payload = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            raise self._http_error(error) from error
        except (TimeoutError, URLError, OSError) as error:
            raise OllamaConnectionError(
                f"Could not reach Ollama at {self.settings.base_url}: {error}"
            ) from error
        if len(raw_payload) > MAX_RESPONSE_BYTES:
            raise OllamaProtocolError("Ollama's response was too large")
        payload = self._decode_object(raw_payload)
        error_message = payload.get("error")
        if isinstance(error_message, str) and error_message:
            raise OllamaProtocolError(f"Ollama rejected the request: {error_message}")
        return payload

    def _build_request(
        self, method: str, path: str, body: Mapping[str, Any] | None = None
    ) -> Request:
        data = None
        headers = {"Accept": "application/json", "User-Agent": f"Ron/{__version__}"}
        if body is not None:
            data = json.dumps(body, allow_nan=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        return Request(
            f"{self.settings.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )

    @staticmethod
    def _decode_object(payload: bytes) -> dict[str, Any]:
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OllamaProtocolError("Ollama returned malformed JSON") from error
        if not isinstance(decoded, dict):
            raise OllamaProtocolError("Ollama returned a non-object JSON response")
        return decoded

    @staticmethod
    def _http_error(error: HTTPError) -> OllamaError:
        detail = ""
        try:
            payload = json.loads(error.read(64 * 1024))
            if isinstance(payload, dict) and isinstance(payload.get("error"), str):
                detail = f": {payload['error']}"
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        if error.code == 404:
            return OllamaProtocolError(f"Ollama API endpoint or model was not found{detail}")
        return OllamaConnectionError(f"Ollama returned HTTP {error.code}{detail}")
