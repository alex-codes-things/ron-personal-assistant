"""Create Ron's Sherpa-ONNX keyword file without a version-specific CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from ron.voice.keyword_file import prepare_keyword


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokens", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        prepare_keyword(arguments.tokens, arguments.output)
    except (OSError, RuntimeError) as error:
        print(f"Wake-word preparation failed: {error}")
        return 1
    print(f"Hey Ron keyword ready: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
