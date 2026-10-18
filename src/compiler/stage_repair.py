"""Bounded source repair for lowering/build gates, with explicit file loading."""
import os
from pathlib import Path

from compiler.backend_design.validation import shape_errors
from compiler.implementation.contracts import SCHEMA
from compiler.implementation.patcher import apply_edits, read_source, restore, source_versions
from utils.jsonio import write_json_atomic
from utils.logging import SynchronousLog


INSTRUCTIONS = '''## Background and role
You repair compilation of an initialized Express/React TypeScript project.
current_task identifies a lowering or build gate. gate_results contains the actual failure.
For lowering preserve minimal typed skeletons; do not implement business logic or weaken contracts.
For build preserve all implemented behavior, requirement contracts and acceptance tests.

## Context and edits
file_catalog provides available files; editable_files contains explicitly loaded current source.
Request exact catalog paths before inspecting/editing them. Return request_files OR edits,
never both. Edits provide only file and full replacement content; version checks are compiler-owned.
Optional release_files removes unneeded files from context, not disk. Do not invent source, disable
type checking, remove build commands, change dependency versions or modify tests/design artifacts.
validation_feedback describes the failure; rejected_patch lists the proposed files with installed:false.
The rejected reply's edits were all rolled back. Only the supplied current file contents reflect
installed code; earlier accepted changes remain installed. Submit coordinated fixes together.
If the cause cannot be repaired in the permitted source scope, explain it in blocked_reason.
Return only request_files, edits and blocked_reason matching the response schema.
'''


def repair_gate(model, output, stage, check, evidence, *, contracts=None):
    root = Path(output).resolve()
    log = SynchronousLog('StageRepair', workspace_root=root)
    artifact = root / '.arc/repairs' / stage
    try:
        attempts = max(1, int(os.getenv('MODEL_ATTEMPT', '5')))
    except ValueError:
        attempts = 5
    catalog = set()
    for layer in ('backend', 'frontend', 'shared'):
        for path in (root / layer / 'src').rglob('*'):
            if path.is_file() and path.suffix in {'.ts', '.tsx', '.js', '.jsx', '.css'}:
                catalog.add(path.relative_to(root).as_posix())
    # Configurations are inspectable, but repairs cannot suppress compilation by editing them.
    configs = {path.relative_to(root).as_posix() for layer in ('backend', 'frontend', 'shared', '')
               for path in (root / layer).glob('*.json') if path.name in
               {'package.json', 'tsconfig.json', 'tsconfig.app.json'}}
    loaded, feedback, rejected_files = set(), [], []
    for attempt in range(1, attempts + 1):
        log.info(f'{stage}: repair {attempt}/{attempts}')
        try:
            for load_round in range(13):
                payload = {'current_task': {'stage': stage, 'contracts': contracts},
                    'gate_results': evidence, 'file_catalog': sorted(catalog | configs),
                    'editable_files': {p: {'content': read_source(root, p), 'exists': (root / p).is_file()}
                                       for p in sorted(loaded & catalog)},
                    'read_only_files': {p: {'content': read_source(root, p)} for p in sorted(loaded & configs)},
                    'validation_feedback': feedback,
                    'rejected_patch': {'installed': False, 'files': rejected_files}}
                expected_versions = source_versions(payload['editable_files'])
                snapshots = {}
                reply = None
                try:
                    reply = model.generate_json(schema_name='stage_repair', instructions=INSTRUCTIONS,
                                                input_payload=payload, output_schema=SCHEMA)
                    errors = shape_errors(reply, SCHEMA)
                    if errors:
                        raise ValueError('; '.join(errors))
                    if reply['blocked_reason']:
                        raise ValueError(reply['blocked_reason'])
                    if reply['request_files'] or reply.get('release_files'):
                        requested = set(reply['request_files'])
                        released = set(reply.get('release_files', []))
                        if reply['edits'] or (requested | released) - catalog - configs:
                            raise ValueError('Read/release only catalog files without edits')
                        if load_round == 12:
                            raise ValueError('File loading budget exhausted')
                        loaded.update(requested)
                        loaded.difference_update(released)
                        continue
                    if not reply['edits']:
                        raise ValueError('Provide a targeted source repair')
                    snapshots = apply_edits(root, reply['edits'], loaded & catalog,
                                            expected_versions=expected_versions)
                    result = check()
                    if result['ok']:
                        write_json_atomic(artifact / 'report.json', {'status': 'REPAIRED', 'attempts': attempt})
                        return result
                    evidence = result
                    raise ValueError('Repaired source still fails the gate')
                except Exception:
                    if snapshots:
                        restore(root, snapshots)
                    edits = reply.get('edits', []) if isinstance(reply, dict) else []
                    rejected_files = sorted({edit['file'] for edit in edits
                                             if isinstance(edit, dict) and isinstance(edit.get('file'), str)}) if isinstance(edits, list) else []
                    raise
        except Exception as exc:
            feedback = [str(exc)]
            log.warning(f'{stage}: ' + str(exc))
    write_json_atomic(artifact / 'report.json', {'status': 'FAILED', 'attempts': attempts,
                                               'feedback': feedback})
    return {'ok': False, 'errors': feedback, 'gate_results': evidence}
