"""Separated system instructions and strict model delta formats."""
import json


def obj(properties, required=None):
    return {'type': 'object', 'additionalProperties': False, 'properties': properties,
            'required': list(properties) if required is None else required}


STRING = {'type': 'string'}
STRINGS = {'type': 'array', 'items': STRING}
TYPE = obj({'name': STRING, 'schema': STRING})
COMMON = {'name': STRING, 'spec': STRING, 'shared': {'type': 'boolean'}, 'shared_reason': STRING}
DECLARATIONS = {
    'API': obj({**COMMON, 'method': {'type': 'string', 'enum': ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']},
                'path': STRING, 'payload': STRING, 'response': STRING}),
    'FUNC': obj({**COMMON, 'input': STRING, 'output': STRING, 'callers': STRINGS, 'callees': STRINGS}),
    'DB': obj({**COMMON, 'input': STRING, 'output': STRING, 'read': STRING, 'write': STRING,
               'callers': STRINGS, 'callees': STRINGS}),
}
SCHEMAS = {kind: obj({'new_modules': {'type': 'array', 'items': declaration},
                      'reuse': {'type': 'array', 'items': obj({'id': STRING} if kind == 'API'
                                                              else {'id': STRING, 'callers': STRINGS})},
                      'new_types': {'type': 'array', 'items': TYPE}})
           for kind, declaration in DECLARATIONS.items()}

BACKGROUND = '''## Background and role
You are a backend architect designing a new application's symbol table from one leaf requirement.
The database entities and fields already exist. This stage defines contracts and collaboration;
it emits no implementation, SQL, UI code, fixtures, or database migrations.

## Contract conventions
Use globally unique PascalCase module names describing a single reusable responsibility.
spec is a concise Hoare-style behavioral contract: {preconditions} operation {postconditions};
include explicit failures and frame conditions (what is preserved). Ground every condition in
requirements. State authentication, authorization and transaction requirements when supported;
do not invent unspecified policies, UI configuration endpoints or prerequisite functionality.
The current task's ancestor descriptions constrain behavior, but are not independent features to implement.

payload/response/input/output are JSON STRINGS containing JSON Schema objects with type:"object".
Describe actual parameters in properties:{parameter_name:schema}; use snake_case names.
List mandatory parameters in required:[...]; omit required when none are mandatory. All objects
are closed in the IR: the compiler supplies additionalProperties:false, required:[] and properties:{}
when omitted, including nested objects. Never set additionalProperties:true. Omission of required
means all declared properties are optional; the compiler never guesses which parameters are mandatory.
Void/no parameters may be written as {"type":"object"}; the compiler expands this to the canonical
empty object. Scalar schema types: string, integer, number, boolean, null.
Use arrays with items, objects with properties, enum, and anyOf for nullable values.
Date, datetime and UUID are strings with format date/date-time/uuid. Never use arbitrary type names.
For custom reusable DTOs emit new_types:[{name:"NoteView",schema:"<JSON Schema object>"}];
refer with {"$ref":"#/types/NoteView"}. Reuse loaded definitions unchanged; new names cannot overwrite
existing definitions. Define only types reachable from interfaces. Recursive types are unsupported.
source is an ARC extension to JSON Schema, used ONLY on top-level API payload properties.
It identifies each parameter's HTTP location; it is not part of a reusable DTO or a return value.

## Reuse and shared symbols
reuse_candidates contains full immutable contracts retrieved by entity/field overlap and lexical relevance.
Prefer reuse ONLY if inputs, outputs, behavior, data requirements and existing callees already suffice
without any modification. Reuse does not permit strengthening a spec or extending an existing call graph.
reuse contains existing IDs only. Never repeat an existing name or method+equivalent route as new.
shared:true is exceptional: only stable cross-domain infrastructure such as authentication, payment
provider integration or logging with a domain-independent contract. A feature used by two requirements,
CRUD, or an entity-specific helper is not automatically shared. shared_reason must justify the flag;
otherwise shared:false and shared_reason:"". Consider shared candidates only when their responsibility fits.

## Response protocol
Return exactly the JSON delta described by the response schema, without Markdown. Do NOT emit id,
reqs, kind or other compiler-owned properties. In the same reply, refer to a new module as @Name;
refer to existing modules by their exact supplied ID. Never guess a future module ID.
validation_feedback and previous_output describe a rejected reply: return a complete corrected delta.
Before returning, map every server-side scenario step to an operation or a reused contract. Keep
transport, business decisions and storage responsibilities separate; never add a module solely to
mirror a visual control. Empty arrays are valid when the current layer needs no new modules.
'''
TASKS = {
    'API': '''## Current task: external API interfaces
An API provides an externally callable HTTP service. Discover all required server operations from
this leaf's description and scenarios. Define method, absolute path, payload and response. For request
properties add source:path/query/header/body. Path uses :parameter syntax; path fields are required.
GET/HEAD have no body. Pure client behavior may yield empty arrays.
The compiler generates IDs and empty callees, then the FUNC pass wires business collaborators.
API reuse entries contain only id. Do not design FUNC or DB.
''',
    'FUNC': '''## Current task: composable business functions
A FUNC sits between transport APIs and storage DB modules: it validates, authorizes and orchestrates.
upstream_modules are the selected APIs. Every API with empty callees must call at least one FUNC.
For each new FUNC, callers may name those API IDs or @Name for other new FUNCs; callees may name
@Name for other new FUNCs or loaded existing FUNC/DB IDs. Do not invent or design DB modules yet.
Declare each FUNC-FUNC edge once using callers OR callees; the compiler makes edges reciprocal.
reuse entries contain id and callers (the new upstream attachment), leaving the reused contract intact.
''',
    'DB': '''## Current task: storage operations
A DB encapsulates database operations. upstream_modules are selected FUNCs. Every FUNC with empty
callees must call at least one DB. read/write are JSON STRINGS containing arrays of entity or
entity.field targets: entity read means row lookup, entity write means insertion/deletion;
field targets mean reading/writing the particular field. Entity access does not imply every field.
Use loaded database targets only; do not create entities/fields or infer operations from dependencies.
DB may call another DB, never API/FUNC. callers may name supplied FUNC IDs or @Name for new DBs;
callees may name @Name or loaded existing DB IDs. No cycles. Include at least one read/write target.
The callers declaration is wiring evidence; the compiler records final reciprocal callers and IDs.
''',
}
EXAMPLES = {
    'API': {'new_modules': [{**dict(name='GetNote', spec='{valid note_id} read note {returns the selected note; absent -> not found; no writes}', shared=False, shared_reason=''),
                            'method': 'GET', 'path': '/api/notes/:note_id',
                            'payload': json.dumps({'type': 'object', 'properties': {'note_id': {'type': 'string', 'format': 'uuid', 'source': 'path'}}, 'required': ['note_id'], 'additionalProperties': False}),
                            'response': json.dumps({'type': 'object', 'properties': {'title': {'type': 'string'}}, 'required': ['title'], 'additionalProperties': False})}], 'reuse': [], 'new_types': []},
    'FUNC': {'new_modules': [{'name': 'ReadNote', 'spec': '{valid note_id} load note {selected note or not found; no writes}', 'shared': False, 'shared_reason': '',
                              'input': '{"type":"object","properties":{"note_id":{"type":"string","format":"uuid"}},"required":["note_id"],"additionalProperties":false}',
                              'output': '{"type":"object","properties":{"title":{"type":"string"}},"required":["title"],"additionalProperties":false}',
                              'callers': ['API_000001'], 'callees': []}], 'reuse': [], 'new_types': []},
    'DB': {'new_modules': [{'name': 'LoadNoteRow', 'spec': '{valid note_id} query note {matching row or not found; no writes}', 'shared': False, 'shared_reason': '',
                            'input': '{"type":"object","properties":{"note_id":{"type":"string","format":"uuid"}},"required":["note_id"],"additionalProperties":false}',
                            'output': '{"type":"object","properties":{"title":{"type":"string"}},"required":["title"],"additionalProperties":false}',
                            'read': '["note","note.title"]', 'write': '[]', 'callers': ['FUNC_000001'], 'callees': []}], 'reuse': [], 'new_types': []},
}
PROMPTS = {kind: BACKGROUND + task + '\n## Return format\n' + json.dumps(SCHEMAS[kind], ensure_ascii=False)
           + '\n## Example (IDs are illustrative; use supplied IDs)\n' + json.dumps(EXAMPLES[kind])
           for kind, task in TASKS.items()}
