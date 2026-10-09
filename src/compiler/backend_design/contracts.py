"""Pass contracts derived from the authoritative backend TypedDict definitions."""
from __future__ import annotations

import types
from typing import Any, Literal, NotRequired, Required, Union, get_args, get_origin, get_type_hints, is_typeddict

from ..ir.backend import BackendIR, BackendModule, BackendField, BackendTypeDefinition, AcceptanceCriterion, DataAccess


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
ANALYSIS_SCHEMA = object_schema({
    "spec": {"type": "string"}, "server_state": {"type": "boolean"},
    "inputs": schema_for(list[BackendField]), "context": schema_for(list[BackendField]),
    "outputs": schema_for(list[BackendField]),
    "acceptance_criteria": schema_for(list[AcceptanceCriterion]),
    "data_operations": schema_for(list[DataAccess]),
    "types": schema_for(list[BackendTypeDefinition]),
    "requested_tables": {"type": "array", "items": {"type": "string"}},
    "unresolved_questions": {"type": "array", "items": {"type": "string"}},
})
DRAFT_SCHEMA = {"anyOf": [], "$defs": MODULE_SCHEMA["$defs"]}
for reference in MODULE_SCHEMA["anyOf"]:
    branch = MODULE_SCHEMA["$defs"][reference["$ref"].rsplit("/", 1)[1]]
    branch = {**branch, "properties": dict(branch["properties"]), "required": list(branch["required"])}
    branch["properties"].pop("acceptance_criteria")
    branch["required"].remove("acceptance_criteria")
    DRAFT_SCHEMA["anyOf"].append(branch)
ARCHITECTURE_SCHEMA = object_schema({
    "new_modules": {"type": "array", "items": DRAFT_SCHEMA},
    "reuse_module_ids": {"type": "array", "items": {"type": "string"}},
    "api_ids": {"type": "array", "items": {"type": "string"}},
})
AUDIT_SCHEMA = object_schema({"issues": {"type": "array", "items": {"type": "string"}}})

COMMON = """Design a technology-neutral relational backend blueprint. No HTTP routes, SQL, ORM,
frameworks, external services, fixture endpoints, or executable internal algorithms.
Use only supplied schema tables/columns and registered data types. Preserve semantic_id identities.
Separate trusted context (identity/time/generated IDs) from untrusted inputs. Describe authorization,
validation, concurrency, atomicity, idempotency and observable failure behavior when required.
Do not invent business requirements. Acceptance criteria are falsifiable given/when/then statements
linked to supplied scenario IDs, including negative cases and prohibited writes. Return JSON only.
"""
ANALYSIS_PROMPT = COMMON + """Analyze the current requirement before allocating modules.
Return behavior, inputs/context/outputs, criteria, logical data_operations, and new shared types.
Pure presentation/client-local requirements have server_state=false and no data_operations.
Inspect supporting reads as well as writes. related_data is a candidate scope, not a closed list.
If a needed table is listed in schema_index but absent from schema_slice, put it in requested_tables;
do not fabricate its columns. Missing schema or unresolved business ambiguity goes in unresolved_questions.
For every scenario include a criterion. Reuse existing semantic identities and type definitions.
"""
ARCHITECTURE_PROMPT = COMMON + """Turn the fixed analysis into a small module architecture.
Return new_modules, reuse_module_ids, api_ids only. New module ids are local KIND.Name
(e.g. API.CancelOrder, FUNC.CancelOwnedOrder, DB.TryCancelOrder); the compiler qualifies them.
Calls refer to local new ids or exact existing ids. Reuse only when full behavior and interfaces
are compatible; reading the same table does not establish equivalence. Existing modules are immutable.
If extension changes behavior, design a new module rather than modifying an existing shared interface.
API calls FUNC only, FUNC calls FUNC/DB, DB is a leaf; do not add pass-through layers without responsibility.
Only DB modules have direct data_access. API includes a technology-neutral entrypoint.
Each call specifies purpose, optional business condition, and optional predecessor local call IDs.
Each draft fixes spec, inputs/context/outputs, calls and direct data_access; do not return criteria yet.
All new modules must be reachable from api_ids. Non-server requirements return three empty lists.
"""
REFINEMENT_PROMPT = COMMON + """Refine the supplied fixed module using the requirement analysis,
related schema and full callee interfaces. Return the complete module with acceptance_criteria.
Keep id/name/kind/requirement_ids/inputs/context/outputs/calls/entrypoint exactly unchanged.
Clarify spec with data sources, call collaboration, observable errors and required consistency.
Only a DB leaf may refine data_access, within the fixed logical operation scope.
Every module needs nonempty falsifiable criteria; pure functions must not hide database reads.
If the frozen interface cannot implement the responsibility, explain this in spec and criteria so
coverage review rejects and reopens architecture; do not silently invent interfaces or calls.
"""
AUDIT_PROMPT = COMMON + """Review the supplied complete blueprint group for behavioral completeness.
Check required inputs/results, scenario coverage, data operation coverage, authorization, failure
behavior, concurrency/atomicity, and whether callee interfaces can supply caller responsibilities.
Flag incompatible shared behavior and contradictory state transitions across requirements.
Do not demand generic CRUD or unspecified business rules. Return issues=[] only when no specific
requirement-backed defect remains. Issues must state the violated criterion and needed repair.
"""
