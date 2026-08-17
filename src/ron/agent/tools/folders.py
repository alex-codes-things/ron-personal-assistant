"""Operations confined to a fixed set of user folders."""

from __future__ import annotations

import os
import secrets
from datetime import datetime
from pathlib import Path

from ron.agent.models import (
    ToolArgument,
    ToolArgumentKind,
    ToolExecutionContext,
    ToolResult,
    ToolRisk,
    ToolStatus,
)
from ron.agent.registry import ToolSpec

FOLDER_NAMES = ("documents", "downloads", "desktop")


def _folders() -> dict[str, Path]:
    home = Path.home().resolve()
    return {
        "documents": home / "Documents",
        "downloads": home / "Downloads",
        "desktop": home / "Desktop",
    }


def _open_path(path: Path) -> None:
    if os.name != "nt" or not hasattr(os, "startfile"):
        raise OSError("Folder opening is available on Windows only")
    os.startfile(str(path))


def build_open_folder_tool() -> ToolSpec:
    def availability() -> tuple[bool, str]:
        if os.name != "nt" or not hasattr(os, "startfile"):
            return False, "Folder opening is available on Windows only."
        return True, "Approved folder opening is ready."

    def open_folder(
        arguments: dict[str, str | int], context: ToolExecutionContext
    ) -> ToolResult:
        context.checkpoint()
        name = str(arguments["folder"])
        path = _folders()[name]
        try:
            path.mkdir(parents=True, exist_ok=True)
            _open_path(path)
        except OSError:
            return ToolResult("open_folder", ToolStatus.FAILED, f"I couldn't open {name}.")
        return ToolResult(
            "open_folder",
            ToolStatus.SUCCESS,
            f"Opening your {name} folder.",
            data={"folder": name},
        )

    return ToolSpec(
        "open_folder",
        "Open Documents, Downloads or Desktop without accepting an arbitrary path.",
        {"folder": ToolArgument(ToolArgumentKind.ENUM, choices=FOLDER_NAMES)},
        ToolRisk.READ_ONLY,
        open_folder,
        timeout_seconds=3.0,
        availability=availability,
    )


def build_blank_document_tool() -> ToolSpec:
    def availability() -> tuple[bool, str]:
        if os.name != "nt" or not hasattr(os, "startfile"):
            return False, "Opening a new text document is available on Windows only."
        return True, "Blank document creation is ready."

    def create_document(
        arguments: dict[str, str | int], context: ToolExecutionContext
    ) -> ToolResult:
        del arguments
        context.checkpoint()
        folder = _folders()["documents"] / "Ron"
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = folder / f"Untitled_{timestamp}_{secrets.token_hex(2)}.txt"
        created = False
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path.touch(mode=0o600, exist_ok=False)
            created = True
            _open_path(path)
        except OSError:
            if created:
                try:
                    if path.is_file() and path.stat().st_size == 0:
                        path.unlink()
                except OSError:
                    pass
            return ToolResult(
                "create_blank_text_document",
                ToolStatus.FAILED,
                "I couldn't create the blank text document safely.",
            )
        return ToolResult(
            "create_blank_text_document",
            ToolStatus.SUCCESS,
            f"Created and opened {path.name} in your Documents\\Ron folder.",
            data={
                "file_name": path.name,
                "folder": "Documents\\Ron",
                "created_path": str(path.resolve()),
            },
        )

    def remove_empty_document(
        result: ToolResult, context: ToolExecutionContext
    ) -> ToolResult:
        context.checkpoint()
        raw_path = result.data.get("created_path")
        if not isinstance(raw_path, str):
            return ToolResult(
                "create_blank_text_document",
                ToolStatus.FAILED,
                "The created document path was unavailable.",
            )
        approved_root = (_folders()["documents"] / "Ron").resolve()
        path = Path(raw_path).resolve()
        try:
            path.relative_to(approved_root)
            if not path.is_file() or path.stat().st_size != 0:
                raise OSError("The file is missing or has user content")
            path.unlink()
        except (OSError, ValueError):
            return ToolResult(
                "create_blank_text_document",
                ToolStatus.FAILED,
                "The blank document was kept because it may contain user changes.",
            )
        return ToolResult(
            "create_blank_text_document",
            ToolStatus.SUCCESS,
            f"removed untouched blank document {path.name}",
        )

    return ToolSpec(
        "create_blank_text_document",
        "Create one uniquely named empty text file under Documents\\Ron and open it.",
        {},
        ToolRisk.REVERSIBLE,
        create_document,
        timeout_seconds=4.0,
        compensator=remove_empty_document,
        availability=availability,
    )


def build_search_folder_tool() -> ToolSpec:
    def search_folder(
        arguments: dict[str, str | int], context: ToolExecutionContext
    ) -> ToolResult:
        folder_name = str(arguments["folder"])
        query = str(arguments["query"]).casefold()
        root = _folders()[folder_name]
        matches: list[str] = []
        visited = 0
        if root.is_dir():
            for current, directories, files in os.walk(root, followlinks=False):
                context.checkpoint()
                directories[:] = [
                    name
                    for name in directories
                    if not (Path(current) / name).is_symlink()
                ]
                for name in (*directories, *files):
                    if query in name.casefold():
                        relative = (Path(current) / name).relative_to(root)
                        matches.append(str(relative))
                        if len(matches) >= 25:
                            break
                visited += 1
                if len(matches) >= 25 or visited >= 10_000:
                    break
        if not matches:
            message = f"I found no names containing {arguments['query']!r} in {folder_name}."
        else:
            preview = ", ".join(matches[:10])
            suffix = f" and {len(matches) - 10} more" if len(matches) > 10 else ""
            message = f"I found {len(matches)} match(es) in {folder_name}: {preview}{suffix}."
        return ToolResult(
            "search_approved_folder",
            ToolStatus.SUCCESS,
            message,
            data={"folder": folder_name, "matches": matches, "truncated": len(matches) == 25},
        )

    return ToolSpec(
        "search_approved_folder",
        "Search names only inside Documents, Downloads or Desktop.",
        {
            "folder": ToolArgument(ToolArgumentKind.ENUM, choices=FOLDER_NAMES),
            "query": ToolArgument(ToolArgumentKind.TEXT, maximum_length=120),
        },
        ToolRisk.READ_ONLY,
        search_folder,
        timeout_seconds=5.0,
        max_output_bytes=32 * 1024,
    )
