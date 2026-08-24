"""Safe generation of Ron's Sherpa-ONNX keyword file."""

from __future__ import annotations

import os
from pathlib import Path

# The first pronunciation remains the model's original American-English form.
# The AO1 variant better covers the rounded vowel many South African/Afrikaans
# English speakers use in "Ron". Both variants map to the same logical keyword,
# so the rest of Ron never needs accent-specific wake logic.
HEY_RON_PHONE_VARIANTS: tuple[tuple[str, ...], ...] = (
    ("HH", "EY1", "R", "AA1", "N"),
    ("HH", "EY1", "R", "AO1", "N"),
)
HEY_RON_PHONES = HEY_RON_PHONE_VARIANTS[0]
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


def keyword_text(available: set[str]) -> str:
    """Build every compatible Hey Ron pronunciation, requiring the baseline."""
    missing_baseline = [phone for phone in HEY_RON_PHONE_VARIANTS[0] if phone not in available]
    if missing_baseline:
        joined = ", ".join(missing_baseline)
        raise RuntimeError(
            f"The downloaded wake-word model lacks required phone tokens: {joined}"
        )

    lines: list[str] = []
    for phones in HEY_RON_PHONE_VARIANTS:
        if all(phone in available for phone in phones):
            lines.append(" ".join(phones) + " @HEY_RON\n")
    return "".join(lines)


def prepare_keyword(tokens_path: Path, output_path: Path) -> None:
    """Validate the model vocabulary, then atomically write Hey Ron variants."""
    available = read_tokens(tokens_path)
    content = keyword_text(available)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(content, encoding="ascii", newline="")
    os.replace(temporary, output_path)
