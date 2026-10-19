"""Re-design identity from the saved domain ER, without rerunning domain model passes."""
from __future__ import annotations
import argparse
import copy
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from compiler.database_design.state import DatabaseSchemaState
from compiler.database_design.refinement import refine_er_model
from compiler.identity.stage import compile_identity
from utils.jsonio import write_json_atomic


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, help='Generated application workspace containing .arc')
    parser.add_argument('--binding', help='Reviewed proposal JSON; bypasses model inference, but never validation')
    args = parser.parse_args(argv)
    root = Path(args.workspace).expanduser().resolve() / '.arc'
    try:
        requirements = json.loads((root / 'preprocessing/requirement_ir.json').read_text())
        er = json.loads((root / 'design/identity/domain_er.json').read_text())
        if er.get('identity'):
            raise ValueError('domain_er.json must contain the domain graph before identity installation')
        state = DatabaseSchemaState()
        for rid in requirements['nodes']:
            state.ensure_requirement(rid)
        for entity in er['entities']:
            state.entities[entity['key']] = {**copy.deepcopy(entity),
                'fields': {field['name']: copy.deepcopy(field) for field in entity['fields']}}
            for rid in entity['requirement_ids']:
                state._link_entity(rid, entity['key'])
            for field in entity['fields']:
                for rid in field['requirement_ids']:
                    if field['origin'] == 'REQUIREMENT':
                        state._link_field(rid, entity['key'], field['name'])
        state.contributions = copy.deepcopy(er.get('contributions', state.contributions))
        state.conflicts = copy.deepcopy(er.get('conflicts', []))
        errors = []
        for relation in er['relationships']:
            for rid in relation['requirement_ids']:
                state.apply_relationships(rid, {'relationships': [relation]}, errors)
        for row in er.get('unique_constraints', []):
            state.add_unique(row['entity'], row['fields'], row['requirement_ids'], row['description'])
        for rid, access in er.get('access', {}).items():
            state.requirement_access[rid] = {key: set(access.get(key, [])) for key in ('read', 'write')}
        if errors:
            raise ValueError('; '.join(errors))
        binding = json.loads(Path(args.binding).expanduser().read_text()) if args.binding else None
        model = None
        if binding is None and requirements.get('bundle', {}).get('identity_proposal') is None:
            from cli.commands import _ensure_dotenv_loaded
            from model.client import Model
            _ensure_dotenv_loaded()
            model = Model.from_env()
        compile_identity(model, root, requirements, state, reviewed_proposal=binding, domain_status=er['status'])
        updated_er = state.to_er_model()
        updated_er['status'] = er['status']
        refined = refine_er_model(updated_er, requirement_ids=set(requirements['nodes']))
        if refined.errors:
            raise ValueError('; '.join(refined.errors))
        write_json_atomic(root / 'design/database/er_model.json', updated_er)
        write_json_atomic(root / 'design/database/schema.json', refined.schema)
        print('Identity design resolved; ER and SchemaIR updated. Rerun lowering and downstream design before using the generated application.')
        print(root / 'design/identity/identity_ir.json')
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Identity design failed: {exc}', file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
