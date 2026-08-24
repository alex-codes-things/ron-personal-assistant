"""Verify Ron's OpenAI connection with one tiny streamed response."""

from __future__ import annotations

from pathlib import Path

from ron.ai import AIError, CloudAISettings, OpenAIClient, SettingsError
from ron.config import load_project_environment


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    environment = load_project_environment(project_root)
    print(environment.status_label())
    try:
        client = OpenAIClient(CloudAISettings.from_environment())
        result = client.stream_chat(
            [{"role": "user", "content": "Reply with exactly: OK"}],
            max_output_tokens=16,
        )
    except SettingsError as error:
        print(f"Cloud configuration is incomplete: {error}")
        return 2
    except AIError as error:
        print(f"Cloud AI check failed safely: {error}")
        return 3

    first_text = result.metrics.first_token_seconds
    timing = "unknown" if first_text is None else f"{first_text:.2f} seconds"
    print(f"Connected to {result.model}.")
    print(f"First visible text: {timing}")
    print(f"Response: {result.text.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
