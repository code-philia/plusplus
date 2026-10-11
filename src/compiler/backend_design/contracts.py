"""Pass contracts derived from the authoritative backend TypedDict definitions."""
from __future__ import annotations

import types
import json
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
LAYER_SCHEMAS = {
    kind: object_schema({
        "new_modules": schema_for(list[module_type]),
        "new_types": schema_for(list[BackendTypeDefinition]),
        "reuse_module_ids": schema_for(list[str]),
        "resolved_calls": schema_for(dict[str, str]),
        **({"api_ids": schema_for(list[str])} if kind == "API" else {}),
        "requested_module_ids": schema_for(list[str]),
        "requested_type_ids": schema_for(list[str]),
        **({"requested_tables": schema_for(list[str])} if kind == "DB" else {}),
    }) for kind, module_type in (("API", APIModule), ("FUNC", FunctionModule), ("DB", DatabaseModule))
}

LAYER_COMMON = """Design backend modules from the supplied requirement using exactly the output schema.
Shared fields are id, kind, requirement_ids, spec; optional context holds trusted runtime values.
Fields use semantic_id, snake_case name, type, required, nullable and description. Preserve existing
semantic identities and compatible types. Types are scalar {kind:scalar,name:...}, array
{kind:array,items:...}, or named {kind:named,type_id:...}; named types must exist and cannot recurse.
Calls use id, module_id, purpose, optional condition and after (earlier local call IDs).
Allowed graph: API -> FUNC; FUNC -> FUNC/DB; DB is a leaf. No cycles or unused modules/types.
Optional acceptance_criteria use id/given/when/then/scenario_ids from supplied requirement scenarios.
Runtime identity/time belong to trusted context, never untrusted inputs. Do not audit DB enforcement.
Design only the current layer. Requirement analysis and module design happen
in this same call; do not produce a separate intent/capability blueprint.
Read the original requirement and scenarios, distinguish setup/action/observation, and reuse
existing behavior before creating modules. Client-local requirements need no backend modules.
Return new_modules, new_types, reuse_module_ids, resolved_calls and context requests.
Prefer local KIND.Name for new modules; already qualified IDs are also accepted. Copy existing IDs exactly. Never reproduce existing
modules/types or change an upstream contract. Define only named types referenced by your new modules.
Reuse only loaded contracts; catalogs are discovery summaries. To load additional contracts return
requested_module_ids/requested_type_ids (DB may also request tables), with all design outputs empty.
Do not request already loaded records. Otherwise all request lists must be empty.
upstream_modules contains the relevant accepted upper-layer contracts, including calls and optional acceptance_criteria.
pending_module_ids identifies unresolved callees; their full responsibilities appear ONCE in the calls
of upstream_modules. Define their requested local IDs, or map a pending ID to a loaded existing module
ID in resolved_calls and include it in reuse_module_ids. Return resolved_calls:{} otherwise.
Calls to NEW lower-layer modules use local IDs (FUNC.Name or DB.Name) and a detailed purpose describing
required behavior, inputs, expected output and failure guarantees. These forward references are resolved
in the next layer. Existing callees use exact loaded IDs. Existing contracts and types remain authoritative.
Do not invent new modules unrelated to the selected API call graph. Preserve requirement bounds,
authorization, errors and scenario observations; spec is behavior, not implementation code or SQL.
"""
LAYER_PROMPTS = {
    "API": LAYER_COMMON + """Design API modules only. Decide paths, methods, authentication, request sources,
response shape/status and observable failures once. api_ids selects this requirement's existing/new APIs.
Use path with Express :parameter syntax, method GET/POST/PUT/PATCH/DELETE, authentication
none/required/optional, request/response field lists, status_code and application/json content_type.
Request fields declare source path/query/header/body; GET has no body. Empty response uses status 204.
Use existing APIs unchanged for supported setup/observation/action behavior. New APIs delegate business
responsibilities to existing FUNC contracts or forward FUNC IDs; describe the delegation in calls.
Do not design FUNC/DB modules, persistence operations or database columns. Preserve existing endpoints.
""",
    "FUNC": LAYER_COMMON + """Design FUNC modules only, fulfilling the accepted API contracts and pending callees.
parameters is an ordered field list; returns describes return-object fields ([] means void).
Decide ordered parameters/returns, trusted context, validation, authorization and business orchestration.
Reuse supplied FUNC/DB interfaces. New FUNCs may call other FUNCs created in this call or forward DB IDs.
All FUNC references must resolve by the end of this layer. Specify persistence responsibilities in call
purposes without defining DB modules or selecting SQL algorithms. Do not repeat API transport design.
""",
    "DB": LAYER_COMMON + """Design DB leaves only, fulfilling FUNC persistence responsibilities.
parameters is an ordered field list; returns describes return-object fields ([] means void).
Use exact names from loaded Schema IR tables/columns for data_access. Decide ordered parameters/returns,
read/create/update/delete effects and required atomicity/concurrency/absence guarantees. DB has no calls.
Reuse an existing DB module only when its complete behavior/interface matches; same table is insufficient.
Request missing tables through requested_tables before designing. Do not redesign API or business logic.
""",
}
from ..ir.integration import PendingCapability
PARENT_DESIGN_SCHEMA = object_schema({
    'api_ids': schema_for(list[str]),
    'pending_capabilities': schema_for(list[PendingCapability]),
})
PARENT_DESIGN_PROMPT = """Integrate a parent requirement after descendant design.
Return only api_ids and pending_capabilities. Select exact IDs from available_api_ids using
api_contracts and supplied types; preserve their paths, authentication, response shapes and behavior.
Do not design modules, types or calls. Parent rules are scope context, not a reason to duplicate child APIs.
Missing parent behavior becomes pending_capabilities with id prefixed requirement_id + '.capability.',
requirement_ids, capability, reason and function_ids=[]. Reuse supplied gap IDs for the same capability.
Output example (assume the API exists in available_api_ids):
{"api_ids":["REQ-1::API.ReadNote"],"pending_capabilities":[]}
"""

LAYER_EXAMPLES: dict[str, dict[str, Any]] = {}
for _kind in LAYER_SCHEMAS:
    LAYER_EXAMPLES[_kind] = {
        "new_modules": [], "new_types": [], "reuse_module_ids": [], "resolved_calls": {},
        "requested_module_ids": [], "requested_type_ids": [],
        **({"api_ids": ["API.ReadNote"]} if _kind == "API" else {}),
        **({"requested_tables": []} if _kind == "DB" else {}),
    }
_example_field = {"semantic_id": "note.body", "name": "body", "type": {"kind": "scalar", "name": "string"},
                  "required": True, "nullable": False, "description": "Note text."}
LAYER_EXAMPLES["API"]["new_modules"] = [{
    "id": "API.ReadNote", "kind": "API", "requirement_ids": ["REQ-1"],
    "spec": "Return the saved note text.", "path": "/api/note", "method": "GET",
    "authentication": "none", "request": [], "response": [_example_field],
    "status_code": 200, "content_type": "application/json", "calls": [{
        "id": "call.read", "module_id": "FUNC.ReadNote", "purpose": "Read the saved note; no parameters; return note.body."}],
}]
LAYER_EXAMPLES["FUNC"]["new_modules"] = [{
    "id": "FUNC.ReadNote", "kind": "FUNC", "requirement_ids": ["REQ-1"],
    "spec": "Retrieve the saved note; return empty text when none exists.",
    "parameters": [], "returns": [_example_field], "calls": [{
        "id": "call.load", "module_id": "DB.LoadNote", "purpose": "Load note.body; no parameters; return empty text when no note is stored."}],
}]
LAYER_EXAMPLES["DB"]["new_modules"] = [{
    "id": "DB.LoadNote", "kind": "DB", "requirement_ids": ["REQ-1"],
    "spec": "Read the saved note body or return empty text when absent.",
    "parameters": [], "returns": [_example_field],
    "data_access": [{"table": "note", "read": ["body"], "create": [], "update": [], "delete": False}],
}]
for _kind in LAYER_PROMPTS:
    LAYER_PROMPTS[_kind] += "\nComplete output example (illustrative requirement; DB assumes note.body exists in schema_slice; copy actual pending IDs and contracts):\n" + json.dumps(LAYER_EXAMPLES[_kind], ensure_ascii=False)

    _request_example = {
        "new_modules": [], "new_types": [], "reuse_module_ids": [], "resolved_calls": {},
        "requested_module_ids": [], "requested_type_ids": ["type.note"],
        **({"api_ids": []} if _kind == "API" else {}),
        **({"requested_tables": []} if _kind == "DB" else {}),
    }
    LAYER_PROMPTS[_kind] += "\nContext-request example (assume type.note is unloaded in type_catalog; return no design in this call):\n" + json.dumps(_request_example)
