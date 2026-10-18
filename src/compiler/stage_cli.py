"""Run a single workflow placeholder without replacing other stages' artifacts."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .preprocessing import RequirementPreprocessor
from .workflow import WorkflowContext, plan_stage
from utils.jsonio import write_json_atomic


def main(stage: str, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f'{stage} workflow placeholder; no business implementation')
    parser.add_argument('requirement_path', nargs='?')
    parser.add_argument('-o', '--output-dir', required=True)
    args = parser.parse_args(argv)
    try:
        output = Path(args.output_dir).expanduser().resolve()
        root = output / '.arc/preprocessing'
        if args.requirement_path:
            from cli.commands import _locate_requirement_file
            parsed = RequirementPreprocessor().compile(Path(_locate_requirement_file(args.requirement_path)))
            if not parsed.ok:
                raise ValueError('; '.join(parsed.errors))
            requirements, dependencies = parsed.requirement_ir, parsed.dependency_graph
            if (root / 'requirement_ir.json').is_file():
                existing = json.loads((root / 'requirement_ir.json').read_text(encoding='utf-8-sig'))
                if existing.get('source') != requirements.get('source'):
                    raise ValueError('Requirements differ from the existing workspace; use a new output directory.')
            else:
                write_json_atomic(root / 'requirement_ir.json', requirements)
                write_json_atomic(root / 'dependency_graph.json', dependencies)
        else:
            requirements = json.loads((root / 'requirement_ir.json').read_text(encoding='utf-8-sig'))
            dependencies = json.loads((root / 'dependency_graph.json').read_text(encoding='utf-8-sig'))
        report, path = plan_stage(WorkflowContext(output, requirements, dependencies), stage)
        print(f'{stage}: {report["status"]}; workflow record: {path}')
        return 0
    except (OSError, ValueError) as exc:
        print(f'{stage}: {exc}')
        return 1
