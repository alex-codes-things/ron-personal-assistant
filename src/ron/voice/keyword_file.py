"""Safe generation of Ron's Sherpa-ONNX keyword file."""

from __future__ import annotations

import os
from pathlib import Path

HEY_RON_PHONES = ("HH", "EY1", "R", "AA1", "N")
# Score and threshold deliberately come from VoiceSettings. Keeping them out of
# this file makes RON_WAKE_SCORE and RON_WAKE_THRESHOLD genuine calibration
# controls instead of silently overriding them per keyword.
KEYWORD_LINE = "HH EY1 R AA1 N @HEY_RON\n"


def read_tokens(path: Path) -> set[str]:
    """Return the token names from either one- or two-column token files."""
    if not path.is_file():
        raise FileNotFoundError(f"Wake-word token file is missing: {path}")
    tokens: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        fields = raw_line.strip().split()
        if fields:
            tokens.add(fields[0])
    return tokens


def prepare_keyword(tokens_path: Path, output_path: Path) -> None:
    """Validate the model vocabulary, then atomically write HEY_RON."""
    available = read_tokens(tokens_path)
    missing = [phone for phone in HEY_RON_PHONES if phone not in available]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            f"The downloaded wake-word model lacks required phone tokens: {joined}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(KEYWORD_LINE, encoding="ascii", newline="")
    os.replace(temporary, output_path)
