"""CLI for the requirement compiler and model configuration."""
from __future__ import annotations
import argparse
import asyncio
import sys
import time
from pathlib import Path
from compiler import CompilationRequest, Compiler
from utils.encoding import configure_utf8_console


def _ensure_dotenv_loaded() -> None:
    # Keep the script-facing import path while configuration lives outside the CLI.
    from utils.environment import ensure_dotenv_loaded
    ensure_dotenv_loaded()


def _locate_requirement_file(input_path: str) -> str:
    # Existing stage scripts still import this private compatibility entrypoint.
    from compiler.preprocessing.paths import locate_requirement_file
    return locate_requirement_file(input_path)


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
    configure_utf8_console()
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
            from cli.output import init_debug_logger, print_cli_banner, print_cli_startup, cli_log, print_compilation_summary
            request = CompilationRequest(requirement_path=Path(_locate_requirement_file(args.requirement_path)),
                output_dir=Path(args.output_dir).expanduser().resolve(), web_port=args.port,
                stop_after_lowering=args.stop_after_lowering, prepare_validation=args.prepare_validation)
            init_debug_logger(str(request.output_dir))
            print_cli_banner()
            print_cli_startup(str(request.output_dir), str(request.requirement_path), False, request.web_port)
            started = time.monotonic()
            result = asyncio.run(Compiler(log_cb=cli_log).compile(request))
            print_compilation_summary(result.to_dict(), str(request.output_dir), time.monotonic() - started)
            code = 0 if result.completed else 1
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        code = 1
    raise SystemExit(code)
