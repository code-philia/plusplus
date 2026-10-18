"""Run requirement preprocessing and database design without project initialization.

Usage: python scripts/design_database.py requirements.yaml -o output-directory
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


# Allow direct execution without installing the compiler package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cli.commands import _ensure_dotenv_loaded, _locate_requirement_file
from utils.jsonio import write_json_atomic
from compiler.database_design import DatabaseSchemaPass
from model.client import Model, ModelConfigurationError
from compiler.preprocessing.stage import RequirementPreprocessor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the initial ER model and database-neutral Schema IR from requirements. "
            "Runs preprocessing and database design only; no project initialization, "
            "lowering, SQL execution or implementation. Reuses existing preprocessing; "
            "only database design artifacts are replaced."
        )
    )
    parser.add_argument("requirement_path", help="Requirements directory or .yaml/.yml file")
    parser.add_argument("-o", "--output-dir", required=True, help="Output workspace directory")
    args = parser.parse_args(argv)

    try:
        _ensure_dotenv_loaded()
        requirement = Path(_locate_requirement_file(str(Path(args.requirement_path).expanduser())))
        output = Path(args.output_dir).expanduser().resolve()
        artifact_root = output / ".arc"
        preprocessing_root = artifact_root / "preprocessing"
        database_root = artifact_root / "design/database"
        print(f"Database design workspace: {output}", flush=True)
        requirement_path = preprocessing_root / "requirement_ir.json"
        dependency_path = preprocessing_root / "dependency_graph.json"
        if requirement_path.exists() or dependency_path.exists():
            if not requirement_path.is_file() or not dependency_path.is_file():
                raise ValueError("Existing preprocessing artifacts are incomplete; they will not be overwritten")
            requirement_ir = json.loads(requirement_path.read_text(encoding="utf-8-sig"))
            dependency_graph = json.loads(dependency_path.read_text(encoding="utf-8-sig"))
            if not isinstance(requirement_ir, dict) or not isinstance(dependency_graph, dict):
                raise ValueError("Existing preprocessing artifacts must be JSON objects")
            if requirement_ir.get("source", {}).get("sha256") != hashlib.sha256(requirement.read_bytes()).hexdigest():
                raise ValueError("Requirement file differs from existing preprocessing; use a new workspace for changed requirements")
        else:
            preprocessing = RequirementPreprocessor().compile(requirement)
            if not preprocessing.ok:
                for error in preprocessing.errors:
                    print(f"ERROR: {error}", file=sys.stderr)
                return 1
            requirement_ir = preprocessing.requirement_ir
            dependency_graph = preprocessing.dependency_graph

        model = Model.from_env()
        if not requirement_path.exists():
            write_json_atomic(requirement_path, requirement_ir)
            write_json_atomic(dependency_path, dependency_graph)
        result = DatabaseSchemaPass(model, artifact_root).compile(
            requirement_ir, dependency_graph,
        )
        schema_path = database_root / "schema.json"
        write_json_atomic(schema_path, result.schema)
        write_json_atomic(database_root / "er_model.json", result.er_model)
        report_path = database_root / "report.json"
        write_json_atomic(report_path, {
            "status": result.schema["status"],
            "node_states": result.node_states,
            "errors": result.errors,
            "warnings": result.warnings,
        })
        print(f"Database design completed: {result.schema['status']}", flush=True)
        print(f"ER: {database_root / 'er_model.json'}")
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
