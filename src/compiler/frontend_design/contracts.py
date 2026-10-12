"""Typed frontend deltas and instructions for page-scoped incremental design."""
from __future__ import annotations

import types
from typing import Any, ForwardRef, Literal, NotRequired, Required, Union, get_args, get_origin, get_type_hints, is_typeddict

from ..ir.frontend import UINode, UIEvent, FrontendState, UIFunction, ValueSource
from ..ir.backend import BackendTypeDefinition
from ..ir.integration import PendingCapability

TABLE_TYPES = {"ui": UINode, "data": FrontendState,
               "functions": UIFunction, "types": BackendTypeDefinition,
               "pending_capabilities": PendingCapability}
PHASE_TABLES = {
    "ui": ("ui",),
    "data": ("ui", "data", "types", "pending_capabilities"),
    "interaction": ("ui", "data", "types", "functions", "pending_capabilities"),
}


def patch_schema(phase: str) -> dict[str, Any]:
    """New records are complete; existing UI uses typed, field-level deltas."""
    defs: dict[str, Any] = {
        "JSONValue": {"anyOf": [
            {"type": "string"}, {"type": "number"}, {"type": "boolean"}, {"type": "null"},
            {"type": "array", "items": {"$ref": "#/$defs/JSONValue"}},
            {"type": "object", "additionalProperties": {"$ref": "#/$defs/JSONValue"}},
        ]},
    }

    def convert(annotation: Any) -> dict[str, Any]:
        origin, args = get_origin(annotation), get_args(annotation)
        if isinstance(annotation, ForwardRef):
            return {"$ref": "#/$defs/" + annotation.__forward_arg__}
        if origin in (Required, NotRequired):
            return convert(args[0])
        if origin is Literal:
            return {"enum": list(args)}
        if origin in (Union, types.UnionType):
            return {"anyOf": [convert(item) for item in args]}
        if origin is list:
            return {"type": "array", "items": convert(args[0])}
        if origin is dict:
            return {"type": "object", "additionalProperties": convert(args[1])}
        if is_typeddict(annotation):
            name = annotation.__name__
            if name not in defs:
                defs[name] = {}
                hints = get_type_hints(annotation, include_extras=True)
                defs[name] = {"type": "object", "additionalProperties": False,
                    "properties": {key: convert(value) for key, value in hints.items()},
                    "required": [key for key, value in hints.items() if get_origin(value) is not NotRequired]}
            return {"$ref": "#/$defs/" + name}
        return {"type": {str: "string", int: "integer", float: "number", bool: "boolean", type(None): "null"}[annotation]}

    def obj(properties: dict[str, Any]) -> dict[str, Any]:
        return {"type": "object", "additionalProperties": False,
                "properties": properties, "required": list(properties)}

    strings = {"type": "array", "items": {"type": "string"}}
    tables = {"ui": ("ui",), "data": ("data", "types"),
              "interaction": ("functions", "types", "pending_capabilities")}[phase]
    ui_fields = {key: convert(value) for key, value in get_type_hints(UINode, include_extras=True).items()
                 if key not in {"id", "children", "bindings", "events", "requirement_ids", "description"}}
    optional_fields = [key for key, value in get_type_hints(UINode, include_extras=True).items()
                       if get_origin(value) is NotRequired and key != "description"]
    if phase == "data":
        ui_fields = {"repeat": ui_fields["repeat"]}
        optional_fields = ["repeat"]
    elif phase == "interaction":
        ui_fields = {"spec": ui_fields["spec"]}
        optional_fields = []
    update = {"type": "object", "additionalProperties": False, "required": ["id"], "properties": {
        "id": {"type": "string"},
        "set": {"type": "object", "additionalProperties": False, "properties": ui_fields},
        "unset": {"type": "array", "items": {"enum": optional_fields}},
        "children": obj({"remove": strings, "add": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id"], "properties": {
                "id": {"type": "string"}, "before_id": {"anyOf": [{"type": "string"}, {"type": "null"}]}}}}}),
        "bindings": obj({"set": {"type": "object", "additionalProperties": convert(ValueSource)}, "remove": strings}),
        "events": obj({"upserts": {"type": "array", "items": convert(UIEvent)}, "deletes": strings}),
    }}
    if phase != "ui":
        update["properties"].pop("children")
    if phase != "interaction":
        update["properties"].pop("events")
    if phase == "interaction":
        update["properties"].pop("unset")
    empty_array = {"type": "array", "items": {"type": "string"}, "maxItems": 0}
    table_upserts = {table: {"type": "array", "items": convert(TABLE_TYPES[table])} for table in tables}
    if phase == "ui":
        defs["UINode"]["properties"]["events"] = empty_array
    properties = {
        "upserts": obj(table_upserts),
        "ui_updates": {"type": "array", "items": update},
    }
    if phase == "ui":
        properties.update({
            "page_assignments": {"type": "array", "items": obj({
                "name": {"type": "string"}, "ui_id": {"type": "string"}})},
            "ui_requirement_updates": {"type": "array", "items": obj({
                "id": {"type": "string"}, "requirement_id": {"type": "string"},
                "description": {"type": "string", "minLength": 1}})},
        })
    schema = obj(properties)
    # Optional operations need no repeated empty arrays in normal replies.
    schema["properties"]["deletes"] = {"type": "object", "additionalProperties": False,
        "properties": {table: strings for table in tables}}
    schema["properties"]["load_ui_ids"] = strings
    # Intermediate annotation conversions may have populated unused definitions.
    # Supply only contracts reachable from this phase's output properties.
    def references(value: Any) -> set[str]:
        if isinstance(value, list):
            return {key for item in value for key in references(item)}
        if not isinstance(value, dict):
            return set()
        own = {value["$ref"].removeprefix("#/$defs/")} if "$ref" in value else set()
        return own | {key for item in value.values() for key in references(item)}
    pending, retained = list(references(schema)), set()
    while pending:
        key = pending.pop()
        if key not in retained:
            retained.add(key)
            pending.extend(references(defs[key]) - retained)
    defs = {key: value for key, value in defs.items() if key in retained}
    return {**schema, "$defs": defs, "x-arc-output-mode": "json_object"}


COMMON = """Design a technology-neutral frontend IR. Return the JSON delta required by this phase's schema.
Use stable existing IDs; prefix new IDs with the current requirement ID and a meaningful suffix.
existing.ui contains nested complete UI records; output children references use IDs.
Use ui_updates for existing nodes and complete upserts for new records. Omitted fields remain unchanged.
Modify only nodes supplied in existing.ui. Summaries locate nodes but are not full editable records.
To read omitted nodes, return load_ui_ids and otherwise empty operations; then continue after loading.
Preserve unrelated sibling controls, data and events. Implement only the current requirement's behavior.
"""

VALUES = """Bindings use {kind:literal,value:JSON} or {kind:data|item,id,path?:[string|integer]}.
Types use scalar/array/named; named IDs must exist in supplied or newly defined types.
scope_ui_id=null is application data; otherwise the state belongs to that UI subtree.
Use state only inside its scope; pass typed inputs to functions reused outside it.
"""

PROMPTS = {
    "ui": COMMON + """
Task: build/refine the page's semantic UI tree and bind relevant nodes to this requirement.
Page identity is requirement.pages[].name. Analysis appears next to its reference path.
NEW_PAGE: with analysis, build ALL visible meaningful UI, including regions unrelated to this
requirement. Without analysis, derive required UI from the description/scenarios.
NEW_PAGE_STATE: compare analysis with baseline_page_states and the current page tree. Insert
missing state-specific nodes, preserving controls absent from this image and existing node IDs.
BIND_ONLY: create/update/delete no visual structure; only bind relevant existing UI nodes.
Use concise English snake_case UI names. Include meaningful regions, forms, fields, text,
actions, navigation, lists and feedback; omit decorative wrappers. New UI has events:[] and
requirement_ids:[]. Static bindings and presentation describe visual intent, not business logic.
For a new page, return page_assignments {name,ui_id}; attach its screen via ui_updates.children.
children updates use {remove:[IDs],add:[{id,before_id?:ID|null}]}; omitted before_id appends.
Existing visual fields use set/unset; bindings use {set:{attribute:value},remove:[attributes]}.
After building or selecting the subtree, return ui_requirement_updates
{id,requirement_id,description} for ONLY nodes implementing this requirement. Extract description
from its description/scenario steps to state each node's responsibility. This operation adds the
requirement ID and replaces description, while preserving prior owners. Do not create data/functions.
""",
    "data": COMMON + VALUES + """
Task: design state needed by this requirement and connect it to the committed UI.
Upsert data and frontend types. Reuse matching state by its ID; avoid duplicate or derived state
without a lifecycle need. State spec explains meaning, initialization and lifecycle.
UI updates may change bindings and repeat only. bindings uses {set:{attribute:value},remove:[attributes]};
repeat defines a data source and stable item key. Cover input values, displayed results,
loading/error/empty/selection states as needed. Keep static text literal.
No UI topology, events, functions, API calls or server capability analysis in this phase.
""",
    "interaction": COMMON + VALUES + """
Task: connect UI events to frontend functions, state changes and supplied backend APIs.
Upsert functions and any required types. UI updates may change events, bindings and spec only.
Event updates use {upserts:[complete events],deletes:[event IDs]}. Events are embedded in UI;
each has its own requirement_ids and ordered FUNC handlers. Preserve unrelated events.
Functions have typed inputs/outputs, reads/writes of data IDs and ordered FUNC/API calls.
Function spec explains local computation, validation, call conditions, result-to-state updates,
navigation and failure/loading behavior. input/result values refer to input or earlier call IDs.
Use exact supplied API IDs and request semantic_ids; never call backend FUNC/DB from the client.
Reuse existing functions by ID when their responsibility and signature match.
Emit pending_capabilities only for required server behavior unsupported by supplied APIs; include
affected function IDs. Resolve a gap only after wiring a supporting API. Do not simulate server success.
Do not create UI nodes, change containment or remove another requirement's events.
""",
}
