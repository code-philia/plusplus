"""Lower existing Frontend IR into an already initialized React project.

Usage: python scripts/lower_frontend.py -o output-directory
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
from compiler.frontend_lowering import FrontendReactLowerer
from compiler.runtime.artifacts import CompilerArtifactStore


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Plan React subtree component and frontend function files, then generate "
            "exported types, props and handler/function skeletons from existing design artifacts. "
            "Only frontend lowering outputs are replaced. No model calls or business implementation."
        )
    )
    parser.add_argument("-o", "--output-dir", required=True, help="Existing initialized project workspace")
    parser.add_argument("--port", type=int, help="Backend proxy port; defaults to the initialized project port")
    args = parser.parse_args(argv)

    try:
        _ensure_dotenv_loaded()
        output = Path(args.output_dir).expanduser().resolve()
        store = CompilerArtifactStore(output)
        manifest, error = store.read_project_manifest()
        if error or manifest is None:
            raise ValueError(error or "Missing initialized project manifest")
        if not (output / "frontend/package.json").is_file():
            raise ValueError("Initialized frontend/package.json is missing")

        ir = _read_object(store.frontend_design_root / "frontend_ir.json")
        backend = _read_object(store.backend_design_root / "backend_ir.json")
        if backend.get("ir_kind") != "backend_design":
            raise ValueError("Expected current Backend IR")
        design_report = _read_object(store.frontend_design_root / "report.json")
        if design_report.get("status") not in {"GENERATED", "GENERATED_WITH_WARNINGS"} or design_report.get("failed_tasks"):
            raise ValueError("Frontend design contains failed tasks; complete scripts/design_frontend.py first")
        route_path = store.backend_lowering_root / "routes.json"
        if not route_path.is_file():
            raise ValueError("Backend routes are missing; run scripts/lower_backend.py first")
        routes = _read_object(route_path)
        project_port = urlsplit(manifest.get("deployment", {}).get("e2eBaseUrl", "")).port
        port = _resolve_web_port(args.port if args.port is not None else project_port)

        print(f"Frontend lowering workspace: {output}", flush=True)
        result = FrontendReactLowerer(output).lower(ir, backend, routes, port)
        for warning in result.warnings:
            print(f"WARNING: {warning}")
        for error in result.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        artifacts = store.write_frontend_lowering(
            report=result.report, sources=result.sources, batches=result.batches,
        )
        if not result.ok:
            return 1
        try:
            store.write_frontend_sources(result.sources)
        except (OSError, ValueError) as exc:
            result.errors.append(f"Cannot publish frontend skeletons: {exc}")
            store.write_frontend_lowering(report=result.report, sources=result.sources, batches=result.batches)
            raise

        print("Frontend lowering completed.", flush=True)
        for relative in sorted(result.sources):
            print(f"Generated: {output / relative}")
        print(f"Report: {artifacts['frontend_lowering_report']}")
        print(f"Module plan: {store.frontend_lowering_root / 'module_plan.json'}")
        print(f"Function plan: {store.frontend_lowering_root / 'function_plan.json'}")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Frontend lowering error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
