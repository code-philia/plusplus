"""Stage-local lowering into conventional Express and React source directories."""
import argparse
import json
from pathlib import Path


def main(stage, argv=None):
    parser = argparse.ArgumentParser(description=f'Generate minimal {stage} TypeScript skeletons')
    parser.add_argument('-o', '--output-dir', required=True)
    if stage == 'frontend':
        parser.add_argument('--component-depth', type=int, default=2)
        parser.add_argument('--min-component-nodes', type=int, default=4)
    args = parser.parse_args(argv)
    try:
        output = Path(args.output_dir).expanduser().resolve()
        arc = output / '.arc'
        requirements = json.loads((arc / 'preprocessing/requirement_ir.json').read_text(encoding='utf-8'))
        design = json.loads((arc / f'design/{stage}/{stage}_ir.json').read_text(encoding='utf-8'))
        if not (output / stage / 'package.json').is_file():
            raise ValueError(f'Initialize {stage}/package.json before lowering')
        if stage == 'backend':
            from compiler.backend_lowering import BackendLoweringPass
            lowerer = BackendLoweringPass()
        else:
            from compiler.frontend_lowering import FrontendLoweringPass
            lowerer = FrontendLoweringPass(component_depth=args.component_depth, min_nodes=args.min_component_nodes)
        plan = lowerer.compile(requirements, design, output)
        print(f'{stage} lowering: {len(plan["files"])} source files')
        print(f'Plan: {arc / "lowering" / stage / "plan.json"}')
        print(f'Generated entrypoint: {plan["entrypoint"]}')
        from .compilation import check_compilation
        result = check_compilation(output, stage)
        print(f'Compilation: {result["status"]}')
        if result.get('output') or result.get('message'):
            print(result.get('output') or result['message'])
        return 0 if result['status'] == 'COMPILED' else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'{stage} lowering error: {exc}')
        return 1
