"""Dependency-free streaming client for OpenAI's Responses API."""

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
from ron.ai.settings import CloudAISettings

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_STREAM_LINE_BYTES = 2 * 1024 * 1024


class OpenAIError(AIError):
    """Base error for OpenAI API failures."""


class OpenAIConnectionError(OpenAIError, AIConnectionError):
    """Raised when OpenAI cannot be reached or is temporarily unavailable."""


class OpenAIProtocolError(OpenAIError, AIProtocolError):
    """Raised when OpenAI returns malformed or incomplete data."""


class OpenAIAuthenticationError(OpenAIError, AIAuthenticationError):
    """Raised when the configured API key is rejected."""


class OpenAIClient:
    """Stream text from OpenAI while keeping all tool execution inside Ron."""

    is_local = False

    def __init__(self, settings: CloudAISettings | None = None) -> None:
        self.settings = settings or CloudAISettings.from_environment()

    @property
    def provider_label(self) -> str:
        return f"OpenAI cloud ({self.settings.model})"

    def version(self) -> str:
        """Return a local label without adding a startup network request."""
        return "Responses API"

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
        """Stream one response and report time to first visible text."""
        del think, temperature
        message_list = self._validate_messages(messages)
        if not 1 <= max_output_tokens <= 8_192:
            raise ValueError("max_output_tokens must be between 1 and 8192")
        if cancel_event is not None and cancel_event.is_set():
            raise InferenceCancelled("The inference was cancelled")

        body: dict[str, object] = {
            "model": self.settings.model,
            "input": message_list,
            "stream": True,
            "store": False,
            "max_output_tokens": max_output_tokens,
        }
        if self.settings.reasoning_effort:
            body["reasoning"] = {"effort": self.settings.reasoning_effort}

        request = self._build_request(body)
        started = perf_counter()
        first_token_seconds: float | None = None
        chunks: list[str] = []
        final_response: dict[str, Any] | None = None
        bytes_received = 0

        try:
            with urlopen(request, timeout=self.settings.request_timeout_seconds) as response:
                for raw_line in response:
                    if cancel_event is not None and cancel_event.is_set():
                        raise InferenceCancelled("The inference was cancelled")
                    bytes_received += len(raw_line)
                    if bytes_received > MAX_RESPONSE_BYTES:
                        raise OpenAIProtocolError("OpenAI's streamed response was too large")
                    if len(raw_line) > MAX_STREAM_LINE_BYTES:
                        raise OpenAIProtocolError("OpenAI returned an oversized stream item")
                    line = raw_line.strip()
                    if not line or not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == b"[DONE]":
                        continue
                    payload = self._decode_object(data)
                    event_type = payload.get("type")
                    if event_type == "response.output_text.delta":
                        delta = payload.get("delta")
                        if isinstance(delta, str) and delta:
                            if first_token_seconds is None:
                                first_token_seconds = perf_counter() - started
                            chunks.append(delta)
                            if on_token is not None:
                                on_token(delta)
                    elif event_type in {"response.completed", "response.incomplete"}:
                        response_payload = payload.get("response")
                        if isinstance(response_payload, dict):
                            final_response = response_payload
                    elif event_type in {"response.failed", "error"}:
                        raise OpenAIProtocolError(self._stream_error_message(payload))
        except (InferenceCancelled, OpenAIError):
            raise
        except HTTPError as error:
            raise self._http_error(error) from error
        except (TimeoutError, URLError, OSError) as error:
            raise OpenAIConnectionError(
                "Could not reach OpenAI. Check the internet connection and try again."
            ) from error

        elapsed_seconds = perf_counter() - started
        if cancel_event is not None and cancel_event.is_set():
            raise InferenceCancelled("The inference was cancelled")
        if final_response is None:
            raise OpenAIProtocolError("OpenAI ended the stream without a completion record")

        final_text = self._response_text(final_response)
        streamed_text = "".join(chunks)
        text = final_text or streamed_text
        if not chunks and text and on_token is not None:
            first_token_seconds = elapsed_seconds
            on_token(text)

        usage = final_response.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        input_tokens = self._positive_int(usage.get("input_tokens"))
        output_tokens = self._positive_int(usage.get("output_tokens"))
        tokens_per_second = (
            output_tokens / elapsed_seconds if output_tokens and elapsed_seconds else None
        )
        status = final_response.get("status")
        done_reason = str(status) if isinstance(status, str) else "completed"
        incomplete = final_response.get("incomplete_details")
        if isinstance(incomplete, dict) and isinstance(incomplete.get("reason"), str):
            done_reason = str(incomplete["reason"])

        return InferenceResult(
            model=str(final_response.get("model", self.settings.model)),
            text=text,
            done_reason=done_reason,
            metrics=InferenceMetrics(
                first_token_seconds=first_token_seconds,
                elapsed_seconds=elapsed_seconds,
                total_duration_seconds=None,
                load_duration_seconds=None,
                prompt_tokens=input_tokens,
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
                raise ValueError("The combined prompt is larger than Ron's safety limit")
            result.append({"role": str(role), "content": content})
        if not result:
            raise ValueError("At least one message is required")
        return result

    def _build_request(self, body: Mapping[str, object]) -> Request:
        headers = {
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
            "User-Agent": f"Ron/{__version__}",
        }
        if self.settings.project_id:
            headers["OpenAI-Project"] = self.settings.project_id
        return Request(
            f"{self.settings.base_url}/responses",
            data=json.dumps(body, allow_nan=False, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )

    @staticmethod
    def _decode_object(payload: bytes) -> dict[str, Any]:
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OpenAIProtocolError("OpenAI returned malformed JSON") from error
        if not isinstance(decoded, dict):
            raise OpenAIProtocolError("OpenAI returned a non-object JSON response")
        return decoded

    @staticmethod
    def _response_text(response: Mapping[str, Any]) -> str:
        output = response.get("output")
        if not isinstance(output, list):
            return ""
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
                refusal = part.get("refusal")
                if isinstance(refusal, str) and refusal:
                    parts.append(refusal)
        return "".join(parts)

    @staticmethod
    def _positive_int(value: object) -> int:
        return value if isinstance(value, int) and value >= 0 else 0

    @staticmethod
    def _stream_error_message(payload: Mapping[str, Any]) -> str:
        error = payload.get("error")
        if not isinstance(error, dict):
            response = payload.get("response")
            error = response.get("error") if isinstance(response, dict) else None
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return f"OpenAI rejected the request: {error['message'][:300]}"
        return "OpenAI could not complete the streamed response"

    @staticmethod
    def _http_error(error: HTTPError) -> AIError:
        if error.code in {401, 403}:
            return OpenAIAuthenticationError(
                "The OpenAI API key was rejected. Check OPENAI_API_KEY in .env."
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
            return OpenAIConnectionError(
                "OpenAI is rate-limited or the API balance is unavailable" + detail
            )
        if error.code >= 500:
            return OpenAIConnectionError(f"OpenAI is temporarily unavailable{detail}")
        return OpenAIProtocolError(f"OpenAI returned HTTP {error.code}{detail}")
