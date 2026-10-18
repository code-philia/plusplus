"""Design framework-neutral frontend contracts for leaf requirements."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from cli.commands import _ensure_dotenv_loaded, _locate_requirement_file
from compiler.frontend_design import FrontendDesignPass
from compiler.preprocessing.stage import RequirementPreprocessor
from model.client import Model, ModelConfigurationError
from utils.jsonio import write_json_atomic


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Design frontend UI, state and interactions for leaf requirements')
    parser.add_argument('requirement_path', nargs='?', help='Requirements directory or YAML file')
    parser.add_argument('-o', '--output-dir', required=True)
    parser.add_argument('--reference-root', help='Image path base directory; defaults to requirement YAML directory')
    args = parser.parse_args(argv)
    try:
        _ensure_dotenv_loaded()
        arc = Path(args.output_dir).expanduser().resolve() / '.arc'
        req_path = arc / 'preprocessing/requirement_ir.json'
        dep_path = arc / 'preprocessing/dependency_graph.json'
        source = (Path(_locate_requirement_file(str(Path(args.requirement_path).expanduser()))).resolve()
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
        reference_root = (Path(args.reference_root).expanduser().resolve() if args.reference_root
                          else source.parent if source else None)
        if reference_root is None and any(page.get('reference') for node in requirements['nodes'].values()
                                          for page in node.get('pages', [])):
            raise ValueError('Provide requirement_path or --reference-root to resolve visual reference images')
        database = json.loads((arc / 'design/database/er_model.json').read_text(encoding='utf-8-sig'))
        backend = json.loads((arc / 'design/backend/backend_ir.json').read_text(encoding='utf-8-sig'))
        result = FrontendDesignPass(Model.from_env(), arc, reference_root=reference_root).compile(
            requirements, database, backend)
        for warning in result.warnings:
            print('WARNING:', warning)
        for error in result.errors:
            print('ERROR:', error, file=sys.stderr)
        print(f'Frontend IR: {arc / "design/frontend/frontend_ir.json"}')
        print(f'Report: {arc / "design/frontend/report.json"}')
        return 0 if result.ok else 1
    except (ModelConfigurationError, OSError, ValueError, KeyError, TypeError) as exc:
        print('Frontend design error:', exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
