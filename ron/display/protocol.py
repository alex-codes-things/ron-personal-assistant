"""Compatibility exports for Ron's shared network protocol.

The protocol now lives in :mod:`ron.network.protocol` so the tablet and future
Ron devices use one bounded JSON-lines implementation.
"""

from ron.network.protocol import (
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    JsonLineDecoder,
    ProtocolError,
    encode_message,
)

__all__ = [
    "MAX_MESSAGE_BYTES",
    "PROTOCOL_VERSION",
    "JsonLineDecoder",
    "ProtocolError",
    "encode_message",
]
