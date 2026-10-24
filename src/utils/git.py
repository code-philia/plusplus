"""Workspace-only checkpoints on a private ref, without touching HEAD or the user's index."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from threading import RLock
from uuid import uuid4

from .jsonio import write_json_atomic
from .logging import SynchronousLog, current_run_id
from .process import resolve_executable, run_command

_checkpoint_lock = RLock()


class GitCommandError(RuntimeError):
    pass


def enabled():
    return os.environ.get('ARC_GIT_CHECKPOINTS', '1').strip().lower() in {'1', 'true', 'yes', 'on'}


def run_git(root, args, *, environment=None):
    environment = dict(environment) if environment is not None else {
        key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    executable = resolve_executable('git', environment)
    if executable is None:
        raise GitCommandError('git is unavailable on PATH')
    try:
        with TemporaryDirectory(prefix='arc-git-hooks-') as hooks:
            result = run_command([executable, '--literal-pathspecs', '-c', 'core.fsmonitor=false',
                '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=' + hooks,
                '-c', 'user.name=ARC Compiler', '-c', 'user.email=arc@localhost.invalid', *args],
                cwd=root, environment=environment, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitCommandError(str(error)) from error
    if result.returncode:
        raise GitCommandError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def checked_path(root, value):
    text = str(value).replace('\\', '/')
    path = PurePosixPath(text)
    if not text or path.is_absolute() or '..' in path.parts or ':' in text:
        raise ValueError('Checkpoint paths must be relative workspace paths')
    if any(part.lower() in {'.git', 'node_modules', '.venv', '.env'} or part.lower().startswith('.env.') for part in path.parts):
        raise ValueError('Checkpoint cannot include repository internals, dependencies or credentials')
    if any(part.endswith((' ', '.')) for part in path.parts):
        raise ValueError('Checkpoint paths cannot contain ambiguous Windows names')
    target = root / path
    if not target.resolve().is_relative_to(root):
        raise ValueError('Checkpoint path escapes workspace')
    if any(parent.is_symlink() or getattr(parent, 'is_junction', lambda: False)()
           for parent in (target, *target.parents) if parent != root and parent.is_relative_to(root)):
        raise ValueError('Checkpoint cannot include path aliases')
    if target.exists() and not target.is_file():
        raise ValueError('Checkpoint paths must name individual files')
    return path.as_posix()


def checkpoint(root, event, *, node=None, phase=None, payload=None, paths=(), status='ACCEPTED'):
    if not enabled():
        return None
    root = Path(root).resolve()
    logger = SynchronousLog('Git', workspace_root=root)
    try:
        with _checkpoint_lock:
            if root == Path(__file__).resolve().parents[2]:
                raise ValueError('Refusing to checkpoint the compiler source repository')
            selected = [checked_path(root, path) for path in paths]
            identity = uuid4().hex
            record = {'id': identity, 'run_id': current_run_id(), 'event': event, 'node': node,
                'phase': phase, 'status': status, 'timestamp': datetime.now(timezone.utc).isoformat(),
                'paths': selected, 'payload': payload}
            metadata = f'.arc/checkpoints/{identity}.json'
            checked_path(root, metadata)
            write_json_atomic(root / metadata, record)
            if not (root / '.git').exists():
                run_git(root, ['init', '--quiet'])
            if (root / '.git').is_symlink() or getattr(root / '.git', 'is_junction', lambda: False)():
                raise ValueError('Checkpoint repository metadata must not be an alias')
            if Path(run_git(root, ['rev-parse', '--show-toplevel'])).resolve() != root:
                raise ValueError('Checkpoint repository must belong to this workspace, not an ancestor')
            reference = 'refs/arc/checkpoints/' + hashlib.sha256(str(root).encode('utf-8')).hexdigest()[:16]
            references = run_git(root, ['for-each-ref', '--format=%(objectname)', reference]).splitlines()
            parent = references[0] if references else None
            with TemporaryDirectory(prefix='arc-git-index-') as directory:
                environment = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
                environment['GIT_INDEX_FILE'] = str(Path(directory) / 'index')
                def git(args):
                    return run_git(root, args, environment=environment)
                git(['read-tree', parent] if parent else ['read-tree', '--empty'])
                for relative in dict.fromkeys([*selected, metadata]):
                    target = root / relative
                    if target.is_file():
                        blob = git(['hash-object', '-w', '--no-filters', '--', relative])
                        mode = '100755' if os.name != 'nt' and target.stat().st_mode & 0o111 else '100644'
                        git(['update-index', '--add', '--cacheinfo', mode, blob, relative])
                    else:
                        git(['update-index', '--force-remove', '--', relative])
                tree = git(['write-tree'])
                message = f'ARC {event}: {node or "workspace"} / {phase or "checkpoint"} [{status}]'
                commit = git(['commit-tree', tree, *(['-p', parent] if parent else []), '-m', message])
                git(['update-ref', '--create-reflog', reference, commit, parent or '0' * len(commit)])
            receipt = {**record, 'commit': commit, 'ref': reference, 'metadata': metadata}
            receipt.pop('payload')
            journal = root / '.arc/git_checkpoints.jsonl'
            checked_path(root, journal.relative_to(root).as_posix())
            with journal.open('a', encoding='utf-8') as output:
                output.write(json.dumps(receipt, ensure_ascii=False) + '\n')
            logger.info(f'GIT_CHECKPOINT node={node} phase={phase} status={status} commit={commit} ref={reference}')
            return receipt
    except (OSError, ValueError, GitCommandError) as error:
        logger.warning(f'GIT_CHECKPOINT_FAILED event={event} node={node} phase={phase}: {error}')
        return None
