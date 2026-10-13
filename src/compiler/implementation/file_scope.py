"""Minimal requirement sources with explicit file discovery and permissions."""
from __future__ import annotations

import posixpath
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from compiler.code_binding import CodeTargetResolver
from compiler.ir.frontend import frontend_events

SOURCE_ROOTS = ("backend/src/", "frontend/src/", "shared/src/")
CONFIG_FILES = {
    "backend/package.json", "backend/tsconfig.json", "backend/drizzle.config.ts",
    "frontend/package.json", "frontend/tsconfig.json", "frontend/tsconfig.app.json",
    "frontend/tsconfig.node.json", "frontend/vite.config.ts", "frontend/index.html",
    "shared/package.json", "shared/tsconfig.json", "package.json", "tsconfig.json",
}
TEXT_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".css", ".scss", ".json", ".svg", ".html"}
TYPE_KEYS = ("input_type", "output_type", "props_type", "context_type", "state_type")


def application_path(relative: str) -> bool:
    path = PurePosixPath(relative)
    return bool(relative and not path.is_absolute() and ".." not in path.parts and ":" not in relative and (
        relative in CONFIG_FILES or relative.startswith(SOURCE_ROOTS) and path.suffix in TEXT_SUFFIXES))


def readable_path(relative: str) -> bool:
    path = PurePosixPath(relative)
    return application_path(relative) or bool(relative.startswith("tests/support/")
        and not path.is_absolute() and ".." not in path.parts and ":" not in relative and path.suffix in TEXT_SUFFIXES)


def import_paths(root: Path, relative: str, source: str) -> set[str]:
    paths = set()
    for specifier in re.findall(r'''(?:from\s+|import\s*(?:\(\s*)?)["']([^"']+)["']''', source):
        if specifier == "@arc/shared":
            stem = "shared/src/index"
        elif specifier.startswith("."):
            stem = posixpath.normpath(posixpath.join(posixpath.dirname(relative), specifier))
            if stem.endswith((".js", ".jsx")):
                stem = posixpath.splitext(stem)[0]
        else:
            continue
        for candidate in (stem, stem + ".ts", stem + ".tsx", stem + "/index.ts", stem + "/index.tsx", stem + ".js"):
            path = (root / candidate).resolve()
            if root in path.parents and path.is_file() and readable_path(candidate):
                paths.add(candidate)
                break
    return paths


class RequirementFileScope:
    def __init__(self, root: Path, registry: dict[str, Any], frontend_ir: dict[str, Any] | None = None) -> None:
        self.root = root.resolve()
        self.registry = registry
        self.frontend_ir = frontend_ir
        self.bindings = {row["module_id"]: row for row in registry.get("code_bindings", [])}
        self.by_file: dict[str, list[dict[str, Any]]] = {}
        for row in self.bindings.values():
            self.by_file.setdefault(row["file"], []).append(row)

    def read(self, relative: str) -> str:
        path = (self.root / relative).resolve()
        if self.root not in path.parents or not path.is_file():
            raise ValueError(f"Missing or unsafe source: {relative}")
        with path.open(encoding="utf-8", newline="") as stream:
            return stream.read()

    def catalog(self, mode: str) -> list[dict[str, Any]]:
        paths = set()
        for prefix in (*SOURCE_ROOTS, "tests/support/"):
            directory = self.root / prefix
            if directory.is_dir():
                paths.update(path.relative_to(self.root).as_posix() for path in directory.rglob("*")
                             if path.is_file() and self.root in path.resolve().parents)
        paths.update(path for path in CONFIG_FILES if (self.root / path).is_file())
        return [{"file": path, "editable": self.editable(path, mode),
                 "module_ids": [row["module_id"] for row in self.by_file.get(path, [])],
                 "owner_requirements": sorted({owner for row in self.by_file.get(path, []) for owner in row.get("owner_requirements", [])})}
                for path in sorted(paths) if readable_path(path)]

    @staticmethod
    def editable(path: str, mode: str) -> bool:
        if not application_path(path):
            return False
        if mode == "repair":
            return True
        prefix = "frontend/" if mode == "frontend" else "backend/"
        return path.startswith((prefix, "shared/"))

    def select(self, requirement_id: str, *, mode: str, target_ids: set[str] | None = None,
               extra_files: set[str] | None = None, test_files: tuple[str, ...] = ()) -> dict[str, Any]:
        resolved = CodeTargetResolver(self.registry).resolve_requirement_targets(requirement_id)
        targets = {row["module_id"] for row in resolved["owned_targets"]
                   if requirement_id in row.get("owner_requirements", [])}
        if target_ids is not None:
            if target_ids - {row["module_id"] for row in resolved["owned_targets"]}:
                raise ValueError("Requested target is not owned or structurally used by this requirement")
            # Authorization includes structural targets, but initial source loading
            # includes only direct associations. Other targets remain requestable.
            targets &= target_ids
        test_imports = {path for relative in test_files for path in import_paths(self.root, relative, self.read(relative))}
        targets.update(row["module_id"] for path in (extra_files or set())
                       for row in self.by_file.get(path, []))
        ui_ancestors = set()
        if self.frontend_ir:
            ui = {row["id"]: row for row in self.frontend_ir["ui"]}
            parents = {child: row["id"] for row in ui.values() for child in row["children"]}
            # Association is explicit ownership, not structural uses or dependency ownership.
            associated = {key for key, row in ui.items()
                          if requirement_id in row.get("requirement_ids", []) and key in self.bindings}
            if mode != "backend":
                targets.update(associated & target_ids if target_ids is not None else associated)
            controls = {key for key in targets if key in ui}
            controls.update(event["ui_id"] for event in frontend_events(self.frontend_ir) if event["id"] in targets)
            if mode != "backend":
                # Include the full composition path to the screen, without
                # expanding siblings, descendants or other page subtrees.
                for key in controls:
                    visited = set()
                    while key in ui and key not in visited:
                        visited.add(key)
                        if ui[key]["kind"] == "screen" or key not in parents:
                            break
                        key = parents[key]
                        ui_ancestors.add(key)
        selected = (targets | ui_ancestors) & self.bindings.keys()
        reasons: dict[str, set[str]] = {}
        def add(path: str, reason: str) -> None:
            if path and readable_path(path) and (self.root / path).is_file():
                reasons.setdefault(path, set()).add(reason)
        for path in test_imports:
            if path.startswith("tests/support/"):
                add(path, "test_support_import")
        for key in selected:
            row = self.bindings[key]
            add(row["file"], "requirement_target" if key in targets else "ui_ancestor_to_page")
            for reference in [*[row.get(key) for key in TYPE_KEYS], *row.get("store_types", [])]:
                if isinstance(reference, dict):
                    add(reference.get("file", ""), "type_contract")
        known = {row["file"] for row in self.catalog(mode)}
        for path in extra_files or set():
            if path not in known:
                raise ValueError(f"File request is outside the application source catalog: {path}")
            add(path, "model_file_request")
        # Imported implementation dependencies stay in the catalog until requested.
        # Direct type imports are sufficient to interpret the loaded signatures.
        for path in list(reasons):
            source = self.read(path)
            type_source = "\n".join(re.findall(r"(?:import|export)\s+type\b[^;]*;", source, re.DOTALL))
            for dependency in import_paths(self.root, path, type_source):
                if dependency not in self.by_file:
                    add(dependency, "direct_type_import")
        writable = {path for path in reasons if self.editable(path, mode)}
        module_rows = [row for row in self.bindings.values() if row["file"] in reasons]
        preserve = sorted({owner for row in module_rows for owner in row.get("owner_requirements", []) if owner != requirement_id})
        design_context: dict[str, Any] = {}
        dependency_ids = {key for row in module_rows for key in row.get("callees", [])}
        design_context["dependency_interfaces"] = [
            {key: row[key] for key in ("module_id", "kind", "file", "symbol", "route", *TYPE_KEYS) if key in row}
            for module_id in sorted(dependency_ids) if module_id in self.bindings
            for row in [self.bindings[module_id]] if row["file"] not in reasons]
        environment = {}
        for layer in ("backend", "frontend", "shared"):
            path = self.root / layer / "package.json"
            if path.is_file():
                package = json.loads(path.read_text(encoding="utf-8"))
                environment[layer] = {key: package[key] for key in
                                      ("type", "scripts", "dependencies", "devDependencies") if key in package}
        design_context["environment_summary"] = environment
        backend_path = self.root / ".arc/design/backend/backend_ir.json"
        if backend_path.is_file():
            backend = json.loads(backend_path.read_text(encoding="utf-8"))
            ids = {row["module_id"] for row in module_rows}
            modules = [row for row in backend["modules"] if row["id"] in ids]
            from compiler.frontend_design.context import required_types
            design_context["backend_subgraph"] = {"modules": modules, "types": required_types(modules, backend["types"])}
        if self.frontend_ir:
            from compiler.implementation.frontend_context import frontend_subgraph
            design_context["frontend_subgraph"] = frontend_subgraph(self.frontend_ir, requirement_id,
                {row["module_id"] for row in module_rows if row["file"].startswith("frontend/")})
        return {"files": sorted(reasons), "editable": sorted(writable),
                "reasons": {path: sorted(values) for path, values in sorted(reasons.items())},
                "targets": copy_rows(module_rows), "file_catalog": self.catalog(mode),
                "preserve_requirements": preserve, "design_context": design_context}


def copy_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    import copy
    return copy.deepcopy(rows)
