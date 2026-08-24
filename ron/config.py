"""Small project-local environment loader used before Ron's services are assembled."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class EnvironmentLoadResult:
    """Non-secret summary of a project .env load."""

    path: Path
    found: bool
    loaded_keys: tuple[str, ...] = ()
    skipped_keys: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def loaded_count(self) -> int:
        return len(self.loaded_keys)

    def status_label(self) -> str:
        if not self.found:
            return ".env not found (process environment/defaults active)"
        warning = f", {len(self.warnings)} warning(s)" if self.warnings else ""
        return f".env loaded ({self.loaded_count} values{warning})"


def _unquote(value: str) -> str:
    """Parse the deliberately small .env syntax Ron documents."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        quote = value[0]
        value = value[1:-1]
        if quote == '"':
            value = (
                value.replace(r"\\", "\\")
                .replace(r'\"', '"')
                .replace(r"\n", "\n")
                .replace(r"\t", "\t")
            )
        return value

    # In unquoted values, treat a whitespace-prefixed # as an inline comment.
    value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
    return value


def load_project_environment(project_root: Path) -> EnvironmentLoadResult:
    """Load ``<project>/.env`` without overriding explicit process variables.

    Existing OS/PowerShell environment variables intentionally win. Values are
    never logged or returned, only key names, so diagnostics cannot leak secrets.
    """
    path = (project_root / ".env").resolve()
    if not path.is_file():
        return EnvironmentLoadResult(path=path, found=False)

    loaded: list[str] = []
    skipped: list[str] = []
    warnings: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as error:
        return EnvironmentLoadResult(
            path=path,
            found=True,
            warnings=(f"Could not read .env: {type(error).__name__}",),
        )

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            warnings.append(f"line {line_number} has no '=' and was ignored")
            continue
        name, raw_value = line.split("=", 1)
        name = name.strip()
        if not _ENV_NAME.fullmatch(name):
            warnings.append(f"line {line_number} has an invalid variable name")
            continue
        if name in os.environ:
            skipped.append(name)
            continue
        os.environ[name] = _unquote(raw_value)
        loaded.append(name)

    return EnvironmentLoadResult(
        path=path,
        found=True,
        loaded_keys=tuple(loaded),
        skipped_keys=tuple(skipped),
        warnings=tuple(warnings),
    )
