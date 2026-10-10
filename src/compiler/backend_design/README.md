# Backend design generation

Read existing requirement IR and database schema, then traverse the requirement
tree in document order. On parent entry extract shared context. Generate child
blueprints, then on parent exit compose the blueprint and add orchestration if needed.
Parents may replace existing modules/interfaces and adjust callers. Changes are
checked in a temporary copy; failed changes do not overwrite the previous design.

The backend IR contains requirements, types and modules, with no lifecycle status,
change reasons or impact records. Module replacements are full module objects.
Deterministic structural, type, schema-reference and graph checks validate replacements.
There are no model coverage reviews, parent revalidation reviews or global semantic
reviews. Behavioral correctness is verified later through implementation tests.

Artifacts in `.arc/design/backend/`:

- `backend_ir.json`: generated blueprint.
- `report.json`: errors and model-call count.
- `passes/*.json`: model inputs, outputs and validation feedback.
- Per-kind module and requirement files: filtered views.

Progress is printed to the terminal and workspace debug log. The result object's
`ok` indicates whether generation completed without errors. The existing pipeline
still receives per-node success/failure information for error reporting.
`projection.py` supplies the deterministic view used by current downstream stages.
