"""Hash-checked, transactional whole-file edits, including creation of assigned test files."""
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from .tools import IndeterminateToolCall


@dataclass(frozen=True, slots=True)
class FileInvocation:
    payload: dict
    versions: dict


def sha256(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def source_versions(files):
    """Capture the exact source supplied to one model call; never send these tokens."""
    return {path: (record['exists'], sha256(record['content'])) for path, record in files.items()}


def source_path(root, relative):
    root = Path(root).resolve()
    relative = relative.replace('\\', '/')
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or any(part.casefold() == '.arc' for part in path.relative_to(root).parts[:1]):
        raise ValueError(f'Out-of-workspace source: {relative}')
    if path != Path(os.path.abspath(root / relative)):
        raise ValueError(f'Linked source paths are not allowed: {relative}')
    return path


def read_source(root, relative):
    path = source_path(root, relative)
    return path.read_text(encoding='utf-8') if path.is_file() else ''


def restore(root, snapshots):
    paths = {relative: source_path(root, relative) for relative in snapshots}
    for relative, content in snapshots.items():
        path = paths[relative]
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')


def apply_edits(root, edits, allowlist, *, expected_versions, ignore_unchanged=False):
    root = Path(root).resolve()
    edits = [{**row, 'file': row['file'].replace('\\', '/')} for row in edits]
    snapshots, seen = {}, set()
    for row in edits:
        relative = row['file']
        if relative not in allowlist or relative in seen:
            raise ValueError(f'Unknown/read-only or duplicate edit file: {relative}')
        seen.add(relative)
        current = read_source(root, relative)
        if relative not in expected_versions or expected_versions[relative] != (
                (root / relative).is_file(), sha256(current)):
            raise ValueError(f'Source changed after it was read: {relative}; use the refreshed file contents')
        if not row['content'].strip():
            raise ValueError(f'Empty replacement source: {relative}')
        if current == row['content'] and ignore_unchanged:
            continue
        if current == row['content']:
            raise ValueError(f'No-op edit: {relative}')
        snapshots[relative] = current if (root / relative).is_file() else None
    try:
        for row in edits:
            if row['file'] not in snapshots:
                continue
            path = source_path(root, row['file'])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(row['content'], encoding='utf-8')
    except BaseException:
        try:
            restore(root, snapshots)
        except BaseException as error:
            raise IndeterminateToolCall('File application failed and restoration could not be confirmed') from error
        raise
    return snapshots


class FileTransaction:
    def __init__(self, root):
        self.root, self.snapshots, self.accepted, self.applied = Path(root).resolve(), {}, False, False

    def __enter__(self):
        return self

    def apply(self, edits, allowlist, *, expected_versions, ignore_unchanged=False):
        if self.applied:
            raise RuntimeError('FileTransaction supports one proposal')
        self.applied = True
        self.snapshots = apply_edits(self.root, edits, allowlist,
            expected_versions=expected_versions, ignore_unchanged=ignore_unchanged)
        return self.snapshots

    def accept(self):
        self.accepted = True

    def __exit__(self, error_type, error, traceback):
        if not self.accepted and self.snapshots:
            try:
                restore(self.root, self.snapshots)
            except BaseException as failure:
                raise IndeterminateToolCall('Proposal rejected but restoration could not be confirmed') from failure
        return False
