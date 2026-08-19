import json
from pathlib import Path
from tempfile import TemporaryDirectory

from ron.app import RonApplication
from ron.core import Coordinator, EventType, RonEvent
from ron.network import (
    DeviceConnectionState,
    DeviceRegistry,
    DeviceTrustState,
    NetworkService,
    NetworkSettings,
    RonDevice,
)
from ron.network.protocol import (
    ProtocolError,
    pairing_tokens_match,
    parse_discovery_reply,
)


def discovery_payload(**overrides) -> bytes:
    message = {
        "type": "ron_device",
        "protocol": 1,
        "request_id": "abc123",
        "device_id": "ron-face",
        "friendly_name": "Ron Face",
        "device_type": "display",
        "port": 8765,
        "capabilities": ["face", "quick_actions"],
        "metadata": {"face_version": "0.1.1"},
    }
    message.update(overrides)
    return json.dumps(message).encode("utf-8")


def test_registry_registers_and_updates_a_device() -> None:
    registry = DeviceRegistry()
    registry.register(
        RonDevice(
            "ron-face",
            "Ron Face",
            "display",
            ip_address="192.168.1.50",
            trust_state=DeviceTrustState.TRUSTED,
            capabilities={"face"},
        )
    )

    updated, previous = registry.update_presence(
        "ron-face",
        state=DeviceConnectionState.ONLINE,
        service_responsive=True,
        network_reachable=True,
        capabilities={"quick_actions"},
    )

    assert previous is DeviceConnectionState.UNKNOWN
    assert updated.connection_state is DeviceConnectionState.ONLINE
    assert updated.trust_state is DeviceTrustState.TRUSTED
    assert updated.capabilities == {"face", "quick_actions"}
    assert updated.last_seen is not None


def test_device_disconnect_degrades_before_becoming_offline() -> None:
    coordinator = Coordinator()
    events: list[RonEvent] = []
    coordinator.subscribe(EventType.DEVICE_DEGRADED, events.append)
    coordinator.subscribe(EventType.DEVICE_DISCONNECTED, events.append)
    service = NetworkService(
        coordinator,
        NetworkSettings(
            discovery_enabled=False,
            degraded_after=5.0,
            offline_after=10.0,
        ),
    )
    device = service.note_device_connected("ron-face", device_type="display")

    degraded = service.note_device_disconnected("ron-face")
    assert degraded.connection_state is DeviceConnectionState.DEGRADED
    assert events[-1].type is EventType.DEVICE_DEGRADED

    assert device.last_seen is not None
    service._age_devices(device.last_seen + 11.0)
    offline = service.registry.get("ron-face")
    assert offline is not None
    assert offline.connection_state is DeviceConnectionState.OFFLINE
    assert events[-1].type is EventType.DEVICE_DISCONNECTED


def test_heartbeat_reconnects_a_degraded_device() -> None:
    coordinator = Coordinator()
    events: list[RonEvent] = []
    coordinator.subscribe(EventType.DEVICE_RECONNECTED, events.append)
    service = NetworkService(coordinator, NetworkSettings(discovery_enabled=False))
    service.note_device_connected("ron-face", device_type="display")
    service.note_device_disconnected("ron-face")

    device = service.note_device_heartbeat("ron-face", metadata={"battery_percent": 75})

    assert device.connection_state is DeviceConnectionState.ONLINE
    assert device.metadata["battery_percent"] == 75
    assert events and events[-1].type is EventType.DEVICE_RECONNECTED


def test_unknown_discovered_device_is_not_automatically_trusted() -> None:
    service = NetworkService(Coordinator(), NetworkSettings(discovery_enabled=False))
    reply = parse_discovery_reply(
        discovery_payload(device_id="ron-light-test", device_type="light"),
        "192.168.1.70",
        expected_request_id="abc123",
    )

    service.note_discovery(reply)
    device = service.registry.get("ron-light-test")

    assert device is not None
    assert device.trust_state is DeviceTrustState.UNKNOWN
    assert device.connection_state is DeviceConnectionState.DEGRADED
    assert device.network_reachable is True
    assert device.service_responsive is False


def test_discovery_reply_uses_packet_source_address_not_payload_address() -> None:
    reply = parse_discovery_reply(
        discovery_payload(ip_address="203.0.113.123"),
        "192.168.1.55",
        expected_request_id="abc123",
    )

    assert reply.ip_address == "192.168.1.55"
    assert reply.port == 8765


def test_discovery_reply_rejects_wrong_request_id() -> None:
    try:
        parse_discovery_reply(
            discovery_payload(),
            "192.168.1.55",
            expected_request_id="different",
        )
    except ProtocolError:
        return
    raise AssertionError("Unrelated discovery replies must be ignored")


def test_discovery_reply_rejects_invalid_messages() -> None:
    invalid = discovery_payload(port=0)
    try:
        parse_discovery_reply(invalid, "192.168.1.55", expected_request_id="abc123")
    except ProtocolError:
        return
    raise AssertionError("Invalid discovery ports must be rejected")


def test_pairing_token_check_rejects_unknown_device_secret() -> None:
    expected = "a" * 40
    assert pairing_tokens_match(expected, expected) is True
    assert pairing_tokens_match(expected, "b" * 40) is False
    assert pairing_tokens_match(expected, "too-short") is False


def test_discovery_socket_failure_is_non_fatal() -> None:
    class BrokenDiscovery:
        def discover_once(self):
            raise OSError("simulated Wi-Fi failure")

    service = NetworkService(
        Coordinator(),
        NetworkSettings(discovery_enabled=True),
        discovery=BrokenDiscovery(),
    )

    service._run_discovery()

    assert service.registry.get("ron-brain") is not None


def test_manual_face_address_is_available_without_discovery() -> None:
    service = NetworkService(
        Coordinator(),
        NetworkSettings(
            discovery_enabled=False,
            face_host="192.168.1.90",
            face_port=9000,
        ),
    )

    assert service.face_endpoint() == ("192.168.1.90", 9000)


def test_network_disabled_does_not_start_background_work() -> None:
    service = NetworkService(
        Coordinator(),
        NetworkSettings(enabled=False, discovery_enabled=True),
    )

    service.start()

    assert service.available is False
    assert service.status_label() == "disabled"
    assert service._thread is None


def test_application_continues_when_network_start_fails() -> None:
    class FakeService:
        def __init__(self, *, fail: bool = False) -> None:
            self.fail = fail
            self.started = False
            self.stopped = False

        def start(self) -> None:
            if self.fail:
                raise OSError("simulated network startup failure")
            self.started = True

        def stop(self) -> None:
            self.stopped = True

    class FakeFace(FakeService):
        def connection_label(self) -> str:
            return "offline"

        def set_expression(self, expression) -> None:
            del expression

    class FakeVoice(FakeService):
        def status_label(self) -> str:
            return "disabled"

    with TemporaryDirectory() as directory_name:
        app = RonApplication(Path(directory_name))
        reminders = FakeService()
        agent = FakeService()
        network = FakeService(fail=True)
        face = FakeFace()
        voice = FakeVoice()
        app.reminders = reminders
        app.agent = agent
        app.network = network
        app.face = face
        app.voice = voice

        app.start()

        assert reminders.started is True
        assert agent.started is True
        assert face.started is True
        assert voice.started is True
        assert app._started is True

        app.stop()
        assert face.stopped is True
        assert voice.stopped is True


def test_network_availability_events_are_debounced_and_recover() -> None:
    class ToggleDiscovery:
        def __init__(self) -> None:
            self.fail = True

        def discover_once(self):
            if self.fail:
                raise OSError("simulated interface loss")
            return ()

    coordinator = Coordinator()
    events: list[RonEvent] = []
    coordinator.subscribe(EventType.NETWORK_AVAILABLE, events.append)
    coordinator.subscribe(EventType.NETWORK_UNAVAILABLE, events.append)
    discovery = ToggleDiscovery()
    service = NetworkService(
        coordinator,
        NetworkSettings(discovery_enabled=True),
        discovery=discovery,
    )

    service._set_network_available(True)
    service._run_discovery()
    service._run_discovery()
    discovery.fail = False
    service._run_discovery()

    assert [event.type for event in events] == [
        EventType.NETWORK_AVAILABLE,
        EventType.NETWORK_UNAVAILABLE,
        EventType.NETWORK_AVAILABLE,
    ]


def test_discovery_rejects_non_finite_json_values() -> None:
    payload = discovery_payload(metadata={"temperature_c": float("nan")})
    try:
        parse_discovery_reply(payload, "192.168.1.55", expected_request_id="abc123")
    except ProtocolError:
        return
    raise AssertionError("Non-finite discovery metadata must be rejected")
