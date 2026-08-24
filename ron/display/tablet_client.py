"""Persistent tablet-face connection with LAN first and safe USB fallback."""

from __future__ import annotations

import json
import logging
import os
import queue
import secrets
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Any

from ron.core import FaceExpression
from ron.display.adb import (
    AdbBridge,
    AdbError,
    AdbUnavailableError,
    DeviceUnauthorizedError,
)
from ron.display.protocol import (
    PROTOCOL_VERSION,
    JsonLineDecoder,
    ProtocolError,
    encode_message,
)
from ron.display.quick_actions import (
    SUPPORTED_QUICK_ACTIONS,
    DesktopQuickActions,
    QuickActionResult,
)


class ConnectionStatus(StrEnum):
    """Observable stages of the tablet connection state machine."""

    STOPPED = "stopped"
    WAITING_FOR_DEVICE = "waiting_for_device"
    UNAUTHORIZED = "unauthorized"
    STARTING_APP = "starting_app"
    CONNECTING = "connecting"
    HANDSHAKING = "handshaking"
    READY = "ready"
    RETRYING = "retrying"


@dataclass(frozen=True, slots=True)
class FaceConnectionUpdate:
    """One isolated notification when the tablet connection stage changes."""

    status: ConnectionStatus
    previous_status: ConnectionStatus = ConnectionStatus.STOPPED


type StatusListener = Callable[[FaceConnectionUpdate], None]
type QuickActionHandler = Callable[[str], QuickActionResult]
type EndpointProvider = Callable[[], tuple[str, int] | None]
type NetworkConnectedHandler = Callable[[str | None, int | None, dict[str, Any]], None]
type NetworkHeartbeatHandler = Callable[[dict[str, Any]], None]
type NetworkDisconnectedHandler = Callable[[], None]


@dataclass(slots=True)
class TabletClientConfig:
    """Replaceable LAN and USB details for the Nexus face transport."""

    adb_path: str | None = None
    preferred_serial: str | None = None
    manual_host: str | None = None
    lan_enabled: bool = True
    component: str = "com.alexcodesthings.ronface/.MainActivity"
    device_port: int = 8765
    token_file: Path = Path("data/face_pairing_token")
    serial_file: Path = Path("data/tablet_serial.json")
    connect_timeout: float = 2.0
    handshake_timeout: float = 4.0
    heartbeat_interval: float = 2.0
    heartbeat_timeout: float = 6.5
    speech_rate_hz: float = 25.0

    @classmethod
    def from_environment(cls, project_root: Path) -> TabletClientConfig:
        """Build configuration while keeping secrets and serials out of Git."""
        data_directory = project_root / "runtime" / "data"
        raw_port = (os.getenv("RON_FACE_PORT") or "8765").strip()
        try:
            device_port = int(raw_port)
        except ValueError:
            device_port = 8765
        device_port = max(1, min(65_535, device_port))
        network_enabled = (os.getenv("RON_NETWORK_ENABLED") or "true").strip().lower()
        return cls(
            adb_path=os.getenv("RON_ADB_PATH") or None,
            preferred_serial=os.getenv("RON_TABLET_SERIAL") or None,
            manual_host=(os.getenv("RON_FACE_HOST") or "").strip() or None,
            lan_enabled=network_enabled not in {"0", "false", "no", "off"},
            device_port=device_port,
            token_file=data_directory / "face_pairing_token",
            serial_file=data_directory / "tablet_serial.json",
        )


@dataclass(slots=True)
class FaceSnapshot:
    """The complete current display state, used after every reconnection."""

    expression: FaceExpression = FaceExpression.IDLE
    speech_active: bool = False
    speech_level: float = 0.0


class TabletFaceClient:
    """Own tablet pairing, LAN/USB transport, heartbeat and resynchronisation."""

    def __init__(
        self,
        config: TabletClientConfig,
        *,
        quick_action_handler: QuickActionHandler | None = None,
        endpoint_provider: EndpointProvider | None = None,
        network_connected_handler: NetworkConnectedHandler | None = None,
        network_heartbeat_handler: NetworkHeartbeatHandler | None = None,
        network_disconnected_handler: NetworkDisconnectedHandler | None = None,
    ) -> None:
        self.config = config
        self._logger = logging.getLogger(__name__)
        self._status = ConnectionStatus.STOPPED
        self._status_lock = threading.RLock()
        self._status_listeners: list[StatusListener] = []
        self._state = FaceSnapshot()
        self._state_lock = threading.RLock()
        self._pending: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=128)
        self._pending_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._sequence = 0
        self._sequence_lock = threading.Lock()
        self._speech_dirty = False
        self._last_health_warning = 0.0
        self._serial = config.preferred_serial or self._load_saved_serial()
        self._pairing_token = self._load_or_create_token()
        self._quick_action_handler = quick_action_handler or DesktopQuickActions().handle
        self._endpoint_provider = endpoint_provider
        self._network_connected_handler = network_connected_handler
        self._network_heartbeat_handler = network_heartbeat_handler
        self._network_disconnected_handler = network_disconnected_handler
        self._active_transport = "none"

    @property
    def status(self) -> ConnectionStatus:
        with self._status_lock:
            return self._status

    @property
    def serial(self) -> str | None:
        return self._serial

    def add_status_listener(self, listener: StatusListener) -> None:
        """Subscribe once; a faulty listener cannot stop reconnection."""
        with self._status_lock:
            if listener not in self._status_listeners:
                self._status_listeners.append(listener)

    def remove_status_listener(self, listener: StatusListener) -> None:
        with self._status_lock:
            if listener in self._status_listeners:
                self._status_listeners.remove(listener)

    def start(self) -> None:
        """Start reconnection in the background; never block Ron's main thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._connection_worker,
            name="ron-tablet-face",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop safely and allow a bounded amount of time for cleanup."""
        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None:
            self._thread.join(timeout=4.0)
        self._set_status(ConnectionStatus.STOPPED)

    def set_expression(self, expression: FaceExpression | str) -> None:
        """Set a reliable semantic state instead of streaming animation frames."""
        parsed = (
            expression
            if isinstance(expression, FaceExpression)
            else FaceExpression(expression)
        )
        with self._state_lock:
            self._state.expression = parsed
        self._queue_reliable({"type": "expression", "value": parsed.value})

    def speech_started(self) -> None:
        with self._state_lock:
            self._state.speech_active = True
            self._state.expression = FaceExpression.SPEAKING
        self._queue_reliable({"type": "speech_started"})

    def set_speech_level(self, level: float) -> None:
        """Replace old live samples; only the newest mouth value matters."""
        bounded = max(0.0, min(1.0, float(level)))
        with self._state_lock:
            self._state.speech_level = bounded
            self._speech_dirty = True
        self._wake_event.set()

    def speech_ended(self) -> None:
        with self._state_lock:
            self._state.speech_active = False
            self._state.speech_level = 0.0
            self._state.expression = FaceExpression.IDLE
            self._speech_dirty = True
        self._queue_reliable({"type": "speech_ended"})

    def snapshot(self) -> FaceSnapshot:
        with self._state_lock:
            return FaceSnapshot(
                expression=self._state.expression,
                speech_active=self._state.speech_active,
                speech_level=self._state.speech_level,
            )

    def _connection_worker(self) -> None:
        backoff = 0.5
        while not self._stop_event.is_set():
            bridge: AdbBridge | None = None
            local_port: int | None = None
            active_serial: str | None = None
            connection: socket.socket | None = None
            lan_endpoint: tuple[str, int] | None = None
            decoder: JsonLineDecoder | None = None
            ready: dict[str, Any] | None = None
            network_announced = False

            try:
                self._set_status(ConnectionStatus.WAITING_FOR_DEVICE)
                lan_endpoint = self._resolve_lan_endpoint()
                if lan_endpoint is not None:
                    try:
                        self._set_status(ConnectionStatus.CONNECTING)
                        connection = socket.create_connection(
                            lan_endpoint,
                            timeout=self.config.connect_timeout,
                        )
                        connection.settimeout(0.20)
                        self._active_transport = "lan"
                        self._set_status(ConnectionStatus.HANDSHAKING)
                        decoder = JsonLineDecoder()
                        ready = self._perform_handshake(connection, decoder)
                    except (OSError, ProtocolError, TimeoutError) as error:
                        self._logger.debug(
                            "Tablet LAN endpoint %s:%s is unavailable: %s",
                            lan_endpoint[0],
                            lan_endpoint[1],
                            error,
                        )
                        if connection is not None:
                            try:
                                connection.close()
                            except OSError:
                                pass
                        connection = None
                        decoder = None
                        ready = None
                        self._active_transport = "none"

                if connection is None:
                    bridge = AdbBridge(self.config.adb_path)
                    device = bridge.resolve_device(self._serial)
                    active_serial = device.serial
                    if self._serial != active_serial:
                        self._serial = active_serial
                        self._save_serial(active_serial)

                    self._set_status(ConnectionStatus.STARTING_APP)
                    bridge.launch_face_app(
                        active_serial,
                        self.config.component,
                        self._pairing_token,
                    )
                    local_port = bridge.create_forward(
                        active_serial,
                        self.config.device_port,
                    )
                    self._set_status(ConnectionStatus.CONNECTING)
                    connection = socket.create_connection(
                        ("127.0.0.1", local_port),
                        timeout=self.config.connect_timeout,
                    )
                    connection.settimeout(0.20)
                    self._active_transport = "usb"
                    self._set_status(ConnectionStatus.HANDSHAKING)
                    decoder = JsonLineDecoder()
                    ready = self._perform_handshake(connection, decoder)

                if decoder is None or ready is None:
                    raise ProtocolError("Tablet transport did not complete setup")

                self._clear_pending()
                self._send_snapshot(connection)
                self._set_status(ConnectionStatus.READY)
                network_announced = True
                self._notify_network_connected(lan_endpoint, ready)
                backoff = 0.5
                self._connected_loop(connection, decoder)
            except AdbUnavailableError as error:
                self._logger.debug(
                    "Tablet face is not reachable by LAN and ADB is unavailable: %s", error
                )
                self._set_status(ConnectionStatus.RETRYING)
            except DeviceUnauthorizedError as error:
                self._logger.debug("Tablet face is waiting for USB permission: %s", error)
                self._set_status(ConnectionStatus.UNAUTHORIZED)
            except (AdbError, OSError, ProtocolError, TimeoutError) as error:
                self._logger.debug("Tablet face connection unavailable: %s", error)
                self._set_status(ConnectionStatus.RETRYING)
            except Exception:
                self._logger.debug(
                    "Unexpected tablet-face failure; reconnecting safely",
                    exc_info=True,
                )
                self._set_status(ConnectionStatus.RETRYING)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass
                if bridge is not None and active_serial is not None and local_port is not None:
                    bridge.remove_forward(active_serial, local_port)
                if network_announced:
                    self._notify_network_disconnected()
                self._active_transport = "none"

            if self._stop_event.is_set():
                break
            self._interruptible_wait(backoff + secrets.randbelow(250) / 1000)
            backoff = min(10.0, backoff * 1.8)

    def _resolve_lan_endpoint(self) -> tuple[str, int] | None:
        if not self.config.lan_enabled:
            return None
        if self.config.manual_host:
            return self.config.manual_host, self.config.device_port
        if self._endpoint_provider is None:
            return None
        try:
            endpoint = self._endpoint_provider()
        except Exception:
            self._logger.debug("Tablet endpoint provider failed safely", exc_info=True)
            return None
        if endpoint is None:
            return None
        host, port = endpoint
        if not isinstance(host, str) or not host.strip():
            return None
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535:
            return None
        return host.strip(), port

    def _perform_handshake(
        self,
        connection: socket.socket,
        decoder: JsonLineDecoder,
    ) -> dict[str, Any]:
        self._send(
            connection,
            {
                "type": "hello",
                "protocol": PROTOCOL_VERSION,
                "client": "ron-core",
                "token": self._pairing_token,
            },
        )
        deadline = monotonic() + self.config.handshake_timeout
        while monotonic() < deadline and not self._stop_event.is_set():
            for message in self._receive(connection, decoder):
                if message.get("type") != "ready":
                    continue
                if message.get("protocol") != PROTOCOL_VERSION:
                    raise ProtocolError("Tablet uses an incompatible protocol version")
                if message.get("device") not in {"ron-face", "nexus-7"}:
                    raise ProtocolError("Connected app did not identify as Ron's tablet face")
                return message
        raise TimeoutError("Tablet did not complete the face-protocol handshake")

    def _connected_loop(
        self,
        connection: socket.socket,
        decoder: JsonLineDecoder,
    ) -> None:
        last_ping = 0.0
        last_pong = monotonic()
        last_speech_send = 0.0
        speech_interval = 1 / self.config.speech_rate_hz

        while not self._stop_event.is_set():
            now = monotonic()
            if now - last_ping >= self.config.heartbeat_interval:
                self._send(
                    connection,
                    {"type": "ping", "sent_at": now},
                )
                last_ping = now

            if now - last_pong > self.config.heartbeat_timeout:
                raise TimeoutError("Tablet missed three heartbeat replies")

            self._flush_reliable(connection)
            if now - last_speech_send >= speech_interval:
                if self._send_latest_speech_level(connection):
                    last_speech_send = now

            for message in self._receive(connection, decoder):
                message_type = message.get("type")
                if message_type == "pong":
                    last_pong = monotonic()
                    self._notify_network_heartbeat({"transport": self._active_transport})
                elif message_type == "device_health":
                    self._handle_device_health(message)
                    self._notify_network_heartbeat(
                        {
                            "transport": self._active_transport,
                            "battery_percent": message.get("battery_percent"),
                            "charging": message.get("charging"),
                            "temperature_c": message.get("temperature_c"),
                        }
                    )
                elif message_type == "request_snapshot":
                    self._send_snapshot(connection)
                elif message_type == "quick_action":
                    self._handle_quick_action(connection, message)
                elif message_type == "face_wake":
                    self._handle_face_wake()

            self._wake_event.wait(0.025)
            self._wake_event.clear()

    def _send_snapshot(self, connection: socket.socket) -> None:
        self._send(connection, self._build_snapshot_message())

    def _handle_quick_action(
        self,
        connection: socket.socket,
        message: dict[str, Any],
    ) -> None:
        request_id = message.get("request_id")
        if (
            not isinstance(request_id, int)
            or isinstance(request_id, bool)
            or request_id <= 0
        ):
            self._logger.warning("Rejected tablet quick action with an invalid request ID")
            return

        action = message.get("action")
        if not isinstance(action, str) or action not in SUPPORTED_QUICK_ACTIONS:
            result = QuickActionResult(False, "That tablet action is not allowed")
        else:
            try:
                result = self._quick_action_handler(action)
                if not isinstance(result, QuickActionResult):
                    raise TypeError("Quick-action handler returned an invalid result")
            except Exception:
                self._logger.exception("Tablet quick-action handler failed safely")
                result = QuickActionResult(False, "The laptop action failed safely")

        self._send(
            connection,
            {
                "type": "quick_action_result",
                "request_id": request_id,
                "status": "success" if result.success else "failed",
                "message": result.message.strip()[:160],
            },
        )

    def _handle_face_wake(self) -> None:
        """Keep a locally awakened tablet in sync with future snapshots."""
        with self._state_lock:
            if self._state.expression is not FaceExpression.SLEEPING:
                return
            self._state.expression = FaceExpression.IDLE
            self._state.speech_active = False
            self._state.speech_level = 0.0
            self._speech_dirty = False
        self._logger.info("Tablet tap woke Ron's face")

    def _build_snapshot_message(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        return {
            "type": "state_snapshot",
            "sequence": self._next_sequence(),
            "expression": snapshot.expression.value,
            "speech_active": snapshot.speech_active,
            "speech_level": snapshot.speech_level,
        }

    def _queue_reliable(self, message: dict[str, Any]) -> None:
        message = {**message, "sequence": self._next_sequence()}
        with self._pending_lock:
            try:
                self._pending.put_nowait(message)
            except queue.Full:
                self._clear_pending()
                self._pending.put_nowait(self._build_snapshot_message())
                self._logger.warning(
                    "Face event queue overflowed; a snapshot will resynchronise it"
                )
        self._wake_event.set()

    def _flush_reliable(self, connection: socket.socket) -> None:
        while True:
            with self._pending_lock:
                try:
                    message = self._pending.get_nowait()
                except queue.Empty:
                    return
            self._send(connection, message)

    def _send_latest_speech_level(self, connection: socket.socket) -> bool:
        with self._state_lock:
            if not self._speech_dirty:
                return False
            level = self._state.speech_level
            active = self._state.speech_active
            self._speech_dirty = False
        if active:
            self._send(
                connection,
                {
                    "type": "speech_level",
                    "value": level,
                    "sampled_at": monotonic(),
                },
            )
        return True

    def _receive(
        self,
        connection: socket.socket,
        decoder: JsonLineDecoder,
    ) -> list[dict[str, Any]]:
        try:
            data = connection.recv(4096)
        except TimeoutError:
            return []
        if not data:
            raise ConnectionError("Tablet closed the face connection")
        return decoder.feed(data)

    @staticmethod
    def _send(connection: socket.socket, message: dict[str, Any]) -> None:
        connection.sendall(encode_message(message))

    def _handle_device_health(self, message: dict[str, Any]) -> None:
        temperature = message.get("temperature_c")
        battery = message.get("battery_percent")
        if not isinstance(temperature, (int, float)):
            return
        now = monotonic()
        if temperature >= 40 and now - self._last_health_warning > 60:
            self._logger.warning(
                "Nexus 7 temperature is %.1f C (battery %s%%); tablet dimming is active",
                temperature,
                battery if isinstance(battery, int) else "unknown",
            )
            self._last_health_warning = now

    def _notify_network_connected(
        self,
        lan_endpoint: tuple[str, int] | None,
        ready: dict[str, Any],
    ) -> None:
        if self._network_connected_handler is None:
            return
        ip_address = lan_endpoint[0] if self._active_transport == "lan" and lan_endpoint else None
        port = lan_endpoint[1] if self._active_transport == "lan" and lan_endpoint else None
        metadata = {
            "transport": self._active_transport,
            "face_version": ready.get("face_version"),
        }
        try:
            self._network_connected_handler(ip_address, port, metadata)
        except Exception:
            self._logger.debug("Tablet network-connected callback failed safely", exc_info=True)

    def _notify_network_heartbeat(self, metadata: dict[str, Any]) -> None:
        if self._network_heartbeat_handler is None:
            return
        safe_metadata = {key: value for key, value in metadata.items() if value is not None}
        try:
            self._network_heartbeat_handler(safe_metadata)
        except Exception:
            self._logger.debug("Tablet network heartbeat callback failed safely", exc_info=True)

    def _notify_network_disconnected(self) -> None:
        if self._network_disconnected_handler is None:
            return
        try:
            self._network_disconnected_handler()
        except Exception:
            self._logger.debug("Tablet network disconnect callback failed safely", exc_info=True)

    def _load_or_create_token(self) -> str:
        path = self.config.token_file
        try:
            token = path.read_text(encoding="utf-8").strip()
            if len(token) >= 32:
                return token
        except FileNotFoundError:
            pass
        except OSError as error:
            raise RuntimeError(f"Cannot read tablet pairing token: {error}") from error

        path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        try:
            path.write_text(token, encoding="utf-8")
            try:
                path.chmod(0o600)
            except OSError:
                pass
        except OSError as error:
            raise RuntimeError(f"Cannot store tablet pairing token: {error}") from error
        return token

    def _load_saved_serial(self) -> str | None:
        try:
            data = json.loads(self.config.serial_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        serial = data.get("serial") if isinstance(data, dict) else None
        return serial if isinstance(serial, str) and serial else None

    def _save_serial(self, serial: str) -> None:
        path = self.config.serial_file
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps({"serial": serial}, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError as error:
            self._logger.warning("Could not remember tablet serial: %s", error)

    def _set_status(self, status: ConnectionStatus) -> None:
        with self._status_lock:
            previous = self._status
            changed = status is not previous
            self._status = status
            listeners = tuple(self._status_listeners) if changed else ()
        if changed:
            self._logger.info("Tablet face status: %s", status.value)
            update = FaceConnectionUpdate(status, previous)
            for listener in listeners:
                try:
                    listener(update)
                except Exception:
                    self._logger.exception("Tablet face status listener failed")

    def _next_sequence(self) -> int:
        with self._sequence_lock:
            self._sequence += 1
            return self._sequence

    def _clear_pending(self) -> None:
        with self._pending_lock:
            while True:
                try:
                    self._pending.get_nowait()
                except queue.Empty:
                    return

    def _interruptible_wait(self, seconds: float) -> None:
        self._stop_event.wait(seconds)
