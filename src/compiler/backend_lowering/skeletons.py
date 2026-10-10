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

RUNTIME_ERRORS_PATH = "backend/src/runtime/errors.ts"
RUNTIME_IDS_PATH = "backend/src/runtime/ids.ts"
RUNTIME_CLOCK_PATH = "backend/src/runtime/clock.ts"
RUNTIME_FILES = (
    RUNTIME_ERRORS_PATH,
    RUNTIME_IDS_PATH,
    RUNTIME_CLOCK_PATH,
)

# Every backend module receives the same cross-cutting runtime. Owning these here
# keeps identity, time and error handling out of the implementation agent's
# hands: it can use them, it cannot reinvent them.
RUNTIME_IMPORTS: tuple[tuple[str, str], ...] = (
    ("NotImplementedError", RUNTIME_ERRORS_PATH),
    ("HttpError", RUNTIME_ERRORS_PATH),
    ("newId", RUNTIME_IDS_PATH),
    ("now", RUNTIME_CLOCK_PATH),
    ("nowIso", RUNTIME_CLOCK_PATH),
)

# Drizzle predicate/ordering helpers a repository realistically needs. Importing
# them unconditionally removes the single most common "I cannot express this
# query inside my region" failure.
DRIZZLE_OPERATORS: tuple[str, ...] = (
    "and",
    "asc",
    "desc",
    "eq",
    "gt",
    "gte",
    "inArray",
    "isNotNull",
    "isNull",
    "like",
    "lt",
    "lte",
    "ne",
    "or",
    "sql",
)


@dataclass(slots=True)
class ModuleSkeletonResult:
    manifest: dict[str, Any]
    sources: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class ModuleSkeletonLowerer:
    """Generate frozen module surfaces and editable source files."""

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
        missing_runtime = sorted(set(RUNTIME_FILES) - planned_files)
        if missing_runtime:
            errors.append(
                f"{prefix}2 FILE_REGISTRY_INVALID: missing planned runtime files {missing_runtime}."
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
                _contract_symbol(binding.get("context_contract_id"), symbols, locations, errors, prefix, module_id),
            )
            sources[path] = source
            manifest_modules.append(
                {
                    "module_id": module_id,
                    "path": path,
                    "function_symbol": str(symbol["symbol"]),
                    "input_contract_id": binding.get("input_contract_id"),
                    "output_contract_id": binding.get("output_contract_id"),
                    "context_contract_id": binding.get("context_contract_id"),
                    "requirement_ids": list(module.get("requirement_ids", [])),
                    "entrypoint": module.get("entrypoint"),
                    "calls": module.get("calls", []),
                    "data_access": module.get("data_access", []),
                    "callees": list(module.get("callees", [])),
                    "database_entities": [row["entity"] for row in database_rows],
                    "exports": exports,
                    "imports": import_plan,
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
        if (not module_id or module_id in result or kind not in MODULE_KINDS
                or not isinstance(row.get("backend_module"), dict)):
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
    entity_tables = {
        str(symbol.get("entity", "")): symbol
        for symbol in symbols.values()
        if symbol.get("kind") == "ENTITY_TABLE"
    }
    targets: list[str] = []
    for effect in effects:
        target = str(effect.get("target", "")) if isinstance(effect, dict) else ""
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
    context_contract: dict[str, Any] | None = None,
) -> tuple[str, list[str], list[dict[str, Any]]]:
    from .modules import render_blueprint_module
    return render_blueprint_module(kind, module, function_symbol, input_contract,
                                   output_contract, context_contract, dependencies,
                                   database_rows, current_path)


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
    return "\n".join(
        [
            "// Generated by ARC. Do not edit.",
            'import type { ApiErrorBody, JsonValue } from "@arc/shared";',
            "",
            "export class NotImplementedError extends Error {",
            "  constructor(moduleId: string) {",
            "    super(`Module ${moduleId} is not implemented`);",
            '    this.name = "NotImplementedError";',
            "  }",
            "}",
            "",
            "/**",
            " * The one way a module reports a domain failure. Throw it from any DB, FUNC or",
            " * API implementation region; the generated API wrapper turns it into the shared",
            " * ApiErrorBody envelope with the right status code.",
            " */",
            "export class HttpError extends Error {",
            "  readonly status: number;",
            "  readonly code: string;",
            "  readonly details: JsonValue | undefined;",
            "",
            "  constructor(",
            "    status: number,",
            "    code: string,",
            "    message: string,",
            "    details?: JsonValue,",
            "  ) {",
            "    super(message);",
            '    this.name = "HttpError";',
            "    this.status = status;",
            "    this.code = code;",
            "    this.details = details;",
            "  }",
            "",
            "  static badRequest(message: string, details?: JsonValue): HttpError {",
            '    return new HttpError(400, "BAD_REQUEST", message, details);',
            "  }",
            "",
            "  static unauthorized(message: string, details?: JsonValue): HttpError {",
            '    return new HttpError(401, "UNAUTHORIZED", message, details);',
            "  }",
            "",
            "  static forbidden(message: string, details?: JsonValue): HttpError {",
            '    return new HttpError(403, "FORBIDDEN", message, details);',
            "  }",
            "",
            "  static notFound(message: string, details?: JsonValue): HttpError {",
            '    return new HttpError(404, "NOT_FOUND", message, details);',
            "  }",
            "",
            "  static conflict(message: string, details?: JsonValue): HttpError {",
            '    return new HttpError(409, "CONFLICT", message, details);',
            "  }",
            "",
            "  static unprocessable(message: string, details?: JsonValue): HttpError {",
            '    return new HttpError(422, "UNPROCESSABLE", message, details);',
            "  }",
            "}",
            "",
            "export function isHttpError(value: unknown): value is HttpError {",
            "  return value instanceof HttpError;",
            "}",
            "",
            "/** Normalize any thrown value into the shared failure envelope. */",
            "export function toErrorBody(error: unknown): {",
            "  status: number;",
            "  body: ApiErrorBody;",
            "} {",
            "  if (isHttpError(error)) {",
            "    return {",
            "      status: error.status,",
            "      body: {",
            "        error: {",
            "          code: error.code,",
            "          message: error.message,",
            "          ...(error.details === undefined ? {} : { details: error.details }),",
            "        },",
            "      },",
            "    };",
            "  }",
            "  if (error instanceof NotImplementedError) {",
            "    return {",
            "      status: 501,",
            '      body: { error: { code: "NOT_IMPLEMENTED", message: error.message } },',
            "    };",
            "  }",
            "  const message = error instanceof Error ? error.message : String(error);",
            "  return {",
            "    status: 500,",
            '    body: { error: { code: "INTERNAL_ERROR", message } },',
            "  };",
            "}",
            "",
            "/**",
            " * Structural view of the response object: express Response satisfies it without",
            " * this file importing express.",
            " */",
            "export interface ErrorResponseTarget {",
            "  headersSent: boolean;",
            "  status(code: number): { json(body: ApiErrorBody): unknown };",
            "}",
            "",
            "/** Write one failure envelope. Safe to call with any thrown value. */",
            "export function sendError(response: ErrorResponseTarget, error: unknown): void {",
            "  if (response.headersSent) return;",
            "  const { status, body } = toErrorBody(error);",
            "  response.status(status).json(body);",
            "}",
            "",
        ]
    )


def _relative_specifier(current_path: str, target_path: str) -> str:
    relative = posixpath.relpath(target_path, posixpath.dirname(current_path))
    if not relative.startswith("."):
        relative = f"./{relative}"
    return re.sub(r"\.ts$", ".js", relative)
