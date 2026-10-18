"""Isolate unrenderable design and provide editable requirement recovery workspaces."""
import copy
from pathlib import Path

from compiler.frontend_design.delta import make_ui
from compiler.frontend_design.validation import ui_index
from compiler.preprocessing.traversal import children_by_id, postorder
from .files import write_stage, digest
from utils.jsonio import read_json, write_json_atomic
from .planning import slug, token
from .types import refs


def leaf_ids(requirements):
    children = children_by_id(requirements)
    return [rid for rid in postorder(requirements) if not children[rid]]


def empty_design(stage):
    if stage == 'database':
        return {'ir_kind': 'database_schema', 'status': 'RESOLVED', 'tables': []}
    if stage == 'backend':
        return {'ir_kind': 'backend_design', 'types': [], 'modules': []}
    return {'ir_kind': 'frontend_design', 'ui': make_ui('UI_APP', 'Application', 'app', 'Application root.'),
            'types': [], 'data': [], 'handlers': [], 'events': []}


def used_types(definitions, contracts):
    wanted = set().union(*(refs(raw) for raw in contracts))
    registry = {row['name']: row for row in definitions}
    pending = list(wanted)
    while pending:
        name = pending.pop()
        if name in registry:
            fresh = refs(registry[name]['schema']) - wanted
            pending.extend(fresh)
            wanted.update(fresh)
    return [copy.deepcopy(row) for row in definitions if row['name'] in wanted]


def backend_slice(design, owners):
    modules = {row['id']: row for row in design['modules']}
    if len(modules) != len(design['modules']):
        raise ValueError('Duplicate module IDs cannot be resolved safely')
    selected = {key for key, row in modules.items() if owners & set(row.get('reqs', []))}
    pending = list(selected)
    while pending:
        for key in modules[pending.pop()]['callees']:
            if key not in modules:
                raise ValueError(f'Unresolved module {key}')
            if key not in selected:
                selected.add(key)
                pending.append(key)
    rows = [copy.deepcopy(row) for row in design['modules'] if row['id'] in selected]
    for row in rows:
        row['callers'] = [key for key in row['callers'] if key in selected]
    raw = [row[field] for row in rows for field in
           (('payload', 'response') if row['kind'] == 'API' else ('input', 'output'))]
    return {**design, 'modules': rows, 'types': used_types(design['types'], raw)}


def frontend_slice(design, pages):
    result = copy.deepcopy(design)
    result['ui']['children'] = copy.deepcopy(pages)
    nodes, _ = ui_index(result['ui'])
    result['data'] = [row for row in result['data'] if row['owner'] in nodes]
    result['events'] = [row for row in result['events'] if row['ui'] in nodes]
    handlers = {row['handler'] for row in result['events']}
    result['handlers'] = [row for row in result['handlers'] if row['id'] in handlers]
    data = {row['id'] for row in result['data']}
    events = {row['id'] for row in result['events']}
    for row in nodes.values():
        row['data'] = [key for key in row['data'] if key in data]
        row['events'] = [key for key in row['events'] if key in events]
    raw = [row['schema'] for row in result['data']]
    raw += [row[field] for row in result['handlers'] for field in ('input', 'output')]
    result['types'] = used_types(result['types'], raw)
    return result


def isolate_design(stage, lowerer, requirements, design, output):
    """Try the valid cumulative subset; failures cannot discard unrelated requirements."""
    accepted, rejected = [], {}
    candidate = empty_design(stage)
    if stage == 'backend':
        units = leaf_ids(requirements)
    else:
        units = design.get('tables', []) if stage == 'database' else design.get('ui', {}).get('children', [])
    for unit in units:
        try:
            if stage == 'backend':
                trial = backend_slice(design, set(accepted + [unit]))
            elif stage == 'frontend':
                trial = frontend_slice(design, accepted + [unit])
            else:
                trial = {**design, 'status': 'RESOLVED', 'tables': accepted + [unit]}
            args = (trial,) if stage == 'database' else (requirements, trial)
            lowerer.compile(*args, output, dry_run=True)
            accepted.append(unit)
            candidate = trial
        except Exception as exc:
            if stage == 'backend':
                owners = [unit]
            elif stage == 'database':
                owners = unit.get('requirement_ids', [])
            else:
                owners = [rid for rid, node in requirements['nodes'].items()
                          if any(page['name'] == unit.get('name') for page in node.get('pages', []))]
            for rid in owners or leaf_ids(requirements):
                rejected[rid] = str(exc)[:1000]
    args = (candidate,) if stage == 'database' else (requirements, candidate)
    plan = lowerer.compile(*args, output)
    plan['recovery_reasons'] = rejected
    return plan, candidate


def bind_recovery(output, stage, plan, reasons):
    """Bind compilation-safe empty files, without inventing routes or business contracts."""
    if not reasons:
        return plan
    output = Path(output).resolve()
    report_path = output / '.arc/lowering' / stage / 'report.json'
    report = read_json(report_path, {})
    records = {row['requirement_id']: row for row in plan.get('recovery', [])}
    files = {path: (output / path).read_text(encoding='utf-8') for path in plan.get('files', [])}
    for rid, reason in sorted(reasons.items()):
        folder = f'{stage}/src/recovery/{slug(rid)}-{token(rid)}'
        names = ('api.ts', 'business.ts', 'storage.ts') if stage == 'backend' else ('View.tsx', 'handlers.ts', 'state.ts')
        paths = [f'{folder}/{name}' for name in names]
        for path in paths:
            files[path] = (output / path).read_text(encoding='utf-8') if (output / path).is_file() else 'export {};\n'
        records[rid] = {'requirement_id': rid, 'reason': reason[:1000], 'file_paths': paths}
    plan['recovery'] = list(records.values())
    plan = write_stage(output, stage, plan, files)
    if report.get('status') == 'COMPILED':
        # Added files are empty modules with no imports or compiler dependencies.
        report.update(generated_files=len(files), recovery_requirements=sorted(records),
                      retained_modified_files=plan['retained_modified_files'])
        write_json_atomic(report_path, report)
    return plan


def minimal_lowering(stage, lowerer, requirements, output):
    """Install a minimal runtime while respecting sources modified after generation."""
    output = Path(output)
    design = empty_design(stage)
    args = (design,) if stage == 'database' else (requirements, design)
    plan, files = lowerer.compile(*args, output, dry_run=True)
    owned = read_json(output / '.arc/lowering' / stage / 'manifest.json', {}).get('files', {})
    for path in files:
        current = output / path
        if current.is_file():
            source = current.read_text(encoding='utf-8')
            if owned.get(path) != digest(source):
                files[path] = source
    return write_stage(output, stage, plan, files), design
