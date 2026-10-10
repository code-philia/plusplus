"""Read-only evidence selection and per-round repair permissions."""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from compiler.code_binding import CodeTargetResolver
from compiler.implementation.file_scope import RequirementFileScope, import_paths
from compiler.implementation.frontend_context import implementation_requirement


class RepairContextBuilder:
    def __init__(self, root: Path, registry: dict[str, Any], frontend_ir=None) -> None:
        self.root = root.resolve()
        self.registry = registry
        self.frontend_ir = frontend_ir
        self.scope = RequirementFileScope(self.root, registry, frontend_ir)

    def read(self, relative: str) -> str:
        path = (self.root / relative).resolve()
        if self.root not in path.parents or not path.is_file():
            raise ValueError(f"Missing or unsafe evidence file: {relative}")
        with path.open(encoding="utf-8", newline="") as handle:
            return handle.read()

    def imports(self, relative: str, source: str) -> set[str]:
        return import_paths(self.root, relative, source)

    def analysis(self, requirement_id: str, requirement: dict[str, Any], *,
                 layer: str, iteration: int, test_result: dict[str, Any],
                 history: list[dict[str, Any]]) -> dict[str, Any]:
        resolved = CodeTargetResolver(self.registry).resolve_requirement_targets(requirement_id)
        bindings = self.registry.get("code_bindings", [])
        tests = {path: self.read(path) for path in test_result["selected_files"]}
        rejection_feedback = [message for event in history if event.get("status") == "REJECTED"
                              for message in event.get("implementation_feedback", [])]
        raw_output = [command.get(key) or "" for command in test_result.get("commands", [])
                      for key in ("stdout", "stderr", "error")]
        evidence = "\n".join([
            *raw_output, *test_result.get("errors", []), *rejection_feedback, *tests.values(),
        ]).replace(chr(92), "/")
        imported = {path for relative, source in tests.items() for path in self.imports(relative, source)}
        changed = {path for event in history for path in event.get("changed_files", [])}
        seeds = list(resolved["owned_targets"])
        # Error locations, test imports/symbols and actual changes may cross layers.
        seeds.extend(row for row in bindings if row.get("file") and (
            row["file"] in evidence or row["file"] in changed or row["file"] in imported
            or row["file"].partition("/")[2] in evidence
            or (row.get("symbol") and re.search(r"\b" + re.escape(row["symbol"]) + r"\b", evidence))
            or ((row.get("route") or {}).get("path") not in {None, "", "/"}
                and row["route"]["path"] in evidence)
        ))
        evidence_paths = {row["file"] for row in seeds} | imported | changed
        catalog = {row["file"]: row for row in self.scope.catalog("repair")}
        evidence_paths &= catalog.keys()
        evidence_paths.update(path for path in catalog if path in evidence or path.partition("/")[2] in evidence)
        scope = self.scope.select(requirement_id, mode="repair", extra_files=evidence_paths,
                                  test_files=tuple(test_result["selected_files"]))
        sources = {path: self.read(path) for path in scope["files"] if path not in tests}
        file_catalog = [{**row, "repairable": row["editable"], "loaded": row["file"] in sources}
                        for row in scope["file_catalog"]]
        file_catalog.extend({"file": path, "repairable": True, "loaded": True} for path in tests)
        requirement, _ = implementation_requirement(requirement, self.root, frontend=True)
        return {
            "requirement_id": requirement_id, "requirement": requirement,
            "test_layer": layer, "iteration": iteration,
            "test_files": tests, "test_result": test_result, "source_files": sources,
            "file_catalog": file_catalog,
            "direct_editable_files": scope["editable"],
            "source_targets": scope["targets"], "file_scope": scope["reasons"],
            "preserve_requirements": scope["preserve_requirements"],
            "history": copy.deepcopy(history),
            **scope["design_context"],
        }

    def expand_analysis(self, context: dict[str, Any], requests: list[dict[str, str]]) -> dict[str, Any]:
        known = {row["file"] for row in self.scope.catalog("repair")}
        requested = {row["file"] for row in requests}
        if requested - known:
            raise ValueError("Analysis requested files outside the application catalog")
        scope = self.scope.select(context["requirement_id"], mode="repair",
                                  extra_files=(set(context["source_files"]) | requested) & known,
                                  test_files=tuple(context["test_files"]))
        expanded = copy.deepcopy(context)
        expanded["source_files"] = {path: self.read(path) for path in scope["files"] if path not in context["test_files"]}
        expanded["file_catalog"] = [{**row, "repairable": row["editable"],
            "loaded": row["file"] in expanded["source_files"]} for row in scope["file_catalog"]]
        expanded["file_catalog"].extend({"file": path, "repairable": True, "loaded": True} for path in context["test_files"])
        expanded.update(source_targets=scope["targets"], file_scope=scope["reasons"],
                        preserve_requirements=scope["preserve_requirements"],
                        direct_editable_files=scope["editable"])
        expanded.update(scope["design_context"])
        # The caller uses this same context when constructing the diagnosed patch.
        context.clear()
        context.update(expanded)
        return context

    @staticmethod
    def direct_repair(context: dict[str, Any], owned_files: set[str]) -> dict[str, Any] | None:
        editable = {
            path: source for path, source in context["source_files"].items()
            if path in owned_files
        }
        if not editable:
            return None
        return {
            key: value for key, value in context.items()
            if key not in {"source_files", "file_catalog"}
        } | {
            "implementation_mode": "direct_repair",
            "editable_files": editable,
            "related_files": {
                path: source for path, source in context["source_files"].items()
                if path not in editable
            },
        }

    @staticmethod
    def repair(context: dict[str, Any], decision: dict[str, Any],
               history: list[dict[str, Any]]) -> dict[str, Any]:
        suspects = {row["file"] for row in decision["suspected_files"]}
        sources = {**context["source_files"], **context["test_files"]}
        allowed = {row["file"] for row in context["file_catalog"] if row["repairable"]}
        if suspects - sources.keys() or suspects - allowed:
            raise ValueError("Repair may edit only loaded, diagnosed repairable files")
        return {
            key: value for key, value in context.items()
            if key not in {"source_files", "file_catalog", "history", "test_files"}
        } | {
            "test_analysis": decision,
            "test_files": {path: source for path, source in context["test_files"].items() if path not in suspects},
            "editable_files": {path: sources[path] for path in sorted(suspects)},
            "related_files": {path: source for path, source in context["source_files"].items() if path not in suspects},
            "history": copy.deepcopy(history),
        }
