"""Safely audition Ron's local British Kokoro voices without starting the assistant."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from ron.voice.settings import VoiceSettings
from ron.voice.speech import KokoroSynthesizer, SoundDevicePlayer

BRITISH_MALE_VOICES = ("bm_george", "bm_fable", "bm_daniel", "bm_lewis")
SAMPLE = (
    "Good evening, Alex. Systems are online and everything appears to be in order. "
    "I was beginning to think you'd decided to take the evening off."
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audition Ron's local British speaking voices")
    parser.add_argument("--voice", choices=BRITISH_MALE_VOICES)
    parser.add_argument("--speed", type=float, default=0.94)
    parser.add_argument("--text", default=SAMPLE)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    voices = (args.voice,) if args.voice else BRITISH_MALE_VOICES
    for voice in voices:
        print(f"\nAuditioning {voice} at speed {args.speed:.2f}...")
        settings = replace(
            VoiceSettings.from_environment(root),
            tts_voice=voice,
            tts_speed=args.speed,
        )
        synth = KokoroSynthesizer(settings)
        player = SoundDevicePlayer(settings)
        samples, rate = synth.synthesize(args.text)
        import threading

        player.play(samples, rate, level_handler=lambda level: None, stop_event=threading.Event())
        if not args.voice:
            input("Press Enter for the next voice...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
