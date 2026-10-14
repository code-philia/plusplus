"""Create bounded workspace archives outside the source directory."""
from __future__ import annotations

import re
import zipfile
from threading import RLock
from pathlib import Path
from utils.logging import SynchronousLog

_archive_lock = RLock()


def snapshot_workspace(root: Path, trigger: str) -> None:
    with _archive_lock:
        _snapshot_workspace(root, trigger)


def _snapshot_workspace(root: Path, trigger: str) -> None:
    parent = root.parent
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(trigger)).strip("._") or "checkpoint"
    # Keep trace archives beside, not inside, the generated workspace. This
    # prevents snapshots from becoming part of the next snapshot and gives
    # each run a stable sibling trace directory.
    trace_dir = parent / f"{root.name}_trace"
    archive = trace_dir / f"{root.name}_{safe}.zip"
    if archive.exists():
        return
    try:
        trace_dir.mkdir(parents=True, exist_ok=True)
        temporary = archive.with_suffix('.zip.tmp')
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for path in root.rglob("*"):
                if not path.is_file() or path.is_symlink() or "node_modules" in path.parts:
                    continue
                try:
                    output.write(path, path.relative_to(root.parent))
                except FileNotFoundError:
                    continue
        temporary.replace(archive)
        SynchronousLog('Snapshot', workspace_root=root).info(f'SNAPSHOT trigger={trigger} path={archive}')
    except OSError as exc:
        SynchronousLog('Snapshot', workspace_root=root).warning(f'Snapshot failed: {trigger}: {exc}')

