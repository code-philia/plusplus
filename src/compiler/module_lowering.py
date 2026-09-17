from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Any


MODULE_MANIFEST_SCHEMA_VERSION = 1
MODULE_KINDS = {"DB", "FUNC", "API"}
MODULE_STATUSES = {
    "DB": "DB_MODULES_GENERATED",
    "FUNC": "FUNC_MODULES_GENERATED",
    "API": "API_MODULES_GENERATED",
}
MODULE_ROOTS = {
    "DB": "backend/src/db/repositories/",
    "FUNC": "backend/src/functions/",
    "API": "backend/src/api/",
}
BARREL_PATHS = {
    "DB": "backend/src/db/repositories/index.ts",
    "FUNC": "backend/src/functions/index.ts",
    "API": "backend/src/api/index.ts",
}
ERROR_CODES = {"DB": "ARC350", "FUNC": "ARC360", "API": "ARC370"}


@dataclass(slots=True)
class ModuleSkeletonResult:
    manifest: dict[str, Any]
    sources: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class ModuleSkeletonLowerer:
    """Generate frozen module surfaces and editable implementation regions."""

    def lower(
        self,
        module_kind: str,
        design_ir: dict[str, Any],
        symbol_registry: dict[str, Any],
        file_registry: dict[str, Any],
    ) -> ModuleSkeletonResult:
        kind = str(module_kind).upper()
        if kind not in MODULE_KINDS:
            return ModuleSkeletonResult(
                manifest={
                    "schema_version": MODULE_MANIFEST_SCHEMA_VERSION,
                    "status": "MODULE_SKELETON_GENERATION_FAILED",
                    "module_kind": kind,
                    "modules": [],
                    "generated_files": [],
                },
                errors=[f"ARC3501 MODULE_KIND_INVALID: unsupported module kind {kind!r}."],
            )

        prefix = ERROR_CODES[kind]
        errors: list[str] = []
        modules = _index_modules(design_ir, errors, prefix)
        symbols = _index_symbols(symbol_registry, errors, prefix)
        bindings = _index_bindings(symbol_registry, errors, prefix)
        locations = _index_locations(file_registry, errors, prefix)
        planned_files = _planned_files(file_registry, errors, prefix)
        _validate_registry_statuses(symbol_registry, file_registry, errors, prefix)

        selected = {
            module_id: module
            for module_id, module in modules.items()
            if module.get("kind") == kind
        }
        sources: dict[str, str] = {}
        manifest_modules: list[dict[str, Any]] = []

        required_barrel = BARREL_PATHS[kind]
        if required_barrel not in planned_files:
            errors.append(
                f"{prefix}2 FILE_REGISTRY_INVALID: missing planned barrel {required_barrel}."
            )
        if kind == "DB" and "backend/src/runtime/errors.ts" not in planned_files:
            errors.append(
                f"{prefix}2 FILE_REGISTRY_INVALID: missing planned runtime error file."
            )

        for module_id, module in sorted(selected.items()):
            symbol = symbols.get(module_id)
            binding = bindings.get(module_id)
            location = locations.get(module_id)
            if symbol is None or symbol.get("kind") != "MODULE":
                errors.append(f"{prefix}3 MODULE_SYMBOL_MISSING: {module_id}.")
                continue
            if binding is None or location is None:
                errors.append(f"{prefix}4 MODULE_PLAN_MISSING: binding or file missing for {module_id}.")
                continue
            path = str(location.get("path", ""))
            if not path.startswith(MODULE_ROOTS[kind]):
                errors.append(
                    f"{prefix}5 MODULE_PATH_INVALID: {module_id} is outside {MODULE_ROOTS[kind]}."
                )
                continue
            if path not in planned_files:
                errors.append(
                    f"{prefix}5 MODULE_PATH_INVALID: {module_id} path is not in File Registry."
                )
                continue
            if binding.get("function_symbol") != symbol.get("symbol"):
                errors.append(f"{prefix}6 MODULE_BINDING_INVALID: function mismatch for {module_id}.")
                continue

            input_contract = _contract_symbol(
                binding.get("input_contract_id"),
                symbols,
                locations,
                errors,
                prefix,
                module_id,
            )
            output_contract = _contract_symbol(
                binding.get("output_contract_id"),
                symbols,
                locations,
                errors,
                prefix,
                module_id,
            )
            dependency_rows = _dependency_rows(
                kind,
                module,
                modules,
                symbols,
                locations,
                path,
                errors,
                prefix,
            )
            database_rows = _database_rows(
                kind,
                module,
                symbols,
                locations,
                path,
                errors,
            )
            source, exports, import_plan = _render_module(
                kind,
                module,
                symbol,
                input_contract,
                output_contract,
                dependency_rows,
                database_rows,
                path,
            )
            sources[path] = source
            manifest_modules.append(
                {
                    "module_id": module_id,
                    "path": path,
                    "function_symbol": str(symbol["symbol"]),
                    "input_contract_id": binding.get("input_contract_id"),
                    "output_contract_id": binding.get("output_contract_id"),
                    "callees": list(module.get("callees", [])),
                    "database_entities": [row["entity"] for row in database_rows],
                    "exports": exports,
                    "imports": import_plan,
                    "implementation_region": {
                        "begin": "// ARC-IMPLEMENTATION-BEGIN",
                        "end": "// ARC-IMPLEMENTATION-END",
                    },
                }
            )

        expected = set(selected)
        generated = {row["module_id"] for row in manifest_modules}
        if generated != expected:
            errors.append(
                f"{prefix}7 MODULE_COVERAGE_INVALID: missing={sorted(expected - generated)}, "
                f"unknown={sorted(generated - expected)}."
            )

        if not errors:
            sources[required_barrel] = _render_module_barrel(
                required_barrel,
                manifest_modules,
            )
            if kind == "DB":
                sources["backend/src/runtime/errors.ts"] = _render_runtime_errors()

        status = MODULE_STATUSES[kind] if not errors else f"{kind}_MODULE_GENERATION_FAILED"
        manifest = {
            "schema_version": MODULE_MANIFEST_SCHEMA_VERSION,
            "status": status,
            "module_kind": kind,
            "modules": manifest_modules,
            "planned_files": sorted(sources),
            "generated_files": [] if errors else sorted(sources),
        }
        return ModuleSkeletonResult(
            manifest=manifest,
            sources={} if errors else sources,
            errors=list(dict.fromkeys(errors)),
        )


def _index_modules(
    design_ir: Any,
    errors: list[str],
    prefix: str,
) -> dict[str, dict[str, Any]]:
    rows = design_ir.get("modules", []) if isinstance(design_ir, dict) else []
    if not isinstance(rows, list):
        errors.append(f"{prefix}1 DESIGN_IR_INVALID: modules must be a list.")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        module_id = str(row.get("id", "")).strip() if isinstance(row, dict) else ""
        kind = str(row.get("kind", "")).strip() if isinstance(row, dict) else ""
        if not module_id or module_id in result or kind not in MODULE_KINDS:
            errors.append(f"{prefix}1 DESIGN_IR_INVALID: invalid or duplicate module {module_id!r}.")
            continue
        result[module_id] = row
    return result


def _index_symbols(
    registry: Any,
    errors: list[str],
    prefix: str,
) -> dict[str, dict[str, Any]]:
    rows = registry.get("symbols", []) if isinstance(registry, dict) else []
    if not isinstance(rows, list):
        errors.append(f"{prefix}2 SYMBOL_REGISTRY_INVALID: symbols must be a list.")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol_id = str(row.get("id", "")).strip() if isinstance(row, dict) else ""
        symbol_name = str(row.get("symbol", "")).strip() if isinstance(row, dict) else ""
        if not symbol_id or not symbol_name or symbol_id in result:
            errors.append(f"{prefix}2 SYMBOL_REGISTRY_INVALID: invalid symbol {symbol_id!r}.")
            continue
        result[symbol_id] = row
    return result


def _index_bindings(
    registry: Any,
    errors: list[str],
    prefix: str,
) -> dict[str, dict[str, Any]]:
    rows = registry.get("module_bindings", []) if isinstance(registry, dict) else []
    if not isinstance(rows, list):
        errors.append(f"{prefix}2 SYMBOL_REGISTRY_INVALID: bindings must be a list.")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        module_id = str(row.get("module_id", "")).strip() if isinstance(row, dict) else ""
        if not module_id or module_id in result:
            errors.append(f"{prefix}2 SYMBOL_REGISTRY_INVALID: invalid binding {module_id!r}.")
            continue
        result[module_id] = row
    return result


def _index_locations(
    registry: Any,
    errors: list[str],
    prefix: str,
) -> dict[str, dict[str, Any]]:
    rows = registry.get("symbol_locations", []) if isinstance(registry, dict) else []
    if not isinstance(rows, list):
        errors.append(f"{prefix}2 FILE_REGISTRY_INVALID: symbol_locations must be a list.")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol_id = str(row.get("symbol_id", "")).strip() if isinstance(row, dict) else ""
        path = str(row.get("path", "")).strip() if isinstance(row, dict) else ""
        if not symbol_id or not path or symbol_id in result:
            errors.append(f"{prefix}2 FILE_REGISTRY_INVALID: invalid location {symbol_id!r}.")
            continue
        result[symbol_id] = row
    return result


def _planned_files(registry: Any, errors: list[str], prefix: str) -> set[str]:
    rows = registry.get("files", []) if isinstance(registry, dict) else []
    if not isinstance(rows, list):
        errors.append(f"{prefix}2 FILE_REGISTRY_INVALID: files must be a list.")
        return set()
    return {
        str(row.get("path", ""))
        for row in rows
        if isinstance(row, dict) and row.get("path")
    }


def _validate_registry_statuses(
    symbol_registry: Any,
    file_registry: Any,
    errors: list[str],
    prefix: str,
) -> None:
    if not isinstance(symbol_registry, dict) or symbol_registry.get("status") != "SYMBOLS_PLANNED":
        errors.append(f"{prefix}2 SYMBOL_REGISTRY_INVALID: symbols are not planned.")
    if not isinstance(file_registry, dict) or file_registry.get("status") != "FILES_PLANNED":
        errors.append(f"{prefix}2 FILE_REGISTRY_INVALID: files are not planned.")


def _contract_symbol(
    contract_id: Any,
    symbols: dict[str, dict[str, Any]],
    locations: dict[str, dict[str, Any]],
    errors: list[str],
    prefix: str,
    module_id: str,
) -> dict[str, Any] | None:
    if contract_id is None:
        return None
    symbol = symbols.get(str(contract_id))
    if symbol is None or symbol.get("kind") != "DATA_CONTRACT":
        errors.append(
            f"{prefix}8 CONTRACT_MISSING: {module_id} references {contract_id!r}."
        )
        return None
    location = locations.get(str(contract_id))
    if location is None:
        errors.append(
            f"{prefix}8 CONTRACT_MISSING: {module_id} contract {contract_id!r} has no file."
        )
        return None
    result = dict(symbol)
    result["source_path"] = str(location["path"])
    return result


def _dependency_rows(
    kind: str,
    module: dict[str, Any],
    modules: dict[str, dict[str, Any]],
    symbols: dict[str, dict[str, Any]],
    locations: dict[str, dict[str, Any]],
    current_path: str,
    errors: list[str],
    prefix: str,
) -> list[dict[str, str]]:
    callees = module.get("callees", [])
    if not isinstance(callees, list):
        errors.append(f"{prefix}9 MODULE_EDGE_INVALID: callees must be a list for {module.get('id')}.")
        return []
    allowed = {"DB": set(), "FUNC": {"FUNC", "DB"}, "API": {"FUNC"}}[kind]
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for callee_id in callees:
        callee_key = str(callee_id)
        callee = modules.get(callee_key)
        symbol = symbols.get(callee_key)
        location = locations.get(callee_key)
        if callee_key in seen:
            errors.append(f"{prefix}9 MODULE_EDGE_INVALID: duplicate callee {callee_key}.")
            continue
        seen.add(callee_key)
        if (
            callee is None
            or symbol is None
            or symbol.get("kind") != "MODULE"
            or location is None
        ):
            errors.append(f"{prefix}9 MODULE_EDGE_INVALID: unknown callee {callee_key}.")
            continue
        callee_kind = str(callee.get("kind", ""))
        if callee_kind not in allowed:
            errors.append(
                f"{prefix}9 MODULE_EDGE_INVALID: {kind} cannot call {callee_kind} ({callee_key})."
            )
            continue
        target_path = str(location["path"])
        rows.append(
            {
                "module_id": callee_key,
                "symbol": str(symbol["symbol"]),
                "path": target_path,
                "specifier": _relative_specifier(current_path, target_path),
            }
        )
    return rows


def _database_rows(
    kind: str,
    module: dict[str, Any],
    symbols: dict[str, dict[str, Any]],
    locations: dict[str, dict[str, Any]],
    current_path: str,
    errors: list[str],
) -> list[dict[str, str]]:
    if kind != "DB":
        return []
    effects = module.get("effects", [])
    if not isinstance(effects, list):
        errors.append(f"ARC3510 DB_EFFECT_INVALID: effects must be a list for {module.get('id')}.")
        return []
    entity_tables = {
        str(symbol.get("entity", "")): symbol
        for symbol in symbols.values()
        if symbol.get("kind") == "ENTITY_TABLE"
    }
    targets: list[str] = []
    for effect in effects:
        operation = str(effect.get("operation", "")) if isinstance(effect, dict) else ""
        target = str(effect.get("target", "")) if isinstance(effect, dict) else ""
        if operation not in {"READ", "CREATE", "UPDATE", "DELETE"} or not target:
            errors.append(
                f"ARC3510 DB_EFFECT_INVALID: DB module {module.get('id')} has non-database effect."
            )
            continue
        if target not in targets:
            targets.append(target)
    rows: list[dict[str, str]] = []
    for target in sorted(targets):
        symbol = entity_tables.get(target)
        if symbol is None:
            errors.append(
                f"ARC3510 DB_EFFECT_INVALID: {module.get('id')} targets unknown entity {target!r}."
            )
            continue
        location = locations.get(str(symbol["id"]))
        if location is None:
            errors.append(f"ARC3510 DB_EFFECT_INVALID: no table file for {target}.")
            continue
        target_path = str(location["path"])
        rows.append(
            {
                "entity": target,
                "symbol": str(symbol["symbol"]),
                "path": target_path,
                "specifier": _relative_specifier(current_path, target_path),
            }
        )
    return rows


def _render_module(
    kind: str,
    module: dict[str, Any],
    function_symbol: dict[str, Any],
    input_contract: dict[str, Any] | None,
    output_contract: dict[str, Any] | None,
    dependencies: list[dict[str, str]],
    database_rows: list[dict[str, str]],
    current_path: str,
) -> tuple[str, list[str], list[dict[str, Any]]]:
    function_name = str(function_symbol["symbol"])
    input_name = str(input_contract["symbol"]) if input_contract else None
    output_name = str(output_contract["symbol"]) if output_contract else None
    dependency_name = f"{function_name}Dependencies"
    exports = [function_name]
    if kind in {"FUNC", "API"}:
        exports.append(dependency_name)

    imports: list[dict[str, Any]] = []
    for contract, symbol in (
        (input_contract, input_name),
        (output_contract, output_name),
    ):
        if contract is not None and symbol is not None:
            imports.append(
                {
                    "symbol": symbol,
                    "from": str(contract["source_path"]),
                    "specifier": "@arc/shared",
                    "type_only": True,
                }
            )
    if kind == "API":
        imports.extend(
            {
                "symbol": symbol,
                "from": "express",
                "specifier": "express",
                "type_only": True,
            }
            for symbol in ("Request", "Response")
        )
    runtime_path = "backend/src/runtime/errors.ts"
    imports.append(
        {
            "symbol": "NotImplementedError",
            "from": runtime_path,
            "specifier": _relative_specifier(current_path, runtime_path),
            "type_only": False,
        }
    )
    for row in [*dependencies, *database_rows]:
        imports.append(
            {
                "symbol": row["symbol"],
                "from": row["path"],
                "specifier": row["specifier"],
                "type_only": False,
            }
        )

    lines = ["// Generated by ARC. Edit only the implementation region."]
    for type_only, specifier, names in _group_imports(imports):
        keyword = "import type" if type_only else "import"
        lines.append(f'{keyword} {{ {", ".join(names)} }} from "{specifier}";')
    lines.extend(
        [
            "",
            "/**",
            f" * @arc-module {module.get('id')}",
            f" * @arc-owner {function_symbol.get('owner_requirement', '')}",
            f" * @arc-kind {kind}",
            " * @arc-generated skeleton",
            " */",
        ]
    )

    if kind in {"FUNC", "API"}:
        dependency_symbols = ", ".join(row["symbol"] for row in dependencies)
        dependency_object = f"{{ {dependency_symbols} }}" if dependency_symbols else "{}"
        lines.extend(
            [
                f"export const {dependency_name} = {dependency_object} as const;",
                "",
            ]
        )
    elif database_rows:
        database_symbols = ", ".join(row["symbol"] for row in database_rows)
        lines.extend([f"const database = {{ {database_symbols} }} as const;", "void database;", ""])

    if kind == "API":
        request_body = input_name or "unknown"
        response_body = output_name or "unknown"
        lines.extend(
            [
                f"export async function {function_name}(",
                f"  req: Request<Record<string, string>, {response_body}, {request_body}>,",
                f"  res: Response<{response_body}>,",
                "): Promise<void> {",
                "  void req;",
                "  void res;",
            ]
        )
    else:
        parameter = f"input: {input_name}" if input_name else ""
        return_type = output_name or "void"
        lines.extend(
            [
                f"export async function {function_name}({parameter}): Promise<{return_type}> {{",
            ]
        )
        if input_name:
            lines.append("  void input;")
    lines.extend(
        [
            "  // ARC-IMPLEMENTATION-BEGIN",
            f'  throw new NotImplementedError("{module.get("id")}");',
            "  // ARC-IMPLEMENTATION-END",
            "}",
            "",
        ]
    )
    return "\n".join(lines), exports, imports


def _group_imports(
    imports: list[dict[str, Any]],
) -> list[tuple[bool, str, list[str]]]:
    grouped: dict[tuple[bool, str], set[str]] = {}
    for row in imports:
        key = (bool(row["type_only"]), str(row["specifier"]))
        grouped.setdefault(key, set()).add(str(row["symbol"]))
    return [
        (type_only, specifier, sorted(names))
        for (type_only, specifier), names in sorted(
            grouped.items(),
            key=lambda item: (item[0][1], item[0][0]),
        )
    ]


def _render_module_barrel(
    barrel_path: str,
    modules: list[dict[str, Any]],
) -> str:
    lines = ["// Generated by ARC. Do not edit."]
    for module in sorted(modules, key=lambda item: item["module_id"]):
        specifier = _relative_specifier(barrel_path, str(module["path"]))
        exports = ", ".join(module["exports"])
        lines.append(f'export {{ {exports} }} from "{specifier}";')
    return "\n".join(lines) + "\n"


def _render_runtime_errors() -> str:
    return (
        "// Generated by ARC. Do not edit.\n"
        "export class NotImplementedError extends Error {\n"
        "  constructor(moduleId: string) {\n"
        '    super(`Module ${moduleId} is not implemented`);\n'
        '    this.name = "NotImplementedError";\n'
        "  }\n"
        "}\n"
    )


def _relative_specifier(current_path: str, target_path: str) -> str:
    relative = posixpath.relpath(target_path, posixpath.dirname(current_path))
    if not relative.startswith("."):
        relative = f"./{relative}"
    return re.sub(r"\.ts$", ".js", relative)
