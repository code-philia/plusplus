"""CLI for the requirement compiler and model configuration."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from compiler import CompilationRequest, Compiler


def _ensure_dotenv_loaded() -> None:
    from dotenv import load_dotenv
    path = Path(os.environ.get('ARC_ENV_FILE') or Path(__file__).resolve().parents[2] / '.env')
    if path.is_file():
        load_dotenv(path, override=False)


def _locate_requirement_file(input_path: str) -> str:
    path = Path(input_path).expanduser().resolve()
    if path.is_dir():
        path = next((path / name for name in ('requirements.yaml', 'requirements.yml')
                     if (path / name).is_file()), path)
    if not path.is_file() or path.suffix not in ('.yaml', '.yml'):
        raise ValueError(f'Requirements YAML not found: {path}')
    return str(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='ARC requirement compiler')
    parser.add_argument('--version', action='version', version='ARC 1.2.0')
    commands = parser.add_subparsers(dest='command', required=True)
    compile_parser = commands.add_parser('compile', help='Design, lower and implement an application')
    compile_parser.add_argument('requirement_path')
    compile_parser.add_argument('-o', '--output-dir', required=True)
    compile_parser.add_argument('--port', type=int, default=3000)
    compile_parser.add_argument('--stop-after-lowering', action='store_true')
    compile_parser.add_argument('--prepare-validation', action='store_true')
    commands.add_parser('config', help='Configure the model client')
    commands.add_parser('doctor', help='Inspect model configuration')
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == 'config':
            from cli.config import interactive_config_setup
            code = interactive_config_setup()
        elif args.command == 'doctor':
            _ensure_dotenv_loaded()
            from cli.config import print_health_check
            code = print_health_check()
        else:
            request = CompilationRequest(requirement_path=Path(_locate_requirement_file(args.requirement_path)),
                output_dir=Path(args.output_dir).expanduser(), web_port=args.port,
                stop_after_lowering=args.stop_after_lowering, prepare_validation=args.prepare_validation)
            result = asyncio.run(Compiler().compile(request))
            print(json.dumps({'status': ('LOWERED' if args.stop_after_lowering else 'COMPLETED') if result.ok else 'FAILED',
                              'implementation_status': ('NOT_RUN' if args.stop_after_lowering else 'IMPLEMENTED') if result.ok else 'INCOMPLETE',
                              **result.to_dict()}, ensure_ascii=False, indent=2))
            code = 0 if result.ok else 1
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        code = 1
    raise SystemExit(code)
