# ARC Final Compiler Guide

ARC treats a structured requirement tree as source code. The compiler resolves
its meaning into frozen intermediate representations, lowers those
representations into a deterministic Web application skeleton, and records the
result in one auditable workspace.

This document is the authoritative description of the current pipeline. The
implementation is Web-only and uses React + TypeScript + Vite + Tailwind CSS v4 on the frontend,
Express + TypeScript on the backend, SQLite + Drizzle for persistence, and one
npm workspace with one lockfile.

## 1. Compiler boundary

```text
requirements.yaml
  -> Requirement IR + dependency graph
  -> Database Schema IR
  -> Backend Design IR + API contracts
  -> Frontend Design IR
  -> initialized Web workspace
  -> Backend and Frontend lowering
  -> npm run build
  -> Code Binding Registry
  -> requirement-by-requirement frozen RED tests
  -> marker-scoped implementation and layered verification
  -> NODE_ACCEPTED
```

The model answers small semantic questions during Database, Backend Design,
Frontend Design, and optional visual-reference analysis. It never chooses
source paths, generated symbol IDs, imports, routes, package versions, or
module call edges. Those facts are compiler-owned and deterministic.

The successful result is `DUAL_DESIGN_FROZEN`, a passing `PROJECT_BUILD` gate,
a validated `CODE_BINDING_READY` registry, and `NODE_ACCEPTED` for every atomic
requirement. Test generation and implementation are one vertical node-by-node
TDD flow: each requirement's tests are frozen and observed RED immediately
before its constrained implementation loop.

## 2. Workspace artifacts

Every compilation writes stage-owned artifacts beneath `.arc`:

```text
.arc/
├── preprocessing/
│   ├── requirement_ir.json
│   └── dependency_graph.json
├── database/
│   ├── database_schema.json
│   └── relationships.json
├── design/
│   ├── requirement_contracts.json
│   ├── api_contracts.json
│   ├── backend/
│   │   ├── api_modules.json
│   │   ├── function_modules.json
│   │   └── db_modules.json
│   └── frontend/
│       ├── visual_references.json
│       ├── screens.json
│       ├── journeys.json
│       ├── api_usages.json
│       └── shared_state_policies.json
├── project/
│   └── project-manifest.json
├── backend/
│   ├── symbol_registry.json
│   ├── file_registry.json
│   ├── type_manifest.json
│   ├── database_schema_manifest.json
│   ├── db_modules_manifest.json
│   ├── func_modules_manifest.json
│   ├── api_modules_manifest.json
│   ├── route_registry.json
│   ├── import_plan.json
│   └── manifest.json
├── frontend/
│   ├── symbol_registry.json
│   ├── file_registry.json
│   ├── route_registry.json
│   ├── import_plan.json
│   └── manifest.json
└── traceability/
    └── requirements.json
```

`traceability/requirements.json` is the only persistent owner of requirement
links. Design artifacts do not duplicate those links. The generated workspace
contains `frontend/`, `backend/`, `shared/`, and the fixed `tests/` workspace
created during Project Initialization. Lowering also writes
`.arc/code/code_bindings.json`, the authoritative IR-to-source and TypeScript
type map used by the node TDD stage. Test materialization adds
`.arc/tests/environment_manifest.json` and `.arc/tests/test_manifest.json`;
executable tests are written beneath the
generated workspace's `tests/unit`, `tests/integration`, and `tests/e2e` roots.

## 3. Requirement preprocessing

The preprocessor parses YAML, normalizes FOLDER and ATOMIC nodes, assigns stable
requirement and scenario IDs, validates dependency references, and emits a
topological implementation order. Optional visual-reference paths are resolved
relative to the requirement directory.

The dependency graph is the only scheduling input used by later stages. A
requirement is never sent to a model together with the entire document when a
local requirement slice is sufficient.

## 4. Database Schema IR

Database design is four serial, stateful passes over each atomic requirement:

1. Entity discovery: reuse or create persistent entities.
2. Field discovery: reuse or create scalar fields and machine-readable field
   properties.
3. Relationship resolution: resolve cardinality and compiler-owned foreign
   keys.
4. Constraint resolution: emit `UNIQUE`, `COMPOSITE_UNIQUE`, or
   `APPLICATION_RULE` constraints.

The schema owns only facts that cannot be derived elsewhere:

- Entity `key`, description, fields, and `requirement_ids`.
- Field name, type, nullability, origin, references, primary-key status,
  description, and machine-readable properties.
- Global relationships and global constraints.
- Requirement traceability.

`relations`, `indexes`, `checks`, `sources`, `logical_type`, and duplicate
entity/table names are not persisted. Relationships, unique indexes, and
field-property checks are derived during lowering. `APPLICATION_RULE` is kept
in the database manifest for the implementation boundary and is not rendered
as SQL. SQLite regex patterns produce a warning and are omitted from DDL
because SQLite does not provide a portable regex function.

The compact persisted database tables are:

```text
.arc/database/database_schema.json
.arc/database/relationships.json
```

Reuse validation checks only the current schema shape, structural references,
and requirement coverage. It does not reconstruct or accept an obsolete schema
format. Invalid model-emitted constraints are warnings (`ARC2240`) and are
omitted; structural schema errors remain fatal.

## 5. Backend Design IR

Backend Design proceeds top-down for each atomic requirement:

```text
Requirement Contract -> API modules -> FUNC modules -> DB modules
```

The Requirement Contract also records compact behavioral obligations for the
requirement scenarios: validation, authorization, computation, state transition,
persistence, transaction, idempotency, external interaction, and error mapping.
Every scenario must be covered. API decomposition assigns each obligation once;
every obligation must reach a FUNC or DB implementation owner, and persistence
obligations must reach a DB leaf. This completeness gate catches missing
responsibilities before source lowering without prescribing implementation code.

The model returns only local semantic descriptions. The compiler allocates
module IDs, preserves child order as call order, and derives reverse callers.
All module interfaces use the same semantic field vocabulary as the requirement
contract. Backend Design does not impose a fixed per-requirement module-count
limit; completeness and graph validation decide whether decomposition is valid.
Each interface field carries `semantic_id`, `name`, `type`, and `required`; the
compiler preserves the `required` flag while materializing child modules.

API contracts and API modules are deliberately different artifacts:

```text
api_contracts.json       id, spec, inputs, outputs, effects
backend/api_modules.json id, callees
```

`api_contracts.json` is the shared frontend/backend interface. Backend API
modules contain only backend call-graph structure; `callers` is derived when
the artifact is read. FUNC and DB module rows retain their interface fields and
`callees`. Allowed edges are API -> FUNC, FUNC -> FUNC/DB, and DB -> leaf.
Cycles and duplicate edges are rejected because they make deterministic
lowering impossible.

The Backend Design prompt receives only the current requirement, its compact
database slice, the fixed parent interface, and the immediate module context.
It does not receive the full design graph or a large output example.

## 6. Frontend Design IR

Frontend Design is a thin product-level IR, not a component plan or a projection
of backend source. One bounded model call sees the requirement set, shared API
contracts, dependency graph, and optional visual evidence, then returns only:

- `visual_references`: content-addressed metadata and optional visual analysis;
- `screens`: routes, purpose, entry conditions, observable states, API needs,
  navigation targets, and visual evidence;
- `journeys`: user actions connecting screens and APIs, including success and
  failure behavior;
- `api_usages`: screen-to-shared-API request and response semantic bindings;
- `shared_state_policies`: only genuinely cross-page/session state, its public
  actions, and persistence policy.

Requirement links remain only in traceability. Layouts, component trees, JSX,
CSS, Props/Event contracts, file decomposition, and page-local state are not
Design IR. They are implementation decisions made together by the frontend
Implementation Agent using the complete connected screen graph and original
visual evidence.

Visual analysis is optional evidence. Path, media, size, and model failures
are warnings; valid metadata is retained and the affected reference is simply
not used for UI scope. Providers that reject `response_format.type=json_schema`
fall back to `json_object`, followed by the same local shape and reference
checks.

Frontend validation is intentionally minimal and lowering-oriented:

- frozen JSON shape and stable Screen/Journey/Shared-State IDs;
- unique routes and Screen symbols;
- closed Screen/Shared-State/visual/navigation references;
- API usages point to existing shared API contracts;
- every atomic requirement has one traceability link.

Frontend semantic field names, semantic IDs, render-obligation IDs, event
names, and Store action names are not rejected for snake_case/camelCase style.
They need only be non-empty bounded strings; lowering quotes property names and
sanitizes values that must become TypeScript identifiers. Stable IR reference
prefixes (`PAGE.`, `STORE.`, `JOURNEY.`), absolute application routes, and
content-addressed visual IDs remain validated because downstream linking relies
on them.

Semantic completeness that can be derived during lowering is not a Design-stage
blocker. Deterministic repair removes unknown properties, clamps bounded text
and arrays, filters unavailable references, and normalizes duplicate list
entries before the minimum checks run.

## 7. Project initialization

After both Design IRs are frozen, `ProjectInitializer` creates the Web project
in a staging directory using the official ecosystem initializer:

```text
npm create vite frontend -- --template react-ts
```

It then creates the Express backend and shared TypeScript workspace, writes one
root `package.json`, pins formal dependencies, installs once, and emits
`.arc/project/project-manifest.json`. Initialization does not generate business
source or a tests directory. The manifest is the sole authority for allowed
skeleton output roots and workspace locations.

## 8. Deterministic Backend lowering

Backend lowering consumes only frozen Database IR, Backend Design IR, Symbol
Registry, File Registry, and Project Manifest:

1. Global Symbol Planning allocates shared contract, entity, module, and
   runtime TypeScript symbols.
2. Global File Planning assigns every symbol/module to an allowed path.
3. Type Lowering emits shared DTOs and entity types.
4. Database Schema Lowering emits SQLite/Drizzle tables and barrels.
5. DB, FUNC, and API Module Lowering emits typed skeleton modules.
6. Global Glue Lowering derives HTTP method/path from API effects and module
   names, then emits routes, app/server glue, barrels, and the import plan.
7. Backend Manifest records the complete file, symbol, module, route, import,
   deployment, and generated-file coverage.

No backend lowering pass calls the model. If an upstream registry is invalid,
the pass fails before writing sources for that pass.

## 9. Frontend runtime seam lowering and generation

The compiler projects the thin Frontend Design IR into an ephemeral runtime IR.
This projection exists only to allocate stable Screen and Shared-State source
targets, typed API clients, routes, imports, and editable implementation regions;
it is not persisted as design and does not introduce Layout or Component IR.

1. Screen, Shared-State, and referenced API-client symbols are allocated.
2. Source targets and routes are assigned under compiler-owned roots.
3. Typed API clients, shared-state runtimes, router, barrels, and minimal Screen
   modules are emitted deterministically.
4. The frontend Implementation Agent receives the connected Screen graph,
   journeys, API usages, visual-reference metadata/analysis, and real runtime seams. It decides
   layout, component decomposition, responsive composition, JSX, CSS utilities,
   and page-local state together while producing the finished interface.
5. Type checking, build, and E2E behavior validate the generated frontend.

The frontend API client uses the Backend Route Registry and shared contract
types; it never imports backend implementation files. `frontend/vite.config.ts`
is generated deterministically from the CLI Web port with the pinned
`@tailwindcss/vite` plugin, so Tailwind utility classes emitted inside editable
Page/Layout/Component regions and `/api` proxy behavior are consistent in
development and preview. The compiler-owned `frontend/src/index.css` imports
Tailwind once; implementation agents never install styling dependencies or
modify the global CSS entry.

## 10. Requirement-local Test Generation and TDD

Project Initialization creates a fourth npm workspace containing Vitest,
Supertest, Playwright, and pinned TypeScript tooling, and installs all four
workspaces with the same root lockfile and `npm ci`. Chromium is installed once
at that boundary and reused by Test Generation; set
`ARC_TEST_INSTALL_BROWSER=0` only when browser provisioning is handled
externally.

Tests are generated serially immediately before implementing each atomic
requirement. The compiler builds a
compact context pack containing the requirement text and scenarios, Requirement
Contract, related Backend and Frontend IR, shared API contracts, the local
Database Schema slice, and owned/dependency Source Cards from the Code Binding
Registry. Referenced TypeScript input/output/Props definitions are included as
type targets, while implementation bodies are never included.

The compiler selects test seams with bounded rules:

- exported FUNC behavior uses Vitest UNIT tests;
- API plus database behavior uses Supertest INTEGRATION tests through the
  exported Express `app`;
- Page/Component/Layout behavior uses Playwright E2E tests through real routes.

The compiler derives required layers from public seams inside each vertical
requirement. UI routes require E2E, API-to-database/effect paths require
Integration, and independently testable rule FUNCs require Unit. The model must
generate one file for every derived layer. This does not mechanically force all
requirements to emit all three layers: the required set follows the actual
module graph and business-rule seams.
The compiler owns test paths and IDs, validates scenario and target coverage,
rejects unknown imports/modules, and limits each requirement to a small number
of core cases. Materialization retries may repair syntax, imports, or symbols;
they may not weaken requirement-derived expectations.

Generated tests must pass TypeScript type checking, Vitest collection, and
Playwright `--list` before they are frozen. Their assertions are then executed
as the node's RED baseline before any implementation patch is requested.
The resulting `.arc/tests/test_manifest.json` has status `TESTS_FROZEN` and
records Requirement -> Scenario -> Test -> IR target -> real source file.

The same `NodeTDDOrchestrator` instance follows
`atomic_implementation_waves`. For each node it generates and freezes only that
node's tests, confirms RED, sends one actionable failure cluster to the bounded
Implementation Agent, applies the proposed marker-scoped edit through Write
Guard, and reruns `typecheck -> Unit -> Integration -> E2E`. A node advances to
`NODE_ACCEPTED` only after its required layers and impacted accepted-node
regressions pass. Dependency nodes must be accepted before the next node starts.

Failure localization is a diagnostic hint rather than a write-authorization
decision. For one node, the Implementation Agent can inspect and edit every
marker-scoped module owned by that requirement, including callers, callees, and
sibling modules; dependency-owned modules remain read-only. Each retry receives
the previous changed modules plus the before/after failure fingerprint so it can
distinguish a useful change from a repeated non-fix.

Implementation context has a 600,000-character safety ceiling. If it is
exceeded, `ARC4532` reports the largest serialized context sections so source
files, frozen tests, Design Context, and failure reports can be projected
independently instead of raising the ceiling blindly.

For ordinary backend TDD repair, the first failure-driven implementation call
contains only the reported writable targets and their direct writable
callers/callees. A later repair may expand to the complete requirement-owned
writable surface. Frozen test source is always projected to the layer present
in the selected failure cluster (Unit, Integration, or E2E); failures without a
layer retain all tests. Frontend bootstrap and frontend-target context scope are
unchanged.

After atomic nodes have been processed, implementation also walks the FOLDER
nodes from the bottom-up `implementation_waves`. A folder does not enter
RED-first Test Generation and does not invent duplicate backend APIs. If Code
Binding assigns it editable Page/Layout/Component/Store or other module targets,
the Implementation Agent receives those owned targets once in `AGGREGATE` mode
to complete the connected frontend and any explicitly owned aggregate module.
Folders with no owned editable targets are accepted as
`NO_IMPLEMENTATION_REQUIRED`. Write Guard continues to enforce exact ownership
and marker-scoped edits in both modes.

When all atomic and aggregate implementation work finishes, the compiler runs
one final workspace build. This refreshes `frontend/dist` after agent edits;
the generated Express backend serves that directory and uses its `index.html`
as the SPA fallback. E2E uses the same deployment shape: build `@arc/frontend`,
then run `@arc/backend` on the single configured port (normally `3000`), and
the browser base URL is that backend URL rather than a separate Vite server.

The generated deployment and E2E target share one compiler port. Set
`ARC_WEB_PORT` before invoking `arc compile` (or use `--port` when the
environment variable is absent); the environment variable takes precedence:

```text
ARC_WEB_PORT=3301 arc compile <requirements> --output-dir <workspace>
```

This value is written into the generated backend default, Playwright
`baseURL`/health probe, frontend development proxy metadata, and project
manifests. At runtime, `PORT` may still override the generated backend default,
but it should be set to the same value when starting the deployed application.

## 11. Validation and failure policy

After the workspace build succeeds, Code Binding lowering joins the frozen
Design IR, symbol/file registries, type manifests, routes, and materialized
sources into `.arc/code/code_bindings.json`. Every Backend module and Frontend
Page/Layout/Component/Store receives a stable source target. Referenced API
clients and public TypeScript types are recorded as additional bindings.

The registry is authoritative for requirement-local Test Generation and TDD. It
records file paths, named exports, public signatures, input/output/Props types,
routes, callees, ownership, and editable marker regions. Validation checks the
real source file, named export, module marker, and implementation markers; line
numbers are deliberately excluded because implementation edits make them
unstable. A valid registry has status `CODE_BINDING_READY` and exposes both
Requirement-to-target and file-to-IR reverse indexes.

Validation protects facts required by the next deterministic pass. It does not
try to prove business behavior that belongs to implementation:

- malformed model shape: repair when lossless, otherwise retry locally;
- unknown optional visual reference: filter and warn;
- unsupported database expression: represent it as `APPLICATION_RULE` or warn
  and omit only the non-executable SQLite expression;
- missing symbol, file, route, contract, or graph edge: fail the owning pass;
- generated source build failure: fail the `PROJECT_BUILD` gate;
- missing source/type binding: fail before node TDD;
- generated test import, symbol, typecheck, or collection failure: retry only
  materialization errors, then fail without changing requirement assertions.

All model requests, compact inputs, raw outputs, repairs, and validation
feedback are written synchronously to the terminal and `.arc/debug.log`.

## 12. Prompt design rules

Every semantic prompt follows the same compact contract:

1. State one task and one ownership boundary.
2. Provide only the current requirement/module and the smallest relevant
   database, contract, registry, or visual slice.
3. State what the model must not decide when the compiler owns it.
4. Use a small output shape supplied through structured output; the prompt
   describes meaning, not a repeated large JSON schema.
5. Give at most one valid example and one empty example where emptiness is
   meaningful.
6. Use explicit `[]`, `null`, and exact ID-copy rules.
7. Return one object and no prose.

The compiler performs all normalization, ID allocation, graph mutation,
reference filtering, path planning, and reverse-index derivation. This keeps
the model interface shallow while compiler modules remain deep: callers provide
a local semantic slice and receive a validated state transition.

## 13. CLI and reproducibility

```text
arc compile <requirements-dir> -o <workspace>
arc doctor
arc config
```

The compiler is synchronous at every pass boundary and serial within each
requirement wave. `--start-from` is an explicit artifact probe for reusing
validated current artifacts in an existing workspace; it is not a second
execution mode and it never converts an invalid artifact into a valid one.
Use `--start-from tdd` after Skeleton lowering and `PROJECT_BUILD` have
succeeded. This boundary validates the persisted Project Manifest and Code
Binding Registry against the current requirements and real source files, skips
all lowering/build passes, validates the already provisioned test environment,
and starts node-by-node TDD without another dependency or browser installation.

Use `--start-from frontend` to regenerate Frontend Design while reusing the
validated Database and Backend Design artifacts in the existing output
workspace:

```text
arc compile <requirements-dir> -o <workspace> --start-from frontend
```

Preprocessing still runs to validate the current requirement tree. Database and
Backend Design are loaded and checked from `.arc`, then Frontend Design and all
later stages run again. Do not combine this mode with `--clean`, because the
upstream artifacts must already exist.

The only environment values required for a full compile are
`OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `MODEL`. Visual settings are optional;
the main model settings are used as fallback. Retry and trace settings only
control bounded local behavior and do not change the IR protocol.
