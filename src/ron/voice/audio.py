"""Bounded microphone capture with no model work in the real-time callback."""

from __future__ import annotations

import math
import queue
import threading
from array import array
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


class VoiceDependencyError(RuntimeError):
    """Raised when the optional voice installation is incomplete."""


class MicrophoneError(RuntimeError):
    """Raised when the configured microphone cannot be opened or read."""


@dataclass(frozen=True, slots=True)
class AudioFrame:
    samples: object
    sample_rate: int
    overflowed: bool = False


class SampleRingBuffer:
    """Small sample-count-bounded buffer used for wake-word pre-roll."""

    def __init__(self, maximum_samples: int) -> None:
        if maximum_samples < 1:
            raise ValueError("maximum_samples must be positive")
        self.maximum_samples = maximum_samples
        self._chunks: deque[array[float]] = deque()
        self._sample_count = 0

    @property
    def sample_count(self) -> int:
        return self._sample_count

    def append(self, samples: Sequence[float]) -> None:
        chunk = array("f", samples)
        if not chunk:
            return
        if len(chunk) >= self.maximum_samples:
            self._chunks.clear()
            self._chunks.append(array("f", chunk[-self.maximum_samples :]))
            self._sample_count = self.maximum_samples
            return
        self._chunks.append(chunk)
        self._sample_count += len(chunk)
        while self._sample_count > self.maximum_samples and self._chunks:
            excess = self._sample_count - self.maximum_samples
            first = self._chunks[0]
            if len(first) <= excess:
                self._chunks.popleft()
                self._sample_count -= len(first)
            else:
                self._chunks[0] = array("f", first[excess:])
                self._sample_count -= excess

    def snapshot(self, maximum_samples: int | None = None) -> array[float]:
        result = array("f")
        for chunk in self._chunks:
            result.extend(chunk)
        if maximum_samples is not None and len(result) > maximum_samples:
            return array("f", result[-maximum_samples:])
        return result

    def clear(self) -> None:
        self._chunks.clear()
        self._sample_count = 0


def linear_resample(
    samples: Sequence[float], source_rate: int, target_rate: int
) -> array[float]:
    """Dependency-free fallback resampler, run in a worker rather than callback."""
    source = array("f", samples)
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    if source_rate == target_rate or len(source) < 2:
        return source
    target_length = max(1, round(len(source) * target_rate / source_rate))
    scale = (len(source) - 1) / max(1, target_length - 1)
    result = array("f")
    for index in range(target_length):
        position = index * scale
        left = int(position)
        right = min(left + 1, len(source) - 1)
        fraction = position - left
        result.append(source[left] + (source[right] - source[left]) * fraction)
    return result


def root_mean_square(samples: Sequence[float]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(float(value) ** 2 for value in samples) / len(samples))


class MicrophoneStream:
    """Capture frames into a bounded queue and recover cleanly after stop."""

    def __init__(
        self,
        *,
        target_sample_rate: int = 16_000,
        device: str | int | None = None,
        queue_frames: int = 64,
    ) -> None:
        self.target_sample_rate = target_sample_rate
        self.device = device
        self._queue: queue.Queue[AudioFrame] = queue.Queue(maxsize=queue_frames)
        self._stream: Any = None
        self._sounddevice: Any = None
        self._native_sample_rate = target_sample_rate
        self._running = False
        self._lock = threading.RLock()
        self._overflow_count = 0
        self._device_label = "not selected"

    @property
    def overflow_count(self) -> int:
        return self._overflow_count

    @property
    def device_label(self) -> str:
        return self._device_label

    @property
    def active(self) -> bool:
        stream = self._stream
        if not self._running or stream is None:
            return False
        try:
            return bool(stream.active)
        except Exception:
            return False

    @staticmethod
    def list_input_devices() -> tuple[dict[str, object], ...]:
        try:
            import sounddevice as sd
        except ImportError as error:
            raise VoiceDependencyError(
                "The sounddevice package is missing. Run scripts/setup_voice.ps1."
            ) from error
        result = []
        for index, item in enumerate(sd.query_devices()):
            if int(item.get("max_input_channels", 0)) > 0:
                result.append(
                    {
                        "index": index,
                        "name": str(item.get("name", "Unknown microphone")),
                        "sample_rate": int(float(item.get("default_samplerate", 0))),
                    }
                )
        return tuple(result)

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            try:
                import numpy as np  # noqa: F401
                import sounddevice as sd
            except ImportError as error:
                raise VoiceDependencyError(
                    "Voice dependencies are missing. Run scripts/setup_voice.ps1."
                ) from error
            self._sounddevice = sd
            selected = self._resolve_device(sd)
            details = sd.query_devices(selected, "input")
            self._device_label = str(details.get("name", selected or "default microphone"))
            native_rate = int(float(details.get("default_samplerate", self.target_sample_rate)))
            try:
                sd.check_input_settings(
                    device=selected,
                    channels=1,
                    dtype="float32",
                    samplerate=self.target_sample_rate,
                )
                self._native_sample_rate = self.target_sample_rate
            except Exception as settings_error:
                if native_rate <= 0:
                    raise MicrophoneError(
                        f"{self._device_label} has no usable sample rate"
                    ) from settings_error
                self._native_sample_rate = native_rate

            try:
                self._stream = sd.InputStream(
                    device=selected,
                    channels=1,
                    dtype="float32",
                    samplerate=self._native_sample_rate,
                    blocksize=0,
                    latency="low",
                    callback=self._callback,
                )
                self._stream.start()
            except Exception as first_error:
                try:
                    self._stream = sd.InputStream(
                        device=selected,
                        channels=1,
                        dtype="float32",
                        samplerate=self._native_sample_rate,
                        blocksize=0,
                        latency="high",
                        callback=self._callback,
                    )
                    self._stream.start()
                except Exception as error:
                    self._stream = None
                    raise MicrophoneError(
                        f"Could not open {self._device_label}: {error}"
                    ) from first_error
            self._running = True

    def stop(self) -> None:
        with self._lock:
            self._running = False
            stream = self._stream
            self._stream = None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def read(self, timeout: float = 0.25) -> array[float] | None:
        try:
            frame = self._queue.get(timeout=timeout)
        except queue.Empty:
            if self._running and not self.active:
                raise MicrophoneError(
                    f"{self._device_label} stopped providing audio"
                ) from None
            return None
        try:
            samples = array("f", frame.samples)  # numpy arrays and sequences both work.
        except (TypeError, ValueError) as error:
            raise MicrophoneError("The microphone returned malformed audio") from error
        if frame.sample_rate != self.target_sample_rate:
            samples = linear_resample(samples, frame.sample_rate, self.target_sample_rate)
        return samples

    def _callback(
        self,
        indata: object,
        frames: int,
        time_info: object,
        status: object,
    ) -> None:
        del frames, time_info
        if not self._running and self._stream is not None:
            # PortAudio can invoke one callback while start() is completing.
            pass
        try:
            samples = indata[:, 0].copy()  # type: ignore[index]
            overflowed = bool(status)
            frame = AudioFrame(samples, self._native_sample_rate, overflowed)
            if overflowed:
                self._overflow_count += 1
            try:
                self._queue.put_nowait(frame)
            except queue.Full:
                self._overflow_count += 1
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    return
                try:
                    self._queue.put_nowait(frame)
                except queue.Full:
                    return
        except Exception:
            # Exceptions cannot escape PortAudio's real-time callback safely.
            self._overflow_count += 1

    def _resolve_device(self, sd: object) -> str | int | None:
        if self.device is None:
            return None
        if isinstance(self.device, int):
            return self.device
        value = self.device.strip()
        if value.isdecimal():
            return int(value)
        matches = [
            index
            for index, item in enumerate(sd.query_devices())  # type: ignore[attr-defined]
            if int(item.get("max_input_channels", 0)) > 0
            and value.casefold() in str(item.get("name", "")).casefold()
        ]
        if not matches:
            raise MicrophoneError(f"No input microphone matched RON_MICROPHONE={value!r}")
        if len(matches) > 1:
            raise MicrophoneError(
                f"RON_MICROPHONE={value!r} matched multiple devices; use its numeric index"
            )
        return matches[0]
