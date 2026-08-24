"""Initialize, verify, or deliberately rebind Ron's external memory drive."""

from __future__ import annotations

import argparse
import shutil
from datetime import UTC, datetime
from pathlib import Path

from ron.storage import StorageManager, StorageState

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BINDING_PATH = PROJECT_ROOT / "runtime" / "memory" / "core" / "storage_binding.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bind a drive/folder as Ron's persistent long-term memory storage."
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Drive root, for example E:\\ on Windows.",
    )
    parser.add_argument(
        "--rebind",
        action="store_true",
        help=(
            "Explicitly replace Ron's existing drive binding. This does not copy old "
            "memory; use it only when intentionally replacing/recovering the storage drive."
        ),
    )
    args = parser.parse_args()
    root = args.path.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        parser.error(f"Storage path does not exist or is not a directory: {root}")

    backup: Path | None = None
    if args.rebind and BINDING_PATH.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup = BINDING_PATH.with_name(f"storage_binding.{stamp}.backup.json")
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BINDING_PATH, backup)
        BINDING_PATH.unlink()

    manager = StorageManager(PROJECT_ROOT, locator=lambda: root)
    health = manager.refresh_once(sync=True)
    print(f"Storage state: {health.state.value}")
    print(f"External root: {health.external_root or 'not available'}")
    print(f"Queued items: {health.pending_items}")
    print(health.detail)

    if health.state is not StorageState.ONLINE:
        if backup is not None and backup.exists():
            shutil.copy2(backup, BINDING_PATH)
            print("Previous storage binding restored because the rebind did not verify.")
        return 1

    if backup is not None:
        print(f"Previous binding backup: {backup}")
    print("Ron storage initialized and verified. You may now start Ron normally.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
