"""Bounded source repair for lowering/build gates, with explicit file loading."""
import os
from pathlib import Path

from compiler.backend_design.validation import shape_errors
from compiler.implementation.contracts import SCHEMA
from compiler.implementation.patcher import apply_edits, read_source, restore, sha256
from utils.jsonio import write_json_atomic
from utils.logging import SynchronousLog


INSTRUCTIONS = '''## Background and role
You repair compilation of an initialized Express/React TypeScript project.
current_task identifies a lowering or build gate. gate_results contains the actual failure.
For lowering preserve minimal typed skeletons; do not implement business logic or weaken contracts.
For build preserve all implemented behavior, requirement contracts and acceptance tests.

## Context and edits
file_catalog provides available files; editable_files contains explicitly loaded current source and
hashes. Request exact catalog paths before inspecting/editing them. Return request_files OR edits,
never both. Edits replace whole loaded files with expected_sha256. Do not invent source, disable
type checking, remove build commands, change dependency versions or modify tests/design artifacts.
Use feedback and previous_output to correct a rejected proposal; rejected edits were rolled back.
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
    loaded, feedback, previous = set(), [], None
    for attempt in range(1, attempts + 1):
        log.info(f'{stage}: repair {attempt}/{attempts}')
        try:
            for load_round in range(13):
                payload = {'current_task': {'stage': stage, 'contracts': contracts},
                    'gate_results': evidence, 'file_catalog': sorted(catalog | configs),
                    'editable_files': {p: {'content': read_source(root, p), 'sha256': sha256(read_source(root, p))}
                                       for p in sorted(loaded & catalog)},
                    'read_only_files': {p: {'content': read_source(root, p)} for p in sorted(loaded & configs)},
                    'validation_feedback': feedback, 'previous_output': previous}
                trace = {'attempt': attempt, 'load_round': load_round, 'input': payload}
                snapshots = {}
                try:
                    reply = model.generate_json(schema_name='stage_repair', instructions=INSTRUCTIONS,
                                                input_payload=payload, output_schema=SCHEMA)
                    previous = reply
                    trace['output'] = reply
                    errors = shape_errors(reply, SCHEMA)
                    if errors:
                        raise ValueError('; '.join(errors))
                    if reply['blocked_reason']:
                        raise ValueError(reply['blocked_reason'])
                    if reply['request_files']:
                        requested = set(reply['request_files'])
                        if reply['edits'] or requested - catalog - configs or not requested - loaded:
                            raise ValueError('Request only unloaded catalog files without edits')
                        if load_round == 12:
                            raise ValueError('File loading budget exhausted')
                        loaded.update(requested)
                        continue
                    if not reply['edits']:
                        raise ValueError('Provide a targeted source repair')
                    snapshots = apply_edits(root, reply['edits'], loaded & catalog)
                    result = check()
                    trace['gate_results'] = result
                    if result['ok']:
                        write_json_atomic(artifact / 'report.json', {'status': 'REPAIRED', 'attempts': attempt})
                        return result
                    evidence = result
                    raise ValueError('Repaired source still fails the gate')
                except Exception:
                    if snapshots:
                        restore(root, snapshots)
                    raise
                finally:
                    write_json_atomic(artifact / f'{attempt:02d}_{load_round:02d}.json', trace)
        except Exception as exc:
            feedback = [str(exc)]
            log.warning(f'{stage}: ' + str(exc))
    write_json_atomic(artifact / 'report.json', {'status': 'FAILED', 'attempts': attempts,
                                               'feedback': feedback, 'gate_results': evidence})
    return {'ok': False, 'errors': feedback, 'gate_results': evidence}
