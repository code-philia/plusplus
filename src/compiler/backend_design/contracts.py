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
                if value is BackendField:
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

COMMON = """Design a technology-neutral relational backend blueprint. No HTTP routes, SQL, ORM,
frameworks, external services, fixture endpoints, or executable internal algorithms.
Use only supplied schema tables/columns and registered data types. Preserve semantic_id identities.
Separate trusted context (identity/time/generated IDs) from untrusted inputs. Describe authorization,
validation, concurrency, atomicity, idempotency and observable failure behavior when required.
Do not invent business requirements. Acceptance criteria are falsifiable given/when/then statements
linked to supplied scenario IDs, including negative cases and prohibited writes. Return JSON only.
Schema IR tables, constraints, defaults and generation are the only storage authority. Treat er_model,
entities and relationships as traceability views, not additional executable schema definitions.
Every supplied runtime_rule with responsibility=backend is an explicit obligation: assign it to affected
write modules and orchestration specs, with transaction boundaries, affected-owner membership checks,
final-member deletion behavior and acceptance criteria. There is no automatic database-global membership check.
Regex/email/URL validation and time-relative rules remain backend behavior from the original requirements;
absence from ER does not waive them. Do not reinterpret operation-specific validation as a storage invariant.
Treat the supplied database design as an accepted input, not something to audit or repair.
Do not reject generation for missing UNIQUE/CHECK/foreign-key constraints, indexes, database
isolation settings, or an unspecified database enforcement mechanism. Keep requirement-backed
uniqueness/concurrency behavior in module specs and criteria; defer its concrete enforcement to
implementation. Example: if traveler_account lacks UNIQUE(username), still design registration
and duplicate checks; do not return a schema defect or demand a schema change before proceeding.
"""
COMMON += r'''
Mandatory validation contract (also applies to fields inside shared object types):
1. Return exactly the supplied JSON shape: required keys must exist, optional keys may be
   omitted, no extra keys, correct JSON types and enum values. Empty sections use [], not null.
2. Field name must match ^[a-z][a-z0-9_]*$: image_source, display_order, viewer_identity.
   INVALID: imageSource, displayOrder, viewerIdentity, Image_source, image-source.
   semantic_id must match ^[a-z][a-z0-9_.]*$: home_banner.image_source, context.viewer.identity.
   INVALID: homeBanner.imageSource, context/viewer, Order.id. Preserve existing semantic IDs.
   Within each field list, both names and semantic_ids must be unique. The same semantic_id
   anywhere in the design must have exactly the same type tree, not string in one place and uuid in another.
3. Each field includes semantic_id/name/type/required/nullable/description. required and nullable
   are separate booleans. Example:
   {"semantic_id":"home_banner.image_source","name":"image_source",
    "type":{"kind":"scalar","name":"string"},"required":true,"nullable":false,
    "description":"Banner image address."}
   Scalar names: string/integer/number/boolean/date/datetime/json/uuid.
   Array: {"kind":"array","items":{"kind":"scalar","name":"string"}}.
   Named: {"kind":"named","type_id":"type.home_banner"}; that id must exist in supplied
   or newly declared types. No recursive type chains (A -> A or A -> B -> A).
   Type ids must be unique/nonblank. Enum values must be a nonempty unique string list.
4. Trusted context and inputs must not share names OR semantic_ids. Example: input order_id
   plus context viewer_identity; INVALID: viewer_identity declared in both lists.
5. Every complete module/requirement has nonblank spec; modules also have nonblank name.
   Acceptance criteria must be nonempty, have locally unique nonblank ids, nonblank given/when/then,
   and only supplied scenario IDs. [] is allowed for criteria without a source scenario.
   Example: {"id":"visible_only","given":"A visible and a hidden banner exist",
   "when":"The visitor requests banners","then":"Only the visible banner is returned",
   "scenario_ids":["REQ-1.1:scenario:1"]}. Copy actual scenario IDs; this example ID is illustrative.
6. data_access uses exact schema table/column names; each table occurs at most once per list.
   read/create/update lists have unique columns. Each access must declare at least one operation.
   Example: {"table":"home_banner","read":["id","image_source","display_order"],
   "create":[],"update":[],"delete":false}. INVALID: an access with all lists empty and delete=false.
   Only DB modules have nonempty DIRECT data_access; API/FUNC use []. DB modules have no calls.
   Include filtering/sorting/join dependency columns in read, not just returned columns.
7. Calls must resolve to registered modules, form no cycle, and obey API -> FUNC, FUNC -> FUNC/DB.
   INVALID: API -> DB, DB -> FUNC, A -> B -> A. Call ids are nonblank and unique within the caller;
   purpose is nonblank. Optional condition must be nonblank. after refers only to EARLIER LOCAL CALL IDS,
   never module ids. Example: calls [{"id":"load","module_id":"DB.LoadBanner","purpose":"Load banner"},
   {"id":"check","module_id":"FUNC.CheckBanner","purpose":"Check visibility","after":["load"],
    "condition":"A banner was found"}]. This example is legal under a FUNC caller.
8. Complete registered module ids have format REQ-1.1::KIND.Name, with KIND matching kind and
   Name matching [A-Za-z][A-Za-z0-9_]*. requirement_ids is nonempty, unique, and references known
   requirements; compiler derives memberships. New-module passes use local ids as instructed below.
   API entrypoint.operation must be nonblank, authentication is none/required/optional.
9. Each requirement has unique API ids referencing API modules. server_state=true requires
   at least one API; server_state=false requires api_ids=[]. related_data references only schema
   tables/columns. All modules must be reachable from some requirement API. Do not add unused helpers.
   Example: API.GetHomeBanners -> FUNC.ListHomeBanners -> DB.ReadHomeBanners, all reachable.
'''
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
Only DB modules have direct data_access. API includes a technology-neutral entrypoint.
Each call specifies purpose, optional business condition, and optional predecessor local call IDs.
Each new module is a COMPLETE final contract: spec, inputs/context/outputs, calls, direct data_access
and nonempty module-specific acceptance_criteria. There is no later module refinement pass.
Use the original requirement to retain exact validation rules and bounds, not just analysis summaries.
Describe observable success, failure, absence and side effects consistently with declared output types.
Do not prescribe source paths, SQL/ORM choices or internal implementation algorithms.
Distribute responsibility by layer; do not copy the entire requirement criteria onto every module.
API criteria cover boundary behavior; FUNC criteria cover its business responsibility; DB criteria cover
its declared storage effects. Criteria may use only supplied own/inherited scenario IDs.
All new modules must be reachable from api_ids. Non-server requirements return three empty lists.
Materialize EVERY analysis.api_capabilities entry as an API reachable from this requirement's
api_ids, reusing compatible APIs where available. Copy its operation exactly to entrypoint.operation
and use exactly its input/context/output semantic_ids. Multiple API entry points per requirement
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
New API inputs/context/outputs must come from the corresponding fixed analysis list: copy
semantic_id/type/required/nullable exactly. Local name may differ but must remain snake_case.
The union of reachable DB operations must EXACTLY match analysis.data_operations by table,
operation and column: no missing read, extra update or hidden delete. Example: analysis read
["id","image_source"] permits splitting these reads across DB leaves, but not adding create.
Do not redefine an existing shared type; exact identical definitions may be reused.
Return complete modules including final spec and acceptance_criteria; no draft-only records.
'''
from ..ir.backend import RequirementBlueprint
from ..ir.integration import PendingCapability
_PARENT_REQUIREMENT_SCHEMA = schema_for(RequirementBlueprint)
_PARENT_REQUIREMENT_SCHEMA['properties'].pop('pending_capabilities')
PARENT_DESIGN_SCHEMA = object_schema({'requirement': _PARENT_REQUIREMENT_SCHEMA,
                                     'pending_capabilities': schema_for(list[PendingCapability])})
PARENT_DESIGN_PROMPT = COMMON + """Integrate this non-leaf requirement once, after all descendants.
Only describe composition and frontend/backend integration using available_api_ids from the completed descendants.
Return requirement (the complete parent RequirementBlueprint) and pending_capabilities (a list).
Do not create or update backend APIs, FUNC/DB modules, types, interfaces, call graphs or child contracts.
Tree containment is not a backend module call. Select only existing supplied descendant API IDs.
Describe how the frontend combines their existing capabilities; preserve child behavior and API contracts.
For every required server capability absent from the supplied API contracts, emit a pending_capabilities
record: stable id prefixed with requirement_id + '.capability.', requirement_ids, capability, reason,
and function_ids=[] (frontend wiring has not been designed). Use [] only when no gap exists.
Do not hide a missing login, search, booking or logout capability only in prose, invent an endpoint,
or claim it is implemented. Parent prose is legitimate scope even when children omit the capability.
Reuse child gap IDs for the same missing capability. Frontend supplementation is handled later.
requirement_id must match the supplied ID. Cover all own scenarios with nonempty falsifiable criteria;
for a grouping parent use a concrete integration criterion. server_state reflects whether selected APIs are used.
related_data is the deterministic union supplied by the compiler: copy it unchanged. No new storage design.
"""
