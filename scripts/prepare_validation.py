"""Configure validation clock/timezone and verify application build/startup."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from cli.commands import _ensure_dotenv_loaded
from compiler.validation_preparation import ValidationPreparationPass, check_validation_readiness
from utils.jsonio import read_json, write_json_atomic


def main(argv=None):
    parser = argparse.ArgumentParser(description='Prepare the implemented application for external validation')
    parser.add_argument('-o', '--output-dir', required=True)
    args = parser.parse_args(argv)
    try:
        _ensure_dotenv_loaded()
        root = Path(args.output_dir).expanduser().resolve()
        requirements = read_json(root / '.arc/preprocessing/requirement_ir.json')
        artifacts = ValidationPreparationPass(None, root).generate(requirements)
        check_validation_readiness(root, validation=True)
        report = root / '.arc/validation_preparation/report.json'
        write_json_atomic(report, {'status': 'PREPARED', 'artifacts': artifacts})
        print('Validation prepared:', report)
        print('Start application: npm start (in ' + str(root) + ')')
        return 0
    except Exception as exc:
        print('Validation preparation failed:', exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
