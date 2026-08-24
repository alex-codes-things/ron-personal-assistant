"""Verify Groq Orpheus directly without silently loading Ron's local fallback."""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

from ron.config import load_project_environment
from ron.voice.settings import VoiceSettings, VoiceSettingsError
from ron.voice.speech import GroqSpeechSynthesizer, SoundDevicePlayer, SpeechOutputError

TEST_PHRASE = "Ron cloud speech is ready."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--play",
        action="store_true",
        help="Play the successful test phrase through Ron's configured speaker",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    environment = load_project_environment(project_root)
    print(environment.status_label())
    try:
        settings = VoiceSettings.from_environment(project_root)
    except VoiceSettingsError as error:
        print(f"Groq voice configuration is incomplete: {error}")
        return 2
    if not settings.groq_api_key:
        print("Groq voice configuration is incomplete: GROQ_API_KEY is missing")
        return 2

    synthesizer = GroqSpeechSynthesizer(settings)
    started = time.perf_counter()
    stream = synthesizer.stream_synthesize(TEST_PHRASE)
    try:
        first_samples, sample_rate = next(stream)
    except SpeechOutputError as error:
        print(f"Groq voice check failed safely: {error}")
        return 3
    except StopIteration:
        print("Groq voice check failed safely: Groq returned no speech audio")
        return 3

    first_packet_seconds = time.perf_counter() - started
    frame_count = len(first_samples)

    def audio_sequence():
        nonlocal frame_count
        yield first_samples, sample_rate
        for samples, rate in stream:
            if rate != sample_rate:
                raise SpeechOutputError("Groq changed sample rate during one response")
            frame_count += len(samples)
            yield samples, rate

    try:
        if args.play:
            SoundDevicePlayer(settings).play_sequence(
                audio_sequence(),
                level_handler=lambda level: None,
                stop_event=threading.Event(),
            )
        else:
            for _samples, _rate in audio_sequence():
                pass
    except SpeechOutputError as error:
        if args.play:
            print(f"Streaming or playback failed safely: {error}")
            return 4
        print(f"Groq voice stream failed safely: {error}")
        return 3

    stream_seconds = time.perf_counter() - started
    audio_seconds = frame_count / sample_rate
    print(f"Connected to {synthesizer.provider_label}.")
    print(
        f"First PCM packet in {first_packet_seconds:.2f} seconds; complete stream in "
        f"{stream_seconds:.2f} seconds ({audio_seconds:.2f} s audio)."
    )
    if args.play:
        print("Live streaming playback completed.")
    else:
        print("Add --play to hear the test phrase while it streams.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
