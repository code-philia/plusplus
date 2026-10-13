"""Implement requirements directly in source, without design or lowering stages."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from cli.commands import _ensure_dotenv_loaded, _locate_requirement_file, _resolve_web_port
from compiler import CompilationRequest, Compiler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Direct requirement-to-source compilation; no design IR or lowering')
    parser.add_argument('requirement_path')
    parser.add_argument('-o', '--output-dir', required=True)
    parser.add_argument('--port', type=int)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--prepare-validation', action='store_true')
    args = parser.parse_args(argv)
    try:
        _ensure_dotenv_loaded()
        root = Path(args.output_dir).expanduser().resolve()
        port = _resolve_web_port(args.port)
        if args.resume and args.port is None:
            import json
            port = int(json.loads((root / '.arc/implementation/direct_workspace.json').read_text())['web_port'])
        result = asyncio.run(Compiler().compile(CompilationRequest(
            requirement_path=Path(_locate_requirement_file(str(Path(args.requirement_path).expanduser()))).resolve(),
            output_dir=root, web_port=port, resume=args.resume, prepare_validation=args.prepare_validation,
        )))
        print(f'Direct implementation: {"PASSED" if result.ok else "FAILED"}')
        for rid in result.failed_nodes:
            print(f'{rid}: {result.states.get(rid, "FAILED")}', file=sys.stderr)
        return 0 if result.ok else 1
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f'Direct implementation error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
