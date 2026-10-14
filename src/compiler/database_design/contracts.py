"""Model response contracts and prompts for the three ER discovery passes."""

from __future__ import annotations

import re
from typing import Any




IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


FIELD_TYPES = {"string", "integer", "number", "boolean", "date", "datetime", "json", "uuid", "foreign_key"}


RELATIONSHIP_TYPES = {"ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_MANY"}


CONSTRAINT_TYPES = {
    "PRIMARY_KEY", "FOREIGN_KEY", "NOT_NULL", "UNIQUE", "COMPOSITE_UNIQUE"
}


FIELD_ORIGINS = {"REQUIREMENT", "RELATIONSHIP", "SYSTEM"}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


ENTITY_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["requirement_id", "reuse_entities", "new_entities"],
    "properties": {
        "requirement_id": {"type": "string"},
        "reuse_entities": {"type": "array", "items": {"type": "string"}},
        "new_entities": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["key", "description"],
                "properties": {"key": {"type": "string", "pattern": IDENTIFIER_PATTERN.pattern}, "description": {"type": "string"}},
            },
        },
    },
}


FIELD_PROPERTIES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "min_length", "max_length", "enum", "minimum", "maximum", "default", "unique", "comparison",
    ],
    "properties": {
        "min_length": _nullable({"type": "integer", "minimum": 0}),
        "max_length": _nullable({"type": "integer", "minimum": 0}),
        "enum": _nullable({"type": "array", "items": {"type": "string"}, "minItems": 1}),
        "minimum": _nullable({"type": "number"}),
        "maximum": _nullable({"type": "number"}),
        "default": _nullable({"type": ["string", "number", "boolean"]}),
        "unique": _nullable({"type": "boolean"}),
        "comparison": _nullable({"enum": ["exact", "case_insensitive"]}),
    },
}


FIELD_DECLARATION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["name", "type", "nullable", "description", "properties"],
    "properties": {
        "name": {"type": "string", "pattern": IDENTIFIER_PATTERN.pattern},
        "type": {"type": "string", "enum": sorted(FIELD_TYPES - {"uuid", "foreign_key"})},
        "nullable": {"type": "boolean"},
        "description": {"type": "string"},
        "properties": FIELD_PROPERTIES_SCHEMA,
    },
}


FIELD_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["requirement_id", "entities"],
    "properties": {
        "requirement_id": {"type": "string"},
        "entities": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["entity", "new_fields", "update_fields"],
                "properties": {
                    "entity": {"type": "string"},
                    "new_fields": {
                        "type": "array",
                        "items": FIELD_DECLARATION_SCHEMA,
                    },
                    "update_fields": {
                        "type": "array",
                        "items": FIELD_DECLARATION_SCHEMA,
                    },
                },
            },
        },
    },
}


RELATIONSHIP_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["entity_key", "relationships"],
    "properties": {
        "entity_key": {"type": "string"},
        "relationships": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["key", "parent", "child", "type", "parent_min", "parent_max", "child_min", "child_max", "description", "evidence_requirement_ids"],
                "properties": {
                    "key": {"type": "string", "pattern": IDENTIFIER_PATTERN.pattern},
                    "parent": {"type": "string"}, "child": {"type": "string"},
                    "type": {"type": "string", "enum": sorted(RELATIONSHIP_TYPES)},
                    "parent_min": {"type": "integer", "enum": [0, 1]},
                    "parent_max": {"type": "string", "enum": ["one", "many"]},
                    "child_min": {"type": "integer", "enum": [0, 1]},
                    "child_max": {"type": "string", "enum": ["one", "many"]},
                    "description": {"type": "string"},
                    "evidence_requirement_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                },
            },
        },
    },
}


ENTITY_INSTRUCTIONS = """You are an ER-modeling specialist focused on persistent domain entities.
Your task is to discover entities in the shared ER model for one requirement.
For exactly one requirement, decide which persistent entities it reuses and which persistent entities must
be introduced. Existing entities are compiler symbols: reuse them when their meaning fits. Do not produce fields,
relationships, constraints, APIs, modules, or SQL. Entity keys are singular snake_case. Case-only name differences
are the same symbol. Terms explicitly paired by the requirement, such as "journey/train", are aliases: reuse the
existing entity whose description matches. UI selection, page context, form state, and other transient nouns are
not database entities. Include every persistent entity used by the requirement. When wording permits several names,
choose the existing entity whose persistent meaning best matches the requirement. If none matches, introduce one
concise entity. Do not emit alternatives or unresolved decisions.

Return exactly one JSON object with these keys and no others:
- requirement_id: copy the supplied requirement.requirement_id exactly; example IDs below are illustrative.
- reuse_entities: array of existing entity keys; use [] when none.
- new_entities: array of {"key": singular_snake_case, "description": string}; use [] when none.
- read/write: arrays of entity keys accessed by this requirement; use [] when none.

Valid output example:
{"requirement_id":"REQ-3.2","reuse_entities":["traveler","train_service"],"new_entities":[{"key":"booking","description":"A confirmed booking."}],"read":["traveler","train_service"],"write":["booking"]}
Valid empty output example:
{"requirement_id":"REQ-4.1","reuse_entities":[],"new_entities":[],"read":[],"write":[]}
"""


ER_SCOPE = """Scope: design the initial schema for a brand-new SQLite project, not migrations, existing data,
fixtures, query/index tuning, or inferred implementation optimizations. ER contains persistent domain
entities, logical attributes, semantic relationships, and explicitly requirement-backed storage invariants.
Entity names beginning with arc_ or sqlite_ are reserved for the compiler/database.
A storage invariant applies to every stored instance, not just a form, operation, actor, or state transition.
Do not place UI validation, authorization, workflows, concurrency protocols, cross-field calculations,
regex/email/URL validation, or time-relative rules (past/future/current time) in ER properties or descriptions
as storage constraints. These belong to backend specifications derived from the original requirements.
Unknown or unexpressible rules must not be approximated by invented columns or constraints.
"""

FIELD_INSTRUCTIONS = ER_SCOPE + """You are an ER-modeling specialist focused on persistent attributes.
Analyze exactly one requirement using related_entities. Do not create entities, foreign keys, relationships,
cross-field constraints, modules, SQL or physical database options. Do not persist transient inputs or display values.

- A missing field goes in new_fields. An existing field needing a contribution goes in update_fields.
- Both lists use complete declarations with name, type, nullable, description, properties.
- An update is THIS requirement's complete contribution, not a replacement of the shared definition.
  Include only storage invariants justified by this requirement; null means this requirement contributes no
  constraint. Do not copy another requirement's bounds/default/enum just because they are in the shared field.
- The compiler merges contributions: type must agree; nullable uses AND; lower bounds use max, upper bounds
  use min; enum sets intersect; uniqueness uses OR. Specified defaults and unique comparison semantics must
  agree. Conflicting proposals are recorded separately; do not assume incompatible contributions replace
  the accepted shared field definition.
- Updating this requirement's contribution cannot remove any other requirement's facts.
  own_field_contributions contains this requirement's earlier declarations: preserve its still-applicable
  invariants when revising a complete contribution. Do not copy facts owned only by other requirements.
- List existing fields used unchanged in reuse_fields (field names within the entity).
  This records requirement association without changing storage invariants.
- Do not rename fields or modify id, *_id, SYSTEM or RELATIONSHIP fields.
  Return only entities with changes or reused fields.

Field attributes:
- name: singular snake_case matching ^[a-z][a-z0-9_]*$.
- type: string, integer, number, boolean, date, datetime or json. uuid/foreign_key are compiler-owned.
- nullable: true unless this requirement explicitly requires the field on every stored instance.
  Mandatory form input alone is insufficient for nullable=false.
- description: persistent domain meaning, without workflows or hidden constraints.
- properties: all eight keys below; null for unspecified/inapplicable rules. Only explicit storage invariants:
  min_length/max_length: nonnegative integer, string only, inclusive Unicode code point count; NUL is unsupported;
  minimum/maximum: finite inclusive bounds, integer/number only; integral values for integer;
  enum: nonempty distinct strings, string only, allowed values for ALL stored rows (not a dropdown's subset);
  unique: true for an explicit single-field business key; false/null contributes no constraint;
  comparison: exact/case_insensitive for a unique string; null means exact when unique=true.
    case_insensitive means ASCII A-Z folding only, not Unicode case folding or locale-specific comparison;
  default: a type-compatible literal string/finite number/boolean, null means no contributed default.
    A default is a storage insertion default, not a form initial value, example, SQL expression or current time.

Return {"requirement_id": supplied requirement.requirement_id, "entities": [
  {"entity": existing_key, "new_fields": [complete declarations], "update_fields": [complete declarations],
   "reuse_fields": [existing field names]}
], "read": [entity or entity.field], "write": [entity or entity.field]}.
Omit entities only when all three field lists are empty. Access arrays describe actual behavior.
Example:
{"requirement_id":"REQ-1.2","entities":[{"entity":"user","new_fields":[],"update_fields":[{"name":"username","type":"string","nullable":true,"description":"Account username.","properties":{"min_length":null,"max_length":20,"enum":null,"minimum":null,"maximum":null,"default":null,"unique":null,"comparison":null}}],"reuse_fields":[]}],"read":["user","user.username"],"write":[]}
Example assumes user.username already exists in related_entities and this requirement contributes
the storage bound. For a missing field use new_fields with the same declaration shape.
Unchanged field reuse example: {"requirement_id":"REQ-1.2","entities":[{"entity":"user","new_fields":[],"update_fields":[],"reuse_fields":["username"]}],"read":["user","user.username"],"write":[]}.
No access or changes example: {"requirement_id":"REQ-1.2","entities":[],"read":[],"write":[]}.
"""
ENTITY_INSTRUCTIONS = ER_SCOPE + ENTITY_INSTRUCTIONS


RELATIONSHIP_INSTRUCTIONS = ER_SCOPE + """You are an ER-modeling specialist discovering relationships from an existing shared ER graph.
The analysis unit is focus_entity, not a single requirement. Read its complete fields and associated
related_requirements together; they describe the domain meaning of this entity across the application.
The er_graph contains every entity's name, description, attribute headers and requirement IDs, plus existing
relationships. requirement_catalog contains bounded excerpts for all requirements; related_requirements
contains full requirements associated with the focus entity and their direct prerequisite requirements.
Use the graph to discover plausible endpoint entities, then use requirement evidence to establish relationships.
Emit only relationships incident to focus_entity. Endpoints may be any existing graph entity, including itself.
Do not scan all entity pairs. Do not infer associations solely from shared pages, similar names or dependencies.
A read-only requirement may establish a persistent domain association. Cite actual supporting requirement IDs in
nonempty evidence_requirement_ids; no synthetic entity-analysis ID is a requirement. Do not invent facts missing
from truncated excerpts. Do not create entities, attributes, foreign keys, APIs, modules, or SQL.

For each relationship, choose a stable key describing its semantic role (for example booking_creator or order_item),
the two entity endpoints, and cardinality at both ends. `parent` is the entity whose identity is referenced when the
relationship is lowered; `child` is the entity that references it. `parent_min`/`parent_max` describe how many child
instances one parent may have; `child_min`/`child_max` describe how many parent instances one child may have.
Minimum is 0 or 1; maximum is `one` or `many`. Derive type consistently: ONE_TO_ONE means one/one, ONE_TO_MANY
means parent_max=many and child_max=one, MANY_TO_MANY means many/many. Do not use parent/child direction to imply
optionality; use the four cardinality values.

An existing relationship with the same key is the same semantic relationship. Repeat its complete current definition
only when additional requirement evidence or stronger minimum participation is found. Minimum participation is a contribution: 0 means no minimum contributed, 1 means an explicitly required
storage invariant. The compiler merges minima using max; a later 0 cannot erase an earlier 1. Preserve endpoints, direction and maximum cardinalities; contradictory definitions
must not be silently replaced or bypassed by inventing a new key. Different roles between the same entity pair require
different keys. For self-relationships, explain the two roles in description. Do not emit unchanged relationships merely
because entities co-occur. Minimum participation must hold for every stored instance, not only after
a particular workflow step. Parent-side and many-to-many minima become explicit backend transaction obligations,
not database-global scans. Do not assert them merely because the UI displays related records. Return [] when no stable association
is established. Self-relationships are allowed when the requirement clearly describes them. Do not emit alternatives.

Return exactly one JSON object with entity_key (copy focus_entity.key) and relationships. Each relationship contains exactly:
key, parent, child, type, parent_min, parent_max, child_min, child_max, description, evidence_requirement_ids.

Example:
{"entity_key":"traveler","relationships":[{"key":"traveler_bookings","parent":"traveler","child":"booking","type":"ONE_TO_MANY","parent_min":0,"parent_max":"many","child_min":1,"child_max":"one","description":"A traveler may own many bookings; every booking belongs to one traveler.","evidence_requirement_ids":["REQ-3.2"]}]}
Example assumes both entities exist in er_graph and REQ-3.2 explicitly supports the relationship.
No-relationship example: {"entity_key":"traveler","relationships":[]}
"""


# Access is reported by the requirement, not inferred from schema creation.
for decision_schema in (ENTITY_DECISION_SCHEMA, FIELD_DECISION_SCHEMA):
    for operation in ('read', 'write'):
        decision_schema['properties'][operation] = {'type': 'array', 'items': {'type': 'string'}}
        decision_schema['required'].append(operation)
field_entity_schema = FIELD_DECISION_SCHEMA['properties']['entities']['items']
field_entity_schema['properties']['reuse_fields'] = {'type': 'array', 'items': {'type': 'string'}}
field_entity_schema['required'].append('reuse_fields')
ACCESS_INSTRUCTIONS = """
Also return read and write arrays for the CURRENT requirement's actual data access, not schema
creation. read contains an entity key for querying its rows, or entity.field for reading a
specific persisted field. write contains an entity key for inserting/deleting rows, or
entity.field for writing a specific field. Both modes may apply to the same target.
Entity access does not imply access to every field; report each explicitly supported field
access separately. Only this requirement's described behavior/scenario steps justify access;
do not attribute prerequisite requirement operations merely because dependencies are supplied.
Use [] for no access. Never guess read/write from new versus reused schema definitions.
All new entity/field descriptions are one concise sentence explaining domain meaning/purpose.
"""
ENTITY_INSTRUCTIONS += ACCESS_INSTRUCTIONS + """
In entity discovery, read/write targets are entity keys only (fields are discovered later).
"""
FIELD_INSTRUCTIONS += ACCESS_INSTRUCTIONS + """
Return reuse_fields in every entity entry, including [] when none. Any accessed existing field,
including compiler-owned id, must be listed in reuse_fields unless declared in new_fields or
update_fields. read/write may contain entity keys and entity.field references. Do not invent
fields to satisfy access. Refer only to related_entities and fields declared in this reply.
Empty output: {"requirement_id":"REQ-1", "entities":[], "read":[], "write":[]}.
"""
