"""Small load-or-patch model protocol for test generation, implementation and repair."""
import json
from compiler.backend_design.contracts import obj, STRING, STRINGS

SCHEMA = obj({
    'request_files': STRINGS,
    'release_files': STRINGS,
    'edits': {'type': 'array', 'items': obj({'file': STRING, 'content': STRING})},
    'blocked_reason': STRING,
}, required=['request_files', 'edits', 'blocked_reason'])
BACKGROUND = '''## Background and role
You implement ONE leaf requirement in a generated TypeScript Express/React application using TDD.
The database, backend and frontend symbol tables and lowering plans supply exact contracts and file paths.
Parent descriptions constrain behavior; dependency requirements are not additional implementation tasks.
Preserve previously implemented behavior, especially when files/components are shared.
When current_task.recovery is present, design or lowering could not supply complete contracts.
Its file_paths are loaded editable recovery files, not implemented modules. Derive missing interfaces
and behavior from the requirement/scenarios, implement those files, and load shared app/router,
page composition, types or database initialization to wire the real flows. Recovery placeholders
impose no signatures or routes. Preserve valid existing behavior; do not treat an incomplete IR as
a reason to abandon this requirement. Generate HTTP/browser acceptance tests from observable
behavior without importing nonexistent recovery exports; source implementation follows test generation.
During frontend recovery, the recovered backend files and app.ts may be supplied read-only so
you can integrate actual routes even when design-stage API contracts were unavailable.

## File scope and incremental loading
editable_files contains current full text for the entire writable allowlist.
always_loaded_files lists current requirement-associated module/UI files supplied in this invocation. Their
FULL CONTENTS are already in editable_files or read_only_files, not merely available for discovery.
Read those supplied contents directly. You can return edits on the FIRST invocation without a
preliminary file request. Re-reading a supplied file is supported and returns its latest version.
Their editability depends on this phase: editable_files is the ONLY write allowlist; read_only_files
remains read-only. Shared files not directly associated with the current requirement (including schema,
connection, seed, server entrypoints and styles) are listed with paths/summaries in shared_catalog;
their source is loaded only after a request. Directly bound modules are initially supplied even
when shared; released or budget-evicted files can be read again through file_index. Other modules are
retrieved as summaries in related_catalog. Request exact paths via request_files before using/editing them.
Catalogs omit files whose contents are already supplied. Summaries are compact discovery hints;
load a record to obtain its complete source and contract. Backend/frontend implementation source and
discovery catalogs are restricted to that layer; frontend additionally receives needed backend APIs
as contracts only. Unit/integration tests accompany backend, browser tests accompany frontend.
Discovery summaries do not authorize edits. Loading a module supplies its source on the next invocation;
the compiler recomputes phase-specific permissions. Loading does not override read-only restrictions.
Configurations are read-only. Tests are writable in test_generation and repair; initial implementation
phases keep tests read-only. During repair, loaded current or regression acceptance tests can be
corrected when their assumptions, setup, selectors or assertions contradict the governing requirement.
Never guess source.
Do not request a dependency closure or every component. Load the few files required by actual imports,
call chains, composition or observed errors. Any file request must have no edits in that same response.
No recursive context dump; no automatic dependency source inclusion. Preserve public contracts and IDs.
file_index lists discoverable paths/symbols outside the current working set. Read through request_files;
optionally use release_files to drop unneeded full source from context (not from disk). Reads and
releases may be combined, but not combined with edits. Current/failing tests stay pinned. The compiler
may release older optional files to fit the source budget; requested files are delivered whole, never
truncated. Reload any released file before editing it. Permission rules remain phase-specific.
context_state.last_operation pairs the latest read/release request with results and content_ref
pointing into editable_files/read_only_files, where each source appears once with its current content.
Every invocation refreshes source from disk; old source versions and full history remain in traces,
not in this prompt. Context requests do not install rejected edits.

## Patch protocol and corrections
Return exactly the JSON schema: request_files, edits, blocked_reason, and optional release_files, no Markdown.
These are the only allowed top-level keys. Never emit type, json_object, response_format,
schema, status or explanatory fields. JSON Schema's type and the API transport's json_object mode
describe the format; they are NOT fields to copy into your response. Start with {"request_files": ...}.
Each edit supplies only the file path and full replacement content for a loaded writable file.
The compiler manages source versions internally and refreshes context if a file changed during the call.
Do not include unchanged files, delete files or modify .arc artifacts. Edit only the supplied allowlist.
validation_feedback describes the last failure. rejected_patch lists only the files proposed by
the rejected reply: installed:false means NONE of that reply's edits are installed. Their replacement
contents are deliberately omitted. Use the freshly supplied editable_files/read_only_files as the
actual source state; earlier accepted patches remain installed. If the failed proposal coordinated
several files, inspect their actual contents and submit all necessary changes together.
blocked_reason is empty unless a real design/dependency gap prevents implementation. Do not claim success
from reasoning: the compiler runs compilation and tests. Test edits must preserve scenario coverage
and the intended acceptance behavior; never weaken tests merely to match a faulty implementation.

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
Requirements, scenarios and ancestor constraints define acceptance behavior. IR contracts identify typed
seams and paths; visual analysis describes presentation. Neither implementation placeholders nor IR
details may create new acceptance requirements. Do not test every visible control merely because the
UI pass discovered the complete screenshot; test behavior covered by this leaf. If evidence is genuinely
contradictory, explain it in blocked_reason instead of inventing policy, fixture data or exact outcomes.
Do not edit application source yet, nor import seed to insert data inside tests. Assert real behavior,
not placeholders, snapshots of generated skeletons, or the fact that an implementation throws.
Write assertions for the FINISHED product even though current source is a scaffold: successful actions
must assert the expected data, persistence, response and visible interaction from the scenarios.
Never expect Not implemented/NotImplementedError, placeholder HTTP 501, empty placeholder UI or
missing behavior as a successful outcome. Legitimate requirement-defined validation/authentication
errors are still testable. Current source supplies import paths/signatures, never the acceptance oracle.
Generated tests are compiled without execution first; implementation is completed before their first
runtime execution. Do not make tests pass against the scaffold or suppress an expected future failure.
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
Acceptance tests are read-only in this phase. Add supported preexisting records to the shared seed module.
Avoid unrelated refactoring or signatures that contradict design. Do not return no-op patches as completion.
''',
    'repair': '''## Task: diagnose the observed gate failure and repair it
Read gate_results together with the failed test's full source in editable_files. failure_context
identifies automatically loaded failing tests, including earlier requirements' regression tests and
their requirement evidence. Reporter failed_tests includes file, case, location and assertion errors.
gate_results contains failed commands' actual output and structured assertion errors, not merely a
pass/fail table. Read expected/actual values, stack traces, compiler diagnostics and runtime errors.
The current requirement and active contracts/source are supplied with refreshed content.
Accepted edits remain on disk even after a file leaves context; reload it when needed. This is
a cumulative working application, not a reset to the skeleton.
Trace each failure from assertion/expected behavior through the relevant route, service, repository
or UI handler. Request missing source before diagnosing its exact behavior; a stack trace alone is
not sufficient evidence for an unrelated rewrite. Fix the cause rather than only the final symptom.
application_startup/application_runtime failures are application defects: read their startup/server
logs and load the relevant failure_context.startup_files before fixing seed SQL, initialization or
server wiring. Execution timeouts and missing test reports also need diagnosis; they are not proof
of an unavailable environment. Preserve the intended tests when repairing application startup.
E2E case, locator, global-run and command timeouts are test failures and enter this repair loop.
Diagnose missing UI, unwired handlers, failed requests, blocked startup or incorrect test waits;
do not merely increase timeouts or weaken assertions to hide the failure.
Fix actual compilation/runtime/acceptance failures while preserving prior passed requirements.
Determine whether the defect is in implementation or test by comparing the failed assertion with its
requirement/scenario. Correct tests when their expected values, setup, selectors, asynchronous waiting
or API usage misrepresent the intended finished behavior. Preserve every valid acceptance obligation,
including prior regression coverage. Do not delete scenarios, skip/focus checks, reduce assertions,
mock the behavior under test or adopt broken implementation output as the expected result.
Correct source when the test is valid; do not rewrite a correct assertion to avoid fixing the product.
For each test edit use blocked_reason only for real blockers; put the requirement-grounded rationale
in a short code comment beside the corrected setup/assertion, without adding response fields.
Previous accepted patches are installed; rejected proposals are not. Return the minimal coordinated
source changes, including shared source if the failure requires it.
''',
}
TASKS['backend_implementation'] = '''## Task: implement the initial backend version
Tests have been generated and compiled, but have NOT been executed yet. Implement the current leaf's
API/controllers, services/business functions and repositories/storage contracts as one coherent version.
Wire server routes, parameter/result data flow, authentication transport and required startup fixtures.
Only backend source and backend shared source are in scope. Frontend source is deliberately omitted.
Acceptance tests are visible for expected behavior but read-only in this phase.
Preserve contracts and previously implemented behavior. Return complete changed source files; do not
leave required business bodies as placeholders just because there is no runtime failure evidence yet.
'''
TASKS['frontend_implementation'] = '''## Task: implement the initial frontend version
The initial backend implementation is available. Tests have NOT been executed yet. Implement the
current leaf's UI, state, events and handlers, API calls, page composition and navigation; use the
requirement's visual analysis for layout, content and style. Load shared components/App/styles as needed.
Treat current_task.requirement.pages[*].analysis as the visual reference evidence for each named page
and reference path. Follow its concrete visual style: page composition and proportions, alignment,
spacing/padding, color palette and backgrounds, typography hierarchy/weight/size, borders, radii,
shadows, icons, imagery and visible control states. Build a coherent page matching that evidence;
do not substitute a generic dashboard/card layout or leave unstyled placeholder controls.
Use regions/layout_cues for structure, style_cues for styling, and visible_controls/text_cues for
presentation. Requirements/scenarios remain authoritative for behavior and actual business data;
reference appearance does not authorize extra backend features or unrelated interactions.
Preserve existing shared visual conventions and already implemented pages. Load shared CSS or
components before changing them. When analysis is missing, use the established app style and the
requirement evidence rather than claiming to have inspected an unavailable image.
Only frontend source and frontend shared source are in scope. Backend implementation source is deliberately
omitted; use the supplied backend API contracts for integration. Acceptance tests are read-only in this phase.
Preserve contracts and earlier UI
behavior. Wire the required interactions instead of leaving lowering's event.wired=false as completion.
After this phase the compiler executes only the current requirement's acceptance tests, then repairs.
Historical test sources are checked for compilation only; no runtime regression suite is executed.
'''
EXAMPLE = {'request_files': ['backend/src/db/seed.ts'], 'edits': [], 'blocked_reason': ''}
PATCH_EXAMPLE = {'request_files': [], 'edits': [{'file': 'tests/unit/example.test.ts',
                 'content': '<complete changed file contents>'}], 'blocked_reason': ''}
EXAMPLE_PATHS = {
    'test_generation': ('tests/tdd/vitest.config.ts', 'tests/unit/example.test.ts'),
    'backend_implementation': ('backend/src/db/seed.ts', 'backend/src/app.ts'),
    'frontend_implementation': ('frontend/src/App.tsx', 'frontend/src/App.tsx'),
    'implementation': ('backend/src/app.ts', 'backend/src/app.ts'),
    'repair': ('backend/src/app.ts', 'backend/src/app.ts'),
}
def phase_examples(phase):
    load_path, edit_path = EXAMPLE_PATHS[phase]
    load = {**EXAMPLE, 'request_files': [load_path]}
    patch = {**PATCH_EXAMPLE, 'edits': [{**PATCH_EXAMPLE['edits'][0], 'file': edit_path}]}
    return json.dumps(load), json.dumps(patch)

PROMPTS = {phase: BACKGROUND + task + '\n## Return format\n' + json.dumps(SCHEMA)
           + '\n## Example: read a file when needed\n' + phase_examples(phase)[0]
           + '\n## Example: replace a changed file (use actual allowed paths and contents)\n' + phase_examples(phase)[1]
           for phase, task in TASKS.items()}
