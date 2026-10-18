"""Design leaf backend API/FUNC/DB modules from an existing database IR."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from cli.commands import _ensure_dotenv_loaded, _locate_requirement_file
from compiler.backend_design import BackendDesignPass
from compiler.preprocessing.stage import RequirementPreprocessor
from model.client import Model, ModelConfigurationError
from utils.jsonio import write_json_atomic


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Design backend modules for leaf requirements only')
    parser.add_argument('requirement_path', nargs='?', help='Requirements directory or YAML file; optional with existing preprocessing')
    parser.add_argument('-o', '--output-dir', required=True)
    args = parser.parse_args(argv)
    try:
        _ensure_dotenv_loaded()
        arc = Path(args.output_dir).expanduser().resolve() / '.arc'
        req_path = arc / 'preprocessing/requirement_ir.json'
        dep_path = arc / 'preprocessing/dependency_graph.json'
        source = (Path(_locate_requirement_file(str(Path(args.requirement_path).expanduser())))
                  if args.requirement_path else None)
        if req_path.exists() or dep_path.exists():
            if not req_path.is_file() or not dep_path.is_file():
                raise ValueError('Existing preprocessing artifacts are incomplete; they will not be overwritten')
            requirements = json.loads(req_path.read_text(encoding='utf-8-sig'))
            if source and requirements.get('source', {}).get('sha256') != hashlib.sha256(source.read_bytes()).hexdigest():
                raise ValueError('Requirements differ from existing preprocessing; use a new workspace')
        else:
            if source is None:
                raise ValueError('Provide requirement_path when preprocessing artifacts do not exist')
            parsed = RequirementPreprocessor().compile(source)
            if not parsed.ok:
                raise ValueError('; '.join(parsed.errors))
            requirements = parsed.requirement_ir
            write_json_atomic(req_path, requirements)
            write_json_atomic(dep_path, parsed.dependency_graph)
        database = json.loads((arc / 'design/database/er_model.json').read_text(encoding='utf-8-sig'))
        result = BackendDesignPass(Model.from_env(), arc).compile(requirements, database)
        for warning in result.warnings:
            print('WARNING:', warning)
        for error in result.errors:
            print('ERROR:', error, file=sys.stderr)
        print(f'Backend IR: {arc / "design/backend/backend_ir.json"}')
        print(f'Report: {arc / "design/backend/report.json"}')
        return 0 if result.ok else 1
    except (ModelConfigurationError, OSError, ValueError, KeyError, TypeError) as exc:
        print('Backend design error:', exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
