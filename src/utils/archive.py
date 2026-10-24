"""Threshold snapshots of generated workspaces, excluding credentials and path aliases."""
from __future__ import annotations

import re
import os
from pathlib import Path
from threading import RLock
from zipfile import ZIP_DEFLATED, ZipFile

from .logging import SynchronousLog

_archive_lock = RLock()


def snapshot_workspace(root, trigger):
    root = Path(root).resolve()
    safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(trigger)).strip('._') or 'checkpoint'
    directory = root.parent / (root.name + '_trace')
    with _archive_lock:
        archive = directory / f'{root.name}_{safe}.zip'
        if archive.exists():
            return archive
        directory.mkdir(parents=True, exist_ok=True)
        temporary = archive.with_suffix('.zip.tmp')
        try:
            with ZipFile(temporary, 'w', compression=ZIP_DEFLATED) as output:
                def excluded(path):
                    name = path.name.lower()
                    return (name in {'.git', 'node_modules', '.venv', '.env'} or name.startswith('.env.')
                            or path.is_symlink() or getattr(path, 'is_junction', lambda: False)())
                for directory, directories, filenames in os.walk(root, followlinks=False):
                    current = Path(directory)
                    directories[:] = [name for name in directories if not excluded(current / name)]
                    for name in filenames:
                        path = current / name
                        if not excluded(path) and path.is_file() and path.resolve().is_relative_to(root):
                            output.write(path, Path(root.name) / path.relative_to(root))
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
        SynchronousLog('Snapshot', workspace_root=root).info(f'SNAPSHOT trigger={trigger} path={archive}')
        return archive
