"""Small model deltas; compiler-owned identities and reciprocal links are omitted."""
import json
from compiler.backend_design.contracts import obj, STRING, STRINGS, TYPE

KINDS = ['region', 'form', 'list', 'item', 'input', 'button', 'text', 'image', 'dialog', 'navigation', 'other']
TRIGGERS = ['click', 'change', 'input', 'submit', 'load', 'focus', 'blur', 'keydown', 'select', 'toggle', 'close']
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
    'visual': obj({'analysis': STRING}),
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
Custom types use #/types/Name and new_types:[{name:"PascalCase",schema:"<JSON Schema>"}].
Never redefine supplied types; recursive types and unused new types are forbidden.
Handler input/output must be object schemas; void is an empty object. Data schema may be any type.
Existing data/handlers/events can be reused only unchanged, with identical behavior and contracts.
Reuse IDs must come from supplied records. The compiler adds current reqs without changing contracts.
'''
TASKS = {
    'ui': '''## Task: discover the UI and bind the requirement
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
''',
    'data': '''## Task: minimal typed frontend state
Design only state required by this leaf: inputs, fetched results, selections, loading and error state.
Do not create separate UIData/UIValue, duplicate DTO fields as unrelated global state, or model static
labels as data. owner is the smallest suitable common UI ancestor of consumers; app is for genuinely
cross-page state. initial is a JSON STRING containing a value matching schema; null requires a nullable
schema. Every new_data needs at least one binding. Bind state only to loaded UI, within owner's subtree.
Use globally unambiguous @Name aliases for this reply even though data names are owner-scoped snake_case.
Reuse supplied state unchanged when it already suffices. Return empty arrays if state is unnecessary.
''',
    'interaction': '''## Task: events and their handlers together
Connect scenario actions, page/region load, input changes, submissions, navigation and supported failures.
new_events.ui must be a loaded UI explicitly bound to this leaf. trigger is a framework-neutral semantic
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
never invent an endpoint. spec uses {preconditions} action {postconditions}, explaining event parameter
mapping, data updates, API request/response mappings, navigation and failure behavior as needed.
input/output are object contracts. UI-only actions need no API. Keep orchestration in spec, not a separate
handler call graph. Every new or explicitly reused handler must be used by an event. Use @Name for a new
handler. Reuse_events contains loaded IDs only. Data already committed by the Data pass cannot be invented
here. blocked_reason is empty on success. If required state or a backend capability is unavailable,
or an immutable existing event contradicts the requirement, explain the precise gap in blocked_reason;
do not drop required behavior just to make an empty delta pass validation. The compiler feeds the
reason back and rolls back this leaf if the gap cannot be resolved within the attempts.
''',
    'visual': '''## Task: complete visual reference analysis
Describe the WHOLE visible page, including spatial hierarchy, major regions, all meaningful controls,
labels, repeated item structure, overlays/dialogs, selections and visible empty/loading/error states.
The image is the only visual evidence. Distinguish visible facts from unknown behavior. Do not discuss
only the current requirement. Return a concise but complete textual hierarchy in analysis.
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
    'visual': {'analysis': 'A page with a top navigation region, a main search input and a results list. The results list repeats a title and description item.'},
}
PROMPTS = {phase: COMMON + (TYPES if phase in {'data', 'interaction'} else '') + TASKS[phase]
           + '\n## Return format\n' + json.dumps(SCHEMAS[phase], ensure_ascii=False)
           + '\n## Example (IDs are illustrative; use supplied IDs)\n' + json.dumps(EXAMPLES[phase], ensure_ascii=False)
           for phase in SCHEMAS}
