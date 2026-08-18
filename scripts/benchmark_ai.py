"""Check Ron's local model and measure real interactive latency."""

from __future__ import annotations

import json
from pathlib import Path

from ron.ai.benchmark import run_benchmark, save_report
from ron.ai.ollama_client import OllamaClient, OllamaConnectionError, OllamaError
from ron.ai.settings import LocalAISettings, SettingsError


def _format_measurement(value: object, unit: str) -> str:
    return "n/a" if not isinstance(value, (int, float)) else f"{value:.2f} {unit}"


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    try:
        settings = LocalAISettings.from_environment()
        client = OllamaClient(settings)
        print(f"Ollama {client.version()} is running at {settings.base_url}.")
        if not client.has_configured_model():
            print(
                f"Model {settings.model!r} is not installed. Run: ollama pull {settings.model}"
            )
            return 2

        print(f"Warming {settings.model}; this can take longer on the first run...")
        report = run_benchmark(client)
        destination = save_report(report, project_root / "runtime" / "data" / "benchmarks")
    except SettingsError as error:
        print(f"Invalid AI configuration: {error}")
        return 2
    except OllamaConnectionError as error:
        print(f"Local AI is unavailable: {error}")
        print("Start Ollama, then run this benchmark again.")
        return 3
    except OllamaError as error:
        print(f"The local AI returned an invalid response: {error}")
        return 4

    summary = report["summary"]
    assert isinstance(summary, dict)
    print("\nRon local-AI benchmark")
    print(f"  Model:              {report['model']}")
    print(
        "  First visible text: "
        f"{_format_measurement(summary['average_first_token_seconds'], 's')}"
    )
    print(
        "  Generation speed:   "
        f"{_format_measurement(summary['average_tokens_per_second'], 'tokens/s')}"
    )
    print(f"  Interactive rating: {summary['rating']}")
    print("\nIndividual checks:")
    cases = report["cases"]
    assert isinstance(cases, list)
    for case in cases:
        assert isinstance(case, dict)
        first_token = _format_measurement(case["first_token_seconds"], "s")
        tokens_per_second = _format_measurement(case["tokens_per_second"], "tokens/s")
        print(f"  {case['name']:<12} first text {first_token:<10} {tokens_per_second}")
    print(f"\nPrivate JSON report: {destination}")
    print("Share the summary above before we choose Ron's final model size.")

    json.dumps(report, allow_nan=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
