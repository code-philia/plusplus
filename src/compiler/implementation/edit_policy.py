"""Phase-specific source visibility and executable write authorization."""
from __future__ import annotations


class EditPolicy:
    @staticmethod
    def phase_path(path, phase):
        if phase == 'backend_implementation':
            return path.startswith(('backend/', 'shared/', 'tests/unit/', 'tests/integration/')) or path in {'tests/tdd/setup.ts', 'tests/tdd/vitest.config.ts'}
        if phase == 'frontend_implementation':
            return path.startswith(('frontend/', 'shared/', 'tests/e2e/')) or path == 'tests/tdd/playwright.config.ts'
        return True

    @staticmethod
    def is_editable_file(path, record, phase, test_targets):
        is_test = path in test_targets or bool(record and record['kind'] == 'acceptance_test')
        if phase == 'test_generation':
            writable = path in test_targets
        else:
            writable = record is not None and (record['kind'] == 'source' or phase == 'repair' and is_test)
        if phase in {'backend_implementation', 'frontend_implementation'}:
            layer = phase.split('_')[0]
            writable = writable and (path.startswith(layer + '/') or path.startswith('shared/'))
        if phase == 'test_generation' and record and record['kind'] == 'test_support':
            writable = False
        return writable

    @staticmethod
    def discovery_path(path, phase):
        if phase in {'backend_implementation', 'frontend_implementation'}:
            return path.startswith((phase.split('_')[0] + '/', 'shared/'))
        return True

    def source_paths(self, direct, active, released, recovery, phase):
        paths = (direct | active) - released
        if phase in {'backend_implementation', 'frontend_implementation'}:
            paths = {path for path in paths if self.discovery_path(path, phase)}
        if phase == 'frontend_implementation' and recovery.get('backend'):
            paths.update(recovery['backend']['file_paths'])
            paths.add('backend/src/app.ts')
        return paths

    def authorize(self, paths, records, phase, test_targets):
        return {path for path in paths if self.is_editable_file(path, records.get(path), phase, test_targets)}
