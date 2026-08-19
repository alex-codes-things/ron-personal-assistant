"""Optional background network awareness for Ron's local devices."""

from __future__ import annotations

import logging
import math
import os
import threading
from dataclasses import dataclass
from time import monotonic
from typing import Any

from ron.core import Coordinator, EventType, RonEvent
from ron.network.devices import (
    DeviceConnectionState,
    DeviceRegistry,
    DeviceTrustState,
    RonDevice,
)
from ron.network.discovery import DiscoveryConfig, LanDiscovery
from ron.network.protocol import DiscoveryReply


@dataclass(frozen=True, slots=True)
class NetworkSettings:
    """A few practical network settings with safe defaults."""

    enabled: bool = True
    discovery_enabled: bool = True
    discovery_port: int = 8766
    discovery_interval: float = 10.0
    degraded_after: float = 8.0
    offline_after: float = 20.0
    face_host: str | None = None
    face_port: int = 8765

    @classmethod
    def from_environment(cls) -> NetworkSettings:
        degraded = _float_env("RON_NETWORK_DEGRADED_AFTER", 8.0, 3.0, 120.0)
        offline = _float_env("RON_NETWORK_DEVICE_TIMEOUT", 20.0, degraded + 1.0, 300.0)
        return cls(
            enabled=_bool_env("RON_NETWORK_ENABLED", True),
            discovery_enabled=_bool_env("RON_NETWORK_DISCOVERY", True),
            discovery_port=_int_env("RON_NETWORK_DISCOVERY_PORT", 8766, 1024, 65_535),
            discovery_interval=_float_env("RON_NETWORK_DISCOVERY_INTERVAL", 10.0, 3.0, 120.0),
            degraded_after=degraded,
            offline_after=offline,
            face_host=(os.getenv("RON_FACE_HOST") or "").strip() or None,
            face_port=_int_env("RON_FACE_PORT", 8765, 1, 65_535),
        )


class NetworkService:
    """Track Ron devices without making local assistant features depend on them."""

    def __init__(
        self,
        coordinator: Coordinator,
        settings: NetworkSettings | None = None,
        *,
        discovery: LanDiscovery | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.settings = settings or NetworkSettings.from_environment()
        self.registry = DeviceRegistry()
        self._discovery = discovery or LanDiscovery(
            DiscoveryConfig(port=self.settings.discovery_port)
        )
        self._logger = logging.getLogger(__name__)
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._available = False
        self._last_discovery = 0.0

        self.registry.register(
            RonDevice(
                device_id="ron-brain",
                friendly_name="Ron Brain",
                device_type="brain",
                connection_state=DeviceConnectionState.UNKNOWN,
                trust_state=DeviceTrustState.TRUSTED,
                capabilities={"ai", "voice", "agent", "network"},
            )
        )
        if self.settings.face_host:
            self.registry.register(
                RonDevice(
                    device_id="ron-face",
                    friendly_name="Ron Face",
                    device_type="display",
                    ip_address=self.settings.face_host,
                    port=self.settings.face_port,
                    trust_state=DeviceTrustState.TRUSTED,
                    capabilities={"face", "quick_actions"},
                    metadata={"source": "manual"},
                )
            )

    @property
    def available(self) -> bool:
        return self._available and self.settings.enabled

    def start(self) -> None:
        """Start background discovery/ageing; return immediately."""
        if self._started or not self.settings.enabled:
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._started = True
        self._set_network_available(True)
        self.note_device_connected(
            "ron-brain",
            device_type="brain",
            friendly_name="Ron Brain",
            trust_state=DeviceTrustState.TRUSTED,
            capabilities={"ai", "voice", "agent", "network"},
            metadata={"local": True},
        )
        self._thread = threading.Thread(
            target=self._worker,
            name="ron-network",
            daemon=True,
        )
        try:
            self._thread.start()
        except Exception:
            self._thread = None
            self._started = False
            self._set_network_available(False)
            raise
        self._logger.info("Ron Network started")

    def stop(self) -> None:
        if not self._started:
            return
        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        self._started = False
        self._set_network_available(False)
        self._logger.info("Ron Network stopped")

    def face_endpoint(self) -> tuple[str, int] | None:
        """Return the best known LAN endpoint for the tablet face."""
        if not self.settings.enabled:
            return None
        device = self.registry.get("ron-face")
        if device is None or not device.ip_address or not device.port:
            return None
        return device.ip_address, device.port

    def note_discovery(self, reply: DiscoveryReply) -> None:
        """Record network reachability without assuming authentication succeeded."""
        existing = self.registry.get(reply.device_id)
        trust = existing.trust_state if existing is not None else DeviceTrustState.UNKNOWN
        self.registry.register(
            RonDevice(
                device_id=reply.device_id,
                friendly_name=reply.friendly_name,
                device_type=reply.device_type,
                ip_address=reply.ip_address,
                port=reply.port,
                trust_state=trust,
                capabilities=set(reply.capabilities),
                metadata={**reply.metadata, "source": "discovery"},
            )
        )
        device, previous = self.registry.update_presence(
            reply.device_id,
            ip_address=reply.ip_address,
            port=reply.port,
            state=(
                existing.connection_state
                if existing is not None
                and existing.connection_state is DeviceConnectionState.ONLINE
                else DeviceConnectionState.DEGRADED
            ),
            trust_state=trust,
            network_reachable=True,
            service_responsive=(existing.service_responsive if existing else False),
            capabilities=set(reply.capabilities),
            metadata={**reply.metadata, "source": "discovery"},
            seen_at=monotonic(),
        )
        self._publish_transition(device, previous)
        self._wake_event.set()

    def note_device_connected(
        self,
        device_id: str,
        *,
        ip_address: str | None = None,
        port: int | None = None,
        device_type: str = "unknown",
        friendly_name: str | None = None,
        trust_state: DeviceTrustState = DeviceTrustState.TRUSTED,
        capabilities: set[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RonDevice:
        if self.settings.enabled:
            self._set_network_available(True)
        self.registry.register(
            RonDevice(
                device_id=device_id,
                friendly_name=friendly_name or device_id,
                device_type=device_type,
                ip_address=ip_address,
                port=port,
                trust_state=trust_state,
                capabilities=capabilities or set(),
                metadata=metadata or {},
            )
        )
        device, previous = self.registry.update_presence(
            device_id,
            ip_address=ip_address,
            port=port,
            state=DeviceConnectionState.ONLINE,
            trust_state=trust_state,
            network_reachable=True,
            service_responsive=True,
            capabilities=capabilities,
            metadata=metadata,
            seen_at=monotonic(),
        )
        self._publish_transition(device, previous)
        return device

    def note_device_heartbeat(
        self,
        device_id: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> RonDevice:
        device, previous = self.registry.update_presence(
            device_id,
            state=DeviceConnectionState.ONLINE,
            network_reachable=True,
            service_responsive=True,
            metadata=metadata,
            seen_at=monotonic(),
        )
        self._publish_transition(device, previous)
        return device

    def note_device_disconnected(self, device_id: str) -> RonDevice:
        """Use DEGRADED first so one brief disconnect does not mean offline."""
        device, previous = self.registry.update_presence(
            device_id,
            state=DeviceConnectionState.DEGRADED,
            service_responsive=False,
        )
        self._publish_transition(device, previous)
        self._wake_event.set()
        return device

    def mark_unpaired(self, device_id: str) -> RonDevice:
        device, previous = self.registry.update_presence(
            device_id,
            state=DeviceConnectionState.DEGRADED,
            trust_state=DeviceTrustState.UNPAIRED,
            service_responsive=False,
        )
        self._publish_transition(device, previous)
        return device

    def status_label(self) -> str:
        if not self.settings.enabled:
            return "disabled"
        if not self._available:
            return "unavailable"
        devices = sorted(
            (device for device in self.registry.all() if device.device_id != "ron-brain"),
            key=lambda device: device.device_id,
        )
        if not devices:
            return "available; 0 devices"
        visible = ", ".join(
            f"{device.device_id} {device.connection_state.value}" for device in devices[:4]
        )
        if len(devices) > 4:
            visible += f", +{len(devices) - 4} more"
        return f"available; {visible}"

    def _worker(self) -> None:
        try:
            while not self._stop_event.is_set():
                now = monotonic()
                self._age_devices(now)
                if (
                    self.settings.discovery_enabled
                    and now - self._last_discovery >= self.settings.discovery_interval
                ):
                    self._last_discovery = now
                    self._run_discovery()
                self._wake_event.wait(1.0)
                self._wake_event.clear()
        except Exception:
            self._logger.exception("Ron Network worker stopped after an unexpected failure")
            self._set_network_available(False)

    def _run_discovery(self) -> None:
        try:
            replies = self._discovery.discover_once()
        except OSError as error:
            self._logger.debug("LAN discovery unavailable: %s", error)
            self._set_network_available(False)
            return
        except Exception:
            self._logger.exception("LAN discovery failed safely")
            return
        self._set_network_available(True)
        for reply in replies:
            try:
                self.note_discovery(reply)
            except (ValueError, TypeError):
                self._logger.debug("Ignored invalid discovered device", exc_info=True)

    def _age_devices(self, now: float) -> None:
        for device in self.registry.all():
            if device.device_id == "ron-brain" or device.last_seen is None:
                continue
            age = now - device.last_seen
            if age >= self.settings.offline_after:
                desired = DeviceConnectionState.OFFLINE
                reachable = False
            elif age >= self.settings.degraded_after:
                desired = DeviceConnectionState.DEGRADED
                reachable = device.network_reachable
            else:
                continue
            if device.connection_state is desired:
                continue
            updated, previous = self.registry.update_presence(
                device.device_id,
                state=desired,
                network_reachable=reachable,
                service_responsive=False,
            )
            self._publish_transition(updated, previous)

    def _publish_transition(
        self,
        device: RonDevice,
        previous: DeviceConnectionState,
    ) -> None:
        current = device.connection_state
        if current is previous:
            return
        if current is DeviceConnectionState.ONLINE:
            event_type = (
                EventType.DEVICE_RECONNECTED
                if previous in {DeviceConnectionState.DEGRADED, DeviceConnectionState.OFFLINE}
                else EventType.DEVICE_CONNECTED
            )
        elif current is DeviceConnectionState.DEGRADED:
            event_type = EventType.DEVICE_DEGRADED
        elif current is DeviceConnectionState.OFFLINE:
            event_type = EventType.DEVICE_DISCONNECTED
        else:
            return
        self.coordinator.publish(
            RonEvent(
                event_type,
                {
                    "device_id": device.device_id,
                    "friendly_name": device.friendly_name,
                    "device_type": device.device_type,
                    "state": current.value,
                    "previous_state": previous.value,
                },
            )
        )

    def _set_network_available(self, available: bool) -> None:
        if self._available == available:
            return
        self._available = available
        self.coordinator.publish(
            RonEvent(
                EventType.NETWORK_AVAILABLE if available else EventType.NETWORK_UNAVAILABLE,
                {"available": available},
            )
        )


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    try:
        value = float(raw) if raw is not None and raw.strip() else default
    except ValueError:
        value = default
    if not math.isfinite(value):
        value = default
    return max(minimum, min(maximum, value))


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None and raw.strip() else default
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))
