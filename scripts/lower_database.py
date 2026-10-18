"""Generate SQLite connection, schema initialization and shared startup seed."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from compiler.database_lowering import DatabaseLoweringPass
from compiler.lowering.compilation import check_compilation
from compiler.lowering.recovery import isolate_design, minimal_lowering, leaf_ids, bind_recovery
from utils.jsonio import read_json, write_json_atomic


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output-dir', required=True)
    args = parser.parse_args(argv)
    try:
        output = Path(args.output_dir).expanduser().resolve()
        schema = json.loads((output / '.arc/design/database/schema.json').read_text(encoding='utf-8-sig'))
        requirements = read_json(output / '.arc/preprocessing/requirement_ir.json')
        lowerer = DatabaseLoweringPass()
        reasons = {}
        try:
            plan = lowerer.compile(schema, output)
        except Exception as exc:
            print(f'WARNING: isolating database design conflicts: {exc}')
            try:
                plan, schema = isolate_design('database', lowerer, requirements, schema, output)
                reasons.update(plan.pop('recovery_reasons', {}))
            except Exception as isolation_error:
                reasons.update({rid: str(isolation_error)[:1000] for rid in leaf_ids(requirements)})
                plan, schema = minimal_lowering('database', lowerer, requirements, output)
        print(f'Database lowering: {len(plan["files"])} files; shared seed: backend/src/db/seed.ts')
        result = check_compilation(output, 'database')
        if result['status'] != 'COMPILED':
            reasons.update({rid: str(result)[:1000] for rid in leaf_ids(requirements)})
            plan, schema = minimal_lowering('database', lowerer, requirements, output)
            result = check_compilation(output, 'database')
        write_json_atomic(output / '.arc/design/database/schema.json', schema)
        if reasons:
            # Persist bindings now when backend lowering has already run. Otherwise
            # backend lowering picks these requirements up from the database plan.
            plan['recovery_requirements'] = reasons
            write_json_atomic(output / '.arc/lowering/database/plan.json', plan)
            backend_plan = read_json(output / '.arc/lowering/backend/plan.json', {})
            if backend_plan:
                bind_recovery(output, 'backend', backend_plan, reasons)
        print(result)
        return 0 if result['status'] == 'COMPILED' else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('Database lowering error:', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
