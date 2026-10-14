"""Hash-checked, transactional whole-file edits, including creation of assigned test files."""
import hashlib
from pathlib import Path


def sha256(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def read_source(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or relative.startswith('.arc/'):
        raise ValueError(f'Out-of-workspace source: {relative}')
    return path.read_text(encoding='utf-8') if path.is_file() else ''


def restore(root, snapshots):
    for relative, content in snapshots.items():
        path = Path(root) / relative
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')


def apply_edits(root, edits, allowlist, *, ignore_unchanged=False):
    root = Path(root).resolve()
    snapshots, seen = {}, set()
    for row in edits:
        relative = row['file']
        if relative not in allowlist or relative in seen:
            raise ValueError(f'Unknown/read-only or duplicate edit file: {relative}')
        seen.add(relative)
        current = read_source(root, relative)
        if sha256(current) != row['expected_sha256']:
            raise ValueError(f'Stale source hash: {relative}; request current file')
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
            path = root / row['file']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(row['content'], encoding='utf-8')
    except OSError:
        restore(root, snapshots)
        raise
    return snapshots
