"""Pass contracts derived from the authoritative backend TypedDict definitions."""
from __future__ import annotations

import types
from typing import Any, Literal, NotRequired, Required, Union, get_args, get_origin, get_type_hints, is_typeddict

from ..ir.backend import BackendIR, BackendModule, BackendTypeDefinition, APIModule, FunctionModule, DatabaseModule



def object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    definitions: dict[str, Any] = {}

    def hoist(value: Any) -> Any:
        if isinstance(value, dict):
            definitions.update(value.get('$defs', {}))
            return {key: hoist(item) for key, item in value.items() if key != '$defs'}
        if isinstance(value, list):
            return [hoist(item) for item in value]
        return value

    normalized = hoist(properties)
    return {"type": "object", "additionalProperties": False, "properties": normalized,
            "required": list(properties), "$defs": definitions}


def schema_for(annotation: Any) -> dict[str, Any]:
    """Generate recursive JSON schemas with shared TypedDict definitions."""
    definitions: dict[str, Any] = {}

    def convert(value: Any) -> dict[str, Any]:
        origin, args = get_origin(value), get_args(value)
        if origin in (NotRequired, Required):
            return convert(args[0])
        if origin is Literal:
            return {"enum": list(args)}
        if origin in (Union, types.UnionType):
            return {"anyOf": [convert(item) for item in args]}
        if origin is list:
            return {"type": "array", "items": convert(args[0])}
        if origin is dict:
            return {"type": "object", "additionalProperties": convert(args[1])}
        if is_typeddict(value):
            name = value.__name__
            if name not in definitions:
                definitions[name] = {}
                hints = get_type_hints(value, include_extras=True)
                definitions[name] = {"type": "object", "additionalProperties": False,
                    "properties": {key: convert(item) for key, item in hints.items()},
                    "required": [key for key, item in hints.items() if get_origin(item) is not NotRequired]}
            return {"$ref": f"#/$defs/{name}"}
        return {"type": {str: "string", bool: "boolean", int: "integer", float: "number", type(None): "null"}[value]}

    root = convert(annotation)
    if is_typeddict(annotation):
        root = dict(definitions[annotation.__name__])
    return {**root, "$defs": definitions}


IR_SCHEMA = schema_for(BackendIR)
MODULE_SCHEMA = schema_for(BackendModule)
LAYER_SCHEMAS = {}
for kind, module_type in (("API", APIModule), ("FUNC", FunctionModule), ("DB", DatabaseModule)):
    schema = object_schema({
        "new_modules": schema_for(list[module_type]),
        "new_types": schema_for(list[BackendTypeDefinition]),
        **({"resolved_calls": schema_for(dict[str, str])} if kind != "API" else {}),
        **({"api_ids": schema_for(list[str])} if kind == "API" else {}),
        "requested_module_ids": schema_for(list[str]),
        "requested_type_ids": schema_for(list[str]),
        **({"requested_tables": schema_for(list[str])} if kind == "DB" else {}),
    })
    schema["required"] = ["new_modules", *(["api_ids"] if kind == "API" else [])]
    LAYER_SCHEMAS[kind] = schema

LAYER_COMMON = """Design only the current backend layer. Return the JSON delta defined by its schema.
Use exact loaded IDs when reusing contracts; do not regenerate existing modules or alter upstream interfaces.
New IDs use KIND.Name; the compiler qualifies them with the current requirement ID.
Define new_types only for named types referenced by new modules; omit empty optional fields.
Fields preserve semantic_id and compatible types. Use snake_case parameter names. Trusted identity/time
belong in context. spec describes observable behavior, errors and guarantees, not implementation code.
Calls use module_id and purpose describing responsibility, inputs, results and failure guarantees.
Reuse loaded modules by referencing their IDs directly. Catalogs are summaries: request unloaded
contracts with requested_module_ids/requested_type_ids and return no design until they are loaded.
"""
LAYER_PROMPTS = {
    "API": LAYER_COMMON + """
Task: identify all server behavior required by the description/scenarios and design its REST interfaces.
Persistence, account uniqueness, authentication and authoritative queries require server APIs even
when the requirement emphasizes a page or UI. Only entirely client-local behavior needs no API.
Define path, method, authentication, request sources, response fields, status and observable errors.
GET has no body. Use :parameter path syntax and application/json; empty responses use status 204.
New APIs delegate business work to loaded FUNC IDs or forward FUNC.Name calls. Do not design FUNC/DB
modules or database columns. api_ids selects the new/existing API entrypoints used by this requirement,
including relevant setup, action and observation interfaces. Return empty lists for client-only tasks.
""",
    "FUNC": LAYER_COMMON + """
Task: fulfill the unresolved FUNC calls in upstream_modules. Their call purposes define the work.
Define ordered parameters and returns ([] means void), validation, authorization, business rules
and orchestration. API transport decisions are fixed and must not be redesigned.
A FUNC may call loaded FUNC/DB contracts, new FUNCs in this reply, or forward DB.Name leaves.
Describe persistence responsibilities in call purposes; do not design DB modules or SQL algorithms.
Define each unresolved FUNC ID or use resolved_calls {pending_ID:loaded_ID} to reuse a matching contract.
All FUNC calls must resolve by the end of this layer. Request missing contracts before relying on them.
""",
    "DB": LAYER_COMMON + """
Task: fulfill the unresolved DB calls in upstream_modules using the loaded schema_slice.
Define ordered parameters/returns, precise data_access and persistence guarantees, including absence,
atomicity and concurrency where needed. Use exact table/column names. DB modules are leaves with no calls.
Reuse only when complete behavior/interface matches; accessing the same table alone is insufficient.
Define each pending DB ID or map it to a loaded DB contract through resolved_calls.
Request missing schema tables through requested_tables. Do not redesign APIs or business orchestration.
""",
}

from ..ir.integration import PendingCapability
PARENT_DESIGN_SCHEMA = object_schema({
    "api_ids": schema_for(list[str]),
    "pending_capabilities": schema_for(list[PendingCapability]),
})
PARENT_DESIGN_SCHEMA["required"] = ["api_ids"]
PARENT_DESIGN_PROMPT = """Task: integrate the parent requirement using completed descendant APIs.
Select exact api_ids from api_contracts that support its behavior. Preserve existing interfaces;
do not create modules, types or calls. Child requirements explain completed functional scope.
Report missing parent server behavior through pending_capabilities; use requirement.id + '.capability.'
for new gap IDs and function_ids:[]. Preserve existing gap IDs for matching capabilities.
Omit pending_capabilities when there are no new gaps. Do not duplicate child APIs.
"""
