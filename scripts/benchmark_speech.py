"""Measure Ron's selected speech provider without playing its audio."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from ron.core import Coordinator
from ron.voice.settings import VoiceSettings
from ron.voice.speech import SpeechOutputService, build_speech_synthesizer

DEFAULT_TEXT = (
    "Right away. I found the setting and changed it safely. "
    "Everything is ready for the next request."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default=DEFAULT_TEXT, help="Safe text to synthesize")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    settings = VoiceSettings.from_environment(root)
    synthesizer = build_speech_synthesizer(settings)
    service = SpeechOutputService(
        Coordinator(), settings, synthesizer=synthesizer, player=object()
    )
    chunks = service._chunks_for(args.text)
    if not chunks:
        raise ValueError("The benchmark text produced no speakable content")

    started = time.perf_counter()
    loader = getattr(synthesizer, "load", None)
    if callable(loader):
        loader()
    load_seconds = time.perf_counter() - started

    results: list[tuple[float, float, int]] = []
    for chunk in chunks:
        started = time.perf_counter()
        samples, sample_rate = synthesizer.synthesize(chunk)
        synthesis_seconds = time.perf_counter() - started
        audio_seconds = len(samples) / sample_rate
        results.append((synthesis_seconds, audio_seconds, len(chunk)))

    provider = getattr(synthesizer, "provider_label", type(synthesizer).__name__)
    print(f"Provider: {provider}; chunks: {len(chunks)}")
    print(f"Local model load: {load_seconds:.2f} s")
    for index, (synthesis, audio, characters) in enumerate(results, start=1):
        real_time_factor = synthesis / max(0.01, audio)
        print(
            f"Chunk {index}: {characters} chars, {synthesis:.2f} s synthesis, "
            f"{audio:.2f} s audio, {real_time_factor:.2f}x real time"
        )
    print(f"First audio ready: {results[0][0]:.2f} s after speakable text is available")
    if settings.effective_tts_provider == "groq":
        print(
            "This used the configured free-plan TTS request allowance; normal turns "
            f"are capped at {settings.groq_tts_max_requests_per_turn} request(s)."
        )
    else:
        print(
            "Later chunks are prefetched during playback, so their synthesis normally "
            "does not become an additional silent pause."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
