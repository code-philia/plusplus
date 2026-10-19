# Referenced requirements bundles

The preprocessor accepts a YAML file, a requirements bundle directory, or a parent
directory containing `requirements/requirements.yaml`. Plain YAML documents keep
the existing design path. A bundle is detected by its `reference/` directory or
`[ER: path#fragment]`, `[OPA: path#fragment]`, and `[BPMN: path#fragment]` citations.
Paths are local to the directory containing the YAML; external paths and symlinks
escaping that directory are rejected. Inputs are never rewritten.

`requirement_ir.json` retains the original descriptions and adds `bundle`:

- `files`: relative paths and SHA256 hashes of consumed documents and capture files.
- `bindings`: each requirement's resolved/unresolved citations, entity CRUD,
  permission IDs, process IDs and accepted UI-state associations.
- `er`: normalized entities, fields and relationships with source locations and IDs.
- `ui`: navigation, routes, collected states, contracts, recipes, packaged captures,
  design-system evidence and validation metadata. Only `index.json.node_links` is
  used for accepted associations; candidate links are not promoted.
- `authorization`: the published policy data, Rego and input schema, retained as
  evidence. Loading this metadata does not install authentication or authorization.
- `processes`: referenced BPMN nodes, lanes, sequence flows and conditions.
- `diagnostics`: unresolved references, uncovered requirements, unlinked pages,
  uncollected states, partial policy coverage and logical type mapping obligations.

UI index schema version 2 is supported. Capture paths come from the published index,
not the historical exploration paths inside state files. Capture hashes, state and
recipe identities, link ownership, and parent states are checked. States declared
but not collected remain coverage gaps; they do not require nonexistent evidence
files. Missing files for collected/accepted evidence are errors.

The source identity includes `bundle_sha256`, combining the YAML bytes with the
consumed reference hashes. Reusing preprocessing after references change requires
a new workspace. `.arc/preprocessing/bundle-report.json` records bindings and
diagnostics for both the main compiler and the standalone database-design script.
Invalid bundles produce a report before compilation stops.

## Importing ER into database design

`reference/er/model.mmd` supports the subset emitted by the domain serializer:
entity blocks, typed attributes, `PK`/`UK`, quoted labels, `(required)` and explicit
relationship cardinalities. Unsupported syntax/types are errors. The optional
`reference/er/model.json` companion accepts the domain ER JSON shape (`entities`
with `id/name/attributes`, and `relationships` with `id/name/source_ref/target_ref/
source_cardinality/target_cardinality`). This same-bundle companion takes priority
over `model.mmd`, preserving relationship IDs lost by Mermaid serialization.
Unspecified JSON cardinalities are rejected rather than interpreted as evidence.

Database design first seeds the published entities and relationships, then runs
the existing entity, field and relationship passes to enrich the imported design
from requirement descriptions. CamelCase source IDs become snake_case compiler
symbols. Source IDs, original logical types, locations and requirement ownership
are retained in `.arc/design/database/import-report.json` and the final ER's
`reference` metadata. The pre-enrichment model is written to
`.arc/design/database/imported_er_model.json`.

The compiler retains its UUID `id` identity. A single source business PK becomes a
non-null unique field; a composite source PK becomes a non-null composite unique
key. A non-UUID source field named `id` is rejected as an identity conflict. Source
fields retain their type, required and unique facts across model enrichment.
Imported relationship endpoints and cardinalities cannot be redefined. The final
ER is refined into the existing SchemaIR, generating foreign keys, one-to-one
uniqueness and many-to-many association tables, then lowered to SQLite sources.

Field design can additionally return root `unique_constraints`, an array of
`{entity, fields, description}`, for explicit requirement-backed composite business
keys. These constraints survive ER serialization and relational refinement. Scalar
bounds, lengths and enums are emitted as SQLite CHECK constraints as well as
SchemaIR metadata. Decimal fields map to `number`, with precision/rounding recorded
as a backend obligation; `file` maps to an attachment-reference string. Neither
mapping supplies an upload service or financial precision policy.

Mermaid diagrams do not encode the original `rel_*` IDs. Such citations remain
unresolved diagnostics. Relationships are still imported with stable generated
keys, and ownership is attributed by their endpoint entity references (recorded as
`binding_basis: endpoint_references`), without pretending the citation was resolved.
Unreferenced global facts belong to the actual root requirement. No sibling
`extraction.json` is consulted and no relationship IDs are guessed.

This input/database integration retains UI and policy evidence for later stages.
It does not yet generate a shell/routes/page-state design from that metadata or
install policy enforcement; explicit YAML `pages` remains the frontend input.
