"""Run only frontend design against existing database and backend artifacts.

Usage: python scripts/design_frontend.py requirements.yaml -o output-directory
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


# Allow direct execution without installing the compiler package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from main import build_compile_parser, _ensure_dotenv_loaded, _locate_requirement_file
from compiler.artifacts import CompilerArtifactStore
from compiler.frontend_design import FrontendIRGenerationPass
from compiler.model_client import Model
from compiler.visual_reference import VisualReferenceResolver, VisualReferenceAnalyzer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers()
    build_compile_parser(subparsers)
    command = subparsers.choices["compile"]
    command.prog = parser.prog
    command.description = (
        "Generate only frontend design using existing preprocessing, database schema "
        "and backend IR. No lowering, implementation or design validation. "
        "Existing frontend design results are overwritten; --port is accepted but unused."
    )
    command.add_argument("--no-visuals", action="store_true",
                         help="Ignore optional reference images and their cached analyses.")
    args = command.parse_args(argv)
    if args.clean or args.resume or args.start_from != "zero" or args.checkpoint_ref or args.restart_output:
        command.error(
            "Frontend-only design reads the existing output directory directly; "
            "--clean, --resume and checkpoint/restart options are not supported."
        )

    try:
        _ensure_dotenv_loaded()
        requirement = Path(_locate_requirement_file(args.requirement_path))
        output = Path(args.output_dir).expanduser().resolve()
        store = CompilerArtifactStore(output)

        def read(relative: str) -> dict:
            path = store.root / relative
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError(f"Expected a JSON object: {path}")
            return value

        requirements = read("preprocessing/requirement_ir.json")
        dependencies = read("preprocessing/dependency_graph.json")
        # The backend blueprint already encapsulates the database-facing design.
        # Require the existing database artifact, without regenerating/auditing it.
        read("design/database/schema.json")
        backend = read("design/backend/backend_ir.json")
        if requirements.get("source", {}).get("sha256") != hashlib.sha256(requirement.read_bytes()).hexdigest():
            raise ValueError(
                "Requirement file differs from the existing preprocessing artifacts; "
                "use the original requirement file or regenerate the preceding design stages."
            )

        print(f"Frontend design workspace: {output}", flush=True)
        visuals, analyses, warnings = [], [], []
        if not args.no_visuals:
            resolved = VisualReferenceResolver().resolve(requirement, requirements)
            visuals = resolved.references
            warnings.extend(issue.format() for issue in resolved.errors)
            if visuals:
                print(f"Loading/analyzing {len(visuals)} optional reference images.", flush=True)
                analyzed = VisualReferenceAnalyzer.from_env(store.root).analyze_cached(
                    visuals, store.frontend_design_root / "visual_cache.json",
                )
                analyses = analyzed.references
                warnings.extend(issue.format() for issue in analyzed.errors)

        result = FrontendIRGenerationPass(Model.from_env(), store.root).compile(
            requirements, dependencies, backend, visuals, analyses,
        )
        result.report["warnings"].extend(warnings)
        if warnings and result.report["status"] == "GENERATED":
            result.report["status"] = "GENERATED_WITH_WARNINGS"
        store.write_frontend_design(frontend_ir=result.frontend_ir, report=result.report,
                                    batches=result.batches, traceability=result.traceability)
        print(f"Frontend design completed: {result.report['status']}")
        print(f"IR: {store.frontend_design_root / 'frontend_ir.json'}")
        print(f"Report: {store.frontend_design_root / 'report.json'}")
        print(f"Pass logs: {store.frontend_design_root / 'passes'}")
        for warning in result.report["warnings"]:
            print(f"WARNING: {warning}")
        for failure in result.report["failed_tasks"]:
            print(f"ERROR: {failure['task_id']}: {failure['message']}", file=sys.stderr)
        return 0 if result.ok and not result.report["failed_tasks"] else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Frontend design error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
