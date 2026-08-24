from pathlib import Path

import pytest

from ron.voice.keyword_file import KEYWORD_LINE, prepare_keyword


def test_prepare_keyword_supports_two_column_token_file(tmp_path: Path) -> None:
    tokens = tmp_path / "tokens.txt"
    tokens.write_text(
        "<blk> 0\nHH 1\nEY1 2\nR 3\nAA1 4\nAO1 5\nN 6\n",
        encoding="utf-8",
    )
    output = tmp_path / "keywords.txt"

    prepare_keyword(tokens, output)

    content = output.read_text(encoding="ascii")
    assert content.startswith(KEYWORD_LINE)
    assert "HH EY1 R AO1 N @HEY_RON" in content
    assert ":" not in KEYWORD_LINE
    assert "#" not in KEYWORD_LINE


def test_prepare_keyword_rejects_incompatible_model(tmp_path: Path) -> None:
    tokens = tmp_path / "tokens.txt"
    tokens.write_text("HH 1\nEY1 2\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="AA1, N"):
        prepare_keyword(tokens, tmp_path / "keywords.txt")
