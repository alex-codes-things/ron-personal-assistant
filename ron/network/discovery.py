"""Low-frequency UDP discovery for Ron devices on the same local network."""

from __future__ import annotations

import logging
import secrets
import socket
from dataclasses import dataclass
from time import monotonic

from ron.network.protocol import (
    DiscoveryReply,
    ProtocolError,
    build_discovery_request,
    parse_discovery_reply,
)


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    port: int = 8766
    timeout: float = 0.35


class LanDiscovery:
    """Perform one short discovery round; scheduling belongs to NetworkService."""

    def __init__(self, config: DiscoveryConfig | None = None) -> None:
        self.config = config or DiscoveryConfig()
        self._logger = logging.getLogger(__name__)

    def discover_once(self) -> tuple[DiscoveryReply, ...]:
        request_id = secrets.token_urlsafe(9)
        request = build_discovery_request(request_id)
        replies: dict[str, DiscoveryReply] = {}

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", 0))
            sock.settimeout(min(0.2, self.config.timeout))
            sock.sendto(request, ("255.255.255.255", self.config.port))

            deadline = monotonic() + self.config.timeout
            while monotonic() < deadline:
                try:
                    payload, source = sock.recvfrom(8192)
                except TimeoutError:
                    continue
                except OSError:
                    break
                try:
                    reply = parse_discovery_reply(
                        payload,
                        source[0],
                        expected_request_id=request_id,
                    )
                except ProtocolError as error:
                    self._logger.debug("Ignored invalid discovery response: %s", error)
                    continue
                replies[reply.device_id] = reply

        return tuple(replies.values())
