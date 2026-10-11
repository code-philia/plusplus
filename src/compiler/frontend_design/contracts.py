"""Typed frontend deltas and instructions for page-scoped incremental design."""
from __future__ import annotations

import types
import json
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
    tables = PHASE_TABLES[phase]
    ui_fields = {key: convert(value) for key, value in get_type_hints(UINode, include_extras=True).items()
                 if key not in {"id", "children", "bindings", "events", "requirement_ids"}}
    optional_fields = [key for key, value in get_type_hints(UINode, include_extras=True).items()
                       if get_origin(value) is NotRequired]
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
    table_deletes = {table: strings for table in tables}
    if phase != "ui":
        table_upserts["ui"] = empty_array
        table_deletes["ui"] = empty_array
    else:
        # Fresh UI has no interaction yet; existing events are preserved by omission.
        defs["UINode"]["properties"]["events"] = empty_array
    schema = obj({
        "upserts": obj(table_upserts),
        "ui_updates": {"type": "array", "items": update},
        "deletes": obj(table_deletes),
        "reuse_ids": strings,
        "implements_ids": strings,
        "load_ui_ids": strings,
        **({"page_assignments": {"type": "array", "items": obj({
            "name": {"type": "string"}, "ui_id": {"type": "string"}})}} if phase == "ui" else {}),
    })
    return {**schema, "$defs": defs, "x-arc-output-mode": "json_object"}


COMMON = """Design a technology-neutral frontend blueprint from requirements and optional visual evidence.
Return only the requested JSON delta. No components, React, JSX, DOM elements, CSS, HTTP routes,
files, stores, handlers/effects tables, version, audit, scores or validation results.
The UI tree is a semantic composition tree, not a framework component tree.
Give every new UI a concise English snake_case name describing its semantic responsibility,
e.g. registration, app_header, registration_form, email_field, submit_action.
Names must not include requirement IDs, generated hashes or framework terminology. Preserve names
of existing objects unless their responsibility changes. IDs remain stable when names change.
Use supplied exact IDs for existing objects; use the supplied new_id_prefix plus meaningful suffixes
for new objects (e.g. <prefix>ui.query, <prefix>fn.search, <prefix>data.query).
For NEW UI only, return COMPLETE records in upserts.ui, with children as ordered IDs.
For EXISTING UI, use ui_updates instead of regenerating the complete record.
Each update has id plus only the sections permitted by the CURRENT phase's schema.
bindings uses {set:{attribute:ValueSource},remove:[attributes]}; events uses
{upserts:[complete changed/new events],deletes:[event IDs]}.
UI-only children uses {remove:[IDs],add:[{id,before_id?:ID|null}]}.
Omit untouched sections/fields. Missing before_id appends; remove+add moves a child.
Do not replace all children, bindings or events. Omitted values and siblings survive.
Only UI phase creates/deletes UI or changes containment; data updates bindings/repeat;
interaction updates events/bindings and behavioral spec. Only interaction changes events.
Do not set requirement_ids in ui_updates: implements_ids establishes new direct responsibilities.
Other tables continue to use complete records in upserts. Return ui_updates:[] when unchanged.
existing.ui is a NESTED forest of loaded full records; children are embedded records in INPUT ONLY.
loaded_ui_ids defines the editable UI nodes. ui_tree_summary preserves the entire tree's topology;
omitted descendants are not deletions. All OUTPUT child references use IDs.
If you need a UI absent from loaded_ui_ids, return load_ui_ids with exact tree-summary/children IDs
and an otherwise empty delta. List all existing UI records you need for the next attempt; this
replaces the prior explicit selection to allow switching regions. The compiler loads records and asks
again. Otherwise use []. At most four context requests are allowed per phase; work on a small region.
Never edit an existing UI that has not been loaded. Missing descendants remain unchanged.
For large trees work on the current semantic region; ask to load another region when necessary.
For unchanged reused objects return their IDs in reuse_ids, not duplicate records.
deletes contains only IDs intentionally superseded by your design changes. Otherwise use empty lists.
requirement_ids means DIRECT implementation, not use, tree containment or scheduling history.
New objects directly implement the current requirement by default. Preserve existing direct owners
when updating objects. Add an owner only for a new semantic responsibility explicitly implementing
that requirement. Editing children to attach UI, moving nodes or changing shared layout does not
make the parent/container implement the attached requirement. reuse_ids records use only.
implements_ids lists changed existing objects whose NEW semantic responsibility directly implements
the current requirement. Use [] for structural maintenance and pure reuse. To create only shared
shell structure, set requirement_ids=[]; it is used by the current task, not directly owned by it.
Never copy ancestor requirement IDs onto all descendants, or current IDs onto every reused object.
Existing shared responsibilities must survive changes. Read supplied related requirement scenarios.
Do not invent product behaviors. Parent requirements describe shared composition; do not duplicate
child-specific UI and behavior. Backend-only requirements may return an entirely empty delta.
The compiler maintains root_ui_id; preserve that root. New-node OUTPUT children are ordered IDs.
UI containment follows application layout and navigation, not the requirement tree.
Ancestors supply context, not additional feature tasks.
declared_pages is the authoritative page scope. Page identity is the EXACT pages[].name,
not an image path or image hash. page_matches maps those names to existing screen IDs.
Use the same screen for every requirement sharing a page name, even with no image reference.
missing_pages lists page names needing a new screen subtree. In UI output, page_assignments
contains {name,ui_id} for new declared pages; unchanged existing assignments may be omitted.
Never assign an undeclared page or duplicate an existing screen. Each screen corresponds to one
page name. All UI/data/interaction calls for a requirement use the same declared page scope.
The optional pages[].reference supplies cached visual evidence only and does not identify a page.
Do not create login/search/booking surfaces just because an ancestor mentions them during a leaf task.
When building shared shell structure, add only the regions needed to host the current leaf surface.
structural_ui contains compact ancestor summaries for discovery only. Before updating an ancestor,
request its full record with load_ui_ids, then preserve all sibling IDs and events.
ui_tree_summary is discovery only; absent subtrees are not implicitly editable.
Every non-root UI has ONE parent. Reusing the same surface means referencing/updating its existing
node at its existing position, never attaching it to a second parent. Similar controls at different
locations are distinct UI instances; reuse their data/functions where appropriate. Repeated items
use repeat, not copied subtrees. To move a UI update both old and new parent children.
UI kinds: screen,group,section,form,field,navigation,dialog,tabs,tab,table,row,cell,heading,link,progress,text,input,choice,action,image,list,feedback.
Use form for a submission unit, field for a labeled input region, navigation for shared navigation,
section for a meaningful page region, dialog for an overlay, and table/row/cell for tabular information.
Do not mirror every DOM wrapper: each node must have a meaningful visible or interaction responsibility.
bindings keys are semantic attributes such as label,value,options,visible,enabled,items.
Additional semantic attributes: heading level, link destination, tab selected, progress maximum,
cell header, action submits_form. A table contains rows, rows contain cells.
Do not use DOM/CSS properties or framework lifecycle names.
presentation is optional natural-language layout/visual intent. Reference images are optional;
use only supplied visual_reference_ids and preserve their relevant layout and visual cues.
Pictures supply visual evidence, not additional functional scope.
"""

STATE_CONTRACT = """scope_ui_id=null means shared application data; otherwise local to that UI subtree instance.
Only bind/read/write scoped data from within its owner subtree. A shared function reused across
screens needs explicit typed inputs or shared data; do not capture a different screen's local state.
Values: {kind:literal,value:JSON}, or {kind:data|input|result|item,id:string,path?:[string|integer]}.
UI bindings use literal/data/item only. input refers to current event payload/function input semantic_id.
result refers to an earlier local call ID and its output semantic_id in path[0]. item refers to a
current/ancestor repeat UI ID. No executable expressions; derived values use ordinary functions.
Fields: semantic_id,name,type,required,nullable,description. Types use scalar/array/named exactly
as backend IR. Scalars: string,integer,number,boolean,date,datetime,json,uuid.
Reuse backend named types; put only frontend-specific definitions in types.
"""
EVENT_CONTRACT = """Each UI owns events:[]; events are embedded records, never a separate top-level table.
An event keeps its stable id and DIRECT requirement_ids independently from its UI owner.
"""
CAPABILITY_CONTRACT = """The backend API catalog is the capability boundary. For every required server behavior with no
compatible API, emit pending_capabilities in data/interaction instead of hiding the gap
only in function spec. Fields: stable id (<prefix>capability.<operation>), requirement_ids, capability,
reason, function_ids (empty until known). Reuse supplied pending IDs for the same missing capability;
include affected frontend function IDs once designed. Preserve unrelated gaps.
Delete a gap only if a supplied existing API fully supports it and the affected interaction is wired;
local simulation, disabled UI or a local logout that leaves the server session valid is not resolution.
Do not invent API IDs or claim unsupported server effects. Local-only behavior needs no gap.
"""

PROMPTS = {
    "ui": COMMON + """
Pass 1: plan page topology AND design the current requirement UI in one call.
Existing UI updates may use set/unset for schema-listed fields, children and bindings.
New UI records must include events:[]; ui_updates.events is absent from this pass's schema.
Each page is a screen-rooted subtree of the application UI tree. When page_ids is populated,
inspect and update the loaded corresponding page subtree, preserving unrelated controls.
For each missing_pages name, create a new screen subtree and attach it via ui_updates.children,
then return its page_assignments entry with the exact declared page name. Use ui_tree_summary
to locate shared application structure; never substitute an undeclared page by semantic similarity.
Give screens logical routes when needed. Establish shared navigation/header/footer regions once.
For NON_LEAF tasks reuse completed child pages and add only parent-level composition.
Screen routes and UI presentation are optional prose/locations; the prohibition on HTTP routes
means backend endpoints, not the screen's route field.
Add semantic regions, text, controls, list containers and feedback as required.
Place all new nodes into the tree by adding child IDs to existing parents with ui_updates.
Reuse the existing screen/surface rather than creating a new screen for every requirement.
Define visual composition and static bindings now; leave dynamic bindings for the next pass.
Do not create data/events/functions. Preserve already established shared layouts and sibling nodes.
""",
    "data": COMMON + """
Pass 2: define typed frontend data and UI bindings for this requirement's committed UI.
Always return upserts.ui=[] and deletes.ui=[]. ui_updates supports only bindings,
set.repeat and unset:["repeat"]. No children/events or other set fields, including spec.
Use the same declared page subtree selected/created by UI; do not switch pages or reconstruct UI.
Reuse existing state when its meaning, lifecycle and type match. Use local subtree scope by default;
promote genuinely shared state to the nearest shared ancestor or application scope.
Cover user input, display results, loading, empty/error/selection states as requirements need them.
Include initialization rules in data spec. Omitted initial means uninitialized, not null.
Use supplied backend API types for data crossing the API boundary, preserving named type IDs.
Complete repeat source and stable item keys, dynamic visible/enabled/value/items bindings.
Do not duplicate state merely to store a label or a formatting convenience. Use literals for static
content; store derived values only when their lifecycle requires persistence. Describe derivation
in data spec when state is involved; function behavior and UI behavioral spec belong to interaction.
Never insert code or expression strings. Do not create events or functions yet.
""",
    "interaction": COMMON + """
Pass 3: update events through ui_updates.events and connect ordered frontend function calls.
Always return upserts.ui=[] and deletes.ui=[]. ui_updates supports only events,
bindings and set.spec. No children/unset or other set fields; preserve repeat and presentation.
Use the same committed page subtree as UI and Data; preserve its node identities and containment.
Complete the full interaction: payload -> function arguments -> state reads -> backend API calls ->
result/state writes -> bound UI, including initialization/enter, derived state recomputation,
local-only actions, navigation and applicable success/failure/loading/empty behavior.
Reuse an existing function only when its interface and responsibilities match; otherwise create one.
Embedded events use id,name,spec,requirement_ids,payload fields,handlers (ordered FUNC calls only); no ui_id.
An event can implement a requirement independently of the containing UI. Preserve other requirements' events.
Functions have inputs/outputs, direct reads/writes of data IDs, and ordered calls (FUNC or API).
API target_id must copy a supplied backend API ID exactly. arguments keys are its INPUT semantic_ids.
Never call backend FUNC/DB or pass backend trusted context as client arguments. Copy output types.
Each function spec must state how inputs/results become outputs/state, conditions, navigation targets,
and applicable error, reentry/concurrency rules. reads/writes lists alone are not behavior.
Call failures stop the list unless caller spec explicitly defines handling; never use a skipped result
unconditionally. Specify call interleaving with local computations in spec; no executable step DSL.
Preserve shared function behavior for its original requirements when updating it.
This pass generates behavior; do not audit or reject the existing design.
""",
}


PROMPTS["data"] += STATE_CONTRACT + CAPABILITY_CONTRACT
PROMPTS["interaction"] += STATE_CONTRACT + EVENT_CONTRACT + CAPABILITY_CONTRACT


# Complete JSON envelopes keep examples aligned with each phase's output protocol.
EXAMPLES: dict[str, dict[str, Any]] = {}
for _phase, _tables in PHASE_TABLES.items():
    EXAMPLES[_phase] = {
        "upserts": {table: [] for table in _tables},
        "ui_updates": [], "deletes": {table: [] for table in _tables},
        "reuse_ids": [], "implements_ids": [], "load_ui_ids": [],
        **({"page_assignments": []} if _phase == "ui" else {}),
    }
EXAMPLES["ui"]["upserts"]["ui"] = [{
    "id": "REQ-1.ui.heading", "name": "page_heading", "kind": "heading",
    "spec": "Page heading.", "requirement_ids": ["REQ-1"], "children": [],
    "bindings": {"label": {"kind": "literal", "value": "Settings"}}, "events": [],
}]
EXAMPLES["ui"]["ui_updates"] = [{"id": "ui.settings", "children": {
    "remove": [], "add": [{"id": "REQ-1.ui.heading", "before_id": None}]}}]
EXAMPLES["data"]["upserts"]["data"] = [{
    "id": "REQ-1.data.email", "name": "email", "type": {"kind": "scalar", "name": "string"},
    "nullable": False, "spec": "Editable email input, initially empty.",
    "requirement_ids": ["REQ-1"], "scope_ui_id": "ui.settings", "initial": "",
}]
EXAMPLES["data"]["ui_updates"] = [{"id": "ui.email", "bindings": {
    "set": {"value": {"kind": "data", "id": "REQ-1.data.email"}}, "remove": []}}]
EXAMPLES["interaction"]["upserts"]["functions"] = [{
    "id": "REQ-1.fn.clear_email", "spec": "Set email to the empty string.",
    "requirement_ids": ["REQ-1"], "inputs": [], "outputs": [],
    "reads": [], "writes": ["REQ-1.data.email"], "calls": [],
}]
EXAMPLES["interaction"]["ui_updates"] = [{"id": "ui.clear_email", "set": {
    "spec": "Clear the email when activated."}, "events": {"upserts": [{
        "id": "REQ-1.event.clear_email", "name": "activate", "spec": "Clear the current email.",
        "requirement_ids": ["REQ-1"], "payload": [], "handlers": [{
            "id": "call.clear_email", "kind": "FUNC", "target_id": "REQ-1.fn.clear_email", "arguments": {}}],
    }], "deletes": []}}]
for _phase in PROMPTS:
    PROMPTS[_phase] += "\nComplete output example (assume referenced existing UI is loaded on the declared Settings page; copy actual supplied IDs):\n" + json.dumps(EXAMPLES[_phase], ensure_ascii=False)
