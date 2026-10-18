"""Run leaf-only TDD after database/backend/frontend design and lowering."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from cli.commands import _ensure_dotenv_loaded
from compiler.implementation import NodeTDDOrchestrator
from model.client import Model, ModelConfigurationError


def main(argv=None):
    parser = argparse.ArgumentParser(description='Implement leaf requirements using symbol-scoped TDD')
    parser.add_argument('-o', '--output-dir', required=True)
    parser.add_argument('--port', type=int, default=33100)
    args = parser.parse_args(argv)
    try:
        _ensure_dotenv_loaded()
        root = Path(args.output_dir).expanduser().resolve()
        arc = root / '.arc'
        def load(relative):
            return json.loads((arc / relative).read_text(encoding='utf-8-sig'))
        result = NodeTDDOrchestrator(Model.from_env(), root, port=args.port).compile(
            load('preprocessing/requirement_ir.json'), load('design/database/er_model.json'),
            load('design/backend/backend_ir.json'), load('design/frontend/frontend_ir.json'),
            load('lowering/backend/plan.json'), load('lowering/frontend/plan.json'), load('lowering/database/plan.json'))
        for error in result.errors:
            print('ERROR:', error)
        for warning in result.warnings:
            print('WARNING:', warning)
        print('TDD report:', arc / 'implementation/report.json')
        return 0 if result.ok else 1
    except (ModelConfigurationError, ValueError, OSError, KeyError, TypeError) as exc:
        print('TDD error:', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
