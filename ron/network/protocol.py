"""Shared bounded JSON-lines and discovery protocol for Ron devices."""

from __future__ import annotations

import hmac
import ipaddress
import json
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 8_192
DISCOVERY_REQUEST_TYPE = "ron_discover"
DISCOVERY_RESPONSE_TYPE = "ron_device"


class ProtocolError(ValueError):
    """Raised when a Ron network message is malformed or unsafe."""


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
                decoded = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ProtocolError("Received malformed JSON") from error
            if not isinstance(decoded, dict):
                raise ProtocolError("Top-level protocol messages must be objects")
            messages.append(decoded)

        return messages


@dataclass(frozen=True, slots=True)
class DiscoveryReply:
    """Validated non-secret information advertised by one Ron device."""

    device_id: str
    friendly_name: str
    device_type: str
    ip_address: str
    port: int
    capabilities: frozenset[str]
    metadata: dict[str, Any]


def build_discovery_request(request_id: str) -> bytes:
    if not request_id or len(request_id) > 80:
        raise ProtocolError("Discovery request ID is invalid")
    return encode_datagram(
        {
            "type": DISCOVERY_REQUEST_TYPE,
            "protocol": PROTOCOL_VERSION,
            "request_id": request_id,
            "requester": "ron-brain",
        }
    )


def parse_discovery_reply(
    payload: bytes,
    source_ip: str,
    *,
    expected_request_id: str | None = None,
) -> DiscoveryReply:
    message = decode_datagram(payload)
    if message.get("type") != DISCOVERY_RESPONSE_TYPE:
        raise ProtocolError("Not a Ron discovery response")
    if message.get("protocol") != PROTOCOL_VERSION:
        raise ProtocolError("Discovery protocol version mismatch")
    if expected_request_id is not None and message.get("request_id") != expected_request_id:
        raise ProtocolError("Discovery response does not match this request")

    device_id = _bounded_string(message.get("device_id"), "device_id", 80).lower()
    friendly_name = _bounded_string(message.get("friendly_name", device_id), "friendly_name", 120)
    device_type = _bounded_string(message.get("device_type", "unknown"), "device_type", 60).lower()
    try:
        address = str(ipaddress.ip_address(source_ip))
    except ValueError as error:
        raise ProtocolError("Discovery source address is invalid") from error

    port = message.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535:
        raise ProtocolError("Discovery response contains an invalid port")

    raw_capabilities = message.get("capabilities", [])
    if not isinstance(raw_capabilities, list) or len(raw_capabilities) > 32:
        raise ProtocolError("Discovery capabilities are invalid")
    capabilities = frozenset(
        _bounded_string(value, "capability", 80).lower()
        for value in raw_capabilities
    )

    raw_metadata = message.get("metadata", {})
    if not isinstance(raw_metadata, dict):
        raise ProtocolError("Discovery metadata must be an object")
    metadata: dict[str, Any] = {}
    for key, value in list(raw_metadata.items())[:16]:
        if isinstance(key, str) and len(key) <= 80 and isinstance(value, (str, int, float, bool)):
            metadata[key] = value

    return DiscoveryReply(
        device_id=device_id,
        friendly_name=friendly_name,
        device_type=device_type,
        ip_address=address,
        port=port,
        capabilities=capabilities,
        metadata=metadata,
    )


def encode_datagram(message: dict[str, Any]) -> bytes:
    encoded = encode_message(message)
    return encoded[:-1]


def decode_datagram(payload: bytes) -> dict[str, Any]:
    if not payload or len(payload) > MAX_MESSAGE_BYTES:
        raise ProtocolError("Discovery datagram has an invalid size")
    try:
        decoded = json.loads(payload.decode("utf-8"), parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError("Discovery datagram contains malformed JSON") from error
    if not isinstance(decoded, dict):
        raise ProtocolError("Discovery datagram must be a JSON object")
    return decoded


def pairing_tokens_match(expected: str, presented: str) -> bool:
    """Compare pairing secrets without leaking useful prefix timing."""
    if not isinstance(expected, str) or not isinstance(presented, str):
        return False
    if len(expected) < 32 or len(presented) < 32:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), presented.encode("utf-8"))


def _bounded_string(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ProtocolError(f"Discovery {field} must be text")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum:
        raise ProtocolError(f"Discovery {field} has an invalid length")
    return cleaned


def _reject_json_constant(value: str) -> None:
    raise ProtocolError(f"Non-finite JSON value is not allowed: {value}")
