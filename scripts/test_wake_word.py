"""Dry-run Hey Ron detection; this script cannot route or execute a command."""

from __future__ import annotations

import argparse
import math
import os
import time
from pathlib import Path

from ron.voice.audio import MicrophoneStream, root_mean_square
from ron.voice.settings import VoiceSettings
from ron.voice.vad import SileroEndpointDetector
from ron.voice.wake_word import SherpaWakeWordDetector


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--device", help="Numeric device index or unique name fragment")
    parser.add_argument("--threshold", type=float, help="Temporary 0-1 wake threshold")
    args = parser.parse_args()
    if not 5.0 <= args.seconds <= 3_600.0:
        raise SystemExit("--seconds must be between 5 and 3600")
    if args.device:
        os.environ["RON_MICROPHONE"] = args.device
    if args.threshold is not None:
        os.environ["RON_WAKE_THRESHOLD"] = str(args.threshold)

    root = Path(__file__).resolve().parents[1]
    settings = VoiceSettings.from_environment(root)
    microphone = MicrophoneStream(
        target_sample_rate=settings.sample_rate,
        device=settings.microphone_device,
        queue_frames=settings.audio_queue_frames,
    )
    wake = SherpaWakeWordDetector(settings)
    vad = SileroEndpointDetector(settings)
    wake.load()
    vad.load()
    microphone.start()
    detections = 0
    level_samples: list[float] = []
    report_at = time.monotonic() + 3.0
    print(
        f"Wake-only dry test on {microphone.device_label}. Say 'Hey Ron'. "
        f"Threshold: {settings.wake_threshold:.2f}. "
        "No transcript, router, AI model or tool is connected."
    )
    try:
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            samples = microphone.read(0.5)
            if samples is None:
                continue
            level_samples.append(root_mean_square(samples))
            detected = wake.feed(samples)
            vad.feed(samples)
            if detected:
                detections += 1
                print(f"\nDetected HEY_RON at {time.strftime('%H:%M:%S')}")
            now = time.monotonic()
            if now >= report_at and level_samples:
                average = sum(level_samples) / len(level_samples)
                dbfs = 20 * math.log10(max(average, 1e-8))
                speech = "speech" if vad.speech_detected else "quiet"
                print(f"Listening: {dbfs:.1f} dBFS, VAD={speech}")
                level_samples.clear()
                report_at = now + 3.0
    except KeyboardInterrupt:
        print("\nDry test stopped.")
    finally:
        microphone.stop()
    print(f"Detections: {detections}; audio overflows: {microphone.overflow_count}")
    if detections == 0:
        print(
            "No wake word was detected. First confirm the level changes while speaking. "
            "Then retry this safe test with --threshold 0.25."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
