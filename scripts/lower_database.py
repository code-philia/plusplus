"""Generate SQLite connection, schema initialization and shared startup seed."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from compiler.database_lowering import DatabaseLoweringPass
from compiler.lowering.compilation import check_compilation


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output-dir', required=True)
    args = parser.parse_args(argv)
    try:
        output = Path(args.output_dir).expanduser().resolve()
        schema = json.loads((output / '.arc/design/database/schema.json').read_text(encoding='utf-8'))
        plan = DatabaseLoweringPass().compile(schema, output)
        print(f'Database lowering: {len(plan["files"])} files; shared seed: backend/src/db/seed.ts')
        result = check_compilation(output, 'database')
        print(result)
        return 0 if result['status'] == 'COMPILED' else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('Database lowering error:', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
