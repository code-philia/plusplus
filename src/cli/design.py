"""Standalone design commands using the same stages as the compiler pipeline."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

from compiler import CompilationRequest
from compiler.backend_design.workflow import BackendDesignInput, BackendDesignStage
from compiler.database_design.workflow import DatabaseDesignInput, DatabaseDesignStage
from compiler.foundation import PreprocessingStage
from compiler.frontend_design.workflow import FrontendDesignInput, FrontendDesignStage
from compiler.stages import StageStatus
from compiler.visual_analysis import VisualAnalysisInput, VisualAnalysisOutput, VisualAnalysisStage
from compiler.preprocessing.paths import locate_requirement_file
from model.client import Model, ModelConfigurationError
from utils.encoding import configure_utf8_console
from utils.environment import ensure_dotenv_loaded
from utils.jsonio import read_json_object


async def preprocessing(output, source):
    root = output / '.arc/preprocessing'
    paths = root / 'requirement_ir.json', root / 'dependency_graph.json'
    if any(path.exists() for path in paths):
        if not all(path.is_file() for path in paths):
            raise ValueError('Existing preprocessing artifacts are incomplete; they will not be overwritten')
        requirements, dependencies = (read_json_object(path) for path in paths)
        if source and requirements.get('source', {}).get('sha256') != hashlib.sha256(source.read_bytes()).hexdigest():
            raise ValueError('Requirements differ from existing preprocessing; use a new workspace')
        return requirements, dependencies
    if source is None:
        raise ValueError('Provide requirement_path when preprocessing artifacts do not exist')
    result = await PreprocessingStage(CompilationRequest(requirement_path=source, output_dir=output)).run()
    value = result.require_value()
    return value.requirement_ir, value.dependency_graph


async def run_design(stage, args):
    output = Path(args.output_dir).expanduser().resolve()
    arc = output / '.arc'
    source = (Path(locate_requirement_file(str(Path(args.requirement_path).expanduser()))).resolve()
              if args.requirement_path else None)
    requirements, dependencies = await preprocessing(output, source)
    reference_root = None
    if stage == 'frontend':
        reference_root = (Path(args.reference_root).expanduser().resolve() if args.reference_root
                          else source.parent if source else None)
        if reference_root is None and any(page.get('reference') for node in requirements['nodes'].values()
                                          for page in node.get('pages', [])):
            raise ValueError('Provide requirement_path or --reference-root to resolve visual reference images')
    if stage == 'database':
        result = await DatabaseDesignStage(DatabaseDesignInput(requirements, dependencies, arc), Model.from_env()).run()
    else:
        database = read_json_object(arc / 'design/database/er_model.json')
        if stage == 'backend':
            result = await BackendDesignStage(BackendDesignInput(requirements, database,
                VisualAnalysisOutput({}, {}), arc), Model.from_env()).run()
        else:
            backend = read_json_object(arc / 'design/backend/backend_ir.json')
            model = Model.from_env()
            visual = (await VisualAnalysisStage(VisualAnalysisInput(requirements, arc,
                reference_root or output)).run()).require_value()
            result = await FrontendDesignStage(FrontendDesignInput(requirements, database,
                backend, visual, arc), model).run()
    for warning in result.warnings:
        print('WARNING:', warning)
    for error in result.errors:
        print('ERROR:', error, file=sys.stderr)
    filename = 'schema.json' if stage == 'database' else stage + '_ir.json'
    print(f'{stage.title()} IR: {arc / "design" / stage / filename}')
    print(f'Report: {arc / "design" / stage / "report.json"}')
    return 0 if result.status == StageStatus.SUCCEEDED else 1


def main(stage, argv=None):
    configure_utf8_console()
    parser = argparse.ArgumentParser(description=f'Design {stage} contracts for leaf requirements')
    parser.add_argument('requirement_path', **({} if stage == 'database' else {'nargs': '?'}),
                        help='Requirements directory, YAML file, or Markdown with one YAML block')
    parser.add_argument('-o', '--output-dir', required=True)
    if stage == 'frontend':
        parser.add_argument('--reference-root', help='Image path base directory')
    args = parser.parse_args(argv)
    try:
        ensure_dotenv_loaded()
        return asyncio.run(run_design(stage, args))
    except (ModelConfigurationError, OSError, ValueError, KeyError, TypeError) as error:
        print(f'{stage.title()} design error: {error}', file=sys.stderr)
        return 1
