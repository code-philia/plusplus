"""Generate model-written seed code and install startup hooks in a completed application."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cli.commands import _ensure_dotenv_loaded
from compiler.validation_preparation import ValidationPreparationPass
from model.client import Model, ModelConfigurationError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output-dir", required=True, help="Completed generated application workspace")
    args = parser.parse_args()
    try:
        _ensure_dotenv_loaded()
        root = Path(args.output_dir).expanduser().resolve()
        requirements = json.loads((root / ".arc/preprocessing/requirement_ir.json").read_text(encoding="utf-8"))
        model = Model.from_env()
        model.set_usage_path(root / ".arc/model_usage.jsonl")
        artifacts = ValidationPreparationPass(model, root).generate(requirements)
        for name, path in artifacts.items():
            print(f"{name}: {path}")
        print("Application startup will initialize the validation database and seed it once before serving requests.")
        return 0
    except (OSError, ValueError, KeyError, TypeError, ModelConfigurationError) as exc:
        print(f"Validation preparation error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
