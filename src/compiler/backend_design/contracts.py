"""Pass contracts derived from the authoritative backend TypedDict definitions."""
from __future__ import annotations

import types
from typing import Any, Literal, NotRequired, Required, Union, get_args, get_origin, get_type_hints, is_typeddict

from ..ir.backend import BackendIR, BackendModule, BackendTypeDefinition

FIELD_NAME_PATTERN = r'^[a-z][a-z0-9_]*$'
SEMANTIC_ID_PATTERN = r'^[a-z][a-z0-9_.]*$'


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
                if 'semantic_id' in hints and 'name' in hints:
                    definitions[name]['properties']['name']['pattern'] = FIELD_NAME_PATTERN
                    definitions[name]['properties']['semantic_id']['pattern'] = SEMANTIC_ID_PATTERN
            return {"$ref": f"#/$defs/{name}"}
        return {"type": {str: "string", bool: "boolean", int: "integer", float: "number", type(None): "null"}[value]}

    root = convert(annotation)
    if is_typeddict(annotation):
        root = dict(definitions[annotation.__name__])
    return {**root, "$defs": definitions}


IR_SCHEMA = schema_for(BackendIR)
MODULE_SCHEMA = schema_for(BackendModule)
ANALYSIS_SCHEMA = object_schema({
    "intent": {"type": "string"}, "server_state": {"type": "boolean"},
    "capabilities": {"type": "array", "items": object_schema({
        "purpose": {"type": "string"},
        "role": {"enum": ["setup", "action", "observation"]},
        "scenario_ids": schema_for(list[str]),
        "reuse_module_ids": schema_for(list[str]),
        "missing_behavior": {"type": "string"},
    })},
    "requested_tables": schema_for(list[str]),
    "requested_module_ids": schema_for(list[str]),
    "requested_type_ids": schema_for(list[str]),
})
ARCHITECTURE_SCHEMA = object_schema({
    "new_modules": {"type": "array", "items": MODULE_SCHEMA},
    "new_types": schema_for(list[BackendTypeDefinition]),
    "reuse_module_ids": schema_for(list[str]),
    "api_ids": schema_for(list[str]),
})

COMMON = """Design complete backend modules from the supplied requirement and accepted Schema IR.
Return JSON only using exactly the supplied schema. Do not audit database enforcement.
Shared module keys are id, kind, requirement_ids, spec; optional context, calls and
acceptance_criteria describe trusted values, collaboration and scenario guarantees.
API modules directly contain path (REST resource path, Express :parameter syntax), method
(GET/POST/PUT/PATCH/DELETE), authentication (none/required/optional), request field list,
response field list, status_code and content_type. No entrypoint object.
Each request field additionally declares source: path/query/header/body. GET requests use
query/path/header, not body. Response fields define the JSON response object; [] means no body.
Use JSON content_type application/json and status_code 204 for no-body responses.
FUNC and DB modules contain parameters (ordered parameter fields) and returns (return-object
fields; [] means void). Every parameter and return field references a scalar/array/named type.
Only DB modules declare data_access using exact table and column names. DB modules are leaves.
Each field has semantic_id, name, type, required, nullable, description. Names use snake_case;
semantic IDs use lowercase dotted identifiers. Preserve semantic IDs and compatible types.
Type references: scalar {kind:scalar,name:string}, array {kind:array,items:...},
named {kind:named,type_id:type.example}. Named types must exist in types; no recursive chains.
Calls use id, module_id, purpose, optional condition and after (earlier local call IDs).
Allowed calls: API -> FUNC, FUNC -> FUNC/DB. No cycles. Avoid unused modules.
Describe all required validation, authorization, atomicity and observable failure behavior in spec.
Optional acceptance_criteria use id/given/when/then/scenario_ids, referring only to supplied scenarios.
Runtime identity/time values belong to context, never to untrusted request fields.
"""
ANALYSIS_PROMPT = """Analyze requirement intent AFTER inspecting the supplied reuse_context.
Return JSON only using the supplied schema. This is discovery, not a second interface design.
The requirement and its scenarios define behavior; existing module contracts define their actual
interfaces, types, routes, authentication and storage effects. Read those contracts before planning.
Return intent, server_state, capabilities and optional context requests (empty lists when unnecessary).
For each relevant scenario operation distinguish setup (Given/preparation), action (current feature),
and observation (Then/reload/navigation/later visitor). Identify supporting capabilities without
assigning all setup behavior to the current feature or inventing unrelated features.
Each capability records purpose, role, supplied scenario_ids, reuse_module_ids and missing_behavior.
Use exact existing module IDs when their full behavior is suitable. missing_behavior is empty when
reuse suffices; otherwise describe only the behavior still needed. Client-local behavior needs no
backend capability. Do not invent input/output catalogs, type definitions, acceptance criteria or
DB operation lists here. Do not rewrap an existing API response or redefine its semantic fields.
reuse_context contains complete selected modules and types plus module/type catalogs. If an ID in
a catalog looks relevant but its contract is not loaded, request it through requested_module_ids or
requested_type_ids before deciding reuse. Requests load additional contracts and trigger reanalysis.
Request exact table names from schema_index through requested_tables when schema_slice lacks them.
Existing records are referenced by ID, not copied or regenerated. Missing backend behavior is design
work for the next phase, not a validation failure. Do not audit database enforcement.
"""
ARCHITECTURE_PROMPT = COMMON + """Design the final authoritative modules using requirement intent
and reuse_context. Analysis provides behavior/reuse guidance, not fixed interfaces or storage lists.
Return new_modules, new_types, reuse_module_ids and api_ids. New module IDs are local KIND.Name
(e.g. API.CancelOrder, FUNC.CancelOwnedOrder, DB.TryCancelOrder); the compiler qualifies them.
Use exact existing IDs for reused modules and named types. Never reproduce an existing record in
new_modules/new_types. Emit new_types only for genuinely new named types referenced by new modules,
including nested type dependencies; choose distinct descriptive IDs for genuinely different types.
Preserve existing semantic field identities and their actual types, including optionality/nullability.
Reuse APIs with their actual response shape (flat fields or wrapped object), method, path and auth;
do not redesign an existing API to match an independently imagined interface. Prefer the existing
API for an already supported REST endpoint. Reading the same table alone does not establish reuse.
For each analysis capability, reuse supplied behavior where sufficient and design only missing
behavior. Setup and observation capabilities can use existing API entry points unchanged.
Calls refer to local new IDs or exact existing IDs. Include reused API entries/direct callees in
reuse_module_ids; api_ids is the requirement's entry set. Every new module should be reachable.
API -> FUNC, FUNC -> FUNC/DB, DB leaves. DB data_access comes from the FINAL complete module
behavior, including supporting reads and writes; there is no analysis operation list to match.
Decide request/response, ordered parameters/returns, trusted context, named types and layer-specific
acceptance criteria here ONCE. Existing contracts and new_modules/new_types are the source of truth.
Retain exact original requirement rules, bounds and scenario observations, not just the intent summary.
Describe session transport, authorization, atomicity, errors and absence where relevant in specs.
Distribute guarantees by layer. Do not prescribe source paths, SQL/ORM algorithms or implementation.
Client-local requirements return empty new_modules/new_types/reuse_module_ids/api_ids.
"""
from ..ir.integration import PendingCapability
PARENT_DESIGN_SCHEMA = object_schema({
    'api_ids': schema_for(list[str]),
    'pending_capabilities': schema_for(list[PendingCapability]),
})
PARENT_DESIGN_PROMPT = COMMON + """Integrate the supplied parent requirement after descendants.
Return api_ids selecting existing available descendant APIs and pending_capabilities only.
Do not create/update modules or types. Missing server capabilities become pending_capabilities
with stable id prefixed requirement_id + '.capability.', requirement_ids, capability, reason,
and function_ids=[]. Preserve children and identify all missing parent behavior.
"""
