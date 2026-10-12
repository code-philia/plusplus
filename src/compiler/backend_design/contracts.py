"""Pass contracts derived from the authoritative backend TypedDict definitions."""
from __future__ import annotations

import types
from typing import Any, Literal, NotRequired, Required, Union, get_args, get_origin, get_type_hints, is_typeddict

from ..ir.backend import BackendIR, BackendModule, BackendType, BackendTypeDefinition, APIModule, FunctionModule, DatabaseModule



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
        "semantic_type_changes": schema_for(dict[str, BackendType]),
        "updated_modules": schema_for(list[BackendModule]),
        **({"resolved_calls": schema_for(dict[str, str])} if kind != "API" else {}),
        **({"api_ids": schema_for(list[str])} if kind == "API" else {}),
        "requested_module_ids": schema_for(list[str]),
        "requested_type_ids": schema_for(list[str]),
        **({"requested_tables": schema_for(list[str])} if kind == "DB" else {}),
    })
    schema["required"] = ["new_modules"]
    LAYER_SCHEMAS[kind] = schema

LAYER_COMMON = """Design only the current backend layer. Return the JSON delta defined by its schema.
Use exact loaded IDs when reusing contracts; do not regenerate existing modules or alter upstream interfaces.
New IDs use KIND.Name; the compiler qualifies them with the current requirement ID.
Do not qualify new IDs with another requirement's ID. Every new module must be reachable
from the selected APIs; remove unused helpers. Existing contracts must be loaded before referencing them.
Define new_types only for named types referenced by new modules; omit empty optional fields.
semantic_types indexes existing semantic IDs, their types and owning contracts. Preserve those types.
The semantic ID describes the value itself: a color string and a record containing that color
are different values and need different IDs, including when one is input and the other output.
semantic_conflict_contracts contains full affected contracts loaded after a conflict, with named
definitions in semantic_conflict_types. Prefer fixing a misused ID over changing established types.
For an intentional migration, declare semantic_type_changes {existing_semantic_id:new_type} and
submit updated_modules with exact existing IDs for ALL affected modules across layers in the SAME
reply. This is an explicit revision, never unchanged reuse. Keep unrelated behavior, routes, calls,
field names and metadata intact; revise only the declared semantic types and necessary named type
references. Use fresh new_types IDs instead of overwriting definitions. Every remaining occurrence,
including nested fields, must agree with the declared type. Do not silently overwrite a contract.
Without semantic_type_changes, updated_modules is available only during a confirmed coverage repair.
Never redeclare existing type IDs. Field names and semantic_ids must be unique within each interface
(API request names are unique per source). Named types must be defined or loaded and nonrecursive.
Fields preserve semantic_id and compatible types. Use snake_case parameter names. Trusted identity/time
belong in context. spec describes observable behavior, errors and guarantees, not implementation code.
Put all validation, error, persistence and concurrency guarantees in spec. Do not return separate
acceptance_criteria, guarantees, Given/When/Then records or duplicated scenario metadata.
Calls use module_id and purpose describing responsibility, inputs, results and failure guarantees.
Call IDs are unique within a module; after contains local call IDs only, with no ordering cycles.
The call graph must be acyclic. API calls FUNC only; FUNC calls FUNC/DB; DB has no calls.
validation_feedback reports structural AND contract errors in previous_output. Correct that rejected
design using the supplied contracts; do not repeat the same output or revise accepted upstream modules.
Reuse loaded modules by referencing their IDs directly. Catalogs are summaries: request unloaded
contracts with requested_module_ids/requested_type_ids and return no design until they are loaded.
There are TWO mutually exclusive reply modes:
1. Load existing context: new_modules:[], omit new_types/resolved_calls/api_ids.
Request only exact unloaded IDs from the supplied catalogs/schema_index. Loaded modules, upstream
contracts, unknown IDs and forward calls are not context requests. Omit empty request fields.
2. Design: return new_modules/new_types and selected api_ids or resolved_calls as appropriate;
omit all requested_* fields. A required module/type that does not exist must be newly designed.
Forward FUNC/DB calls express work for a later layer; never request them as if they already exist.
API layer creates API modules with forward FUNC.Name calls; FUNC layer implements those calls
and may introduce forward DB.Name calls; DB layer implements those persistence leaves.
If validation_feedback reports invalid requests, copy an exact catalog ID or remove the request
and design the missing responsibility in its proper layer. Do not repeat an invented request ID.
reuse_context.modules contains contracts retrieved through entity/domain overlap with earlier requirements.
Review these modules before designing new ones. Reuse ONLY when the entire existing module can remain
unchanged: inputs, outputs, context, behavior and calls must already support the current requirement.
Reuse via api_ids (API), resolved_calls (FUNC/DB), or exact existing IDs in calls; the compiler only
adds the current requirement association. Never rewrite a reused contract or its named types.
conflict_candidates contains full existing contracts found by name/route lookup. All matching names,
IDs and routes are rejected. Reuse a fully compatible module or give a different responsibility
an unoccupied name; explanations cannot authorize duplicate names or routes.
An API method+route must remain unique: reuse a compatible API or design a genuinely different
resource operation with a distinct route. Never change a route merely to evade a duplicate operation.
"""
LAYER_PROMPTS = {
    "API": LAYER_COMMON + """
Task: identify all server behavior required by the description/scenarios and design its REST interfaces.
Persistence, account uniqueness, authentication and authoritative queries require server APIs even
when the requirement emphasizes a page or UI. Only entirely client-local behavior needs no API.
Define path, method, authentication, request sources, response fields, status and observable errors.
GET has no body. Use :parameter path syntax and application/json; empty responses use status 204.
Route path fields exactly match :parameters and are required/nonnullable. status_code is the
successful 2xx status; a 204 response has no fields. Describe failure statuses in spec.
New APIs delegate business work to loaded FUNC IDs or forward FUNC.Name calls. Do not design FUNC/DB
modules or database columns. api_ids selects the new/existing API entrypoints used by this requirement,
including relevant setup, action and observation interfaces. Return empty lists for client-only tasks.
The compiler automatically selects every new API. api_ids is optional and selects ONLY loaded
existing APIs to reuse; do not repeat new API IDs. This avoids duplicate entrypoint bookkeeping.
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
Use one data_access entry per table and unique columns per operation, referencing loaded schema only.
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
PARENT_DESIGN_PROMPT = """Task: check business coverage of this parent requirement after ALL leaf designs are complete.
api_contracts contains local descendant APIs and relevant APIs retrieved from other branches.
collaborators summarizes their reachable FUNC/DB behavior and table access. Check that claimed API
behavior has corresponding collaborators; do not treat an API spec alone as proof of coverage.
api_catalog is a global API index; request full contracts with requested_api_ids when a potentially
matching API is not loaded. A context request must use api_ids:[] and no pending_capabilities.
Do not conclude a global capability is missing merely because its API is outside this branch or
absent from the loaded slice. Inspect the global catalog and load matching contracts first.
Check every explicit server behavior in the description and scenario steps, including behavior
not sampled by a scenario. Check required data, mutations, ownership, authorization and observable
results, rather than just module existence. Visual controls alone are not server behavior.
Select supporting loaded APIs and report only concrete remaining design gaps. A gap reason must
identify the missing behavior and explain why the relevant loaded contracts cannot support it.
When repair_context is present, recheck the original requirement against the revised global design;
do not carry an old gap forward if it is now covered.
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
PARENT_DESIGN_SCHEMA['properties']['requested_api_ids'] = schema_for(list[str])
