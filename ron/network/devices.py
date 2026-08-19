"""Small thread-safe registry for devices that belong to Ron's local network."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from threading import RLock
from time import monotonic
from typing import Any


class DeviceConnectionState(StrEnum):
    """A deliberately small set of useful device health states."""

    UNKNOWN = "unknown"
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class DeviceTrustState(StrEnum):
    """Whether Ron may trust control messages from a discovered device."""

    TRUSTED = "trusted"
    UNPAIRED = "unpaired"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class RonDevice:
    """One network-aware device as understood by Ron's laptop brain."""

    device_id: str
    friendly_name: str
    device_type: str
    ip_address: str | None = None
    port: int | None = None
    last_seen: float | None = None
    connection_state: DeviceConnectionState = DeviceConnectionState.UNKNOWN
    trust_state: DeviceTrustState = DeviceTrustState.UNKNOWN
    capabilities: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)
    network_reachable: bool = False
    service_responsive: bool = False

    def copy(self) -> RonDevice:
        """Return an isolated snapshot safe for callers to inspect."""
        return replace(
            self,
            capabilities=set(self.capabilities),
            metadata=dict(self.metadata),
        )


class DeviceRegistry:
    """Keep device state in memory without turning it into a database layer."""

    def __init__(self) -> None:
        self._devices: dict[str, RonDevice] = {}
        self._lock = RLock()

    def register(self, device: RonDevice) -> RonDevice:
        """Register or merge a device while preserving recent runtime state."""
        device_id = _normalise_device_id(device.device_id)
        with self._lock:
            existing = self._devices.get(device_id)
            if existing is None:
                stored = device.copy()
                stored.device_id = device_id
                self._devices[device_id] = stored
                return stored.copy()

            if device.friendly_name:
                existing.friendly_name = device.friendly_name
            if device.device_type:
                existing.device_type = device.device_type
            if device.ip_address:
                existing.ip_address = device.ip_address
            if device.port is not None:
                existing.port = device.port
            existing.capabilities.update(device.capabilities)
            existing.metadata.update(device.metadata)
            if device.trust_state is not DeviceTrustState.UNKNOWN:
                existing.trust_state = device.trust_state
            return existing.copy()

    def get(self, device_id: str) -> RonDevice | None:
        with self._lock:
            device = self._devices.get(_normalise_device_id(device_id))
            return device.copy() if device is not None else None

    def all(self) -> tuple[RonDevice, ...]:
        with self._lock:
            return tuple(device.copy() for device in self._devices.values())

    def update_presence(
        self,
        device_id: str,
        *,
        ip_address: str | None = None,
        port: int | None = None,
        state: DeviceConnectionState | None = None,
        trust_state: DeviceTrustState | None = None,
        network_reachable: bool | None = None,
        service_responsive: bool | None = None,
        capabilities: set[str] | None = None,
        metadata: dict[str, Any] | None = None,
        seen_at: float | None = None,
    ) -> tuple[RonDevice, DeviceConnectionState]:
        """Update one device and return its new snapshot plus previous state."""
        normalised = _normalise_device_id(device_id)
        with self._lock:
            device = self._devices.get(normalised)
            if device is None:
                device = RonDevice(normalised, normalised, "unknown")
                self._devices[normalised] = device

            previous = device.connection_state
            if ip_address is not None:
                device.ip_address = ip_address
            if port is not None:
                device.port = port
            if trust_state is not None:
                device.trust_state = trust_state
            if network_reachable is not None:
                device.network_reachable = network_reachable
            if service_responsive is not None:
                device.service_responsive = service_responsive
            if capabilities:
                device.capabilities.update(capabilities)
            if metadata:
                device.metadata.update(metadata)
            if seen_at is not None:
                device.last_seen = seen_at
            elif network_reachable or service_responsive:
                device.last_seen = monotonic()
            if state is not None:
                device.connection_state = state
            return device.copy(), previous

    def remove(self, device_id: str) -> RonDevice | None:
        with self._lock:
            device = self._devices.pop(_normalise_device_id(device_id), None)
            return device.copy() if device is not None else None


def _normalise_device_id(device_id: str) -> str:
    value = device_id.strip().lower()
    if not value or len(value) > 80:
        raise ValueError("Device ID must contain 1-80 characters")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-_.")
    if any(character not in allowed for character in value):
        raise ValueError("Device ID contains unsupported characters")
    return value
