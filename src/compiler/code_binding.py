from __future__ import annotations

from compiler.ir.frontend import frontend_events

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any


CODE_BINDING_SCHEMA_VERSION = 2
CODE_BINDING_READY = "CODE_BINDING_READY"


@dataclass(slots=True)
class CodeBindingResult:
    registry: dict[str, Any]
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class CodeBindingLowerer:
    """Join frozen IR and lowering manifests to their materialized source surfaces."""

    def lower(
        self,
        *,
        output_root: Path,
        requirement_ir: dict[str, Any],
        dependency_graph: dict[str, Any],
        design_ir: dict[str, Any],
        frontend_ir: dict[str, Any],
        backend_symbol_registry: dict[str, Any],
        backend_type_manifest: dict[str, Any],
        backend_module_manifests: dict[str, dict[str, Any]],
        backend_route_registry: dict[str, Any],
        frontend_lowering_report: dict[str, Any] | None = None,
    ) -> CodeBindingResult:
        errors: list[str] = []
        root = output_root.expanduser().resolve()

        _index_rows(
            backend_symbol_registry.get("symbols"), "id", "backend symbols", errors
        )
        type_definitions = _index_rows(
            backend_type_manifest.get("definitions"),
            "symbol_id",
            "backend type definitions",
            errors,
        )
        backend_modules = _index_rows(
            design_ir.get("modules"), "id", "Backend Design modules", errors
        )
        backend_routes = _index_rows(
            backend_route_registry.get("routes"),
            "module_id",
            "backend routes",
            errors,
        )
        lowered_backend: dict[str, dict[str, Any]] = {}
        for kind in ("DB", "FUNC", "API"):
            manifest = backend_module_manifests.get(kind)
            if not isinstance(manifest, dict):
                errors.append(f"ARC4301 CODE_BINDING_INPUT_INVALID: missing {kind} module manifest.")
                continue
            for module_id, row in _index_rows(
                manifest.get("modules"), "module_id", f"{kind} module manifest", errors
            ).items():
                if module_id in lowered_backend:
                    errors.append(
                        f"ARC4301 CODE_BINDING_INPUT_INVALID: duplicate lowered module {module_id}."
                    )
                lowered_backend[module_id] = row

        type_bindings = _backend_type_bindings(type_definitions)
        type_by_id = {str(row["type_id"]): row for row in type_bindings}

        bindings: list[dict[str, Any]] = []
        for module_id, module in sorted(backend_modules.items()):
            lowered = lowered_backend.get(module_id)
            if lowered is None:
                errors.append(
                    f"ARC4302 CODE_BINDING_COVERAGE_INVALID: no lowered source for {module_id}."
                )
                continue
            kind = str(module.get("kind", "")).upper()
            input_type = _type_reference(lowered.get("input_contract_id"), type_by_id)
            output_type = _type_reference(lowered.get("output_contract_id"), type_by_id)
            context_type = _type_reference(lowered.get("context_contract_id"), type_by_id)
            symbol = str(lowered.get("function_symbol", "")).strip()
            route = copy.deepcopy(backend_routes.get(module_id)) if kind == "API" else None
            binding = {
                "module_id": module_id,
                "owner_requirements": _strings(module.get("requirement_ids")),
                "kind": kind,
                "file": str(lowered.get("path", "")),
                "symbol": symbol,
                "input_type": input_type,
                "output_type": output_type,
                "context_type": context_type,
                "parameters": copy.deepcopy(lowered.get("parameters", [])),
                "calling_convention": lowered.get("calling_convention"),
                "store_types": [_type_reference(type_id, type_by_id) for type_id in lowered.get("referenced_type_ids", [])],
                "props_type": None,
                "route": _compact_backend_route(route),
                "callees": _strings(module.get("callees")),
            }
            bindings.append(binding)

        frontend_items: dict[str, dict[str, Any]] = {}
        if frontend_ir.get("ir_kind") == "frontend_design":
            locations = {row["entity_id"]: row for row in (frontend_lowering_report or {}).get("bindings", [])}
            ui_units = (frontend_lowering_report or {}).get('ui_plan', {}).get('ui_units', {})
            for table, kind in (("ui", "UI"), ("events", "UI_EVENT"), ("functions", "UI_FUNC")):
                for item in (frontend_events(frontend_ir) if table == "events" else frontend_ir.get(table, [])):
                    identity = item["id"]
                    frontend_items[identity] = item
                    location = locations.get(identity)
                    if location is None:
                        errors.append(f"ARC4302 CODE_BINDING_COVERAGE_INVALID: no lowered source for {identity}.")
                        continue
                    bindings.append({"module_id": identity, "owner_requirements": _strings(item.get("requirement_ids")),
                        "kind": kind, "file": location["file"], "symbol": location["symbol"],
                        "visibility": location.get('visibility', 'local' if kind == 'UI'
                                                   and identity in ui_units and ui_units[identity] != identity else 'export'),
                        "input_type": copy.deepcopy(location.get("input_type")),
                        "output_type": copy.deepcopy(location.get("output_type")),
                        "props_type": copy.deepcopy(location.get("props_type")),
                        "context_type": copy.deepcopy(location.get("context_type")),
                        "component_id": location.get("component_id"),
                        "parameters": copy.deepcopy(location.get("parameters", [])),
                        "calling_convention": location.get("calling_convention"),
                        "store_types": copy.deepcopy(location.get("store_types", [])),
                        "route": {"path": item["route"]} if "route" in item else None,
                        "callees": _strings(location.get("callees"))})
                    type_bindings.extend(_frontend_declared_types([
                        *[location[key] for key in ("input_type", "output_type", "props_type", "context_type") if isinstance(location.get(key), dict)],
                        *location.get("store_types", [])]))
            for location in locations.values():
                if location.get("kind") != "API_CLIENT":
                    continue
                identity = location["entity_id"]
                api_id = identity.removeprefix("API_CLIENT::")
                bindings.append({"module_id": identity, "source_ir_id": api_id,
                    "owner_requirements": _strings(location.get("requirement_ids")), "kind": "API_CLIENT",
                    "file": location["file"], "symbol": location["symbol"],
                    "input_type": copy.deepcopy(location.get("input_type")),
                    "output_type": copy.deepcopy(location.get("output_type")),
                    "props_type": copy.deepcopy(location.get("props_type")),
                    "context_type": copy.deepcopy(location.get("context_type")),
                    "component_id": location.get("component_id"),
                    "parameters": copy.deepcopy(location.get("parameters", [])),
                    "calling_convention": location.get("calling_convention"),
                    "store_types": copy.deepcopy(location.get("store_types", [])),
                    "route": _compact_backend_route(backend_routes.get(api_id)), "callees": [api_id]})
                type_bindings.extend(_frontend_declared_types([
                    *[location[key] for key in ("input_type", "output_type", "props_type", "context_type") if isinstance(location.get(key), dict)],
                    *location.get("store_types", [])]))
        type_bindings = _deduplicate_type_bindings(type_bindings, errors)

        bindings.sort(key=lambda row: str(row.get("module_id", "")))
        binding_by_id = _unique_bindings(bindings, errors)
        _validate_coverage(backend_modules, frontend_items, binding_by_id, errors)
        _validate_sources(root, bindings, type_bindings, errors)

        requirement_targets = _requirement_targets(
            requirement_ir,
            bindings,
            frontend_ir,
            dependency_graph,
        )
        _validate_requirement_permissions(
            requirement_targets,
            binding_by_id,
            errors,
        )
        registry = {
            "schema_version": CODE_BINDING_SCHEMA_VERSION,
            "status": CODE_BINDING_READY if not errors else "CODE_BINDING_INVALID",
            "code_bindings": bindings,
            "type_bindings": type_bindings,
            "requirement_targets": requirement_targets,
        }
        return CodeBindingResult(registry=registry, errors=list(dict.fromkeys(errors)))


class CodeTargetResolver:
    """Read-only query interface used by future test generation and TDD stages."""

    def __init__(self, registry: dict[str, Any]) -> None:
        if not isinstance(registry, dict) or registry.get("status") != CODE_BINDING_READY:
            raise ValueError("Code Binding Registry is not ready.")
        self._bindings = {
            str(row["module_id"]): row
            for row in registry.get("code_bindings", [])
            if isinstance(row, dict) and row.get("module_id")
        }
        self._types = {
            str(row["type_id"]): row
            for row in registry.get("type_bindings", [])
            if isinstance(row, dict) and row.get("type_id")
        }
        self._requirements = {
            str(row["requirement_id"]): row
            for row in registry.get("requirement_targets", [])
            if isinstance(row, dict) and row.get("requirement_id")
        }

    def resolve(self, module_id: str) -> dict[str, Any] | None:
        row = self._bindings.get(str(module_id))
        return copy.deepcopy(row) if row is not None else None

    def resolve_type(self, type_id: str) -> dict[str, Any] | None:
        row = self._types.get(str(type_id))
        return copy.deepcopy(row) if row is not None else None

    def resolve_requirement_targets(self, requirement_id: str) -> dict[str, Any]:
        row = self._requirements.get(str(requirement_id))
        if row is None:
            raise KeyError(f"Unknown requirement: {requirement_id}")
        owned_targets = [
            copy.deepcopy(self._bindings[module_id])
            for module_id in row.get("writable", [])
            if module_id in self._bindings
        ]
        dependency_targets = [
            copy.deepcopy(self._bindings[module_id])
            for module_id in row.get("read_only", [])
            if module_id in self._bindings
        ]
        type_ids = _referenced_type_ids([*owned_targets, *dependency_targets])
        return {
            **copy.deepcopy(row),
            "owned_targets": owned_targets,
            "dependency_targets": dependency_targets,
            "type_targets": [
                copy.deepcopy(self._types[type_id])
                for type_id in type_ids
                if type_id in self._types
            ],
        }

def resolve_requirement_targets(
    registry: dict[str, Any], requirement_id: str
) -> dict[str, Any]:
    return CodeTargetResolver(registry).resolve_requirement_targets(requirement_id)




def _referenced_type_ids(targets: list[dict[str, Any]]) -> list[str]:
    type_ids: set[str] = set()
    for target in targets:
        references = [
            target.get("input_type"),
            target.get("output_type"),
            target.get("props_type"),
            target.get("context_type"),
            *target.get("store_types", []),
        ]
        for reference in references:
            if not isinstance(reference, dict):
                continue
            type_id = str(reference.get("type_id", "")).strip()
            if type_id:
                type_ids.add(type_id)
    return sorted(type_ids)


def _backend_type_bindings(
    definitions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "type_id": type_id,
            "file": str(definition.get("path", "")),
            "symbol": str(definition.get("symbol", "")),
        }
        for type_id, definition in sorted(definitions.items())
    ]


def _frontend_declared_types(
    references: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "type_id": str(reference.get("type_id", "")),
            "file": str(reference.get("file", "")),
            "symbol": str(reference.get("symbol", "")),
        }
        for reference in references
    ]


def _type_reference(type_id: Any, types: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    normalized = str(type_id or "").strip()
    if not normalized:
        return None
    row = types.get(normalized)
    if row is None:
        return {"type_id": normalized, "symbol": None, "file": None}
    return {
        "type_id": normalized,
        "symbol": row.get("symbol"),
        "file": row.get("file"),
    }


def _compact_backend_route(route: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(route, dict):
        return None
    return {
        key: copy.deepcopy(route.get(key))
        for key in ("route_id", "method", "path", "input_source", "request_fields", "status_code", "content_type")
    }


def _requirement_targets(
    requirement_ir: dict[str, Any],
    bindings: list[dict[str, Any]],
    frontend_ir: dict[str, Any],
    dependency_graph: dict[str, Any],
) -> list[dict[str, Any]]:
    requirement_ids = [
        str(value)
        for value in requirement_ir.get("node_order", [])
        if str(value).strip()
    ]
    by_id = {str(row["module_id"]): row for row in bindings}
    graph: dict[str, set[str]] = {
        module_id: {value for value in _strings(row.get("callees")) if value in by_id}
        for module_id, row in by_id.items()
    }
    semantic_owners_by_requirement = {
        requirement_id: {
            str(row["module_id"])
            for row in bindings
            if requirement_id in _strings(row.get("owner_requirements"))
        }
        for requirement_id in requirement_ids
    }
    # Semantic module membership is a starting point, not a single-writer
    # restriction on requirements that refine a shared public module.
    owned_by_requirement = {rid: set(ids) for rid, ids in semantic_owners_by_requirement.items()}
    structural_by_requirement = {rid: set() for rid in requirement_ids}
    if frontend_ir.get("ir_kind") == "frontend_design":
        ui = {row["id"]: row for row in frontend_ir["ui"]}
        parents = {child: row["id"] for row in ui.values() for child in row["children"]}
        from compiler.ir.frontend import frontend_events
        events = {row["id"]: row for row in frontend_events(frontend_ir)}
        for rid in requirement_ids:
            link = frontend_ir.get("requirement_links", {}).get(rid, {})
            used = set(link.get("uses", [])) | set(link.get("implements", []))
            used &= by_id.keys()
            used = {key for key in used if by_id[key]["kind"] in {"UI", "UI_EVENT", "UI_FUNC", "API_CLIENT"}}
            controls = {key for key in owned_by_requirement[rid] | used if key in ui}
            controls.update(events[key]["ui_id"] for key in owned_by_requirement[rid] | used if key in events)
            structural = set(used)
            for key in controls:
                seen = set()
                while key in parents and key not in seen:
                    seen.add(key)
                    key = parents[key]
                    if key in by_id:
                        structural.add(key)
            structural_by_requirement[rid] = structural - owned_by_requirement[rid]
            owned_by_requirement[rid].update(structural)
    requirement_dependencies = dependency_graph.get("requirement_dependencies", {})
    atomic_dependencies = dependency_graph.get("atomic_dependencies", {})

    def dependency_closure(requirement_id: str) -> set[str]:
        source = (
            atomic_dependencies
            if requirement_id in atomic_dependencies
            else requirement_dependencies
        )
        result: set[str] = set()
        pending = [str(value) for value in source.get(requirement_id, [])]
        while pending:
            dependency_id = pending.pop()
            if dependency_id in result or dependency_id == requirement_id:
                continue
            result.add(dependency_id)
            nested_source = (
                atomic_dependencies
                if dependency_id in atomic_dependencies
                else requirement_dependencies
            )
            pending.extend(str(value) for value in nested_source.get(dependency_id, []))
        return result

    rows: list[dict[str, Any]] = []
    for requirement_id in requirement_ids:
        owned = owned_by_requirement.get(requirement_id, set())
        # Preserve prerequisite modules as context; invocation-level file scopes
        # decide whether implementation or diagnosed repair needs their files.
        reachable: set[str] = set(
            semantic_owners_by_requirement.get(requirement_id, set()) - owned
        )
        reachable.update({
            module_id
            for dependency_id in dependency_closure(requirement_id)
            for module_id in owned_by_requirement.get(dependency_id, set())
            if module_id not in owned
        })
        pending = [*owned, *reachable]
        while pending:
            current = pending.pop()
            for dependency in graph.get(current, set()):
                if dependency not in reachable and dependency not in owned:
                    reachable.add(dependency)
                    pending.append(dependency)
        rows.append(
            {
                "requirement_id": requirement_id,
                "writable": sorted(owned),
                "structural_writable": sorted(structural_by_requirement[requirement_id]),
                "read_only": sorted(reachable),
            }
        )
    return rows


def _validate_requirement_permissions(
    requirement_targets: list[dict[str, Any]],
    binding_by_id: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    """Check semantic and explicitly derived composition starting permissions."""

    writable_owners: dict[str, list[str]] = {}
    permissions_by_requirement: dict[str, tuple[set[str], set[str]]] = {}
    known_modules = set(binding_by_id)
    for row in requirement_targets:
        requirement_id = str(row.get("requirement_id", "")).strip()
        writable = set(_strings(row.get("writable")))
        read_only = set(_strings(row.get("read_only")))
        overlap = sorted(writable & read_only)
        if overlap:
            errors.append(
                "ARC4306 CODE_BINDING_OWNERSHIP_INVALID: "
                f"{requirement_id} marks modules as both writable and read-only "
                f"{overlap}."
            )
        referenced = writable | read_only
        unknown = sorted(referenced - known_modules)
        if unknown:
            errors.append(
                "ARC4306 CODE_BINDING_OWNERSHIP_INVALID: "
                f"{requirement_id} references unknown modules {unknown}."
            )
        for module_id in writable:
            writable_owners.setdefault(module_id, []).append(requirement_id)
        permissions_by_requirement[requirement_id] = (writable, read_only)

    known_requirements = set(permissions_by_requirement)
    for module_id, binding in sorted(binding_by_id.items()):
        semantic_owners = {
            value
            for value in _strings(binding.get("owner_requirements"))
            if value in known_requirements
        }
        owners = sorted(set(writable_owners.get(module_id, [])))
        if binding.get("kind") in {"UI", "UI_EVENT", "UI_FUNC"}:
            if semantic_owners and not owners:
                errors.append(
                    "ARC4306 CODE_BINDING_OWNERSHIP_INVALID: "
                    f"{module_id} has no writable requirement."
                )
        elif semantic_owners and not owners:
            errors.append(
                "ARC4306 CODE_BINDING_OWNERSHIP_INVALID: "
                f"{module_id} has no writable requirement."
            )
        structural_owners = {str(row["requirement_id"]) for row in requirement_targets
                             if module_id in row.get("structural_writable", [])}
        invalid_owners = sorted(set(owners) - semantic_owners - structural_owners)
        if invalid_owners:
            errors.append(
                "ARC4306 CODE_BINDING_OWNERSHIP_INVALID: "
                f"{module_id} is writable by non-semantic owners {invalid_owners}."
            )
        for requirement_id in sorted(semantic_owners):
            writable, read_only = permissions_by_requirement[requirement_id]
            if module_id not in writable and module_id not in read_only:
                errors.append(
                    "ARC4306 CODE_BINDING_OWNERSHIP_INVALID: "
                    f"{module_id} is missing from semantic owner {requirement_id}."
                )


def _validate_coverage(
    backend_modules: dict[str, dict[str, Any]],
    frontend_items: dict[str, dict[str, Any]],
    bindings: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    expected = set(backend_modules) | set(frontend_items)
    missing = sorted(expected - set(bindings))
    if missing:
        errors.append(
            f"ARC4302 CODE_BINDING_COVERAGE_INVALID: missing bindings for {missing}."
        )


def _validate_sources(
    output_root: Path,
    bindings: list[dict[str, Any]],
    type_bindings: list[dict[str, Any]],
    errors: list[str],
) -> None:
    cache: dict[str, str] = {}

    def source(relative: str) -> str | None:
        normalized = _safe_relative_path(relative)
        if normalized is None:
            errors.append(f"ARC4304 CODE_BINDING_FILE_INVALID: invalid path {relative!r}.")
            return None
        if normalized in cache:
            return cache[normalized]
        path = (output_root / Path(normalized)).resolve()
        if output_root != path and output_root not in path.parents:
            errors.append(f"ARC4304 CODE_BINDING_FILE_INVALID: path escapes workspace {relative!r}.")
            return None
        if not path.is_file():
            errors.append(f"ARC4304 CODE_BINDING_FILE_INVALID: source does not exist {relative!r}.")
            return None
        try:
            cache[normalized] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"ARC4304 CODE_BINDING_FILE_INVALID: cannot read {relative!r}: {exc}")
            return None
        return cache[normalized]

    for binding in bindings:
        module_id = str(binding.get("module_id", ""))
        content = source(str(binding.get("file", "")))
        if content is None:
            continue
        symbol = str(binding.get("symbol", ""))
        local_ui = binding.get('kind') in {'UI', 'UI_EVENT', 'UI_FUNC'} and binding.get('visibility') == 'local'
        present = _has_local_function(content, symbol) if local_ui else _has_named_export(content, symbol)
        if not present:
            surface = 'local function' if local_ui else 'export'
            errors.append(
                f"ARC4305 CODE_BINDING_SYMBOL_INVALID: {module_id} {surface} {symbol!r} is missing."
            )
    for binding in type_bindings:
        type_id = str(binding.get("type_id", ""))
        content = source(str(binding.get("file", "")))
        if content is None:
            continue
        symbol = str(binding.get("symbol", ""))
        if not _has_named_export(content, symbol):
            errors.append(
                f"ARC4307 TYPE_BINDING_SYMBOL_INVALID: {type_id} export {symbol!r} is missing."
            )


def _has_local_function(content: str, symbol: str) -> bool:
    if not symbol:
        return False
    # Generated internal UI renderers are top-level declarations, not imports
    # or occurrences in blueprint comments. Exported boundaries remain checked separately.
    return re.search(rf'^\s*(?:async\s+)?function\s+{re.escape(symbol)}\s*\(',
                     content, re.MULTILINE) is not None


def _has_named_export(content: str, symbol: str) -> bool:
    if not symbol:
        return False
    pattern = re.compile(
        rf"\bexport\s+(?:default\s+)?(?:async\s+)?"
        rf"(?:function|interface|type|const|class)\s+{re.escape(symbol)}\b"
    )
    return pattern.search(content) is not None


def _safe_relative_path(value: str) -> str | None:
    normalized = str(value).replace("\\", "/").strip().strip("/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or "." in path.parts or ".." in path.parts:
        return None
    if not normalized.endswith((".ts", ".tsx")):
        return None
    return normalized


def _deduplicate_type_bindings(
    rows: list[dict[str, Any]], errors: list[str]
) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        type_id = str(row.get("type_id", ""))
        if not type_id:
            errors.append("ARC4303 TYPE_BINDING_COVERAGE_INVALID: empty type id.")
            continue
        existing = result.get(type_id)
        if existing is not None and (
            existing.get("file") != row.get("file")
            or existing.get("symbol") != row.get("symbol")
        ):
            errors.append(
                f"ARC4303 TYPE_BINDING_COVERAGE_INVALID: conflicting binding for {type_id}."
            )
            continue
        if existing is None:
            result[type_id] = row
    return [result[key] for key in sorted(result)]


def _unique_bindings(
    rows: list[dict[str, Any]], errors: list[str]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        module_id = str(row.get("module_id", ""))
        if not module_id or module_id in result:
            errors.append(
                f"ARC4302 CODE_BINDING_COVERAGE_INVALID: invalid or duplicate binding {module_id!r}."
            )
            continue
        result[module_id] = row
    return result


def _index_rows(
    value: Any,
    key: str,
    label: str,
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        errors.append(f"ARC4301 CODE_BINDING_INPUT_INVALID: {label} must be a list.")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in value:
        row_id = str(row.get(key, "")).strip() if isinstance(row, dict) else ""
        if not row_id or row_id in result:
            errors.append(
                f"ARC4301 CODE_BINDING_INPUT_INVALID: invalid or duplicate {label} id {row_id!r}."
            )
            continue
        result[row_id] = row
    return result


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


__all__ = [
    "CODE_BINDING_READY",
    "CodeBindingLowerer",
    "CodeBindingResult",
    "CodeTargetResolver",
    "resolve_requirement_targets",
]
