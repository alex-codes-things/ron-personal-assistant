from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = (
    ROOT
    / "tablet"
    / "app"
    / "src"
    / "main"
    / "java"
    / "com"
    / "alexcodesthings"
    / "ronface"
)


def source(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_signal_server_listens_on_lan_and_keeps_pairing_handshake() -> None:
    server = source("SignalServer.java")

    assert 'InetAddress.getByName("0.0.0.0")' in server
    assert '"token"' in server
    assert "constantTimeEquals" in server
    assert 'ready.put("device", "ron-face")' in server


def test_discovery_responder_advertises_no_pairing_secret() -> None:
    responder = source("DiscoveryResponder.java")

    assert '"ron_discover"' in responder
    assert 'reply.put("device_id", "ron-face")' in responder
    assert "Protocol.DISCOVERY_PORT" in responder
    assert "pairingToken" not in responder
    assert '"token"' not in responder


def test_main_activity_starts_and_stops_discovery_with_the_face() -> None:
    activity = source("MainActivity.java")

    assert "discoveryResponder = new DiscoveryResponder()" in activity
    assert "discoveryResponder.start()" in activity
    assert "discoveryResponder.stop()" in activity
