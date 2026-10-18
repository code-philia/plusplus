"""Stage-local lowering into conventional Express and React source directories."""
import argparse
import json
from pathlib import Path
from utils.encoding import configure_utf8_console


def main(stage, argv=None):
    configure_utf8_console()
    parser = argparse.ArgumentParser(description=f'Generate minimal {stage} TypeScript skeletons')
    parser.add_argument('-o', '--output-dir', required=True)
    if stage == 'frontend':
        parser.add_argument('--component-depth', type=int, default=2)
        parser.add_argument('--min-component-nodes', type=int, default=4)
    args = parser.parse_args(argv)
    try:
        output = Path(args.output_dir).expanduser().resolve()
        arc = output / '.arc'
        requirements = json.loads((arc / 'preprocessing/requirement_ir.json').read_text(encoding='utf-8-sig'))
        design = json.loads((arc / f'design/{stage}/{stage}_ir.json').read_text(encoding='utf-8-sig'))
        if not (output / stage / 'package.json').is_file():
            raise ValueError(f'Initialize {stage}/package.json before lowering')
        if stage == 'backend':
            from compiler.backend_lowering import BackendLoweringPass
            lowerer = BackendLoweringPass()
        else:
            from compiler.frontend_lowering import FrontendLoweringPass
            lowerer = FrontendLoweringPass(component_depth=args.component_depth, min_nodes=args.min_component_nodes)
        from .recovery import isolate_design, minimal_lowering, bind_recovery, leaf_ids
        from utils.jsonio import read_json, write_json_atomic
        reasons = {}
        if stage == 'backend':
            reasons.update(read_json(arc / 'lowering/database/plan.json', {}).get('recovery_requirements', {}))
        try:
            plan = lowerer.compile(requirements, design, output)
        except Exception as exc:
            print(f'WARNING: isolating {stage} design conflicts: {exc}')
            try:
                plan, design = isolate_design(stage, lowerer, requirements, design, output)
                reasons.update(plan.pop('recovery_reasons', {}))
            except Exception as isolation_error:
                reasons.update({rid: str(isolation_error)[:1000] for rid in leaf_ids(requirements)})
                plan, design = minimal_lowering(stage, lowerer, requirements, output)
        from .compilation import check_compilation
        check_result = check_compilation(output, stage)
        if check_result['status'] != 'COMPILED':
            reasons.update({rid: str(check_result)[:1000] for rid in leaf_ids(requirements)})
            plan, design = minimal_lowering(stage, lowerer, requirements, output)
        states = read_json(arc / 'design' / stage / 'report.json', {}).get('node_states', {})
        for rid in leaf_ids(requirements):
            if states.get(rid) in {'FAILED', 'SKIPPED'}:
                if stage == 'backend' or requirements['nodes'][rid].get('pages'):
                    reasons[rid] = f'{stage} design incomplete; implement from the requirement.'
        plan = bind_recovery(output, stage, plan, reasons)
        write_json_atomic(arc / f'design/{stage}/{stage}_ir.json', design)
        print(f'{stage} lowering: {len(plan["files"])} source files')
        print(f'Plan: {arc / "lowering" / stage / "plan.json"}')
        print(f'Generated entrypoint: {plan["entrypoint"]}')
        result = check_compilation(output, stage) if reasons else check_result
        print(f'Compilation: {result["status"]}')
        if result.get('output') or result.get('message'):
            print(result.get('output') or result['message'])
        return 0 if result['status'] == 'COMPILED' else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'{stage} lowering error: {exc}')
        return 1
