"""Requirement-scoped source discovery, import closure and per-call file permissions."""
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
        targets = {row["module_id"] for row in resolved["owned_targets"]}
        if target_ids is not None:
            if target_ids - targets:
                raise ValueError("Requested target is not owned or structurally used by this requirement")
            targets = set(target_ids)
        test_imports = {path for relative in test_files for path in import_paths(self.root, relative, self.read(relative))}
        targets.update(row["module_id"] for path in (extra_files or set()) | test_imports for row in self.by_file.get(path, []))
        relevant_frontend = set(targets)
        # Frontend uses and composition are relevant even when they establish no
        # direct semantic ownership (shell, outlet, reused state or handlers).
        if self.frontend_ir:
            ui = {row["id"]: row for row in self.frontend_ir["ui"]}
            parents = {child: row["id"] for row in ui.values() for child in row["children"]}
            association = self.frontend_ir.get("requirement_links", {}).get(requirement_id, {})
            targets.update(key for key in [*association.get("implements", []), *association.get("uses", [])] if key in self.bindings)
            relevant_frontend.update(targets)
            controls = {key for key in targets if key in ui}
            controls.update(event["ui_id"] for event in frontend_events(self.frontend_ir) if event["id"] in targets)
            for key in list(controls):
                seen = set()
                while key in parents and key not in seen:
                    seen.add(key)
                    key = parents[key]
                    targets.add(key)
        # Explicit callee closure crosses API_CLIENT -> API -> FUNC -> DB for
        # reading; the agent's phase determines which layers can be edited.
        pending, selected = list(targets), set()
        while pending:
            key = pending.pop()
            if key in selected or key not in self.bindings:
                continue
            selected.add(key)
            row = self.bindings[key]
            for dependency in row.get("callees", []):
                target = self.bindings.get(dependency, {})
                if row["kind"] == "UI" and target.get("kind") in {"UI", "UI_EVENT"} and dependency not in relevant_frontend:
                    continue
                pending.append(dependency)
        reasons: dict[str, set[str]] = {}
        def add(path: str, reason: str) -> None:
            if path and readable_path(path) and (self.root / path).is_file():
                reasons.setdefault(path, set()).add(reason)
        for path in test_imports:
            add(path, "test_import")
        for key in selected:
            row = self.bindings[key]
            add(row["file"], "requirement_target" if key in targets else "callee")
            for reference in [*[row.get(key) for key in TYPE_KEYS], *row.get("store_types", [])]:
                if isinstance(reference, dict):
                    add(reference.get("file", ""), "type_contract")
        layers = {path.split("/", 1)[0] for path in reasons}
        if mode == "frontend":
            layers.add("frontend")
        elif mode == "backend":
            layers.add("backend")
        elif mode == "repair":
            layers.update(("backend", "frontend"))
        support = {
            "backend": ("backend/src/server.ts", "backend/src/app.ts", "backend/src/generated/router.ts",
                        "backend/src/generated/context.ts", "backend/src/runtime/context.ts", "backend/package.json", "backend/tsconfig.json"),
            "frontend": ("frontend/src/App.tsx", "frontend/src/main.tsx", "frontend/src/index.css", "frontend/vite.config.ts",
                         "frontend/package.json", "frontend/tsconfig.json", "frontend/tsconfig.app.json"),
        }
        for layer in layers:
            for path in support.get(layer, ()):
                add(path, "integration_support")
        known = {row["file"] for row in self.catalog(mode)}
        for path in extra_files or set():
            if path not in known:
                raise ValueError(f"File request is outside the application source catalog: {path}")
            add(path, "model_file_request")
        pending, visited = list(reasons), set()
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            source = self.read(path)
            type_source = "\n".join(re.findall(r"(?:import|export)\s+type\b[^;]*;", source, re.DOTALL))
            type_imports = import_paths(self.root, path, type_source)
            for dependency in import_paths(self.root, path, source):
                # Do not expand a shared entrypoint into every unrelated page or
                # route module. They remain discoverable through the catalog.
                if dependency in self.by_file and dependency not in reasons and dependency not in type_imports:
                    continue
                if dependency not in reasons:
                    pending.append(dependency)
                add(dependency, "source_import")
        writable = {path for path in reasons if self.editable(path, mode)}
        module_rows = [row for row in self.bindings.values() if row["file"] in reasons]
        preserve = sorted({owner for row in module_rows for owner in row.get("owner_requirements", []) if owner != requirement_id})
        design_context: dict[str, Any] = {}
        requirement_path = self.root / ".arc/preprocessing/requirement_ir.json"
        if requirement_path.is_file():
            nodes = json.loads(requirement_path.read_text(encoding="utf-8")).get("nodes", {})
            design_context["shared_requirements"] = [{"id": key, **{name: nodes[key][name] for name in
                ("name", "description", "scenarios") if name in nodes[key]}} for key in preserve if key in nodes]
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
