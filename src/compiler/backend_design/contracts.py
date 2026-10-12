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
Put all validation, error, persistence and concurrency guarantees in spec. Do not return separate
acceptance_criteria, guarantees, Given/When/Then records or duplicated scenario metadata.
Calls use module_id and purpose describing responsibility, inputs, results and failure guarantees.
Reuse loaded modules by referencing their IDs directly. Catalogs are summaries: request unloaded
contracts with requested_module_ids/requested_type_ids and return no design until they are loaded.
There are TWO mutually exclusive reply modes:
1. Load existing context: new_modules:[], omit new_types/resolved_calls, and API uses api_ids:[].
Request only exact unloaded IDs from the supplied catalogs/schema_index. Loaded modules, upstream
contracts, unknown IDs and forward calls are not context requests. Omit empty request fields.
2. Design: return new_modules/new_types and selected api_ids or resolved_calls as appropriate;
omit all requested_* fields. A required module/type that does not exist must be newly designed.
Forward FUNC/DB calls express work for a later layer; never request them as if they already exist.
API layer creates API modules with forward FUNC.Name calls; FUNC layer implements those calls
and may introduce forward DB.Name calls; DB layer implements those persistence leaves.
If validation_feedback reports invalid requests, copy an exact catalog ID or remove the request
and design the missing responsibility in its proper layer. Do not repeat an invented request ID.
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
Static forms, navigation links, labels, layout and client-only controls do not require an API to
describe their configuration. Inherited constraints apply only to the current behavior; they do
not require implementing every ancestor feature in this leaf.
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
Only schema_slice/schema_index tables exist. A guessed session/authenticated_session table is not
loadable. Never invent tables/columns or use another table as an unrelated substitute. Explain a
missing storage responsibility in spec using the supported runtime/contracts where applicable.
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
Report a gap only for explicit parent server behavior requiring authoritative data, persistence,
authentication or business operations that the supplied APIs cannot support. Client-rendered
search inputs, navigation, headers, dashboard controls, labels and layouts do not need backend
configuration APIs. Evaluate underlying server data/actions separately from those visual controls.
The absence of an API returning a control does not establish a backend capability gap.
Scope cross-cutting persistence statements to the completed child capabilities; do not claim that
all metadata/preferences lack persistence when child CRUD/read APIs already provide it.
A source explicitly leaving ordering, tie-breaking, eviction or another policy unspecified creates
no new requirement and no missing capability. Do not invent policy or report its absence as a gap.
"""
