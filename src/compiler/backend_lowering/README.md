# Backend lowering

`BackendLowerer.lower(blueprint, schema, project_manifest, database_manifest,
output_root, default_port=3000)` returns a complete static source plan.
`lower_backend(...)` reads committed artifacts by default, checks destinations,
and writes sources plus lowering registries. No model calls or SQL execution.
The current strategy targets the initialized Express/TypeScript/Drizzle/SQLite
profile. Backend IR remains technology neutral.

Planning finishes before source publication. Invalid schema hashes, graph/type
references, missing database bindings, path collisions, or files without a
producer reject the candidate. Existing modified implementation files are not
overwritten. Stale prior generated paths must be resolved before replacement.

## File layout

```text
shared/src/contracts/
  runtime.ts
  backend/<kind>-<direction>-<contract-hash>.ts
  index.ts
backend/src/
  api/<creation-requirement>/<module-name>.ts
  functions/<creation-requirement>/<module-name>.ts
  db/repositories/<creation-requirement>/<module-name>.ts
  runtime/{context,errors,ids,clock}.ts
  generated/{router,module-registry,dependency-registry}.ts
  {app,server,index}.ts
```

Each module has one file. Creation requirement is derived from the qualified
module ID, not its evolving usage associations; shared modules are generated once.
Contracts use content-addressed filenames and symbols, unaffected by new consumers.
Input, output and trusted context have separate contracts. Named object/enum and
array types are rendered as resolved TypeScript types; optional and nullable are
independent. Database schema/connection/init code belongs to database lowering.

Each module exports a typed business function, dependency object and frozen
blueprint metadata. Spec, calls and given/when/then criteria are retained in the
source. DB dependencies contain only the declared table bindings and the common
connection/query helpers. The connection getter resolves its current binding.
FUNC and DB functions accept `(input, context)`; empty contracts use an empty
record type. Unimplemented business functions throw `NotImplementedError`.

API files additionally export an HTTP handler that obtains server-owned context,
selects query/body input, calls the typed operation and maps thrown errors.
Route method is derived from transitive data effects; route name from the business
operation, with requirement qualification for collisions. Routes and authentication
requirements are recorded in the manifest.

`setRequestContext` is the explicit integration point for identity/context middleware.
Required authentication fails closed without an authenticated server context.
Missing required ambient values fail as unimplemented context providers. Input
coercion/validation, authentication middleware, response-specific business status,
queries and conditional call sequencing remain implementation responsibilities:
prose collaboration is not an executable program, and lowering does not guess it.

Artifacts under `.arc/lowering/backend/` include `lowering.json`, `sources.json`,
`installed-sources.json`, `manifest.json`, and symbols/files/types/module/routes/import
registries. TDD reads these frozen registries rather than replanning source paths.
The public `BackendGlueLowerer` compatibility import remains for legacy checkpoints;
the compiler's current flow uses the unified `lower_backend` entry point.
