"""Repeatable local-model latency benchmark for Ron."""

from __future__ import annotations

import json
import os
import platform
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from ron.ai.ollama_client import InferenceResult, OllamaClient


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    name: str
    prompt: str
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class CaseResult:
    name: str
    response_preview: str
    first_token_seconds: float | None
    elapsed_seconds: float
    load_duration_seconds: float | None
    output_tokens: int
    tokens_per_second: float | None


DEFAULT_CASES = (
    BenchmarkCase("minimal", "Reply with only: OK", 8),
    BenchmarkCase(
        "conversation",
        "The user asks: 'How are you?' Reply warmly and naturally in one short sentence.",
        48,
    ),
    BenchmarkCase(
        "decision",
        "Classify this request as CHAT or AGENT. Reply with one word only: "
        "'Please open Notepad and create a shopping list.'",
        8,
    ),
)


def speed_rating(first_token_seconds: float | None, tokens_per_second: float | None) -> str:
    """Give a conservative UX rating without hiding either latency measurement."""
    if first_token_seconds is None or tokens_per_second is None:
        return "incomplete"
    if first_token_seconds < 1.0 and tokens_per_second >= 20.0:
        return "excellent"
    if first_token_seconds < 2.5 and tokens_per_second >= 10.0:
        return "good"
    if first_token_seconds < 5.0 and tokens_per_second >= 5.0:
        return "usable"
    return "slow"


def run_benchmark(client: OllamaClient) -> dict[str, object]:
    """Warm the model and run short tasks matching Ron's first AI milestone."""
    client.preload()
    results: list[CaseResult] = []
    for case in DEFAULT_CASES:
        response: InferenceResult = client.stream_chat(
            [{"role": "user", "content": case.prompt}],
            max_output_tokens=case.max_output_tokens,
            temperature=0.0,
        )
        metrics = response.metrics
        results.append(
            CaseResult(
                name=case.name,
                response_preview=response.text.strip().replace("\n", " ")[:160],
                first_token_seconds=metrics.first_token_seconds,
                elapsed_seconds=metrics.elapsed_seconds,
                load_duration_seconds=metrics.load_duration_seconds,
                output_tokens=metrics.output_tokens,
                tokens_per_second=metrics.tokens_per_second,
            )
        )

    first_tokens = [
        result.first_token_seconds
        for result in results
        if result.first_token_seconds is not None
    ]
    generation_speeds = [
        result.tokens_per_second
        for result in results
        if result.tokens_per_second is not None
    ]
    average_first_token = sum(first_tokens) / len(first_tokens) if first_tokens else None
    average_tokens_per_second = (
        sum(generation_speeds) / len(generation_speeds) if generation_speeds else None
    )

    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "ollama_version": client.version(),
        "model": client.settings.model,
        "settings": {
            "base_url": client.settings.base_url,
            "keep_alive": client.settings.keep_alive,
            "context_size": client.settings.context_size,
        },
        "computer": {
            "platform": platform.platform(),
            "processor": platform.processor() or "unknown",
            "logical_cpu_count": os.cpu_count(),
        },
        "summary": {
            "average_first_token_seconds": average_first_token,
            "average_tokens_per_second": average_tokens_per_second,
            "rating": speed_rating(average_first_token, average_tokens_per_second),
        },
        "cases": [asdict(result) for result in results],
    }


def save_report(report: dict[str, object], directory: Path) -> Path:
    """Write a timestamped private report beneath the ignored data directory."""
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = directory / f"local-ai-{timestamp}.json"
    destination.write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return destination
