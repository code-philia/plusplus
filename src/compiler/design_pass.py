from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from arcbench_agent_runtime.jsonio import read_json, write_json_atomic
from core.logging import SynchronousLog

from .design_validator import DesignValidator, assign_module_files
from .model_client import StructuredModel, describe_model_error


MODULE_PROMPT_VERSION = "module-draft-v4"
DATAFLOW_PROMPT_VERSION = "dataflow-link-v4"
MODULE_KINDS = {"PAGE", "API", "FUNCTION", "REPOSITORY"}
IO_FIELD_TYPES = {"string", "integer", "number", "boolean", "date", "datetime", "json"}
SYMBOL_JSON_PATTERN = r"^[a-z][a-z0-9_]*$"
MODULE_ID_JSON_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
IO_REF_JSON_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+\.(input|output)\.[a-z][a-z0-9_]*$"
SYMBOL_PATTERN = re.compile(SYMBOL_JSON_PATTERN)
MODULE_ID_PATTERN = re.compile(MODULE_ID_JSON_PATTERN)


IO_FIELD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "type", "required"],
    "properties": {
        "name": {"type": "string", "pattern": SYMBOL_JSON_PATTERN},
        "type": {"type": "string", "enum": sorted(IO_FIELD_TYPES)},
        "required": {"type": "boolean"},
    },
}

MODULE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "kind", "route", "input", "output", "errors", "reads", "writes", "generates"],
    "properties": {
        "id": {"type": "string", "pattern": MODULE_ID_JSON_PATTERN},
        "kind": {"type": "string", "enum": sorted(MODULE_KINDS)},
        "route": {"type": "string"},
        "input": {"type": "array", "items": IO_FIELD_SCHEMA},
        "output": {"type": "array", "items": IO_FIELD_SCHEMA},
        "errors": {"type": "array", "items": {"type": "string"}},
        "reads": {"type": "array", "items": {"type": "string"}},
        "writes": {"type": "array", "items": {"type": "string"}},
        "generates": {"type": "array", "items": {"type": "string"}},
    },
}

MODULE_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["requirement_id", "modules"],
                "properties": {
                    "requirement_id": {"type": "string"},
                    "modules": {"type": "array", "items": MODULE_SCHEMA},
                },
            },
        },
    },
}

MAPPING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["target", "source"],
    "properties": {
        "source": {"type": "string", "pattern": IO_REF_JSON_PATTERN},
        "target": {"type": "string", "pattern": IO_REF_JSON_PATTERN},
    },
}

LINK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["type", "from", "to", "mapping"],
    "properties": {
        "type": {"type": "string", "enum": ["CALL", "RETURN"]},
        "from": {"type": "string", "pattern": MODULE_ID_JSON_PATTERN},
        "to": {"type": "string", "pattern": MODULE_ID_JSON_PATTERN},
        "mapping": {"type": "array", "items": MAPPING_SCHEMA, "minItems": 1},
    },
}

DATAFLOW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["requirement_id", "links", "entrypoints", "outcomes"],
                "properties": {
                    "requirement_id": {"type": "string"},
                    "links": {"type": "array", "items": LINK_SCHEMA},
                    "entrypoints": {"type": "array", "items": {"type": "string"}},
                    "outcomes": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}


MODULE_INSTRUCTIONS = """You are the MODULE_DRAFT pass of a requirement compiler.
Analyze every requirement in requirements and return exactly one item for each requirement_id. Design the complete
whole-program skeleton contribution for those requirements. Use only PAGE, API, FUNCTION, and REPOSITORY modules.
PAGE connects to API, API connects to FUNCTION, and FUNCTION connects to FUNCTION or REPOSITORY. Each module is one
opaque transformation with module-local input and output field arrays. A module input receives arguments from its
caller, while its output is returned to that caller; output is not an argument channel for modules it calls. Never
emit global or named IO types and never describe how input is computed into output. Prefer deep intent-level modules,
not generic CRUD or trivial
pass-through wrappers. route is a page path, an API string such as "POST /api/bookings", or an empty string. Only
REPOSITORY modules may list database reads and writes, and all listed entity keys must come from database. A writing
REPOSITORY input must contain every required database field,
using the same snake_case names as the logical database columns, except fields explicitly listed in generates as
entity.field. Runtime-provided values such as authenticated user_id are ordinary entrypoint input fields.
NAMING RULES: every input and output field name must be snake_case (account_id, date_of_birth), never
camelCase. Every module id must contain at least two dot-separated snake_case segments and use a domain-first form
such as account.register_page, account.register_api, or account.repository. A bare id such as register_page is
invalid. Do not create database entities, files,
implementation details, tests, or natural-language descriptions. Keep the design minimal while covering all stated
behavior and scenarios. A module has one fixed Interface; use separate module ids for distinct transformations.
"""


DATAFLOW_INSTRUCTIONS = """You are the DATAFLOW_LINK pass of a requirement compiler.
Return exactly one item for every input requirement_id. Use only the supplied modules and database entities. Do not
create or alter modules or fields. links is an ordered execution-event list and every invocation is represented by a
matched pair. CALL goes from caller to callee and maps <caller>.input.<field> to <callee>.input.<field>. RETURN goes
from that callee back to its caller and maps <callee>.output.<field> to <caller>.output.<field>. A module output is
therefore returned upstream and must never be used as the data source of a downstream CALL. Preserve execution order:
if A calls B and then C, emit [CALL A->B, RETURN B->A, CALL A->C, RETURN C->A]. Nested calls must be properly nested.
PAGE may call API; API may call FUNCTION; FUNCTION may call FUNCTION or REPOSITORY. Give every requirement at least
one PAGE/API entrypoint and at least one observable module outcome. Keep links minimal and preserve requirement
provenance. Use the exact snake_case fields declared by MODULE_DRAFT.
"""


@dataclass(slots=True)
class DesignPassResult:
    design_ir: dict[str, Any]
    node_states: dict[str, str]
    errors: list[str] = field(default_factory=list)
    cache_paths: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


class DesignPass:
    """Synthesize and link a compact whole-program Design IR."""

    def __init__(self, model: StructuredModel, artifact_root: Path) -> None:
        self._model = model
        self._artifact_root = artifact_root.expanduser().resolve()
        self._log = SynchronousLog("DesignPass", workspace_root=self._artifact_root.parents[1])
        self._retry_count = _bounded_env_int("ARC_STRUCTURED_OUTPUT_RETRY_COUNT", 2, 0, 10)
        self._batch_size = _bounded_env_int("ARC_DESIGN_BATCH_SIZE", 6, 1, 24)
        self._trace_enabled = _enabled_env_flag("ARC_DESIGN_TRACE", default=True)

    def compile(
        self,
        requirement_ir: dict[str, Any],
        dependency_graph: dict[str, Any],
        database_schema: dict[str, Any],
        *,
        resume: bool = False,
    ) -> DesignPassResult:
        nodes = requirement_ir.get("nodes", {})
        waves = _ordered_waves(requirement_ir.get("atomic_units", []), dependency_graph)
        dependencies = dependency_graph.get("atomic_dependencies", {})
        errors: list[str] = []
        states: dict[str, str] = {}
        cache_paths: dict[str, str] = {}
        registry = _ModuleRegistry(errors)

        for wave_index, wave in enumerate(waves, start=1):
            self._trace(f"MODULE_DRAFT_WAVE wave={wave_index} requirements={','.join(wave)}")
            catalog = registry.headers()
            pending: list[tuple[str, dict[str, Any]]] = []
            for batch_ids in _batches(wave, self._batch_size):
                context = {
                    "known_modules": catalog["modules"],
                    "database": _database_slice(database_schema, batch_ids, dependencies),
                    "requirements": [_requirement_context(nodes, node_id, dependencies) for node_id in batch_ids],
                }
                items = self._run_batch(
                    phase="module_drafts",
                    prompt_version=MODULE_PROMPT_VERSION,
                    schema_name="arc_module_draft",
                    instructions=MODULE_INSTRUCTIONS,
                    output_schema=MODULE_DRAFT_SCHEMA,
                    requirement_ids=batch_ids,
                    context=context,
                    validate_item=_validate_module_item,
                    resume=resume,
                    errors=errors,
                    cache_paths=cache_paths,
                )
                if items is None:
                    states.update({node_id: "FAILED" for node_id in batch_ids})
                    continue
                pending.extend((item["requirement_id"], item) for item in items)

            for node_id, item in sorted(pending):
                before = len(errors)
                registry.apply(node_id, item)
                states[node_id] = "FAILED" if len(errors) > before else "MODULED"

        draft = registry.finish()
        self._trace(
            "SYMBOL_REDUCED "
            f"modules={len(draft['modules'])}"
        )
        if errors:
            return DesignPassResult(_empty_design(draft), states, errors, cache_paths)

        requirement_records: list[dict[str, Any]] = []
        links: list[dict[str, Any]] = []
        for wave_index, wave in enumerate(waves, start=1):
            self._trace(f"DATAFLOW_LINK_WAVE wave={wave_index} requirements={','.join(wave)}")
            for batch_ids in _batches(wave, self._batch_size):
                context = {
                    "modules": draft["modules"],
                    "database": _database_slice(database_schema, batch_ids, dependencies),
                    "requirements": [_requirement_context(nodes, node_id, dependencies) for node_id in batch_ids],
                }
                items = self._run_batch(
                    phase="dataflow_links",
                    prompt_version=DATAFLOW_PROMPT_VERSION,
                    schema_name="arc_dataflow_links",
                    instructions=DATAFLOW_INSTRUCTIONS,
                    output_schema=DATAFLOW_SCHEMA,
                    requirement_ids=batch_ids,
                    context=context,
                    validate_item=_validate_dataflow_item,
                    resume=resume,
                    errors=errors,
                    cache_paths=cache_paths,
                )
                if items is None:
                    states.update({node_id: "FAILED" for node_id in batch_ids})
                    continue
                items_by_id = {item["requirement_id"]: item for item in items}
                for node_id in batch_ids:
                    item = items_by_id[node_id]
                    for link in item["links"]:
                        links.append({**link, "sources": [node_id]})
                    requirement_records.append({
                        "id": node_id,
                        "data": _requirement_domains(database_schema, node_id, dependencies),
                        "entrypoints": sorted(set(item["entrypoints"])),
                        "outcomes": sorted(set(item["outcomes"])),
                    })
                    if states.get(node_id) != "FAILED":
                        states[node_id] = "LINKED"

        design = {
            "schema_version": 3,
            "status": "PROPOSED",
            "modules": assign_module_files(draft["modules"]),
            "links": links,
            "requirements": sorted(requirement_records, key=lambda item: item["id"]),
        }
        errors.extend(DesignValidator().validate(design, requirement_ir, database_schema, dependencies))
        if errors:
            self._trace("DESIGN_VALIDATION_ERROR " + "; ".join(errors))
        else:
            self._trace(
                "DESIGN_VALIDATED "
                f"modules={len(design['modules'])} links={len(design['links'])}"
            )
        design["status"] = "RESOLVED" if not errors else "PROPOSED"
        if errors:
            for node_id in requirement_ir.get("atomic_units", []):
                if states.get(node_id) == "LINKED":
                    states[node_id] = "FAILED"
        else:
            states.update({node_id: "DESIGNED" for node_id in requirement_ir.get("atomic_units", [])})
        return DesignPassResult(design, states, errors, cache_paths)

    def _run_batch(
        self,
        *,
        phase: str,
        prompt_version: str,
        schema_name: str,
        instructions: str,
        output_schema: dict[str, Any],
        requirement_ids: list[str],
        context: dict[str, Any],
        validate_item: Callable[[str, dict[str, Any]], list[str]],
        resume: bool,
        errors: list[str],
        cache_paths: dict[str, str],
    ) -> list[dict[str, Any]] | None:
        input_hash = _stable_hash({"prompt_version": prompt_version, "context": context})
        cache_path = self._cache_path(phase, requirement_ids)
        for node_id in requirement_ids:
            cache_paths[f"{phase}:{node_id}"] = str(cache_path)
        if resume:
            cached = read_json(cache_path, {})
            payload = {"items": cached.get("items")}
            if cached.get("input_sha256") == input_hash:
                cache_errors = _validate_batch(requirement_ids, payload, validate_item)
                if not cache_errors:
                    self._trace(
                        "CACHE_HIT "
                        f"phase={phase} batch={','.join(requirement_ids)} path={cache_path.name}"
                    )
                    return payload["items"]

        feedback: list[str] = []
        for attempt in range(self._retry_count + 1):
            request_payload = dict(context)
            if feedback:
                request_payload["previous_errors"] = feedback
            self._trace(
                "MODEL_CALL "
                f"phase={phase} attempt={attempt + 1}/{self._retry_count + 1} "
                f"batch={','.join(requirement_ids)} "
                f"modules={len(context.get('modules', context.get('known_modules', [])))} "
                f"entities={len(context.get('database', []))}"
            )
            try:
                payload = self._model.generate_json(
                    schema_name=schema_name,
                    instructions=instructions,
                    input_payload=request_payload,
                    output_schema=output_schema,
                )
            except Exception as exc:
                feedback = [f"Model call failed: {describe_model_error(exc)}"]
                self._trace("MODEL_ERROR phase=" + phase + " " + feedback[0])
            else:
                self._trace_json(
                    "MODEL_RESULT "
                    f"phase={phase} attempt={attempt + 1} batch={','.join(requirement_ids)}",
                    payload,
                )
                if phase == "module_drafts":
                    payload, normalization_events = _normalize_module_payload(payload)
                    for event in normalization_events:
                        self._trace(event)
                feedback = _validate_batch(requirement_ids, payload, validate_item)
                if not feedback:
                    write_json_atomic(cache_path, {
                        "schema_version": 1,
                        "prompt_version": prompt_version,
                        "input_sha256": input_hash,
                        "items": payload["items"],
                    })
                    return payload["items"]
                self._trace("MODEL_VALIDATION_ERROR phase=" + phase + " " + "; ".join(feedback))
            if attempt >= self._retry_count:
                errors.append(f"{phase} failed for {', '.join(requirement_ids)}: {'; '.join(feedback)}")
        return None

    def _trace(self, message: str) -> None:
        if self._trace_enabled:
            self._log.info(message)

    def _trace_json(self, heading: str, payload: dict[str, Any]) -> None:
        if self._trace_enabled:
            self._log.info(heading + "\n" + json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))

    def _cache_path(self, phase: str, requirement_ids: list[str]) -> Path:
        label = "-".join(requirement_ids)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("._")[:48] or "batch"
        suffix = hashlib.sha256(label.encode("utf-8")).hexdigest()[:10]
        return self._artifact_root / phase / f"{safe}-{suffix}.json"


class _ModuleRegistry:
    def __init__(self, errors: list[str]) -> None:
        self._errors = errors
        self._modules: dict[str, dict[str, Any]] = {}

    def headers(self) -> dict[str, Any]:
        return {
            "modules": [
                _without_owner(item)
                for item in sorted(self._modules.values(), key=lambda value: value["id"])
            ],
        }

    def apply(self, node_id: str, item: dict[str, Any]) -> None:
        for raw_module in item["modules"]:
            module_id = raw_module["id"]
            normalized = {
                **raw_module,
                "owner": node_id,
                "input": sorted(raw_module["input"], key=lambda field: field["name"]),
                "output": sorted(raw_module["output"], key=lambda field: field["name"]),
                "errors": sorted(set(raw_module["errors"])),
                "reads": sorted(set(raw_module["reads"])),
                "writes": sorted(set(raw_module["writes"])),
                "generates": sorted(set(raw_module["generates"])),
            }
            current = self._modules.get(module_id)
            if current is None:
                self._modules[module_id] = normalized
            elif _without_owner(current) != _without_owner(normalized):
                self._errors.append(f"Conflicting module Interface: {module_id} ({node_id})")

    def finish(self) -> dict[str, Any]:
        return {"modules": [self._modules[key] for key in sorted(self._modules)]}


def _normalize_module_payload(payload: Any) -> tuple[Any, list[str]]:
    """Canonicalize recoverable model naming without inventing domain semantics."""

    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return payload, []
    normalized = copy.deepcopy(payload)
    events: list[str] = []
    for item in normalized["items"]:
        if not isinstance(item, dict):
            continue
        requirement_id = str(item.get("requirement_id", "?"))
        for module in item.get("modules", []):
            if not isinstance(module, dict):
                continue
            original_id = module.get("id")
            canonical_id = _canonical_module_id(original_id, module.get("kind"))
            if canonical_id != original_id:
                module["id"] = canonical_id
                events.append(
                    f"DESIGN_NAME_NORMALIZED {requirement_id} module={original_id}->{canonical_id}"
                )
            module_id = str(module.get("id", "?"))
            for direction in ("input", "output"):
                for field in module.get(direction, []):
                    if not isinstance(field, dict):
                        continue
                    _normalize_symbol_field(
                        field,
                        "name",
                        f"{requirement_id} {module_id}.{direction}",
                        events,
                    )
    return normalized, events


def _normalize_symbol_field(
    item: dict[str, Any],
    key: str,
    label: str,
    events: list[str],
) -> None:
    original = item.get(key)
    canonical = _canonical_symbol(original)
    if canonical != original:
        item[key] = canonical
        events.append(f"DESIGN_NAME_NORMALIZED {label} {original}->{canonical}")


def _canonical_module_id(value: Any, kind: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    segments = value.split(".")
    canonical_segments = [_canonical_symbol(segment) for segment in segments]
    if any(not isinstance(segment, str) or not SYMBOL_PATTERN.fullmatch(segment) for segment in canonical_segments):
        return value
    if len(canonical_segments) == 1 and kind in MODULE_KINDS:
        canonical_segments.insert(0, str(kind).lower())
    return ".".join(canonical_segments)


def _canonical_symbol(value: Any) -> Any:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
        return value
    first_pass = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    snake_case = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first_pass).lower()
    return snake_case if SYMBOL_PATTERN.fullmatch(snake_case) else value


def _validate_module_item(node_id: str, item: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    seen_modules: set[str] = set()
    for module in item.get("modules", []):
        module_id = module.get("id")
        if not isinstance(module_id, str) or not MODULE_ID_PATTERN.fullmatch(module_id):
            errors.append("module id must be a dot-separated snake_case name")
            continue
        if module_id in seen_modules:
            errors.append(f"duplicate module: {module_id}")
        seen_modules.add(module_id)
        if module.get("kind") not in MODULE_KINDS:
            errors.append(f"invalid module kind: {module_id}")
        for direction in ("input", "output"):
            fields = module.get(direction)
            if not isinstance(fields, list):
                errors.append(f"{module_id}.{direction} must be an array")
                continue
            seen_fields: set[str] = set()
            for field in fields:
                field_name = field.get("name") if isinstance(field, dict) else None
                if not isinstance(field_name, str) or not SYMBOL_PATTERN.fullmatch(field_name):
                    errors.append(f"invalid field name in {module_id}.{direction}")
                elif field_name in seen_fields:
                    errors.append(f"duplicate field: {module_id}.{direction}.{field_name}")
                else:
                    seen_fields.add(field_name)
                if not isinstance(field, dict) or field.get("type") not in IO_FIELD_TYPES:
                    errors.append(f"invalid field type in {module_id}.{direction}")
    return [f"{node_id}: {error}" for error in errors]


def _validate_dataflow_item(node_id: str, item: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    calls: list[tuple[str, str]] = []
    for link in item.get("links", []):
        if not isinstance(link, dict):
            errors.append("link must be an object")
            continue
        link_type = link.get("type")
        source_module = link.get("from")
        target_module = link.get("to")
        if link_type not in {"CALL", "RETURN"}:
            errors.append("link type must be CALL or RETURN")
            continue
        if not isinstance(source_module, str) or not MODULE_ID_PATTERN.fullmatch(source_module):
            errors.append("link from must be a module id")
            continue
        if not isinstance(target_module, str) or not MODULE_ID_PATTERN.fullmatch(target_module):
            errors.append("link to must be a module id")
            continue
        if link_type == "CALL":
            calls.append((source_module, target_module))
        elif not calls or calls.pop() != (target_module, source_module):
            errors.append(f"unmatched RETURN: {source_module} -> {target_module}")
        mappings = link.get("mapping")
        if not isinstance(mappings, list) or not mappings:
            errors.append(f"link mapping must not be empty: {source_module} -> {target_module}")
            continue
        sources: set[str] = set()
        targets: set[str] = set()
        for mapping in mappings:
            source = mapping.get("source") if isinstance(mapping, dict) else None
            target = mapping.get("target") if isinstance(mapping, dict) else None
            source_direction = "input" if link_type == "CALL" else "output"
            target_direction = "input" if link_type == "CALL" else "output"
            if not _qualified_field(source, source_module, source_direction):
                errors.append(f"invalid mapping source: {source}")
            if not _qualified_field(target, target_module, target_direction):
                errors.append(f"invalid mapping target: {target}")
            if source in sources:
                errors.append(f"duplicate mapping source: {source}")
            if target in targets:
                errors.append(f"duplicate mapping target: {target}")
            sources.add(source)
            targets.add(target)
    for caller, callee in reversed(calls):
        errors.append(f"CALL has no matching RETURN: {caller} -> {callee}")
    return [f"{node_id}: {error}" for error in errors]


def _qualified_field(value: Any, module_id: str, direction: str) -> bool:
    prefix = f"{module_id}.{direction}."
    return isinstance(value, str) and value.startswith(prefix) and bool(
        SYMBOL_PATTERN.fullmatch(value[len(prefix):])
    )


def _validate_batch(
    requirement_ids: list[str],
    payload: Any,
    validate_item: Callable[[str, dict[str, Any]], list[str]],
) -> list[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return ["items must be an array"]
    expected = set(requirement_ids)
    returned: set[str] = set()
    errors: list[str] = []
    for item in payload["items"]:
        if not isinstance(item, dict):
            errors.append("item must be an object")
            continue
        node_id = str(item.get("requirement_id", ""))
        if node_id not in expected:
            errors.append(f"unexpected requirement_id: {node_id}")
            continue
        if node_id in returned:
            errors.append(f"duplicate requirement_id: {node_id}")
            continue
        returned.add(node_id)
        errors.extend(validate_item(node_id, item))
    missing = sorted(expected - returned)
    if missing:
        errors.append("missing requirement_id: " + ", ".join(missing))
    return errors


def _requirement_context(nodes: dict[str, Any], node_id: str, dependencies: dict[str, Any]) -> dict[str, Any]:
    node = nodes.get(node_id, {})
    ancestors: list[dict[str, str]] = []
    parent_id = node.get("parent_id")
    while isinstance(parent_id, str) and parent_id:
        parent = nodes.get(parent_id)
        if not isinstance(parent, dict):
            break
        ancestors.append({
            "id": parent_id,
            "name": str(parent.get("name", "")),
            "description": str(parent.get("description", "")),
        })
        parent_id = parent.get("parent_id")
    ancestors.reverse()
    return {
        "requirement_id": node_id,
        "name": str(node.get("name", "")),
        "description": str(node.get("description", "")),
        "scenarios": node.get("scenarios", []),
        "dependencies": dependencies.get(node_id, []),
        "ancestors": ancestors,
    }


def _database_slice(schema: dict[str, Any], node_ids: list[str], dependencies: dict[str, Any]) -> list[dict[str, Any]]:
    related = set(node_ids)
    for node_id in node_ids:
        related.update(_dependency_closure(node_id, dependencies))
    entities = []
    for entity in schema.get("entities", []):
        if related.intersection(entity.get("sources", [])):
            entities.append({
                "key": entity.get("key"),
                "fields": [
                    {
                        "name": field.get("name"),
                        "type": field.get("type"),
                        "required": field.get("required", False),
                    }
                    for field in entity.get("fields", [])
                ],
                "relations": [
                    {
                        "name": relation.get("name"),
                        "target_entity": relation.get("target_entity"),
                        "cardinality": relation.get("cardinality"),
                        "required": relation.get("required", False),
                    }
                    for relation in entity.get("relations", [])
                ],
            })
    return entities


def _requirement_domains(
    schema: dict[str, Any],
    node_id: str,
    dependencies: dict[str, Any],
) -> list[str]:
    related = {node_id} | _dependency_closure(node_id, dependencies)
    return sorted(
        str(entity.get("key"))
        for entity in schema.get("entities", [])
        if related.intersection(entity.get("sources", [])) and entity.get("key")
    )


def _dependency_closure(node_id: str, dependencies: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    pending = list(dependencies.get(node_id, []))
    while pending:
        current = str(pending.pop())
        if current in result:
            continue
        result.add(current)
        pending.extend(dependencies.get(current, []))
    return result


def _ordered_waves(atomic_ids: Any, dependency_graph: dict[str, Any]) -> list[list[str]]:
    declared = {str(item) for item in atomic_ids if str(item)} if isinstance(atomic_ids, list) else set()
    result: list[list[str]] = []
    seen: set[str] = set()
    for raw_wave in dependency_graph.get("implementation_waves", []):
        if not isinstance(raw_wave, list):
            continue
        wave = sorted({str(item) for item in raw_wave} & declared - seen)
        if wave:
            result.append(wave)
            seen.update(wave)
    remaining = sorted(declared - seen)
    if remaining:
        result.append(remaining)
    return result


def _batches(items: list[str], size: int) -> list[list[str]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


def _without_owner(operation: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in operation.items() if key != "owner"}


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def design_hash(design_ir: dict[str, Any]) -> str:
    return _stable_hash(design_ir)


def verify_design_manifest(
    design_ir: dict[str, Any],
    database_schema: dict[str, Any],
    manifest: dict[str, Any],
) -> bool:
    """Return whether the frozen database and Design IR still match their manifest."""

    return (
        manifest.get("design_sha256") == _stable_hash(design_ir)
        and manifest.get("database_sha256") == _stable_hash(database_schema)
    )


def database_hash(database_schema: dict[str, Any]) -> str:
    return _stable_hash(database_schema)


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(os.environ.get(name, str(default))), maximum))
    except ValueError:
        return default


def _enabled_env_flag(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def _empty_design(draft: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "status": "PROPOSED",
        "modules": assign_module_files(draft["modules"]),
        "links": [],
        "requirements": [],
    }
