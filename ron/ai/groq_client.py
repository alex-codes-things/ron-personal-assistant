"""Dependency-free streaming client for Groq's Chat Completions API."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable, Mapping
from time import perf_counter
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ron import __version__
from ron.ai.errors import (
    AIAuthenticationError,
    AIConnectionError,
    AIError,
    AIProtocolError,
    InferenceCancelled,
)
from ron.ai.ollama_client import InferenceMetrics, InferenceResult, TokenHandler
from ron.ai.settings import GroqAISettings

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_STREAM_LINE_BYTES = 2 * 1024 * 1024


class GroqError(AIError):
    """Base error for Groq API failures."""


class GroqConnectionError(GroqError, AIConnectionError):
    """Raised when Groq cannot be reached or is temporarily unavailable."""


class GroqProtocolError(GroqError, AIProtocolError):
    """Raised when Groq returns malformed or incomplete data."""


class GroqAuthenticationError(GroqError, AIAuthenticationError):
    """Raised when the configured Groq API key is rejected."""


class GroqClient:
    """Stream Groq text while keeping tool validation and execution inside Ron."""

    is_local = False

    def __init__(self, settings: GroqAISettings | None = None) -> None:
        self.settings = settings or GroqAISettings.from_environment()

    @property
    def provider_label(self) -> str:
        return f"Groq free cloud ({self.settings.model})"

    def version(self) -> str:
        """Return a local label without adding a startup network request."""
        return "Chat Completions API"

    def has_configured_model(self) -> bool:
        """Cloud model access is verified lazily on the first normal request."""
        return True

    def preload(self) -> None:
        """Cloud models require no laptop-side preload."""

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
        """Stream one final-answer response and report time to first visible text."""
        del think
        message_list = self._validate_messages(messages)
        if not 1 <= max_output_tokens <= 8_192:
            raise ValueError("max_output_tokens must be between 1 and 8192")
        if not 0.0 <= temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        if cancel_event is not None and cancel_event.is_set():
            raise InferenceCancelled("The inference was cancelled")

        body: dict[str, object] = {
            "model": self.settings.model,
            "messages": message_list,
            "stream": True,
            "max_completion_tokens": max_output_tokens,
            "temperature": max(temperature, 1e-8),
            "reasoning_effort": self.settings.reasoning_effort,
            "include_reasoning": False,
        }
        request = self._build_request(body)
        started = perf_counter()
        first_token_seconds: float | None = None
        chunks: list[str] = []
        finish_reason: str | None = None
        model = self.settings.model
        prompt_tokens = 0
        output_tokens = 0
        bytes_received = 0
        saw_done = False

        try:
            with urlopen(request, timeout=self.settings.request_timeout_seconds) as response:
                for raw_line in response:
                    if cancel_event is not None and cancel_event.is_set():
                        raise InferenceCancelled("The inference was cancelled")
                    bytes_received += len(raw_line)
                    if bytes_received > MAX_RESPONSE_BYTES:
                        raise GroqProtocolError("Groq's streamed response was too large")
                    if len(raw_line) > MAX_STREAM_LINE_BYTES:
                        raise GroqProtocolError("Groq returned an oversized stream item")
                    line = raw_line.strip()
                    if not line or not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    if data == b"[DONE]":
                        saw_done = True
                        continue
                    payload = self._decode_object(data)
                    if isinstance(payload.get("error"), dict):
                        raise GroqProtocolError(self._stream_error_message(payload))
                    payload_model = payload.get("model")
                    if isinstance(payload_model, str) and payload_model:
                        model = payload_model
                    choices = payload.get("choices")
                    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                        choice = choices[0]
                        delta = choice.get("delta")
                        if isinstance(delta, dict):
                            content = delta.get("content")
                            if isinstance(content, str) and content:
                                if first_token_seconds is None:
                                    first_token_seconds = perf_counter() - started
                                chunks.append(content)
                                if on_token is not None:
                                    on_token(content)
                        reason = choice.get("finish_reason")
                        if isinstance(reason, str) and reason:
                            finish_reason = reason
                    usage = self._find_usage(payload)
                    if usage:
                        prompt_tokens = self._positive_int(
                            usage.get("prompt_tokens", usage.get("input_tokens"))
                        )
                        output_tokens = self._positive_int(
                            usage.get("completion_tokens", usage.get("output_tokens"))
                        )
        except (InferenceCancelled, GroqError):
            raise
        except HTTPError as error:
            raise self._http_error(error) from error
        except (TimeoutError, URLError, OSError) as error:
            raise GroqConnectionError(
                "Could not reach Groq. Check the internet connection and try again."
            ) from error

        elapsed_seconds = perf_counter() - started
        if cancel_event is not None and cancel_event.is_set():
            raise InferenceCancelled("The inference was cancelled")
        if finish_reason is None and not saw_done:
            raise GroqProtocolError("Groq ended the stream without a completion record")
        text = "".join(chunks)
        if not text:
            raise GroqProtocolError("Groq completed the request without a text response")
        tokens_per_second = (
            output_tokens / elapsed_seconds if output_tokens and elapsed_seconds else None
        )

        return InferenceResult(
            model=model,
            text=text,
            done_reason=finish_reason or "completed",
            metrics=InferenceMetrics(
                first_token_seconds=first_token_seconds,
                elapsed_seconds=elapsed_seconds,
                total_duration_seconds=None,
                load_duration_seconds=None,
                prompt_tokens=prompt_tokens,
                output_tokens=output_tokens,
                tokens_per_second=tokens_per_second,
            ),
        )

    def _validate_messages(
        self, messages: Iterable[Mapping[str, str]]
    ) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        character_count = 0
        allowed_roles = {"system", "developer", "user", "assistant"}
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role not in allowed_roles or not isinstance(content, str):
                raise ValueError("Each message needs a valid role and text content")
            character_count += len(content)
            if character_count > self.settings.max_prompt_characters:
                raise ValueError("The combined prompt is larger than Ron's Groq safety limit")
            result.append({"role": str(role), "content": content})
        if not result:
            raise ValueError("At least one message is required")
        return result

    def _build_request(self, body: Mapping[str, object]) -> Request:
        return Request(
            f"{self.settings.base_url}/chat/completions",
            data=json.dumps(body, allow_nan=False, separators=(",", ":")).encode("utf-8"),
            headers={
                "Accept": "text/event-stream",
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
                "User-Agent": f"Ron/{__version__}",
            },
            method="POST",
        )

    @staticmethod
    def _decode_object(payload: bytes) -> dict[str, Any]:
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GroqProtocolError("Groq returned malformed JSON") from error
        if not isinstance(decoded, dict):
            raise GroqProtocolError("Groq returned a non-object JSON response")
        return decoded

    @staticmethod
    def _find_usage(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        usage = payload.get("usage")
        if isinstance(usage, dict):
            return usage
        x_groq = payload.get("x_groq")
        if isinstance(x_groq, dict) and isinstance(x_groq.get("usage"), dict):
            return x_groq["usage"]
        return {}

    @staticmethod
    def _positive_int(value: object) -> int:
        return value if isinstance(value, int) and value >= 0 else 0

    @staticmethod
    def _stream_error_message(payload: Mapping[str, Any]) -> str:
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return f"Groq rejected the request: {error['message'][:300]}"
        return "Groq could not complete the streamed response"

    @staticmethod
    def _http_error(error: HTTPError) -> AIError:
        if error.code in {401, 403}:
            return GroqAuthenticationError(
                "The Groq API key was rejected. Check GROQ_API_KEY in .env."
            )
        detail = ""
        try:
            payload = json.loads(error.read(64 * 1024))
            if isinstance(payload, dict):
                api_error = payload.get("error")
                if isinstance(api_error, dict) and isinstance(api_error.get("message"), str):
                    detail = f": {api_error['message'][:300]}"
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        if error.code == 429:
            return GroqConnectionError("Groq's free-plan rate limit was reached" + detail)
        if error.code >= 500:
            return GroqConnectionError(f"Groq is temporarily unavailable{detail}")
        return GroqProtocolError(f"Groq returned HTTP {error.code}{detail}")
