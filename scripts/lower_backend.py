"""Lower existing backend design into an already initialized project.

Usage: python scripts/lower_backend.py -o output-directory
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cli.commands import _ensure_dotenv_loaded, _resolve_web_port
from compiler.backend_lowering import lower_backend
from compiler.runtime.artifacts import CompilerArtifactStore


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate backend module files, exported types, function skeletons and server routes "
            "from existing design artifacts in an initialized project. "
            "Requires existing database lowering artifacts; replaces only backend lowering outputs. "
            "No model calls, business implementation or SQL execution."
        )
    )
    parser.add_argument("-o", "--output-dir", required=True, help="Existing initialized project workspace")
    parser.add_argument("--port", type=int, help="Server port; defaults to the initialized project port")
    args = parser.parse_args(argv)

    try:
        _ensure_dotenv_loaded()
        output = Path(args.output_dir).expanduser().resolve()
        store = CompilerArtifactStore(output)
        manifest, error = store.read_project_manifest()
        if error or manifest is None:
            raise ValueError(error or "Missing initialized project manifest")
        if not (output / "backend/package.json").is_file():
            raise ValueError("Initialized backend/package.json is missing")

        blueprint = _read_object(store.backend_design_root / "backend_ir.json")
        schema = _read_object(store.database_root / "schema.json")
        report_path = store.backend_design_root / "report.json"
        if report_path.is_file() and _read_object(report_path).get("errors"):
            raise ValueError("Backend design report contains unresolved errors")
        project_port = urlsplit(manifest.get("deployment", {}).get("e2eBaseUrl", "")).port
        port = _resolve_web_port(args.port if args.port is not None else project_port)
        database_manifest_path = store.lowering_root / "database/manifest.json"
        if not database_manifest_path.is_file():
            raise ValueError("Database lowering manifest is missing; run scripts/lower_database.py first")
        database_manifest = _read_object(database_manifest_path)

        print(f"Backend lowering workspace: {output}", flush=True)
        result = lower_backend(
            output, store, manifest, blueprint=blueprint, schema=schema,
            database_manifest=database_manifest, default_port=port,
        )
        for warning in result.warnings:
            print(f"WARNING: {warning}")
        for error in result.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        if not result.ok:
            return 1

        print("Backend lowering completed.", flush=True)
        for relative in sorted(result.sources):
            print(f"Generated: {output / relative}")
        print(f"Report: {result.artifacts['backend_lowering_report']}")
        print(f"Module plan: {store.backend_lowering_root / 'module_plan.json'}")
        print(f"Routes: {store.backend_lowering_root / 'routes.json'}")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Backend lowering error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
