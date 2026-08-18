"""Versioned, bounded JSON-lines protocol shared with Ron's tablet face."""

from __future__ import annotations

import json
from typing import Any

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 8_192


class ProtocolError(ValueError):
    """Raised when a tablet message is malformed or unsafe."""


def encode_message(message: dict[str, Any]) -> bytes:
    """Encode one compact newline-delimited message with a size limit."""
    try:
        payload = json.dumps(
            message,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ProtocolError("Message is not valid JSON") from error

    if len(payload) > MAX_MESSAGE_BYTES:
        raise ProtocolError("Message exceeds the protocol size limit")
    return payload + b"\n"


class JsonLineDecoder:
    """Safely rebuild JSON messages split or combined by a TCP stream."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        """Accept socket bytes and return every newly completed object."""
        if not data:
            return []
        self._buffer.extend(data)

        if len(self._buffer) > MAX_MESSAGE_BYTES and b"\n" not in self._buffer:
            self._buffer.clear()
            raise ProtocolError("Unterminated message exceeds the size limit")

        messages: list[dict[str, Any]] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                break

            raw = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            if not raw:
                continue
            if len(raw) > MAX_MESSAGE_BYTES:
                raise ProtocolError("Message exceeds the protocol size limit")

            try:
                decoded = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ProtocolError("Received malformed JSON") from error
            if not isinstance(decoded, dict):
                raise ProtocolError("Top-level protocol messages must be objects")
            messages.append(decoded)

        return messages
