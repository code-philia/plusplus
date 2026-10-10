"""Create bounded workspace archives outside the source directory."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path


def snapshot_workspace(root: Path, trigger: str) -> None:
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
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for path in root.rglob("*"):
                if not path.is_file() or "node_modules" in path.parts:
                    continue
                output.write(path, path.relative_to(root.parent))
    except OSError:
        return


