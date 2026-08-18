"""Safe, serial-specific Android Debug Bridge operations."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum


class AdbError(RuntimeError):
    """Base error for ADB discovery and tunnel operations."""


class AdbUnavailableError(AdbError):
    """ADB is not installed or cannot be started."""


class DeviceUnavailableError(AdbError):
    """The configured tablet is disconnected or offline."""


class DeviceUnauthorizedError(AdbError):
    """The tablet has not authorised this computer for USB debugging."""


class AmbiguousDeviceError(AdbError):
    """More than one device is connected and no serial was configured."""


class DeviceState(StrEnum):
    DEVICE = "device"
    OFFLINE = "offline"
    UNAUTHORIZED = "unauthorized"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AdbDevice:
    """One device reported by `adb devices -l`."""

    serial: str
    state: DeviceState
    description: str = ""


class AdbBridge:
    """Run bounded ADB commands without ever guessing between devices."""

    def __init__(self, executable: str | None = None, timeout: float = 8.0) -> None:
        candidate = executable or os.getenv("RON_ADB_PATH") or shutil.which("adb")
        if not candidate:
            raise AdbUnavailableError(
                "ADB was not found. Add Android platform-tools to PATH or set RON_ADB_PATH."
            )
        self.executable = candidate
        self.timeout = timeout

    def list_devices(self) -> list[AdbDevice]:
        """Return connected devices and their exact authorisation states."""
        output = self._run("devices", "-l")
        devices: list[AdbDevice] = []
        for line in output.splitlines()[1:]:
            stripped = line.strip()
            if not stripped or stripped.startswith("*"):
                continue
            fields = stripped.split()
            if len(fields) < 2:
                continue
            state_text = fields[1]
            try:
                state = DeviceState(state_text)
            except ValueError:
                state = DeviceState.UNKNOWN
            devices.append(AdbDevice(fields[0], state, " ".join(fields[2:])))
        return devices

    def resolve_device(self, preferred_serial: str | None) -> AdbDevice:
        """Resolve exactly one ready tablet or raise a specific failure."""
        devices = self.list_devices()
        if preferred_serial:
            matching = next(
                (device for device in devices if device.serial == preferred_serial),
                None,
            )
            if matching is None:
                raise DeviceUnavailableError(
                    f"Configured tablet {preferred_serial!r} is not connected"
                )
            self._require_ready(matching)
            return matching

        ready = [device for device in devices if device.state is DeviceState.DEVICE]
        unauthorized = [
            device for device in devices if device.state is DeviceState.UNAUTHORIZED
        ]
        if len(ready) == 1:
            return ready[0]
        if len(ready) > 1:
            raise AmbiguousDeviceError(
                "Multiple Android devices are connected; set RON_TABLET_SERIAL"
            )
        if unauthorized:
            raise DeviceUnauthorizedError(
                "Accept the USB-debugging trust prompt on the Nexus 7"
            )
        raise DeviceUnavailableError("No authorised Android tablet is connected")

    def launch_face_app(self, serial: str, component: str, pairing_token: str) -> None:
        """Open the native face and provide its first-use pairing token."""
        self._run(
            "-s",
            serial,
            "shell",
            "am",
            "start",
            "-n",
            component,
            "--es",
            "ron_pairing_token",
            pairing_token,
        )

    def create_forward(self, serial: str, device_port: int) -> int:
        """Ask ADB for a free computer port forwarded directly over USB."""
        output = self._run(
            "-s",
            serial,
            "forward",
            "tcp:0",
            f"tcp:{device_port}",
        ).strip()
        port_text = output.removeprefix("tcp:")
        try:
            port = int(port_text)
        except ValueError as error:
            raise AdbError(f"ADB returned an invalid forwarded port: {output!r}") from error
        if not 1 <= port <= 65_535:
            raise AdbError(f"ADB returned an out-of-range port: {port}")
        return port

    def remove_forward(self, serial: str, local_port: int) -> None:
        """Remove one known tunnel without disturbing other ADB forwards."""
        try:
            self._run(
                "-s",
                serial,
                "forward",
                "--remove",
                f"tcp:{local_port}",
            )
        except AdbError:
            return

    def _require_ready(self, device: AdbDevice) -> None:
        if device.state is DeviceState.UNAUTHORIZED:
            raise DeviceUnauthorizedError(
                "Accept the USB-debugging trust prompt on the Nexus 7"
            )
        if device.state is not DeviceState.DEVICE:
            raise DeviceUnavailableError(
                f"Tablet {device.serial!r} is currently {device.state.value}"
            )

    def _run(self, *arguments: str) -> str:
        command = [self.executable, *arguments]
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                creationflags=creation_flags,
                text=True,
                timeout=self.timeout,
            )
        except FileNotFoundError as error:
            raise AdbUnavailableError(f"ADB does not exist at {self.executable!r}") from error
        except subprocess.TimeoutExpired as error:
            raise AdbError(f"ADB timed out while running: {' '.join(arguments)}") from error
        except OSError as error:
            raise AdbError(f"ADB could not be started: {error}") from error

        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip() or "unknown ADB error"
            raise AdbError(detail)
        return result.stdout
