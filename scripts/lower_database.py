"""Initialize a project if needed and lower existing database design artifacts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cli.commands import _ensure_dotenv_loaded, _resolve_web_port
from compiler.database_lowering import lower_database
from compiler.project_initialization import ProjectInitializer
from compiler.runtime.artifacts import CompilerArtifactStore


def _design_root(path: Path) -> Path:
    """Accept a design workspace, database artifact directory, or schema file."""
    path = path.expanduser().resolve()
    if path.is_file():
        if path.name != "schema.json":
            raise ValueError("Database design input file must be schema.json")
        return path.parent
    if (path / "schema.json").is_file():
        return path
    return path / ".arc" / "design" / "database"


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Lower existing schema.json into database connection helpers and initialization SQL. "
            "Initialize the output project if needed. No model calls or SQL execution."
        )
    )
    parser.add_argument(
        "design_path", nargs="?",
        help="Design workspace, directory containing schema.json, or schema.json; defaults to output workspace",
    )
    parser.add_argument("-o", "--output-dir", required=True, help="Output project workspace")
    parser.add_argument("--port", type=int, help="Web port used when initializing the project")
    args = parser.parse_args(argv)

    try:
        _ensure_dotenv_loaded()
        output = Path(args.output_dir).expanduser().resolve()
        store = CompilerArtifactStore(output)
        source = _design_root(Path(args.design_path) if args.design_path else output)
        # Validate the input before initialization; design artifacts remain read-only.
        _read_object(source / "schema.json")
        print(f"Database lowering workspace: {output}", flush=True)

        manifest_path = store.root / "project" / "project-manifest.json"
        if not manifest_path.exists():
            print("Initializing project structure and dependencies...", flush=True)
            initialized = ProjectInitializer(output, web_port=_resolve_web_port(args.port)).initialize()
            if not initialized.ok:
                for error in initialized.errors:
                    print(f"ERROR: {error}", file=sys.stderr)
                return 1
        manifest, error = store.read_project_manifest()
        if error or manifest is None:
            raise ValueError(error or "Missing initialized project manifest")

        result = lower_database(output, store, manifest, schema_path=source / "schema.json")
        for warning in result.warnings:
            print(f"WARNING: {warning}")
        for error in result.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        if not result.ok:
            return 1
        print("Database lowering completed.", flush=True)
        for relative in sorted(result.sources):
            print(f"Generated: {output / relative}")
        print(f"Report: {result.artifacts['database_lowering_report']}")
        print(f"Manifest: {result.artifacts['database_lowering_manifest']}")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Database lowering error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
