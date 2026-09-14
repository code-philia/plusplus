from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from arcbench_agent_runtime.jsonio import read_json, write_json_atomic
from core.logging import SynchronousLog

from .model_client import StructuredModel
PROMPT_VERSION = "database-facts-v2"
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


DATABASE_FACT_ITEM_SCHEMA: dict[str, Any] = {
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

DATABASE_FACTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": DATABASE_FACT_ITEM_SCHEMA,
        },
    },
}


DATABASE_FACT_INSTRUCTIONS = """You are the DATABASE_SCHEMA discovery pass of a requirement compiler.
Analyze every atomic requirement in requirements. Return one item per requirement and only facts implied by that
requirement, not a complete system schema. Use stable singular snake_case entity keys and reuse keys listed in
known_entities. Requirements in the same request belong to one dependency wave and may be considered together to
keep entity naming consistent. Include the
minimum technical fields needed to implement explicitly required persistence, identity, authentication,
idempotency, and lookup behavior. Do not invent product features. NOT_REQUIRED means the entity is transient and
will not become a table. Record a relation only on the entity that owns the foreign key, using MANY_TO_ONE or
ONE_TO_ONE. Represent many-to-many relationships as an explicit join entity with two MANY_TO_ONE relations.
All compiler-provided primary keys and every relation foreign key are strings. Declare a relation and let the
compiler derive its <relation_name>_id field; do not emit that field yourself. If you do emit it, its type must be
string. Never use integer identifiers for a relation field.
This is a logical schema: fields contains only scalar domain fields, relations is the sole source of cross-entity
references, and indexes may name either a scalar field or a relation name (for example ["account", "journey"]),
never its derived <relation_name>_id field. Checks may name scalar fields only. Use pattern/min_length/max_length
only with string fields, date_past/date_future only with date or datetime fields, and minimum/maximum only with
integer or number fields.
Relation names are logical domain names and must not end in _id.
Express validation as structured checks; never emit SQL. Every array must be present, including empty arrays.
Return exactly one item for every input requirement_id and no additional items.
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
        self._log = SynchronousLog("DatabaseSchemaPass", workspace_root=self._artifact_root.parents[1])
        retry_text = os.environ.get("ARC_STRUCTURED_OUTPUT_RETRY_COUNT", "2")
        try:
            self._retry_count = max(0, min(int(retry_text), 10))
        except ValueError:
            self._retry_count = 2
        batch_size_text = os.environ.get("ARC_DATABASE_BATCH_SIZE", "8")
        try:
            self._batch_size = max(1, min(int(batch_size_text), 32))
        except ValueError:
            self._batch_size = 8
        self._trace_enabled = _enabled_env_flag("ARC_DATABASE_TRACE", default=True)

    def compile(
        self,
        requirement_ir: dict[str, Any],
        dependency_graph: dict[str, Any] | None = None,
        *,
        resume: bool = False,
    ) -> DatabasePassResult:
        nodes = requirement_ir.get("nodes", {})
        waves = _ordered_waves(
            requirement_ir.get("atomic_units", []),
            dependency_graph or {},
        )
        effective_dependencies = (dependency_graph or {}).get("atomic_dependencies", {})
        errors: list[str] = []
        node_states: dict[str, str] = {}
        cache_paths: dict[str, str] = {}
        accumulator = _SchemaAccumulator(errors, self._trace)

        for wave in waves:
            analysis_nodes: list[dict[str, Any]] = []
            for node_id in wave:
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
                analysis_nodes.append(analysis_node)

            known_entities = sorted({
                entity
                for node in analysis_nodes
                for entity in accumulator.known_entities_for(node)
            })
            wave_results: dict[str, dict[str, Any]] = {}
            for batch in _batches(analysis_nodes, self._batch_size):
                requirement_ids = [str(node["id"]) for node in batch]
                context = {
                    "known_entities": known_entities,
                    "requirements": [self._node_context(node, nodes) for node in batch],
                }
                input_hash = _stable_hash({"prompt_version": PROMPT_VERSION, "context": context})
                cache_path = self._cache_path(requirement_ids)
                for node_id in requirement_ids:
                    cache_paths[node_id] = str(cache_path)
                facts_by_node = self._read_cached_facts(cache_path, input_hash, requirement_ids) if resume else None
                if facts_by_node is not None:
                    self._trace(
                        "CACHE_HIT "
                        f"batch={','.join(requirement_ids)} path={cache_path.name}"
                    )
                if facts_by_node is None:
                    facts_by_node = self._generate_facts(requirement_ids, context, errors)
                    if facts_by_node is not None:
                        write_json_atomic(
                            cache_path,
                            {
                                "schema_version": 1,
                                "prompt_version": PROMPT_VERSION,
                                "input_sha256": input_hash,
                                "items": [facts_by_node[node_id] for node_id in requirement_ids],
                            },
                        )
                if facts_by_node is None:
                    node_states.update({node_id: "FAILED" for node_id in requirement_ids})
                    continue
                wave_results.update(facts_by_node)

            for node_id in sorted(wave_results):
                before_errors = len(errors)
                accumulator.apply(node_id, wave_results[node_id])
                node_states[node_id] = "FAILED" if len(errors) > before_errors else "SCHEMA_ANALYZED"

        schema = accumulator.finish()
        schema["status"] = "RESOLVED" if not errors else "PROPOSED"
        return DatabasePassResult(schema, node_states, errors, cache_paths)

    def _generate_facts(
        self,
        requirement_ids: list[str],
        context: dict[str, Any],
        errors: list[str],
    ) -> dict[str, dict[str, Any]] | None:
        validation_feedback: list[str] = []
        for attempt in range(self._retry_count + 1):
            request_payload = dict(context)
            if validation_feedback:
                request_payload["previous_validation_errors"] = validation_feedback
            self._trace(
                "MODEL_CALL "
                f"attempt={attempt + 1}/{self._retry_count + 1} "
                f"batch={','.join(requirement_ids)} "
                f"known_entities={','.join(context.get('known_entities', [])) or '-'}"
            )
            try:
                payload = self._model.generate_json(
                    schema_name="arc_database_facts",
                    instructions=DATABASE_FACT_INSTRUCTIONS,
                    input_payload=request_payload,
                    output_schema=DATABASE_FACTS_SCHEMA,
                )
            except Exception as exc:
                validation_feedback = [f"Model call failed: {exc}"]
                self._trace("MODEL_ERROR " + validation_feedback[0])
                if attempt >= self._retry_count:
                    errors.append(
                        _format_error(
                            "ARC2102",
                            validation_feedback[0],
                            node_id=", ".join(requirement_ids),
                        )
                    )
                    return None
                continue
            self._trace_json(
                "MODEL_RESULT "
                f"attempt={attempt + 1} batch={','.join(requirement_ids)}",
                payload,
            )
            validation_feedback = _validate_batch_payload(requirement_ids, payload)
            if not validation_feedback:
                return {item["requirement_id"]: item for item in payload["items"]}
            self._trace("MODEL_VALIDATION_ERROR " + "; ".join(validation_feedback))
            if attempt >= self._retry_count:
                errors.append(
                    _format_error(
                        "ARC2103",
                        "Invalid database facts: " + "; ".join(validation_feedback),
                    )
                )
                return None
        return None

    def _trace(self, message: str) -> None:
        if self._trace_enabled:
            self._log.info(message)

    def _trace_json(self, heading: str, payload: dict[str, Any]) -> None:
        if self._trace_enabled:
            self._log.info(heading + "\n" + json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))

    @staticmethod
    def _node_context(
        node: dict[str, Any],
        nodes: dict[str, Any],
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
            "requirement_id": str(node.get("id", "")),
            "name": str(node.get("name", "")),
            "description": str(node.get("description", "")),
            "scenarios": node.get("scenarios", []),
            "dependencies": node.get("dependencies", []),
            "ancestors": ancestors,
        }

    def _cache_path(self, requirement_ids: list[str]) -> Path:
        label = "-".join(requirement_ids)
        safe_label = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("._")[:48] or "batch"
        suffix = hashlib.sha256(label.encode("utf-8")).hexdigest()[:10]
        return self._artifact_root / "database_facts" / f"{safe_label}-{suffix}.json"

    @staticmethod
    def _read_cached_facts(
        path: Path,
        input_hash: str,
        requirement_ids: list[str],
    ) -> dict[str, dict[str, Any]] | None:
        cached = read_json(path, {})
        payload = {"items": cached.get("items")}
        if cached.get("input_sha256") != input_hash or _validate_batch_payload(requirement_ids, payload):
            return None
        return {item["requirement_id"]: item for item in payload["items"]}


class _SchemaAccumulator:
    def __init__(self, errors: list[str], trace: Callable[[str], None]) -> None:
        self._entities: dict[str, dict[str, Any]] = {}
        self._errors = errors
        self._trace = trace

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
            relation_fields = {
                _relation_field_name(relation): str(relation["name"])
                for relation in observation["relations"]
            }
            for raw_field in observation["fields"]:
                relation_name = relation_fields.get(raw_field["name"])
                if relation_name is not None:
                    self._trace(
                        "FK_REFERENCE_NORMALIZED "
                        f"field={key}.{raw_field['name']} relation={relation_name}"
                    )
                    continue
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
                field_name = _relation_field_name(relation)
                if field_name in entity["fields"]:
                    legacy_type = entity["fields"][field_name]["type"]
                    self._trace(
                        "FK_REFERENCE_NORMALIZED "
                        f"field={key}.{field_name} "
                        f"relation={relation['name']} "
                        f"legacy_type={legacy_type}"
                    )
                    del entity["fields"][field_name]
        entities = []
        for key in sorted(self._entities):
            entity = self._entities[key]
            entities.append(
                {
                    "key": key,
                    "table": entity["table"],
                    "sources": sorted(entity["sources"]),
                    "fields": [entity["fields"][name] for name in sorted(entity["fields"])],
                    "indexes": _logical_indexes(entity),
                    "checks": [entity["checks"][check] for check in sorted(entity["checks"])],
                    "relations": [entity["relations"][relation] for relation in sorted(entity["relations"])],
                }
            )
        return {
            "schema_version": 1,
            "status": "PROPOSED",
            "entities": entities,
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
        relation_names, derived_relation_fields = _relation_reference_names(relations)
        if relation_names & field_names:
            errors.append(f"{prefix} relation name conflicts with a scalar field")
        valid_index_references = field_names | relation_names | derived_relation_fields
        for raw_index in indexes:
            if not isinstance(raw_index, dict) or not isinstance(raw_index.get("fields"), list):
                errors.append(f"{prefix} contains an invalid index")
                continue
            if not raw_index["fields"] or any(name not in valid_index_references for name in raw_index["fields"]):
                errors.append(f"{prefix} index references an unknown field")
            if not isinstance(raw_index.get("unique"), bool):
                errors.append(f"{prefix} index unique must be boolean")
        for raw_check in checks:
            if not isinstance(raw_check, dict) or raw_check.get("field") not in field_names:
                errors.append(f"{prefix} check references an unknown field")
                continue
            if raw_check.get("kind") not in CHECK_KINDS or not isinstance(raw_check.get("value"), str):
                errors.append(f"{prefix} contains an invalid check")
            else:
                field = next(
                    (
                        item
                        for item in fields
                        if isinstance(item, dict) and item.get("name") == raw_check.get("field")
                    ),
                    {},
                )
                if not _check_matches_type(str(field.get("type", "")), str(raw_check["kind"])):
                    errors.append(f"{prefix} check kind is incompatible with its field type")
        for raw_relation in relations:
            if not isinstance(raw_relation, dict):
                errors.append(f"{prefix} contains an invalid relation")
                continue
            relation_name = raw_relation.get("name")
            if not isinstance(relation_name, str) or not IDENTIFIER_PATTERN.fullmatch(relation_name):
                errors.append(f"{prefix} relation name must be snake_case")
            elif relation_name.endswith("_id"):
                errors.append(f"{prefix} relation name must be logical, not a physical *_id field")
            target = raw_relation.get("target_entity")
            if not isinstance(target, str) or not IDENTIFIER_PATTERN.fullmatch(target):
                errors.append(f"{prefix} relation target must be snake_case")
            if raw_relation.get("cardinality") not in CARDINALITIES:
                errors.append(f"{prefix} relation cardinality is invalid")
            if not isinstance(raw_relation.get("required"), bool):
                errors.append(f"{prefix} relation required must be boolean")
    return errors


def _validate_batch_payload(requirement_ids: list[str], payload: dict[str, Any]) -> list[str]:
    if not isinstance(payload, dict):
        return ["response must be an object"]
    items = payload.get("items")
    if not isinstance(items, list):
        return ["items must be an array"]

    expected = set(requirement_ids)
    returned: set[str] = set()
    errors: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"items[{index}] must be an object")
            continue
        node_id = str(item.get("requirement_id", ""))
        if node_id not in expected:
            errors.append(f"unexpected requirement_id: {node_id}")
            continue
        if node_id in returned:
            errors.append(f"duplicate requirement_id: {node_id}")
            continue
        returned.add(node_id)
        errors.extend(f"{node_id}: {error}" for error in _validate_facts_payload(node_id, item))

    missing = sorted(expected - returned)
    if missing:
        errors.append("missing requirement_id: " + ", ".join(missing))
    return errors


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _format_error(code: str, message: str, *, node_id: str | None = None) -> str:
    return f"{code}: {message}" + (f" ({node_id})" if node_id else "")


def _ordered_waves(atomic_ids: Any, dependency_graph: dict[str, Any]) -> list[list[str]]:
    """Return deterministic dependency waves without losing unscheduled nodes."""

    declared = [str(node_id) for node_id in atomic_ids if str(node_id)] if isinstance(atomic_ids, list) else []
    declared_set = set(declared)
    ordered: list[list[str]] = []
    seen: set[str] = set()
    waves = dependency_graph.get("implementation_waves", [])
    if isinstance(waves, list):
        for wave in waves:
            if not isinstance(wave, list):
                continue
            current = [
                node_id
                for node_id in sorted(str(item) for item in wave)
                if node_id in declared_set and node_id not in seen
            ]
            if current:
                ordered.append(current)
                seen.update(current)
    remaining = sorted(declared_set - seen)
    if remaining:
        ordered.append(remaining)
    return ordered


def _batches(nodes: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [nodes[index:index + size] for index in range(0, len(nodes), size)]


def _enabled_env_flag(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def _relation_field_name(relation: dict[str, Any]) -> str:
    name = str(relation["name"])
    return name if name.endswith("_id") else f"{name}_id"


def _relation_reference_names(relations: list[Any]) -> tuple[set[str], set[str]]:
    names = {str(relation.get("name", "")) for relation in relations if isinstance(relation, dict)}
    return names - {""}, {_relation_field_name(relation) for relation in relations if isinstance(relation, dict)}


def _logical_indexes(entity: dict[str, Any]) -> list[dict[str, Any]]:
    relations = {str(relation["name"]): relation for relation in entity["relations"].values()}
    physical_aliases = {_relation_field_name(relation): name for name, relation in relations.items()}
    merged: dict[tuple[tuple[str, ...], bool], dict[str, Any]] = {}
    for index in entity["indexes"].values():
        fields = tuple(physical_aliases.get(field, field) for field in index["fields"])
        key = (fields, index["unique"])
        result = merged.setdefault(key, {"fields": list(fields), "unique": index["unique"], "sources": []})
        for source in index["sources"]:
            _append_unique(result["sources"], source)
    return [merged[key] for key in sorted(merged)]


def _check_matches_type(field_type: str, kind: str) -> bool:
    if kind in {"min_length", "max_length", "pattern"}:
        return field_type == "string"
    if kind in {"date_past", "date_future"}:
        return field_type in {"date", "datetime"}
    if kind in {"minimum", "maximum"}:
        return field_type in {"integer", "number"}
    return True
