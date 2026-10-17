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
Module/type names use ASCII letters and digits, start with an uppercase letter and contain no spaces,
underscores or punctuation. Module-name uniqueness is case-insensitive across ALL three layers;
new_types names must be absent from existing_types and unique within this reply.
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
Only these JSON Schema keywords are supported: type, properties, required, additionalProperties,
items, enum, anyOf, $ref, format, description, and the API-only source extension. Every other
keyword is forbidden, including minLength, maxLength, pattern, minimum, maximum, default,
oneOf, allOf and definitions/$defs. format allows ONLY date/date-time/uuid, never email/uri.
Contracts describe structure and types. Put length, numeric bounds, regex, email validation and
other business checks in spec; do not add unused schema metadata or unused new_types.
For custom reusable DTOs emit new_types:[{name:"NoteView",schema:"<JSON Schema object>"}];
refer with {"$ref":"#/types/NoteView"}. Reuse loaded definitions unchanged; new names cannot overwrite
existing definitions. Define only types reachable from interfaces. Recursive types are unsupported.
Every new type must be used by a new module's interface, directly or through another used type.
The interface root itself must be an object with properties; place $ref on a property or array item,
not in place of the entire payload/response/input/output object. $ref nodes may only add description
and an API parameter source; do not combine $ref with type/properties. anyOf nodes likewise contain
only alternatives plus description/source; put nullability and type inside the alternatives.
required contains unique names declared in that same object's properties. enum values must be unique
and match the scalar type. Empty objects remain valid only when consistent with the declared behavior.
source is an ARC extension to JSON Schema, used ONLY on top-level API payload properties.
It identifies each parameter's HTTP location; it is not part of a reusable DTO or a return value.

## Explicit data flow across module boundaries
Design signatures together with behavior, not just a call graph. Every value needed to execute a
module's spec must come from its declared input, a declared callee's output, or a local computation
explicitly described in spec (e.g. hashing a supplied password, generating an identifier, reading
the clock). Database connection/transaction infrastructure is available; request-specific business
values are NOT implicit globals. FUNC/DB cannot read an HTTP request, browser cookie, current user,
current session or an undeclared runtime context. Pass identity, lookup conditions and write values
as explicit typed parameters. Never use an empty input to hide missing identity or selection data.
Optional/nullable inputs must have explicit absent-value behavior; required does not mean non-null.

Within spec explain the essential mappings in plain language: which input supplies each downstream
argument, which earlier result or local computation supplies derived arguments, and which downstream
result becomes the module's output or an HTTP effect. Describe ordering when one call needs another
call's result. Mapping may rename, select, combine or transform fields; signatures need not be identical.
Callers/callees are relationships, NOT argument mappings or execution order. Do not add a separate
data-flow field; keep these explanations in existing spec and values in existing typed contracts.
Each required callee argument needs a producer. Each caller result or promised effect needs a source.
Do not reuse a module merely because its name fits: its input/output must support these mappings.

An empty input is valid for an unfiltered list or an operation needing no caller-specific values.
An empty output is valid for a command whose caller needs only success/failure. A lookup must return
the data or existence result its caller needs. An insert must return its generated ID/token when a
caller needs it for a subsequent operation or transport response. Do not return passwords/hashes to
the public API merely because an internal module uses them. Keep secret transport effects explicit.

## Reuse and shared symbols
reuse_candidates contains full immutable contracts retrieved by entity/field overlap and lexical relevance.
These are initial suggestions, not the whole system's capabilities. module_index lists all existing
module IDs/names/kinds/routes. Use exact indexed IDs in the normal reuse/callees fields when needed:
the compiler loads their contracts and asks you to review the design before accepting it. No separate
context-request response is needed. Unknown IDs remain invalid. Name/route collisions also load the
matching existing contracts automatically; conflict_contracts is read-only context for other layers.
Prefer reuse ONLY if inputs, outputs, behavior, data requirements and existing callees already suffice
without any modification. Reuse does not permit strengthening a spec or extending an existing call graph.
reuse contains existing IDs only. Never repeat an existing name or method+equivalent route as new.
Only IDs in THIS pass's reuse_candidates may appear in reuse, and their kind must match the pass:
API reuses API; FUNC reuses FUNC; DB reuses DB. List each reuse ID ONCE, combining all callers in
that entry. reference_catalog is a catalog of available callee contracts, not extra reuse candidates.
Calling an existing lower-layer module is declared in a new module's callees; do not also put that
module in reuse. Adding another caller to an unchanged module is allowed; changing that module's
input/output/spec or adding new callees to its established graph is forbidden.
shared is a classification, not a permission: an entity-specific module may have multiple callers
without shared:true. Multiple FUNCs can call the same DB; multiple APIs can call the same FUNC.
shared:true is exceptional: only stable cross-domain infrastructure such as authentication, payment
provider integration or logging with a domain-independent contract. A feature used by two requirements,
CRUD, or an entity-specific helper is not automatically shared. shared_reason must justify the flag;
otherwise shared:false and shared_reason:"". Consider shared candidates only when their responsibility fits.

## Response protocol
Return exactly the JSON delta described by the response schema, without Markdown. Do NOT emit id,
reqs, kind or other compiler-owned properties. In the same reply, refer to a new module as @Name;
refer to existing modules by their exact supplied ID. Never guess a future module ID.
validation_feedback and previous_output describe a rejected reply: return a complete corrected delta.
Every delta contains new_modules, reuse and new_types; use [] when absent, never omit a required key.
All caller/callee arrays contain unique IDs or @Name aliases. Indexed IDs may trigger automatic
contract loading; wiring is accepted only after the full contract is supplied for review.
Loading does not permit modifying a reused module or attaching an arbitrary upstream caller.
IDs merely mentioned in spec and IDs in upstream_api_contracts do not themselves authorize wiring. An @Name must name a
new module in THIS reply; it cannot refer to a future pass's module or a type definition.
Allowed call directions are API -> FUNC, FUNC -> FUNC/DB, DB -> DB; no self calls or cycles.
All new or explicitly reused modules must be reachable from the selected upstream operations;
do not create disconnected helpers. Reciprocal links and requirement association are compiler-owned.
Reusing an established operation also associates its existing downstream graph with this requirement;
do not redeclare that entire graph, reselect its children, or invent new collaborators for it.
Before returning, map every server-side scenario step to an operation or a reused contract.
Split transport, business and storage responsibilities when useful; this is design guidance,
not a requirement to populate every layer. Any module may complete its responsibility directly
with callees:[]. Never create helpers or database access just to fill a layer, or add a module
solely to mirror a visual control. Empty arrays are valid when the current layer needs no modules.
'''
TASKS = {
    'API': '''## Current task: external API interfaces
An API provides an externally callable HTTP service. Discover all required server operations from
this leaf's description and scenarios. Define method, absolute path, payload and response. For request
properties add source:path/query/header/body. Path uses :parameter syntax; path fields are required.
GET/HEAD have no body. Pure client behavior may yield empty arrays.
Declare identity/session transport when required by behavior: a session cookie may be represented
by its named value with source:header; spec explains extraction from Cookie. Do not assume every
authentication request carries a session: session lookup/sign-out may accept an absent value.
For login/registration spec must explain how a generated session token reaches Set-Cookie (or the
requirement's supported carrier), and logout how the carrier is cleared. response describes the
JSON response body; do not expose a session token in that body unless the requirement needs it.
Describe where transport values originate: local API behavior or declared collaborator outputs.
An API may fulfill the operation directly; a FUNC is optional. The compiler generates IDs and
empty callees, and the FUNC pass attaches business collaborators only when useful.
API reuse entries contain only id. Do not design FUNC or DB.
Paths are absolute URL paths only: no query string, fragment, whitespace or brace parameters.
Each :parameter has an exact matching payload property with source:path and is listed in required;
do not add path-source properties absent from the route. Routes with the same method differing only
in parameter names or a trailing slash are equivalent and cannot be created twice.
''',
    'FUNC': '''## Current task: composable business functions
A FUNC implements business behavior: validation, calculation, transformations or orchestration.
It may complete its responsibility directly with callees:[]; not every FUNC needs a helper or DB.
Create database collaborators only when its behavior actually requires database reads or writes.
upstream_modules are the selected APIs. Review their spec to decide whether business functions
are useful; empty API callees does not itself indicate missing work. If the APIs already fulfill
the behavior directly, return empty new_modules/reuse/new_types arrays.
For each new FUNC, callers may name those API IDs or @Name for other new FUNCs; callees may name
@Name for other new FUNCs or loaded existing FUNC/DB IDs. Do not invent or design DB modules yet.
Declare each FUNC-FUNC edge once using callers OR callees; the compiler makes edges reciprocal.
reuse entries contain id and callers (the new upstream attachment), leaving the reused contract intact.
reuse accepts existing FUNC IDs from reuse_candidates ONLY. For an existing DB in reference_catalog,
put its ID directly in the new FUNC's callees; NEVER put that DB in this pass's reuse. You may list
the same DB callee in several new FUNCs. Each such edge is declared once per FUNC; the compiler adds
the reciprocal DB.callers and reqs. The DB pass designs/reuses only still-needed storage operations.
For reuse of an existing FUNC, callers may be selected upstream API IDs or @Name of new FUNCs in this
reply. Merge multiple caller IDs into one reuse entry, rather than duplicate entries for the same ID.
New FUNC callers may only be selected upstream APIs or new local FUNCs. Do not mutate a loaded
existing FUNC by making it call a new helper; declare calls from a new caller toward an existing callee.
Read the upstream API's payload, response AND spec. Declare input for all caller-provided identity,
filters and submitted values needed by this function; remove HTTP source metadata at this layer.
For delegated behavior, output supplies the values its caller needs, including transport effects
when applicable, such as a new session token. For a supplied session_id, explain any required lookup/revocation.
For authentication, explain credential lookup, password verification, session creation and returning
the created token with the public account fields; never assume persistence alone authenticates a browser.
Plan downstream data requirements in spec now, so the DB pass can define sufficient signatures.
''',
    'DB': '''## Current task: storage operations
A DB encapsulates database operations. upstream_modules are selected FUNCs; an empty FUNC callees
list is valid and does not imply missing storage. Design DB modules only for actual database access
required by the supplied behavior. When no storage work is needed, return empty new_modules/reuse/new_types
arrays; do not invent database access to fill the layer. read/write are JSON STRINGS containing arrays of entity or
entity.field targets: entity read means row lookup, entity write means insertion/deletion;
field targets mean reading/writing the particular field. Entity access does not imply every field.
Use loaded database targets only; do not create entities/fields or infer operations from dependencies.
DB may call another DB, never API/FUNC. callers may name supplied FUNC IDs or @Name for new DBs;
callees may name @Name or loaded existing DB IDs. No cycles. List direct read/write targets when present.
The callers declaration is wiring evidence; the compiler records final reciprocal callers and IDs.
reuse accepts existing DB IDs from reuse_candidates ONLY. One reused DB can attach to multiple
selected FUNCs: list it once with callers:["FUNC_ID_1","FUNC_ID_2"]. Existing DBs in reference_catalog
may also be targets in a new DB's callees, without a duplicate reuse entry. New/reused DB callers
must be selected upstream FUNCs or @Name of new local DBs, not arbitrary existing DBs/APIs.
Do not alter already populated upstream FUNC callees when that FUNC is reused from another requirement.
Its established storage graph already suffices by the reuse contract. Only newly designed upstream
FUNCs may receive additional new storage collaborators. An existing DB's downstream graph is immutable.
read/write arrays contain unique exact loaded entity/field targets; use "[]" for an unused operation.
Both may be empty when the module has no direct database access, for example when delegating to
another DB. Do not fabricate targets. A listed entity does not replace explicitly needed
field targets. Do not emit SQL, access objects, new table names or fabricated target IDs here.
upstream_api_contracts provides the relevant enclosing APIs so request identity and transport effects
remain visible. Check upstream FUNC input/output/spec and these API contracts before choosing DB
input/output. Return IDs, tokens, credential material or selected fields needed by upstream operations.
A session lookup/deletion needs an explicit session_id input; "the caller's session" is not a query
condition. Creating a session normally returns its generated session_id/token for the FUNC and API
to pass back to the browser. DB output may contain internal secrets; API output must not leak them.
Explain each attached caller's argument/result mapping in spec using its supplied contract/name.
''',
}
EXAMPLES = {
    'API': {'new_modules': [{**dict(name='GetNote', spec='{valid note_id from route} pass note_id to the business reader and use its title result as the response body {returns the selected note title; absent -> not found; no writes}', shared=False, shared_reason=''),
                            'method': 'GET', 'path': '/api/notes/:note_id',
                            'payload': json.dumps({'type': 'object', 'properties': {'note_id': {'type': 'string', 'format': 'uuid', 'source': 'path'}}, 'required': ['note_id'], 'additionalProperties': False}),
                            'response': json.dumps({'type': 'object', 'properties': {'title': {'type': 'string'}}, 'required': ['title'], 'additionalProperties': False})}], 'reuse': [], 'new_types': []},
    'FUNC': {'new_modules': [{'name': 'ReadNote', 'spec': '{valid note_id supplied by GetNote.payload.note_id} pass input.note_id to a storage lookup and return its title result {output.title is the selected note title; absent -> not found; no writes}', 'shared': False, 'shared_reason': '',
                              'input': '{"type":"object","properties":{"note_id":{"type":"string","format":"uuid"}},"required":["note_id"],"additionalProperties":false}',
                              'output': '{"type":"object","properties":{"title":{"type":"string"}},"required":["title"],"additionalProperties":false}',
                              'callers': ['API_000001'], 'callees': []}], 'reuse': [], 'new_types': []},
    'DB': {'new_modules': [{'name': 'LoadNoteRow', 'spec': '{valid note_id supplied by ReadNote.input.note_id} query note using input.note_id as the row selector and return note.title as output.title for ReadNote {matching title or not found; no writes}', 'shared': False, 'shared_reason': '',
                            'input': '{"type":"object","properties":{"note_id":{"type":"string","format":"uuid"}},"required":["note_id"],"additionalProperties":false}',
                            'output': '{"type":"object","properties":{"title":{"type":"string"}},"required":["title"],"additionalProperties":false}',
                            'read': '["note","note.title"]', 'write': '[]', 'callers': ['FUNC_000001'], 'callees': []}], 'reuse': [], 'new_types': []},
}
WIRING_EXAMPLES = {
    'API': {'new_modules': [], 'reuse': [{'id': 'API_000001'}], 'new_types': []},
    'FUNC': {'new_modules': [{**EXAMPLES['FUNC']['new_modules'][0],
                             'callees': ['DB_000001']}], 'reuse': [], 'new_types': []},
    'DB': {'new_modules': [], 'reuse': [{'id': 'DB_000001', 'callers': ['FUNC_000001', 'FUNC_000002']}], 'new_types': []},
}
PROMPTS = {kind: BACKGROUND + task + '\n## Return format\n' + json.dumps(SCHEMAS[kind], ensure_ascii=False)
           + '\n## Example (IDs are illustrative; use supplied IDs)\n' + json.dumps(EXAMPLES[kind])
           + '\n## Wiring example (assume the shown contracts are loaded and behavior matches)\n'
           + json.dumps(WIRING_EXAMPLES[kind])
           for kind, task in TASKS.items()}
