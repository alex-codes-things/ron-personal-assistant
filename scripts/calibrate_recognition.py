"""Dry-run Ron recognition against accent-sensitive phrases; never execute commands."""

from __future__ import annotations

import argparse
import os
import time
from array import array
from difflib import SequenceMatcher
from pathlib import Path

from ron.voice.audio import MicrophoneStream
from ron.voice.normalizer import VoiceNormalizer
from ron.voice.settings import VoiceSettings
from ron.voice.transcriber import FasterWhisperTranscriber
from ron.voice.vad import SileroEndpointDetector

PHRASES = (
    "Hey Ron, set the volume to twenty percent",
    "Hey Ron, set the brightness to fifty percent",
    "Hey Ron, open Spotify",
    "Hey Ron, open Visual Studio Code",
    "Hey Ron, open File Explorer",
    "Hey Ron, open a blank text document",
    "Hey Ron, play Galway Girl on Spotify",
    "Hey Ron, what time is it",
    "Hey Ron, remember that my tablet is a Nexus 7",
)


def _plain(value: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in value).split())


def capture_one(
    microphone: MicrophoneStream,
    vad: SileroEndpointDetector,
    timeout: float,
) -> array[float]:
    # Do not flush here: the caller arms capture *before* telling the user to
    # speak. Flushing after the user starts talking clips the wake phrase.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        samples = microphone.read(0.35)
        if samples is None:
            continue
        segments = vad.feed(samples)
        if segments:
            return array("f", segments[-1])
    raise TimeoutError("No complete speech segment was heard before the timeout")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", help="Numeric device index or unique microphone name")
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args()
    if args.device:
        os.environ["RON_MICROPHONE"] = args.device

    root = Path(__file__).resolve().parents[1]
    settings = VoiceSettings.from_environment(root)
    transcriber = FasterWhisperTranscriber(settings)
    normalizer = VoiceNormalizer(settings)
    vad = SileroEndpointDetector(settings)
    microphone = MicrophoneStream(
        target_sample_rate=settings.sample_rate,
        device=settings.microphone_device,
        queue_frames=settings.audio_queue_frames,
    )

    print("Loading local recognition model. Nothing spoken here can execute a Ron tool...")
    transcriber.load()
    transcriber.warm()
    vad.load()
    microphone.start()
    print(f"Microphone: {microphone.device_label}")
    print(f"ASR model: {settings.asr_model}; beam: {settings.asr_beam_size}\n")

    scores: list[float] = []
    try:
        for index, expected in enumerate(PHRASES, start=1):
            input(
                f"[{index}/{len(PHRASES)}] Press Enter to arm the microphone. "
                f"Wait for READY, then say:\n  {expected}\n"
            )
            # Give the Enter-key/desk noise a moment to pass, then flush it *before*
            # we invite speech. This prevents the start of "Hey Ron" being thrown away.
            time.sleep(0.15)
            microphone.discard_pending()
            print("  READY - speak now.", flush=True)
            try:
                audio = capture_one(microphone, vad, args.timeout)
            except TimeoutError as error:
                print(f"  [missed] {error}\n")
                scores.append(0.0)
                continue
            result = transcriber.transcribe(audio)
            normalized = normalizer.normalize(
                result.text,
                require_wake=True,
                wake_detected=True,
            )
            similarity = SequenceMatcher(None, _plain(expected), _plain(result.text)).ratio()
            scores.append(similarity)
            print(f"  Raw:       {result.text or '[empty]'}")
            print(f"  Corrected: {normalized.text or '[rejected]'}")
            print(f"  Similarity: {similarity:.0%}; ASR confidence: {result.confidence:.0%}")
            if normalized.correction_notes:
                print("  Corrections: " + "; ".join(normalized.correction_notes))
            print()
    finally:
        microphone.stop()

    average = sum(scores) / len(scores) if scores else 0.0
    print(f"Average phrase similarity: {average:.0%}")
    if average >= 0.90:
        print("Recognition looks strong. Test Ron normally next.")
    elif average >= 0.75:
        print(
            "Recognition is usable but still has repeatable misses; "
            "keep the raw lines for tuning."
        )
    else:
        print("Recognition still needs tuning. The raw lines above show exactly what Ron hears.")
    print("No command was routed or executed during this calibration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
