"""Pass contracts derived from the authoritative backend TypedDict definitions."""
from __future__ import annotations

import types
from typing import Any, Literal, NotRequired, Required, Union, get_args, get_origin, get_type_hints, is_typeddict

from ..ir.backend import BackendIR, BackendModule, BackendField, BackendTypeDefinition, AcceptanceCriterion, DataAccess

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
    "spec": {"type": "string"}, "server_state": {"type": "boolean"},
    "inputs": schema_for(list[BackendField]), "context": schema_for(list[BackendField]),
    "outputs": schema_for(list[BackendField]),
    "acceptance_criteria": schema_for(list[AcceptanceCriterion]),
    "data_operations": schema_for(list[DataAccess]),
    "types": schema_for(list[BackendTypeDefinition]),
    "requested_tables": {"type": "array", "items": {"type": "string"}},
    "api_capabilities": {"type": "array", "items": object_schema({
        "operation": {"type": "string"}, "purpose": {"type": "string"},
        "inputs": {"type": "array", "items": {"type": "string"}},
        "context": {"type": "array", "items": {"type": "string"}},
        "outputs": {"type": "array", "items": {"type": "string"}},
        "scenario_ids": {"type": "array", "items": {"type": "string"}},
    })},
})
ARCHITECTURE_SCHEMA = object_schema({
    "new_modules": {"type": "array", "items": MODULE_SCHEMA},
    "reuse_module_ids": {"type": "array", "items": {"type": "string"}},
    "api_ids": {"type": "array", "items": {"type": "string"}},
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
ANALYSIS_PROMPT = COMMON + """Analyze the current requirement before allocating modules.
Return behavior, inputs/context/outputs, criteria, logical data_operations, and new shared types.
Discover api_capabilities from each scenario's COMPLETE observable operation chain, including
Given setup and Then observations after reload, navigation, a later request or another visitor.
Do not limit a requirement to one API or infer capabilities only from its name or When action.
Each capability has operation, purpose, scenario_ids and inputs/context/outputs containing exact
semantic_ids from the corresponding analysis field catalogs. Different APIs may use different
subsets of those catalogs; do not require registration fields for a session-read capability.
Example: registration -> establish a server session -> browser reload -> read the current session
-> display the username needs registration AND current-session retrieval capabilities. Describe
how the browser retains a server-issued credential and supplies it on later requests, how trusted
identity is resolved, and the signed-out result. Persisting a session row alone does not restore UI
state after reload. Include all necessary account/session reads in data_operations. Reuse supplied
capabilities when compatible; do not invent unrelated login/logout features merely from this example.
Criteria must cover later observations through these capabilities, not just assert a successful write.
Pure presentation/client-local requirements have server_state=false and no data_operations.
Inspect supporting reads as well as writes. related_data is a candidate scope, not a closed list.
If a needed table is listed in schema_index but absent from schema_slice, put it in requested_tables;
do not fabricate its columns. Do not output unresolved_questions or audit database design.
For every scenario include a criterion. Reuse existing semantic identities and type definitions.
"""
ANALYSIS_PROMPT += '''Analysis-specific checks:
Cover ALL own scenario IDs; additional references may only come from ancestor_context criteria.
server_state=false requires data_operations=[] AND context=[]. Example: client-local accordion
expansion has no server data/context; loading stored banners has server_state=true.
requested_tables must contain exact names from schema_index; operations may reference only tables
already in schema_slice or requested_tables. Example: request ["home_banner"] before using its
absent schema; never guess columns. Missing database enforcement is not a generation failure.
spec/criteria must be nonempty.
All field/type/criterion naming rules above apply now, before architecture generation.
'''
ARCHITECTURE_PROMPT = COMMON + """Turn the fixed analysis into a small module architecture.
Return new_modules, reuse_module_ids, api_ids only. New module ids are local KIND.Name
(e.g. API.CancelOrder, FUNC.CancelOwnedOrder, DB.TryCancelOrder); the compiler qualifies them.
Calls refer to local new ids or exact existing ids. Reuse only when full behavior and interfaces
are compatible; reading the same table does not establish equivalence. Existing modules are immutable.
If extension changes behavior, design a new module rather than modifying an existing shared interface.
API calls FUNC only, FUNC calls FUNC/DB, DB is a leaf; do not add pass-through layers without responsibility.
Only DB modules have direct data_access. API includes its concrete REST contract.
Each call specifies purpose, optional business condition, and optional predecessor local call IDs.
Each new module is a COMPLETE final contract using its kind-specific interface. There is no later module refinement pass.
Use the original requirement to retain exact validation rules and bounds, not just analysis summaries.
Describe observable success, failure, absence and side effects consistently with declared output types.
Do not prescribe source paths, SQL/ORM choices or internal implementation algorithms.
Distribute responsibility by layer; do not copy the entire requirement criteria onto every module.
API criteria cover boundary behavior; FUNC criteria cover its business responsibility; DB criteria cover
its declared storage effects. Criteria may use only supplied own/inherited scenario IDs.
All new modules must be reachable from api_ids. Non-server requirements return three empty lists.
Materialize EVERY analysis.api_capabilities entry as an API reachable from this requirement's
api_ids, reusing compatible APIs where available. Use its purpose to choose a REST resource path and method; use exactly its input/context/output semantic_ids. Multiple API entry points per requirement
are expected when its complete scenario needs them. Describe session establishment and credential
transport in specs in a technology-neutral way, including the current-session read after reload.
"""
ARCHITECTURE_PROMPT += '''Architecture-specific checks:
new_modules local ids must be unique and match kind: API.GetHomeBanners, FUNC.ListHomeBanners,
DB.ReadHomeBanners. INVALID: REQ-1.1::API.GetHomeBanners for a NEW draft, API.read-banners,
or id=FUNC.ReadBanners with kind=DB. Existing ids are copied fully qualified.
Every existing direct callee and reused API entry must appear in reuse_module_ids, and EVERY
listed reused module must actually be reachable from api_ids. Reuse only supplied candidates.
Example: api_ids=["API.GetHomeBanners"], with a new FUNC calling existing
"REQ-0.1::DB.ReadHomeBanners", requires reuse_module_ids=["REQ-0.1::DB.ReadHomeBanners"].
New API request/context/response fields must come from the corresponding fixed analysis list: copy
semantic_id/type/required/nullable exactly. Local name may differ but must remain snake_case.
The union of reachable DB operations must EXACTLY match analysis.data_operations by table,
operation and column: no missing read, extra update or hidden delete. Example: analysis read
["id","image_source"] permits splitting these reads across DB leaves, but not adding create.
Do not redefine an existing shared type; exact identical definitions may be reused.
Return complete modules including final spec and acceptance_criteria; no draft-only records.
'''
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
