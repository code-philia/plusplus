"""Gate repair policy: catalog visibility, source allowlist and check ownership."""
from __future__ import annotations

from pathlib import Path

from agent.files import FileInvocation, FileTransaction, read_source, source_versions
from agent.tools import JSON_ACTION_SCHEMA
from compiler.context.model_calls import invoke_model
from compiler.repair_presentation import repair_metadata


class GateRepairPolicy:
    context_budget_message = 'File loading budget exhausted'

    def __init__(self, *, model, root, stage, check, evidence, contracts, instructions, logger, attempts):
        self.model, self.root, self.stage, self.check = model, Path(root).resolve(), stage, check
        self.evidence, self.contracts, self.instructions = evidence, contracts, instructions
        self.log, self.attempts, self.loaded = logger, attempts, set()
        self.catalog = {path.relative_to(self.root).as_posix()
            for layer in ('backend', 'frontend', 'shared') for path in (self.root / layer / 'src').rglob('*')
            if path.is_file() and path.suffix in {'.ts', '.tsx', '.js', '.jsx', '.css'}}
        self.configs = {path.relative_to(self.root).as_posix()
            for layer in ('backend', 'frontend', 'shared', '') for path in (self.root / layer).glob('*.json')
            if path.name in {'package.json', 'tsconfig.json', 'tsconfig.app.json'}}

    def begin_attempt(self, attempt):
        self.log.info(f'{self.stage}: repair {attempt}/{self.attempts}')

    def prepare(self, feedback, rejected_files):
        payload = {'current_task': {'stage': self.stage, 'contracts': self.contracts},
            'gate_results': self.evidence, 'file_catalog': sorted(self.catalog | self.configs),
            'editable_files': {path: {'content': read_source(self.root, path), 'exists': (self.root / path).is_file()}
                               for path in sorted(self.loaded & self.catalog)},
            'read_only_files': {path: {'content': read_source(self.root, path)}
                                for path in sorted(self.loaded & self.configs)},
            'validation_feedback': feedback, 'rejected_patch': {'installed': False, 'files': rejected_files}}
        return FileInvocation(payload, source_versions(payload['editable_files']))

    def invoke(self, invocation):
        return invoke_model(self.model, repair_metadata(), schema_name='stage_repair',
            instructions=self.instructions, input_payload=invocation.payload, output_schema=JSON_ACTION_SCHEMA)

    def validate_context(self, invocation, arguments):
        if (set(arguments['request_files']) | set(arguments['release_files'])) - self.catalog - self.configs:
            raise ValueError('Read/release only catalog files without edits')

    def context(self, invocation, arguments):
        self.loaded.update(arguments['request_files'])
        self.loaded.difference_update(arguments['release_files'])
        return {'loaded': sorted(self.loaded)}

    def propose(self, invocation, edits, *, check_cancelled):
        with FileTransaction(self.root) as transaction:
            transaction.apply(edits, self.loaded & self.catalog, expected_versions=invocation.versions)
            result = self.check()
            if not result['ok']:
                self.evidence = result
                raise ValueError('Repaired source still fails the gate')
            check_cancelled()
            transaction.accept()
        return result

    def reject(self, error, invocation, raw):
        self.log.warning(f'{self.stage}: ' + str(error))
        return [str(error)]
