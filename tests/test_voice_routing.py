from pathlib import Path

from ron.routing import PromptRouter, RouteDestination
from ron.voice.normalizer import VoiceNormalizer
from ron.voice.settings import VoiceSettings


class ModelMustNotRun:
    def stream_chat(self, *args, **kwargs):
        del args, kwargs
        raise AssertionError("A deterministic voice command must not call Ollama")


def test_corrected_voice_command_uses_existing_deterministic_router() -> None:
    normalizer = VoiceNormalizer(VoiceSettings(enabled=True, project_root=Path(".")))
    result = normalizer.normalize(
        "Hey Ron open spot the fi",
        require_wake=True,
        wake_detected=True,
    )

    decision = PromptRouter(ModelMustNotRun()).route(result.text)

    assert result.text == "open Spotify"
    assert decision.destination is RouteDestination.AGENT
