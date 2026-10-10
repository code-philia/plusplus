"""Run requirement preprocessing and database design without project initialization.

Usage: python scripts/design_database.py requirements.yaml -o output-directory
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


# Allow direct execution without installing the compiler package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cli.commands import _ensure_dotenv_loaded, _locate_requirement_file
from utils.jsonio import write_json_atomic
from compiler.runtime.artifacts import CompilerArtifactStore
from compiler.database_design import DatabaseSchemaPass
from model.client import Model, ModelConfigurationError
from compiler.preprocessing.stage import RequirementPreprocessor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the initial ER model and database-neutral Schema IR from requirements. "
            "Runs preprocessing and database design only; no project initialization, "
            "lowering, SQL execution or implementation. Existing preprocessing and "
            "database design artifacts in the output directory are overwritten."
        )
    )
    parser.add_argument("requirement_path", help="Requirements directory or .yaml/.yml file")
    parser.add_argument("-o", "--output-dir", required=True, help="Output workspace directory")
    args = parser.parse_args(argv)

    try:
        _ensure_dotenv_loaded()
        requirement = Path(_locate_requirement_file(str(Path(args.requirement_path).expanduser())))
        output = Path(args.output_dir).expanduser().resolve()
        store = CompilerArtifactStore(output)
        print(f"Database design workspace: {output}", flush=True)
        preprocessing = RequirementPreprocessor().compile(requirement)
        if not preprocessing.ok:
            for error in preprocessing.errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1

        model = Model.from_env()
        store.write_preprocessing(
            requirement_ir=preprocessing.requirement_ir,
            dependency_graph=preprocessing.dependency_graph,
        )
        result = DatabaseSchemaPass(model, store.root).compile(
            preprocessing.requirement_ir, preprocessing.dependency_graph,
        )
        schema_path = store.write_database_schema(result.schema)
        report_path = store.database_root / "report.json"
        write_json_atomic(report_path, {
            "status": result.schema["status"],
            "node_states": result.node_states,
            "errors": result.errors,
            "warnings": result.warnings,
        })
        print(f"Database design completed: {result.schema['status']}", flush=True)
        print(f"ER: {store.database_root / 'er_model.json'}")
        print(f"Schema IR: {schema_path}")
        print(f"Report: {report_path}")
        for warning in result.warnings:
            print(f"WARNING: {warning}")
        for error in result.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 0 if result.ok and result.schema["status"] == "RESOLVED" else 1
    except (ModelConfigurationError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Database design error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
