"""Capture one phrase and compare offline ASR models without executing it."""

from __future__ import annotations

import argparse
import gc
import os
import time
from array import array
from dataclasses import replace
from pathlib import Path

from ron.voice.audio import MicrophoneStream
from ron.voice.normalizer import VoiceNormalizer
from ron.voice.settings import VoiceSettings
from ron.voice.transcriber import FasterWhisperTranscriber
from ron.voice.vad import SileroEndpointDetector
from ron.voice.wake_word import SherpaWakeWordDetector


def capture(settings: VoiceSettings, timeout: float) -> array[float]:
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
    wake_seen = False
    print(
        f"Using {microphone.device_label}. Say one complete phrase such as: "
        "'Hey Ron, open Spotify'. Nothing will be routed or executed."
    )
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            samples = microphone.read(0.5)
            if samples is None:
                continue
            was_speech = vad.speech_detected
            detected = wake.feed(samples)
            segments = vad.feed(samples)
            if detected and (was_speech or vad.speech_detected or bool(segments)):
                wake_seen = True
            if wake_seen and segments:
                return array("f", segments[-1])
    finally:
        microphone.stop()
    raise TimeoutError("No complete Hey Ron phrase was captured before the timeout")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", help="Numeric device index or unique name fragment")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--models",
        default="small.en,distil-large-v3",
        help="Comma-separated faster-whisper models to compare",
    )
    args = parser.parse_args()
    if args.device:
        os.environ["RON_MICROPHONE"] = args.device
    root = Path(__file__).resolve().parents[1]
    settings = VoiceSettings.from_environment(root)
    audio = capture(settings, args.timeout)
    print(f"Captured {len(audio) / settings.sample_rate:.2f} seconds of complete speech.\n")

    for model_name in tuple(item.strip() for item in args.models.split(",") if item.strip()):
        model_settings = replace(settings, asr_model=model_name, asr_preload=False)
        transcriber = FasterWhisperTranscriber(model_settings)
        load_started = time.perf_counter()
        transcriber.load()
        transcriber.warm()
        load_seconds = time.perf_counter() - load_started
        fast = transcriber.transcribe(audio)
        accurate = transcriber.retry(audio)
        normalizer = VoiceNormalizer(model_settings)
        normalized_fast = normalizer.normalize(
            fast.text,
            require_wake=True,
            wake_detected=True,
        )
        normalized_accurate = normalizer.normalize(
            accurate.text,
            require_wake=True,
            wake_detected=True,
        )
        print(f"Model: {model_name}")
        print(f"  Load + warm: {load_seconds:.2f} s")
        print(
            f"  Fast beam {model_settings.asr_beam_size}: "
            f"{fast.duration_seconds:.2f} s, confidence {fast.confidence:.0%}"
        )
        print(f"    Raw: {fast.text or '[empty]'}")
        print(f"    Corrected: {normalized_fast.text or '[rejected]'}")
        print(
            f"  Retry beam {model_settings.asr_retry_beam_size}: "
            f"{accurate.duration_seconds:.2f} s, confidence {accurate.confidence:.0%}"
        )
        print(f"    Raw: {accurate.text or '[empty]'}")
        print(f"    Corrected: {normalized_accurate.text or '[rejected]'}")
        print(
            "  Fast real-time factor: "
            f"{fast.duration_seconds / max(0.01, len(audio) / settings.sample_rate):.2f}x\n"
        )
        del transcriber
        gc.collect()
    print(
        "Keep distil-large-v3 when its fast real-time factor is practical. "
        "Use small.en only if the measured fast pass is still too slow."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
