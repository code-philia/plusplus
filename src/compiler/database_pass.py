from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arcbench_agent_runtime.jsonio import read_json, write_json_atomic

from .model_client import StructuredModel
PROMPT_VERSION = "database-facts-v1"
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
FIELD_TYPES = {"string", "integer", "number", "boolean", "date", "datetime", "json"}
PERSISTENCE_VALUES = {"REQUIRED", "NOT_REQUIRED"}
CARDINALITIES = {"MANY_TO_ONE", "ONE_TO_ONE"}
CHECK_KINDS = {
    "min_length",
    "max_length",
    "pattern",
    "date_past",
    "date_future",
    "enum",
    "minimum",
    "maximum",
}


DATABASE_FACTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["requirement_id", "entities"],
    "properties": {
        "requirement_id": {"type": "string"},
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "persistence", "fields", "indexes", "checks", "relations"],
                "properties": {
                    "key": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                    "persistence": {"type": "string", "enum": sorted(PERSISTENCE_VALUES)},
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "name", "type", "required", "unique", "case_insensitive", "description"
                            ],
                            "properties": {
                                "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                                "type": {"type": "string", "enum": sorted(FIELD_TYPES)},
                                "required": {"type": "boolean"},
                                "unique": {"type": "boolean"},
                                "case_insensitive": {"type": "boolean"},
                                "description": {"type": "string"},
                            },
                        },
                    },
                    "indexes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["fields", "unique"],
                            "properties": {
                                "fields": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                                "unique": {"type": "boolean"},
                            },
                        },
                    },
                    "checks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["field", "kind", "value"],
                            "properties": {
                                "field": {"type": "string"},
                                "kind": {"type": "string", "enum": sorted(CHECK_KINDS)},
                                "value": {"type": "string"},
                            },
                        },
                    },
                    "relations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "target_entity", "cardinality", "required"],
                            "properties": {
                                "name": {"type": "string"},
                                "target_entity": {"type": "string"},
                                "cardinality": {"type": "string", "enum": sorted(CARDINALITIES)},
                                "required": {"type": "boolean"},
                            },
                        },
                    },
                },
            },
        },
    },
}


DATABASE_FACT_INSTRUCTIONS = """You are the DATABASE_SCHEMA discovery pass of a requirement compiler.
Analyze exactly one atomic requirement. Return only database facts implied by this requirement, not a complete
system schema. Use stable singular snake_case entity keys and reuse keys listed in known_entities. Include the
minimum technical fields needed to implement explicitly required persistence, identity, authentication,
idempotency, and lookup behavior. Do not invent product features. NOT_REQUIRED means the entity is transient and
will not become a table. Record a relation only on the entity that owns the foreign key, using MANY_TO_ONE or
ONE_TO_ONE. Represent many-to-many relationships as an explicit join entity with two MANY_TO_ONE relations.
Express validation as structured checks; never emit SQL. Every array must be present, including empty arrays.
requirement_id must exactly equal the input target id.
"""


@dataclass(slots=True)
class DatabasePassResult:
    schema: dict[str, Any]
    node_states: dict[str, str]
    errors: list[str] = field(default_factory=list)
    cache_paths: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


class DatabaseSchemaPass:
    """Discover per-node facts and deterministically reduce them into one schema."""

    def __init__(self, model: StructuredModel, artifact_root: Path) -> None:
        self._model = model
        self._artifact_root = artifact_root.expanduser().resolve()
        retry_text = os.environ.get("ARC_STRUCTURED_OUTPUT_RETRY_COUNT", "2")
        try:
            self._retry_count = max(0, min(int(retry_text), 10))
        except ValueError:
            self._retry_count = 2

    def compile(
        self,
        requirement_ir: dict[str, Any],
        dependency_graph: dict[str, Any] | None = None,
        *,
        resume: bool = False,
    ) -> DatabasePassResult:
        nodes = requirement_ir.get("nodes", {})
        atomic_ids = _ordered_atomic_ids(
            requirement_ir.get("atomic_units", []),
            dependency_graph or {},
        )
        effective_dependencies = (dependency_graph or {}).get("atomic_dependencies", {})
        errors: list[str] = []
        node_states: dict[str, str] = {}
        cache_paths: dict[str, str] = {}
        accumulator = _SchemaAccumulator(errors)

        for node_id in atomic_ids:
            node = nodes.get(node_id)
            if not isinstance(node, dict):
                errors.append(_format_error("ARC2101", "Atomic requirement is missing from Requirement IR.", node_id=node_id))
                node_states[node_id] = "FAILED"
                continue
            analysis_node = dict(node)
            analysis_node["dependencies"] = effective_dependencies.get(
                node_id,
                node.get("dependencies", []),
            )
            context = self._node_context(
                analysis_node,
                nodes,
                accumulator.known_entities_for(analysis_node),
            )
            input_hash = _stable_hash({"prompt_version": PROMPT_VERSION, "context": context})
            cache_path = self._cache_path(node_id)
            cache_paths[node_id] = str(cache_path)
            facts = self._read_cached_facts(cache_path, input_hash) if resume else None
            if facts is None:
                facts = self._generate_facts(node_id, context, errors)
                if facts is not None:
                    write_json_atomic(
                        cache_path,
                        {
                            "schema_version": 1,
                            "prompt_version": PROMPT_VERSION,
                            "node_id": node_id,
                            "input_sha256": input_hash,
                            "facts": facts,
                        },
                    )
            if facts is None:
                node_states[node_id] = "FAILED"
                continue
            before_errors = len(errors)
            accumulator.apply(node_id, facts)
            node_states[node_id] = "FAILED" if len(errors) > before_errors else "SCHEMA_ANALYZED"

        schema = accumulator.finish()
        schema["status"] = "RESOLVED" if not errors else "PROPOSED"
        return DatabasePassResult(schema, node_states, errors, cache_paths)

    def _generate_facts(
        self,
        node_id: str,
        context: dict[str, Any],
        errors: list[str],
    ) -> dict[str, Any] | None:
        validation_feedback: list[str] = []
        for attempt in range(self._retry_count + 1):
            request_payload = dict(context)
            if validation_feedback:
                request_payload["previous_validation_errors"] = validation_feedback
            try:
                payload = self._model.generate_json(
                    schema_name="arc_database_facts",
                    instructions=DATABASE_FACT_INSTRUCTIONS,
                    input_payload=request_payload,
                    output_schema=DATABASE_FACTS_SCHEMA,
                )
            except Exception as exc:
                validation_feedback = [f"Model call failed: {exc}"]
                if attempt >= self._retry_count:
                    errors.append(_format_error("ARC2102", validation_feedback[0], node_id=node_id))
                    return None
                continue
            validation_feedback = _validate_facts_payload(node_id, payload)
            if not validation_feedback:
                return payload
            if attempt >= self._retry_count:
                errors.append(
                    _format_error(
                        "ARC2103",
                        "Invalid database facts: " + "; ".join(validation_feedback),
                        node_id=node_id,
                    )
                )
                return None
        return None

    @staticmethod
    def _node_context(
        node: dict[str, Any],
        nodes: dict[str, Any],
        known_entities: list[str],
    ) -> dict[str, Any]:
        ancestors: list[dict[str, str]] = []
        parent_id = node.get("parent_id")
        while isinstance(parent_id, str) and parent_id:
            parent = nodes.get(parent_id)
            if not isinstance(parent, dict):
                break
            ancestors.append(
                {
                    "id": parent_id,
                    "name": str(parent.get("name", "")),
                    "description": str(parent.get("description", "")),
                }
            )
            parent_id = parent.get("parent_id")
        ancestors.reverse()
        return {
            "target": {
                "id": str(node.get("id", "")),
                "name": str(node.get("name", "")),
                "description": str(node.get("description", "")),
                "scenarios": node.get("scenarios", []),
                "dependencies": node.get("dependencies", []),
            },
            "ancestors": ancestors,
            "known_entities": known_entities,
        }

    def _cache_path(self, node_id: str) -> Path:
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", node_id).strip("._") or "requirement"
        suffix = hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:10]
        return self._artifact_root / "database_facts" / f"{safe_id}-{suffix}.json"

    @staticmethod
    def _read_cached_facts(path: Path, input_hash: str) -> dict[str, Any] | None:
        cached = read_json(path, {})
        facts = cached.get("facts")
        if cached.get("input_sha256") != input_hash or not isinstance(facts, dict):
            return None
        node_id = str(cached.get("node_id", ""))
        return facts if not _validate_facts_payload(node_id, facts) else None


class _SchemaAccumulator:
    def __init__(self, errors: list[str]) -> None:
        self._entities: dict[str, dict[str, Any]] = {}
        self._errors = errors

    @property
    def entity_keys(self) -> list[str]:
        return sorted(self._entities)

    def known_entities_for(self, node: dict[str, Any], limit: int = 64) -> list[str]:
        """Return a bounded symbol slice relevant to one requirement."""

        dependencies = set(node.get("dependencies", []))
        searchable = " ".join(
            (
                str(node.get("name", "")),
                str(node.get("description", "")),
                json.dumps(node.get("scenarios", []), ensure_ascii=False),
            )
        ).lower()
        ranked: list[tuple[int, str]] = []
        for key, entity in self._entities.items():
            score = 0
            if set(entity["sources"]) & dependencies:
                score += 2
            terms = {key, key.replace("_", " "), *key.split("_")}
            if any(term and term in searchable for term in terms):
                score += 1
            if score:
                ranked.append((-score, key))
        return [key for _, key in sorted(ranked)[:limit]]

    def apply(self, node_id: str, payload: dict[str, Any]) -> None:
        for observation in payload["entities"]:
            if observation["persistence"] != "REQUIRED":
                continue
            key = observation["key"]
            entity = self._entities.setdefault(
                key,
                {
                    "key": key,
                    "table": key,
                    "sources": [],
                    "fields": {},
                    "indexes": {},
                    "checks": {},
                    "relations": {},
                },
            )
            _append_unique(entity["sources"], node_id)
            for raw_field in observation["fields"]:
                self._merge_field(entity, raw_field, node_id)
            for raw_index in observation["indexes"]:
                fields = tuple(raw_index["fields"])
                index_key = (fields, raw_index["unique"])
                index = entity["indexes"].setdefault(
                    index_key,
                    {"fields": list(fields), "unique": raw_index["unique"], "sources": []},
                )
                _append_unique(index["sources"], node_id)
            for raw_check in observation["checks"]:
                check_key = (raw_check["field"], raw_check["kind"], raw_check["value"])
                check = entity["checks"].setdefault(
                    check_key,
                    {**raw_check, "sources": []},
                )
                _append_unique(check["sources"], node_id)
            for raw_relation in observation["relations"]:
                relation_key = (raw_relation["name"], raw_relation["target_entity"], raw_relation["cardinality"])
                relation = entity["relations"].setdefault(
                    relation_key,
                    {**raw_relation, "sources": []},
                )
                relation["required"] = relation["required"] or raw_relation["required"]
                _append_unique(relation["sources"], node_id)

    def _merge_field(self, entity: dict[str, Any], raw_field: dict[str, Any], node_id: str) -> None:
        name = raw_field["name"]
        existing = entity["fields"].get(name)
        if existing is None:
            entity["fields"][name] = {
                **raw_field,
                "required": raw_field["required"] or name == "id",
                "unique": raw_field["unique"] or name == "id",
                "primary_key": name == "id",
                "sources": [node_id],
            }
            return
        if existing["type"] != raw_field["type"]:
            self._errors.append(
                _format_error(
                    "ARC2201",
                    f"Conflicting types for {entity['key']}.{name}: {existing['type']} vs {raw_field['type']}.",
                    node_id=node_id,
                )
            )
            return
        existing["required"] = existing["required"] or raw_field["required"]
        existing["unique"] = existing["unique"] or raw_field["unique"]
        existing["case_insensitive"] = existing["case_insensitive"] or raw_field["case_insensitive"]
        _append_unique(existing["sources"], node_id)

    def finish(self) -> dict[str, Any]:
        foreign_keys: list[dict[str, Any]] = []
        for key, entity in sorted(self._entities.items()):
            if "id" not in entity["fields"]:
                entity["fields"]["id"] = {
                    "name": "id",
                    "type": "string",
                    "required": True,
                    "unique": True,
                    "case_insensitive": False,
                    "description": "Compiler-provided stable primary key.",
                    "primary_key": True,
                    "sources": list(entity["sources"]),
                }
            for relation in entity["relations"].values():
                target = relation["target_entity"]
                if target not in self._entities:
                    self._errors.append(
                        _format_error(
                            "ARC2202",
                            f"Unresolved database relation: {key}.{relation['name']} -> {target}.",
                            node_id=relation["sources"][0] if relation["sources"] else None,
                        )
                    )
                    continue
                field_name = relation["name"] if relation["name"].endswith("_id") else f"{relation['name']}_id"
                existing_field = entity["fields"].get(field_name)
                if existing_field is not None and existing_field["type"] != "string":
                    self._errors.append(
                        _format_error(
                            "ARC2203",
                            f"Foreign key field {key}.{field_name} must have type string.",
                            node_id=relation["sources"][0] if relation["sources"] else None,
                        )
                    )
                    continue
                if existing_field is None:
                    entity["fields"][field_name] = {
                        "name": field_name,
                        "type": "string",
                        "required": relation["required"],
                        "unique": relation["cardinality"] == "ONE_TO_ONE",
                        "case_insensitive": False,
                        "description": f"Foreign key to {target}.id.",
                        "primary_key": False,
                        "sources": sorted(relation["sources"]),
                    }
                else:
                    existing_field["required"] = existing_field["required"] or relation["required"]
                    existing_field["unique"] = existing_field["unique"] or relation["cardinality"] == "ONE_TO_ONE"
                    for source in relation["sources"]:
                        _append_unique(existing_field["sources"], source)
                foreign_keys.append(
                    {
                        "from_entity": key,
                        "from_field": field_name,
                        "to_entity": target,
                        "to_field": "id",
                        "sources": sorted(relation["sources"]),
                    }
                )
        entities = []
        for key in sorted(self._entities):
            entity = self._entities[key]
            entities.append(
                {
                    "key": key,
                    "table": entity["table"],
                    "sources": sorted(entity["sources"]),
                    "fields": [entity["fields"][name] for name in sorted(entity["fields"])],
                    "indexes": [entity["indexes"][index] for index in sorted(entity["indexes"])],
                    "checks": [entity["checks"][check] for check in sorted(entity["checks"])],
                    "relations": [entity["relations"][relation] for relation in sorted(entity["relations"])],
                }
            )
        return {
            "schema_version": 1,
            "status": "PROPOSED",
            "entities": entities,
            "foreign_keys": sorted(
                foreign_keys,
                key=lambda item: (item["from_entity"], item["from_field"], item["to_entity"]),
            ),
        }


def _validate_facts_payload(node_id: str, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("requirement_id") != node_id:
        errors.append("requirement_id does not match target")
    entities = payload.get("entities")
    if not isinstance(entities, list):
        return [*errors, "entities must be an array"]
    seen_entities: set[str] = set()
    for entity_index, entity in enumerate(entities):
        prefix = f"entities[{entity_index}]"
        if not isinstance(entity, dict):
            errors.append(f"{prefix} must be an object")
            continue
        key = entity.get("key")
        if not isinstance(key, str) or not IDENTIFIER_PATTERN.fullmatch(key):
            errors.append(f"{prefix}.key must be snake_case")
        elif key in seen_entities:
            errors.append(f"duplicate entity key: {key}")
        else:
            seen_entities.add(key)
        if entity.get("persistence") not in PERSISTENCE_VALUES:
            errors.append(f"{prefix}.persistence is invalid")
        fields = entity.get("fields")
        indexes = entity.get("indexes")
        checks = entity.get("checks")
        relations = entity.get("relations")
        if not all(isinstance(value, list) for value in (fields, indexes, checks, relations)):
            errors.append(f"{prefix} fields/indexes/checks/relations must be arrays")
            continue
        field_names: set[str] = set()
        for field_index, raw_field in enumerate(fields):
            field_prefix = f"{prefix}.fields[{field_index}]"
            if not isinstance(raw_field, dict):
                errors.append(f"{field_prefix} must be an object")
                continue
            name = raw_field.get("name")
            if not isinstance(name, str) or not IDENTIFIER_PATTERN.fullmatch(name):
                errors.append(f"{field_prefix}.name must be snake_case")
            elif name in field_names:
                errors.append(f"duplicate field: {key}.{name}")
            else:
                field_names.add(name)
            if raw_field.get("type") not in FIELD_TYPES:
                errors.append(f"{field_prefix}.type is invalid")
            for boolean_name in ("required", "unique", "case_insensitive"):
                if not isinstance(raw_field.get(boolean_name), bool):
                    errors.append(f"{field_prefix}.{boolean_name} must be boolean")
            if not isinstance(raw_field.get("description"), str):
                errors.append(f"{field_prefix}.description must be a string")
        for raw_index in indexes:
            if not isinstance(raw_index, dict) or not isinstance(raw_index.get("fields"), list):
                errors.append(f"{prefix} contains an invalid index")
                continue
            if not raw_index["fields"] or any(name not in field_names for name in raw_index["fields"]):
                errors.append(f"{prefix} index references an unknown field")
            if not isinstance(raw_index.get("unique"), bool):
                errors.append(f"{prefix} index unique must be boolean")
        for raw_check in checks:
            if not isinstance(raw_check, dict) or raw_check.get("field") not in field_names:
                errors.append(f"{prefix} check references an unknown field")
                continue
            if raw_check.get("kind") not in CHECK_KINDS or not isinstance(raw_check.get("value"), str):
                errors.append(f"{prefix} contains an invalid check")
        for raw_relation in relations:
            if not isinstance(raw_relation, dict):
                errors.append(f"{prefix} contains an invalid relation")
                continue
            if not isinstance(raw_relation.get("name"), str) or not IDENTIFIER_PATTERN.fullmatch(raw_relation["name"]):
                errors.append(f"{prefix} relation name must be snake_case")
            target = raw_relation.get("target_entity")
            if not isinstance(target, str) or not IDENTIFIER_PATTERN.fullmatch(target):
                errors.append(f"{prefix} relation target must be snake_case")
            if raw_relation.get("cardinality") not in CARDINALITIES:
                errors.append(f"{prefix} relation cardinality is invalid")
            if not isinstance(raw_relation.get("required"), bool):
                errors.append(f"{prefix} relation required must be boolean")
    return errors


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _format_error(code: str, message: str, *, node_id: str | None = None) -> str:
    return f"{code}: {message}" + (f" ({node_id})" if node_id else "")


def _ordered_atomic_ids(atomic_ids: Any, dependency_graph: dict[str, Any]) -> list[str]:
    """Flatten deterministic dependency waves without losing unscheduled nodes."""

    declared = [str(node_id) for node_id in atomic_ids if str(node_id)] if isinstance(atomic_ids, list) else []
    declared_set = set(declared)
    ordered: list[str] = []
    seen: set[str] = set()
    waves = dependency_graph.get("implementation_waves", [])
    if isinstance(waves, list):
        for wave in waves:
            if not isinstance(wave, list):
                continue
            for node_id in sorted(str(item) for item in wave):
                if node_id in declared_set and node_id not in seen:
                    ordered.append(node_id)
                    seen.add(node_id)
    ordered.extend(sorted(declared_set - seen))
    return ordered
