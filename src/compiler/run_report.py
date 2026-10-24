"""Compilation records and persistence, independent of stage execution policy."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from utils.jsonio import write_json_atomic


@dataclass
class CompilationRunReport:
    path: Path
    run_id: str
    states: dict[str, str]
    artifacts: dict[str, str]
    stage: str = 'preprocessing'
    current: dict[str, Any] | None = None
    stages: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    failed_nodes: list[str] = field(default_factory=list)

    def save(self, status):
        write_json_atomic(self.path, {'status': status, 'run_id': self.run_id, 'stage': self.stage,
            'stages': self.stages, 'node_states': self.states, 'errors': self.errors,
            'failed_nodes': self.failed_nodes})

    def begin(self, name):
        self.stage = name
        record = {'stage': name, 'status': 'RUNNING'}
        self.current = record
        self.stages.append(record)
        self.save('RUNNING')
        return record

    def complete(self, record, path=None, warnings=(), *, status='COMPLETED'):
        record.update(status=status, warnings=list(warnings))
        if path:
            self.artifacts[record['stage']] = str(path)
        self.save('RUNNING')

    def fail(self, record, message, path=None):
        self.errors.append(message)
        record.update(status='FAILED', errors=[message])
        if path:
            self.artifacts[record['stage']] = str(path)
        self.save('RUNNING')

    def add_warning(self, record, message):
        record.setdefault('warnings', []).append(message)
        self.save('RUNNING')

    def skip(self, name, warnings):
        # A skipped stage is recorded without becoming the active execution stage.
        self.stages.append({'stage': name, 'status': 'SKIPPED', 'warnings': warnings})

    def record_fatal_error(self, exc):
        # The caller updates failed nodes before persisting the final failure snapshot.
        self.errors.append(f'{self.stage}: {exc}')
        if self.current is not None:
            self.current.update(status='FAILED', errors=[str(exc)])
