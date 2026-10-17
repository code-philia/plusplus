"""Small model deltas; compiler-owned identities and reciprocal links are omitted."""
import json
from compiler.backend_design.contracts import obj, STRING, STRINGS, TYPE

KINDS = ['region', 'form', 'list', 'item', 'input', 'button', 'text', 'image', 'dialog', 'navigation', 'other']
TRIGGERS = ['click', 'change', 'input', 'submit', 'load', 'focus', 'blur', 'keydown', 'select', 'toggle', 'close']
VISUAL_FIELDS = ('regions', 'visible_controls', 'layout_cues', 'style_cues', 'text_cues')
VISUAL_ANALYSIS_VERSION = 2
SCHEMAS = {
    'ui': obj({
        'add_nodes': {'type': 'array', 'items': obj({'name': STRING, 'kind': {'type': 'string', 'enum': KINDS}, 'description': STRING, 'parent': STRING})},
        'updates': {'type': 'array', 'items': obj({'id': STRING, 'description': STRING})},
        'bindings': {'type': 'array', 'items': obj({'id': STRING, 'description': STRING})},
    }),
    'data': obj({
        'new_data': {'type': 'array', 'items': obj({'name': STRING, 'owner': STRING, 'spec': STRING, 'schema': STRING, 'initial': STRING})},
        'reuse_data': STRINGS,
        'bindings': {'type': 'array', 'items': obj({'ui': STRING, 'data': STRING})},
        'new_types': {'type': 'array', 'items': TYPE},
    }),
    'interaction': obj({
        'blocked_reason': STRING,
        'new_handlers': {'type': 'array', 'items': obj({'name': STRING, 'spec': STRING, 'input': STRING, 'output': STRING,
                                                      'read': STRINGS, 'write': STRINGS, 'apis': STRINGS})},
        'reuse_handlers': STRINGS,
        'handler_updates': {'type': 'array', 'items': obj({'id': STRING, 'spec': STRING, 'read': STRINGS, 'write': STRINGS, 'apis': STRINGS})},
        'new_events': {'type': 'array', 'items': obj({'ui': STRING, 'name': STRING, 'trigger': {'type': 'string', 'enum': TRIGGERS}, 'handler': STRING})},
        'reuse_events': STRINGS,
        'new_types': {'type': 'array', 'items': TYPE},
    }),
    'visual': obj({name: {'type': 'array', 'minItems': 1,
                         'items': {'type': 'string', 'minLength': 1}} for name in VISUAL_FIELDS}),
}

COMMON = '''## Background and role
You are a frontend architect designing one leaf requirement in an existing application.
Produce framework-neutral contracts, not React components, source code, CSS, tests or SQL.
Requirements and scenario steps define behavior. Images define visible structure and states;
image evidence must not invent authentication policies, persistence or server capabilities.
Ancestor descriptions constrain the leaf; do not implement parent or unrelated requirements.

## Identity and response protocol
Return exactly one JSON delta matching the supplied schema, no Markdown or extra properties.
The compiler owns IDs, reqs, the app/page roots and reciprocal UI links. Never output those fields
for new records. Reference supplied records by exact ID; reference same-reply new records by @Name.
Do not guess IDs. Names are semantic, not framework-specific. Preserve unrelated existing design.
validation_feedback and previous_output describe a rejected delta: return a full corrected delta.
Before returning, check each current scenario step has the UI/state/interaction needed in this layer.
Do not create implementation detail nodes or speculative operations just to fill the response.
'''
TYPES = '''## Type and reuse conventions
Use JSON STRINGS containing JSON Schema. Scalar types are string/integer/number/boolean/null;
arrays have items; objects have properties, required and additionalProperties:false. Parameter
names use snake_case. Nullable values use anyOf. String formats: date/date-time/uuid. Enums match
their scalar type. No source HTTP-location extensions in frontend contracts.
Only type, properties, required, additionalProperties, items, enum, anyOf, $ref, format and
description are supported. Other keywords such as minLength/maxLength/pattern/minimum/maximum/
default/oneOf/allOf/$defs are forbidden; format:email/uri is forbidden. Put business validation
in data/handler spec, not extra JSON Schema metadata. Define only actually referenced new_types.
Custom types use #/types/Name and new_types:[{name:"PascalCase",schema:"<JSON Schema>"}].
Never redefine supplied types; recursive types and unused new types are forbidden.
Handler input/output must be object schemas; void is an empty object. Data schema may be any type.
Existing data/handlers/events can be reused only unchanged, with identical behavior and contracts.
Reuse IDs must come from supplied records. The compiler adds current reqs without changing contracts.
'''
TASKS = {
    'ui': '''## Task: discover the UI and bind the requirement
current_task.requirement.pages lists ALL pages involved in this requirement, not just pages first
introduced by it. A repeated name/reference still requires binding the current requirement's
controls/regions and designing its state and interactions in the subsequent passes.
Work only in current_page. selected_ui_tree is this page's nested tree, not a flat component list.
page_mode=new: with an analysis, discover ALL visible regions, controls, text, dialogs and states,
not only those in the leaf's scenarios. Without an image, derive structure from requirement evidence.
Use one representative item template for repeated content. Then bind only the nodes implementing
current requirement description/scenario steps. Do not bind every visual node automatically.
page_mode=bind: the page/reference is already parsed; add_nodes and updates MUST be empty. Bind
existing nodes even when no structure changes; do not return an obsolete reuse_ids field.
page_mode=extend: compare current pages.analysis to baseline_page_states.analysis and insert ONLY
new controls into the existing page. Keep existing nodes and IDs. updates change description only,
recording alternative states without overwriting unrelated requirements. Baseline contains analysis,
not another UI tree. Never duplicate the page or create an app/page node.
New nodes have page-local unique PascalCase names. parent may be a loaded ID or @Name from add_nodes.
updates target loaded existing page nodes. bindings target loaded or new nodes; each description
must extract the requirement's wording or scenario action and explain which part this UI realizes.
The compiler appends this leaf's ID and REPLACES that UI description. Always return bindings for
relevant UI even on first creation. Use the narrowest relevant controls/regions, not just the page.
Requirement bindings describe implemented behavior for traceability and file discovery, not event
permissions. Do not bind an unrelated ancestor merely to authorize an event or state access.
The next passes can use any loaded UI in the declared pages, including their page roots.
''',
    'data': '''## Task: minimal typed frontend state
Design only state required by this leaf: inputs, fetched results, selections, loading and error state.
Do not create separate UIData/UIValue, duplicate DTO fields as unrelated global state, or model static
labels as data. owner describes state lifetime: choose the smallest suitable common UI ancestor of
display consumers AND the controls/regions whose handlers need to read/write that state. Use a page
for coordination between its sibling regions, and app for genuinely cross-page state. Requirement
ownership does not restrict state consumers. The compiler lifts owner to the nearest common ancestor
when actual consumers/events require a wider scope; state ID, schema, initial value and behavior stay
unchanged. initial is a JSON STRING containing a value matching schema; null requires a nullable
schema. Every new_data needs at least one binding. Bind state to its actual loaded UI consumers.
Use globally unambiguous @Name aliases for this reply even though data names are owner-scoped snake_case.
Reuse supplied state unchanged when it already suffices. Return empty arrays if state is unnecessary.
''',
    'interaction': '''## Task: events and their handlers together
Connect scenario actions, page/region load, input changes, submissions, navigation and supported failures.
event_ui_candidates includes all loaded UI in the declared pages and shared app structure. Choose
the actual interaction control, or a page/region root for load initialization, using its exact ID.
An event site does not need a prior binding to this requirement. Keep events at their semantic sites;
do not move a click to an ancestor just to reach state. The compiler coordinates state lifetime by
lifting owners when a handler needs state from sibling regions. Reference supplied state IDs only;
never invent UI/data IDs. Cross-page state uses app lifetime when the referenced pages need it.
Select the handler explicitly in the same response: new_handlers:[{name:"LoadAccount",...}] means
new_events.handler:"@LoadAccount"; an existing handler must appear in reuse_handlers or handler_updates
before a NEW event may reference its exact ID. A handler in the input reuse_handlers catalog alone is
not selected. Reusing an existing event via reuse_events selects its established handler automatically.
new_events.ui must be a loaded UI. The compiler records the current requirement on used event sites
for traceability and source discovery. trigger is a framework-neutral semantic
trigger, not an onClick/effect string. Each event points to exactly one handler. Multiple events may share
one handler. There is at most one event per UI+trigger: reuse it unchanged if its existing handler suffices.
If an established event cannot serve this leaf unchanged, use a permitted handler_updates addition or
report a contradiction; never quietly overwrite another requirement or create a duplicate trigger.
An existing event whose handler needs ADDITIONAL behavior may use handler_updates. This is an explicit
revision, not reuse. Give its loaded handler ID, an ADDITIONAL spec, and additional read/write/apis.
The compiler preserves its ID/name/input/output/original spec and appends these additions. Preserve
all old behavior; do not negate the previous contract. All existing event sites of an updated handler
must be loaded. Also select its event through reuse_events, or attach a new event to the updated handler.
Never list an updated handler in reuse_handlers. Use blocked_reason when a signature change or removal
of old behavior is required, rather than silently changing the contract.
New handlers use globally unique PascalCase names. read/write name loaded data IDs; apis name loaded
backend API IDs, never backend FUNC/DB. APIs must be sufficient, with existing request/response contracts;
never invent an endpoint. api_contracts is the initial relevant slice, not all available capabilities.
api_index lists all existing APIs. Refer to an exact indexed API ID in the normal handler apis list
when needed; the compiler supplies its full contract and types for review on the next attempt.
If reporting a gap involves an unloaded indexed API, name its exact ID/name in blocked_reason so
the compiler can retrieve it. No extra output fields or separate loading protocol are needed.
spec uses {preconditions} action {postconditions}, explaining event parameter
mapping, data updates, API request/response mappings, navigation and failure behavior as needed.
input/output are object contracts. UI-only actions need no API. Keep orchestration in spec, not a separate
handler call graph. Every new or explicitly reused handler must be used by an event. Use @Name for a new
handler. Reuse_events contains loaded IDs only. Data already committed by the Data pass cannot be invented
here. blocked_reason is empty on success. If required state or a backend capability is unavailable,
or an immutable existing event contradicts the requirement, explain the precise gap in blocked_reason;
do not drop required behavior just to make an empty delta pass validation. The compiler feeds the
reason back and rolls back this leaf if the gap cannot be resolved within the attempts.
''',
    'visual': '''## Task: analyze the complete visible page
Analyze the entire image independently of the current requirement's scope. This evidence will support
discovery of a complete UI tree and later visual implementation. Include all visible regions and
meaningful details, even when the current scenario never interacts with them.

## Layout and hierarchy
regions: describe the nested spatial hierarchy from page to header/navigation/sidebar/main/footer,
cards, lists, forms and overlays. Identify each region by a consistent human-readable name and its
parent. For repeated items describe their common structure and visible variations.
layout_cues: describe placement, relative widths/heights, columns, alignment, grouping, spacing,
padding, margins, density, whitespace, overlay position and visible scroll/cropping. Approximate
dimensions or proportions only when supported by the image; do not infer mobile breakpoints.

## Content and visible state
visible_controls: identify every meaningful button, link, input, select, tab, menu, checkbox, icon
control and dialog action, its containing region, visible label/placeholder and selected, disabled,
expanded or other visible state. Describe images/illustrations and recognizable icon shapes.
text_cues: transcribe readable headings, navigation labels, buttons, placeholders, messages, badges
and item content in their original language, associated with their region. Include distinguishing
content in repeated items; mark unreadable text rather than inventing it. Include empty/loading/error
states only when visible. For an absent category explicitly say none is visible.

## Visual style
style_cues: describe overall appearance and region-specific backgrounds, color palette (approximate
colors when useful), text hierarchy, approximate font sizes/weights, line height, borders, separators,
corner radii, shadows, icon treatment, image treatment and selected/disabled emphasis. Preserve
differences between regions; generic claims such as modern/clean are insufficient.

## Evidence limits
The image is the only visual evidence. Separate visible facts from uncertainty. Do not invent unseen
states, interactions, backend behavior, exact fonts or offscreen content. Return concise but complete
descriptive entries in all five categories. IDs, requirement bindings and reference metadata are
compiler-owned and must not be returned.
''',
}
EMPTY = '{"type":"object","properties":{},"required":[],"additionalProperties":false}'
EXAMPLES = {
    'ui': {'add_nodes': [{'name': 'SearchInput', 'kind': 'input', 'description': 'Search term entry.', 'parent': 'UI_000002'}],
           'updates': [], 'bindings': [{'id': '@SearchInput', 'description': 'Enter a search term in the search input.'}]},
    'data': {'new_data': [{'name': 'search_term', 'owner': 'UI_000002', 'spec': 'Search query while this page is open.', 'schema': '{"type":"string"}', 'initial': '""'}],
             'reuse_data': [], 'bindings': [{'ui': 'UI_000003', 'data': '@search_term'}], 'new_types': []},
    'interaction': {'blocked_reason': '', 'handler_updates': [], 'new_handlers': [{'name': 'ChangeSearchTerm', 'spec': '{page open} store entered term {search_term equals input value; no API calls}',
                       'input': '{"type":"object","properties":{"value":{"type":"string"}},"required":["value"],"additionalProperties":false}',
                       'output': EMPTY, 'read': [], 'write': ['DATA_000001'], 'apis': []}],
                    'reuse_handlers': [], 'new_events': [{'ui': 'UI_000003', 'name': 'SearchTermChanged', 'trigger': 'input', 'handler': '@ChangeSearchTerm'}],
                    'reuse_events': [], 'new_types': []},
    'visual': {
        'regions': ['Page > Header containing brand and navigation; Page > Main containing search form and results list. Each result contains a title and summary.'],
        'visible_controls': ['Header: Home link. Main/search form: input with placeholder Search, followed by Search button.'],
        'layout_cues': ['Header spans the top. Main is a centered single column, roughly two thirds of the page width. Search input and button share a horizontal row; results stack below.'],
        'style_cues': ['White page, pale gray header and thin gray separators. Dark headings are larger and heavier than summaries. Search button has blue fill, white text and slightly rounded corners.'],
        'text_cues': ['Header: Example, Home. Search form: Search. First result: Getting started; Read the introductory guide.'],
    },
}
VISUAL_ROLE = '''## Background and role
You are a visual analyst documenting a screenshot for frontend design and implementation.
Your task is to capture the complete visible layout, content and style, rather than design behavior
for a single requirement. current_task identifies the page name and image path; inspect the attached
image. Return exactly one JSON object matching the schema, no Markdown or extra properties.
validation_feedback and previous_output describe a rejected response; return a complete correction.
'''
PROMPTS = {phase: (VISUAL_ROLE if phase == 'visual' else COMMON) + (TYPES if phase in {'data', 'interaction'} else '') + TASKS[phase]
           + '\n## Return format\n' + json.dumps(SCHEMAS[phase], ensure_ascii=False)
           + '\n## Example (IDs are illustrative; use supplied IDs)\n' + json.dumps(EXAMPLES[phase], ensure_ascii=False)
           for phase in SCHEMAS}
