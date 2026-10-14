"""Small load-or-patch model protocol for test generation, implementation and repair."""
import json
from compiler.backend_design.contracts import obj, STRING, STRINGS

SCHEMA = obj({
    'request_files': STRINGS,
    'edits': {'type': 'array', 'items': obj({'file': STRING, 'expected_sha256': STRING, 'content': STRING})},
    'blocked_reason': STRING,
})
BACKGROUND = '''## Background and role
You implement ONE leaf requirement in a generated TypeScript Express/React application using TDD.
The database, backend and frontend symbol tables and lowering plans supply exact contracts and file paths.
Parent descriptions constrain behavior; dependency requirements are not additional implementation tasks.
Preserve previously implemented behavior, especially when files/components are shared.

## File scope and incremental loading
editable_files contains current full text and sha256 for the entire writable allowlist.
Current requirement-associated modules/UI files are always loaded and editable. All shared modules are
listed as summaries in shared_catalog, but their source is not loaded automatically. Other modules are
retrieved as summaries in related_catalog. Request exact paths via request_files before using/editing them.
Discovery summaries do not authorize edits. Loading a shared/source module makes it editable on the next
invocation; configurations are read-only, tests are writable only in test_generation. Never guess source.
Do not request a dependency closure or every component. Load the few files required by actual imports,
call chains, composition or observed errors. Any file request must have no edits in that same response.
No recursive context dump; no automatic dependency source inclusion. Preserve public contracts and IDs.

## Patch protocol and corrections
Return exactly the JSON schema: request_files, edits, blocked_reason, no Markdown.
These THREE keys are the entire top-level response. Never emit type, json_object, response_format,
schema, status or explanatory fields. JSON Schema's type and the API transport's json_object mode
describe the format; they are NOT fields to copy into your response. Start with {"request_files": ...}.
Each edit replaces a WHOLE loaded file, supplies its current expected_sha256 and full new content.
Do not include unchanged files, delete files or modify .arc artifacts. Edit only the supplied allowlist.
validation_feedback and previous_output are rejected proposals; rejected files are not installed.
blocked_reason is empty unless a real design/dependency gap prevents implementation. Do not claim success
from reasoning: the compiler runs compilation and tests. Do not weaken frozen acceptance tests to pass.

## Shared database and seed
backend/src/db/seed.ts is the single cumulative seed module. Every application startup resets only its
selected database connection, creates schema, then inserts all accumulated fixture data before listening.
Load seed.ts when GIVEN or description explicitly needs existing records. Add fixtures from this leaf;
preserve all earlier fixtures. Do not create a session merely because a user account exists. Use the same
encoding/hashing as real authentication. Seed must not derive fixtures from test code or incoming requests.
Database connection and table-creation source are shared modules that can be loaded and edited explicitly.
Do not add per-requirement seeds, test-local inserts, endpoint seeding or lazy fixture hacks.
'''
TASKS = {
    'test_generation': '''## Task: generate acceptance tests before implementing
Write tests only at test_targets. Derive observable expected results from this requirement's scenarios.
Do not edit application source yet, nor import seed to insert data inside tests. Assert real behavior,
not placeholders, snapshots of generated skeletons, or the fact that an implementation throws.
unit: test business contracts when isolated seams are sufficient. integration: HTTP/DB interactions.
e2e: user-visible browser behavior when this leaf has frontend UI. Use the loaded project test runner
configuration and contracts. Tests must compile and be discovered; do not use skip/todo/fixme or mocks
that replace the behavior under test. Pure compilation is not an acceptance assertion.
Application starts in an isolated database with the cumulative startup seed; assertions never preseed
inside test code. Initially create every assigned target; on correction return only changed files,
with full replacement text. Preserve valid existing tests; unchanged targets need no edit.
Unsupported test layers have no target. Infrastructure failures are repaired by the executor,
not by weakening tests or changing expected behavior.
''',
    'implementation': '''## Task: implement the initial version before running tests
Implement backend and frontend contracts needed by this leaf, load additional files when needed.
Wire routes to actual API modules, handlers to UI controls/state/API calls, and page composition.
The lowering plan's event.wired=false is a placeholder, not completed interaction behavior.
Acceptance tests are frozen and read-only. Add supported preexisting records to the shared seed module.
Avoid unrelated refactoring or signatures that contradict design. Do not return no-op patches as completion.
''',
    'repair': '''## Task: diagnose the observed gate failure and repair it
Read gate results and current source. Request missing files before diagnosing their exact behavior.
Fix actual compilation/runtime/acceptance failures while preserving prior passed requirements.
Tests are frozen; do not change expected values, skip checks, or fake responses to satisfy a fixture.
Previous accepted patches are installed; rejected proposals are not. Return the minimal coordinated
source changes, including shared source if the failure requires it.
''',
}
TASKS['backend_implementation'] = '''## Task: implement the initial backend version
Tests have been generated and compiled, but have NOT been executed yet. Implement the current leaf's
API/controllers, services/business functions and repositories/storage contracts as one coherent version.
Wire server routes, parameter/result data flow, authentication transport and required startup fixtures.
Load shared files when necessary. Frontend source and acceptance tests are read-only in this phase.
Preserve contracts and previously implemented behavior. Return complete changed source files; do not
leave required business bodies as placeholders just because there is no runtime failure evidence yet.
'''
TASKS['frontend_implementation'] = '''## Task: implement the initial frontend version
The initial backend implementation is available. Tests have NOT been executed yet. Implement the
current leaf's UI, state, events and handlers, API calls, page composition and navigation; use the
requirement's visual analysis for layout, content and style. Load shared components/App/styles as needed.
Backend source and acceptance tests are read-only in this phase. Preserve contracts and earlier UI
behavior. Wire the required interactions instead of leaving lowering's event.wired=false as completion.
After this phase the compiler runs current acceptance tests and all prior regression tests, then repairs.
'''
EXAMPLE = {'request_files': ['backend/src/db/seed.ts'], 'edits': [], 'blocked_reason': ''}
PATCH_EXAMPLE = {'request_files': [], 'edits': [{'file': 'tests/unit/example.test.ts',
                 'expected_sha256': '<current SHA256 from editable_files>',
                 'content': '<complete changed file contents>'}], 'blocked_reason': ''}
PROMPTS = {phase: BACKGROUND + task + '\n## Return format\n' + json.dumps(SCHEMA)
           + '\n## Example: load first, then patch in the next invocation\n' + json.dumps(EXAMPLE)
           + '\n## Example: replace a changed file (use actual paths, hashes and contents)\n' + json.dumps(PATCH_EXAMPLE)
           for phase, task in TASKS.items()}
