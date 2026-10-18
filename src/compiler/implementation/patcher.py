"""Hash-checked, transactional whole-file edits, including creation of assigned test files."""
import hashlib
from pathlib import Path


def _sha256(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def source_versions(files):
    """Capture the exact source supplied to one model call; never send these tokens."""
    return {path: (record['exists'], _sha256(record['content'])) for path, record in files.items()}


def read_source(root, relative):
    relative = relative.replace('\\', '/')
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


def apply_edits(root, edits, allowlist, *, expected_versions, ignore_unchanged=False):
    root = Path(root).resolve()
    # Model replies may copy native Windows separators from compiler diagnostics.
    # Keep the existing canonical allowlist and version checks authoritative.
    edits = [{**row, 'file': row['file'].replace('\\', '/')} for row in edits]
    snapshots, seen = {}, set()
    for row in edits:
        relative = row['file']
        if relative not in allowlist or relative in seen:
            raise ValueError(f'Unknown/read-only or duplicate edit file: {relative}')
        seen.add(relative)
        current = read_source(root, relative)
        if relative not in expected_versions or expected_versions[relative] != (
                (root / relative).is_file(), _sha256(current)):
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
            path = root / row['file']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(row['content'], encoding='utf-8')
    except OSError:
        restore(root, snapshots)
        raise
    return snapshots
