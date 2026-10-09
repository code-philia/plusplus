"""Model response contracts and prompts for the three ER discovery passes."""

from __future__ import annotations

import re
from typing import Any


SCHEMA_VERSION = 2


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
                "properties": {"key": {"type": "string"}, "description": {"type": "string"}},
            },
        },
    },
}


FIELD_PROPERTIES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "min_length", "max_length", "pattern", "enum", "format", "minimum", "maximum", "date_past",
        "date_future", "default",
    ],
    "properties": {
        "min_length": _nullable({"type": "integer", "minimum": 0}),
        "max_length": _nullable({"type": "integer", "minimum": 0}),
        "pattern": _nullable({"type": "string"}),
        "enum": _nullable({"type": "array", "items": {"type": "string"}, "minItems": 1}),
        "format": _nullable({"type": "string"}),
        "minimum": _nullable({"type": "number"}),
        "maximum": _nullable({"type": "number"}),
        "date_past": _nullable({"type": "boolean"}),
        "date_future": _nullable({"type": "boolean"}),
        "default": _nullable({"type": ["string", "number", "boolean"]}),
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
Your task is to discover entities in the shared ER model for one atomic requirement.
For exactly one atomic requirement, decide which persistent entities it reuses and which persistent entities must
be introduced. Existing entities are compiler symbols: reuse them when their meaning fits. Do not produce fields,
relationships, constraints, APIs, modules, or SQL. Entity keys are singular snake_case. Case-only name differences
are the same symbol. Terms explicitly paired by the requirement, such as "journey/train", are aliases: reuse the
existing entity whose description matches. UI selection, page context, form state, and other transient nouns are
not database entities. Include every persistent entity used by the requirement. When wording permits several names,
choose the existing entity whose persistent meaning best matches the requirement. If none matches, introduce one
concise entity. Do not emit alternatives or unresolved decisions.

Return exactly one JSON object with these keys and no others:
- requirement_id: copy the supplied requirement.requirement_id exactly.
- reuse_entities: array of existing entity keys; use [] when none.
- new_entities: array of {"key": singular_snake_case, "description": string}; use [] when none.

Valid output example:
{"requirement_id":"REQ-3.2","reuse_entities":["traveler","train_service"],"new_entities":[{"key":"booking","description":"A confirmed booking."}]}
Valid empty output example:
{"requirement_id":"REQ-4.1","reuse_entities":[],"new_entities":[]}
"""


FIELD_INSTRUCTIONS = """You are an ER-modeling specialist focused on persistent entity attributes.
Your task is to refine the shared ER model for exactly one atomic requirement, using only its related_entities.
These are logical attributes and business-domain constraints, not MySQL column types or physical storage options.
Do not create entities, relationships, foreign-key fields, cross-field constraints, APIs, modules, or SQL.
Do not persist transient form inputs, confirmation passwords, search criteria, selection state, or derived display
values unless the requirement explicitly gives them a persistent domain meaning.

Compare the requirement with each supplied entity's current fields:
- Existing field already sufficient: silently use it; omit it from both field lists. There is no reuse_fields key.
- Missing attribute: add a complete declaration to new_fields. Its name must not already exist on that entity.
- Existing attribute needs revision: add its complete resulting declaration to update_fields, with exactly the
  same name. This is a full replacement, not a patch: include unchanged attributes and all retained properties.
  Preserve constraints needed by earlier requirements; reconcile them with the current requirement. Do not weaken
  or erase earlier constraints merely because the current requirement does not repeat them. Use null to remove a
  property only when the combined requirements justify removal. Do not rename fields or emit unchanged updates.
- The same field must not occur twice or appear in both lists. Use [] when a list has no changes.
- Never create or update id, names ending in _id, or fields with origin SYSTEM or RELATIONSHIP. The compiler owns
  identifiers and Pass 3 owns relationships and their references. Return only entities with field changes.

Every new_fields and update_fields declaration has exactly these five attributes:
- name: nonempty singular snake_case identifier matching ^[a-z][a-z0-9_]*$. In update_fields it identifies the
  existing attribute; in new_fields it identifies a new attribute. Choose names by domain meaning.
- type: exactly one of string (text), integer (whole number), number (integer or fractional number), boolean
  (true/false), date (calendar date), datetime (date and time), json (structured JSON value). Use logical types,
  not SQL types. uuid and foreign_key are reserved for the compiler.
- nullable: boolean. true permits absent/null values; false requires a value on every stored instance of the
  entity. A form requiring input does not by itself prove all stored instances must have a non-null value.
- description: string explaining the attribute's persistent domain meaning.
- properties: object containing all ten keys below. Use null for every inapplicable or unspecified property;
  false is also allowed for inactive date flags. Do not invent constraints, formats or defaults.

Allowed properties and their values:
- min_length: nonnegative integer or null; minimum character count, string only.
- max_length: nonnegative integer or null; maximum character count, string only; must be >= min_length.
- pattern: regex string or null; required text pattern, string only. Use the regular-expression syntax supported
  by the Python re validator; this is a domain rule, not SQL.
- enum: nonempty list of distinct strings or null; allowed domain values, string only.
- format: string or null; requirement-supported semantic format label such as email, uri, date or date-time,
  on string/date/datetime only. This is descriptive metadata, not an SQL type or an enforced format validator.
- minimum: finite number or null; inclusive lower bound, integer/number only; whole number for integer fields.
- maximum: finite number or null; inclusive upper bound, integer/number only; must be >= minimum; whole number
  for integer fields.
- date_past: boolean or null; true requires a date/datetime strictly before the current date/time. false/null
  imposes no past-only rule. Only date/datetime; must not be true together with date_future.
- date_future: boolean or null; true requires a date/datetime strictly after the current date/time. false/null
  imposes no future-only rule. Only date/datetime; must not be true together with date_past.
- default: string, finite number, boolean or null; a requirement-supported literal matching the field's type and
  constraints. null means no declared default, not an explicit DEFAULT NULL. SQL expressions and JSON object/array
  defaults are not supported by this protocol. Do not encode CURRENT_TIMESTAMP or other expressions as strings.

Return exactly one JSON object with these keys and no others:
- requirement_id: copy requirement.requirement_id exactly.
- entities: [{"entity": existing_key, "new_fields": [complete declarations],
  "update_fields": [complete resulting declarations]}]. Omit entities with no changes. If no entity changes,
  return entities: []. Never emit an entity with both lists empty.

Valid new-field output example:
{"requirement_id":"REQ-3.2","entities":[{"entity":"booking","new_fields":[{"name":"booking_number","type":"string","nullable":false,"description":"Public booking number.","properties":{"min_length":1,"max_length":20,"pattern":null,"enum":null,"format":null,"minimum":null,"maximum":null,"date_past":null,"date_future":null,"default":null}}],"update_fields":[]}]}
Valid update example (username already exists; retain its meaning and constraints and require at most 20 characters):
{"requirement_id":"REQ-1.2","entities":[{"entity":"user","new_fields":[],"update_fields":[{"name":"username","type":"string","nullable":false,"description":"Account username.","properties":{"min_length":1,"max_length":20,"pattern":null,"enum":null,"format":null,"minimum":null,"maximum":null,"date_past":null,"date_future":null,"default":null}}]}]}
Valid unchanged-field output example:
{"requirement_id":"REQ-3.1","entities":[]}
"""


RELATIONSHIP_INSTRUCTIONS = """You are an ER-modeling specialist discovering relationships from an existing shared ER graph.
The analysis unit is focus_entity, not a single requirement. Read its complete fields and all associated
related_requirements together; they describe the domain meaning of this entity across the application.
The er_graph contains every entity's name, description, attribute headers and requirement IDs, plus existing
relationships. requirement_catalog contains bounded excerpts for all atomic requirements; related_requirements
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
only when additional requirement evidence or stronger minimum participation is found. Minimum participation may be strengthened from 0 to 1 when explicitly
required, but must not be weakened. Preserve endpoints, direction and maximum cardinalities; contradictory definitions
must not be silently replaced or bypassed by inventing a new key. Different roles between the same entity pair require
different keys. For self-relationships, explain the two roles in description. Do not emit unchanged relationships merely
because entities co-occur. Return [] when no stable association
is established. Self-relationships are allowed when the requirement clearly describes them. Do not emit alternatives.

Return exactly one JSON object with entity_key (copy focus_entity.key) and relationships. Each relationship contains exactly:
key, parent, child, type, parent_min, parent_max, child_min, child_max, description, evidence_requirement_ids.

Example:
{"entity_key":"traveler","relationships":[{"key":"traveler_bookings","parent":"traveler","child":"booking","type":"ONE_TO_MANY","parent_min":0,"parent_max":"many","child_min":1,"child_max":"one","description":"A traveler may own many bookings; every booking belongs to one traveler.","evidence_requirement_ids":["REQ-3.2"]}]}
No-relationship example: {"entity_key":"traveler","relationships":[]}
"""
