"""Run backend design against an existing database-design output workspace.

Usage: python scripts/design_backend.py requirements.yaml -o output-directory
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


# Allow direct execution without installing the compiler package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cli.commands import build_compile_parser, _ensure_dotenv_loaded, _locate_requirement_file
from compiler.runtime.artifacts import CompilerArtifactStore
from compiler.backend_design import BackendDesignPass
from model.client import Model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers()
    build_compile_parser(subparsers)
    command = subparsers.choices["compile"]
    command.prog = parser.prog
    command.description = (
        "Generate only backend design using existing preprocessing and database "
        "artifacts. Arguments match the original compile command; --port is "
        "accepted but unused. Existing backend design results are overwritten."
    )
    args = command.parse_args(argv)
    if args.clean or args.resume or args.start_from != "zero" or args.checkpoint_ref or args.restart_output:
        command.error(
            "Backend-only design reads the existing output directory directly; "
            "--clean, --resume and checkpoint/restart options are not supported."
        )

    try:
        _ensure_dotenv_loaded()
        requirement = Path(_locate_requirement_file(args.requirement_path))
        output = Path(args.output_dir).resolve()
        store = CompilerArtifactStore(output)

        def read(relative: str) -> dict:
            path = store.root / relative
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError(f"Expected a JSON object: {path}")
            return value

        requirements = read("preprocessing/requirement_ir.json")
        dependencies = read("preprocessing/dependency_graph.json")
        schema = read("design/database/schema.json")
        digest = hashlib.sha256(requirement.read_bytes()).hexdigest()
        if requirements.get("source", {}).get("sha256") != digest:
            raise ValueError(
                "Requirement file differs from the existing preprocessing artifacts; "
                "use the original requirement file or regenerate database design."
            )
        if schema.get("status") != "RESOLVED" or not isinstance(schema.get("tables"), list):
            raise ValueError("Existing schema.json must be a RESOLVED database-neutral schema.")

        model = Model.from_env()
        model.set_usage_path(store.root / "backend_design_usage.jsonl")
        print(f"Backend design workspace: {output}", flush=True)
        result = BackendDesignPass(model, store.root).compile(requirements, dependencies, schema)
        store.write_design(design_ir=result.design_ir)
        print(f"Backend design completed: {result.ok}")
        print(f"IR: {store.backend_design_root / 'backend_ir.json'}")
        print(f"Report: {store.backend_design_root / 'report.json'}")
        for warning in result.warnings:
            print(f"WARNING: {warning}")
        for error in result.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 0 if result.ok else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Backend design error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
