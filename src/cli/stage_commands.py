"""Argument and result adapters for standalone compiler stages."""
import argparse
import asyncio
import sys
from pathlib import Path
from agent.tools import IndeterminateToolCall

from compiler.budgets import ExecutionBudgets
from compiler.implementation.workflow import ImplementationInput, ImplementationStage, PreparedImplementation
from compiler.lowering.standalone import StandaloneLoweringInput, StandaloneLoweringStage
from compiler.preprocessing.queries import unit_ids
from compiler.stages import StageStatus
from compiler.validation_preparation.workflow import ValidationPreparationInput, ValidationPreparationStage
from model.client import Model, ModelConfigurationError
from utils.encoding import configure_utf8_console
from utils.environment import ensure_dotenv_loaded
from utils.jsonio import read_json_object


def print_diagnostics(result):
    for error in result.errors:
        print('ERROR:', error, file=sys.stderr)
    for warning in result.warnings:
        print('WARNING:', warning)


def lower_main(layer, argv=None):
    configure_utf8_console()
    parser = argparse.ArgumentParser(description=f'Generate minimal {layer} TypeScript skeletons')
    parser.add_argument('-o', '--output-dir', required=True)
    if layer == 'frontend':
        parser.add_argument('--component-depth', type=int, default=2)
        parser.add_argument('--min-component-nodes', type=int, default=4)
    args = parser.parse_args(argv)
    try:
        ensure_dotenv_loaded()
        output = Path(args.output_dir).expanduser().resolve()
        inputs = StandaloneLoweringInput(layer, output, getattr(args, 'component_depth', 2),
                                         getattr(args, 'min_component_nodes', 4))
        result = asyncio.run(StandaloneLoweringStage(inputs, ExecutionBudgets.from_environment()).run())
        print_diagnostics(result)
        lowered = result.require_value()
        print(f'{layer} lowering: {len(lowered.plan["files"])} source files')
        print(f'Plan: {output / ".arc/lowering" / layer / "plan.json"}')
        print(lowered.evidence)
        return 0 if lowered.evidence.get('ok') else 1
    except (IndeterminateToolCall, OSError, ValueError, KeyError, TypeError) as error:
        print(f'{layer.title()} lowering error: {error}', file=sys.stderr)
        return 1


def implement_main(argv=None):
    configure_utf8_console()
    parser = argparse.ArgumentParser(description='Implement leaf requirements using symbol-scoped TDD')
    parser.add_argument('-o', '--output-dir', required=True)
    parser.add_argument('--port', type=int, default=33100)
    args = parser.parse_args(argv)
    try:
        ensure_dotenv_loaded()
        if not 1 <= args.port <= 65535:
            raise ValueError('Port must be between 1 and 65535')
        output = Path(args.output_dir).expanduser().resolve()
        arc = output / '.arc'
        requirements = read_json_object(arc / 'preprocessing/requirement_ir.json')
        prepared = PreparedImplementation(requirements=requirements,
            er_model=read_json_object(arc / 'design/database/er_model.json'),
            backend_ir=read_json_object(arc / 'design/backend/backend_ir.json'),
            frontend_ir=read_json_object(arc / 'design/frontend/frontend_ir.json'),
            backend_plan=read_json_object(arc / 'lowering/backend/plan.json'),
            frontend_plan=read_json_object(arc / 'lowering/frontend/plan.json'),
            database_plan=read_json_object(arc / 'lowering/database/plan.json'),
            node_states={rid: 'LOWERED' for rid in unit_ids(requirements)})
        result = asyncio.run(ImplementationStage(ImplementationInput(prepared, output, args.port),
            Model.from_env(), ExecutionBudgets.from_environment()).run())
        print_diagnostics(result)
        print('TDD report:', arc / 'implementation/report.json')
        return 0 if result.status == StageStatus.SUCCEEDED else 1
    except (IndeterminateToolCall, ModelConfigurationError, OSError, ValueError, KeyError, TypeError) as error:
        print('TDD error:', error, file=sys.stderr)
        return 1


def validation_main(argv=None):
    configure_utf8_console()
    parser = argparse.ArgumentParser(description='Prepare the implemented application for external validation')
    parser.add_argument('-o', '--output-dir', required=True)
    parser.add_argument('--port', type=int, default=3000)
    args = parser.parse_args(argv)
    try:
        ensure_dotenv_loaded()
        if not 1 <= args.port <= 65535:
            raise ValueError('Port must be between 1 and 65535')
        output = Path(args.output_dir).expanduser().resolve()
        requirements = read_json_object(output / '.arc/preprocessing/requirement_ir.json')
        result = asyncio.run(ValidationPreparationStage(ValidationPreparationInput(requirements,
            output, args.port), None).run())
        print_diagnostics(result)
        if result.status != StageStatus.SUCCEEDED:
            return 1
        print('Validation prepared:', result.require_value().report_path)
        print('Start application: npm start (in ' + str(output) + ')')
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('Validation preparation failed:', error, file=sys.stderr)
        return 1
