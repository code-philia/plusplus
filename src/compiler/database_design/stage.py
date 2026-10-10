"""Database design stage orchestration and model context construction."""

from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from utils.logging import SynchronousLog

from ..ir import SchemaIR
from model.contracts import StructuredModel
from model.client import describe_model_error
from utils.trace import format_payload_trace
from .common import _format_error
from .contracts import (
    ENTITY_DECISION_SCHEMA,
    ENTITY_INSTRUCTIONS,
    FIELD_DECISION_SCHEMA,
    FIELD_INSTRUCTIONS,
    RELATIONSHIP_DECISION_SCHEMA,
    RELATIONSHIP_INSTRUCTIONS,
)
from .refinement import refine_er_model
from .state import DatabaseSchemaState
from .validation import (
    _normalize_decision_context,
    _validate_decision_context,
    _validate_decision_shape,
)


@dataclass(slots=True)
class DatabasePassResult:
    schema: SchemaIR
    node_states: dict[str, str]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class DatabaseSchemaPass:
    """Design entities, fields and relationships, then derive structural constraints."""

    def __init__(self, model: StructuredModel, artifact_root: Path) -> None:
        self._model = model
        self._artifact_root = artifact_root.expanduser().resolve()
        if hasattr(model, "set_usage_path"):
            model.set_usage_path(self._artifact_root / "model_usage.jsonl")
        self._log = SynchronousLog("DatabaseSchemaPass", workspace_root=self._artifact_root.parent)
        self._retry_count = _bounded_env_int("ARC_STRUCTURED_OUTPUT_RETRY_COUNT", 2, 0, 10)
        self._trace_enabled = _enabled_env_flag("ARC_DATABASE_TRACE", default=True)

    def compile(
        self,
        requirement_ir: dict[str, Any],
        dependency_graph: dict[str, Any] | None = None,
    ) -> DatabasePassResult:
        nodes = requirement_ir.get("nodes", {})
        requirements = [
            node_id
            for wave in _ordered_waves(requirement_ir.get("atomic_units", []), dependency_graph or {})
            for node_id in wave
        ]
        effective_dependencies = (dependency_graph or {}).get("atomic_dependencies", {})
        states = {node_id: "DISCOVERED" for node_id in requirements}
        errors: list[str] = []
        state = DatabaseSchemaState()
        for node_id in requirements:
            state.ensure_requirement(node_id)
        passes: list[tuple[str, str, dict[str, Any], str, Callable[..., None]]] = [
            ("pass1_entities", "arc_database_entities", ENTITY_DECISION_SCHEMA, ENTITY_INSTRUCTIONS, state.apply_entities),
            ("pass2_fields", "arc_database_fields", FIELD_DECISION_SCHEMA, FIELD_INSTRUCTIONS, state.apply_fields),
        ]
        for phase, schema_name, output_schema, instructions, apply_decision in passes:
            self._trace(f"PASS_START phase={phase} requirements={len(requirements)}")
            for node_id in requirements:
                node = nodes.get(node_id)
                if not isinstance(node, dict):
                    errors.append(_format_error("ARC2101", "Atomic requirement is missing from Requirement IR.", node_id=node_id))
                    states[node_id] = "FAILED"
                    continue
                decision = self._run_unit(
                    phase=phase,
                    schema_name=schema_name,
                    output_schema=output_schema,
                    instructions=instructions,
                    node_id=node_id,
                    context=self._context_for(
                        phase, node_id, node, nodes, state, effective_dependencies
                    ),
                    errors=errors,
                    warnings=state.warnings,
                )
                if decision is None:
                    states[node_id] = "FAILED"
                    continue
                before = len(errors)
                apply_decision(node_id, decision, errors)
                if len(errors) != before:
                    states[node_id] = "FAILED"
                    continue
                states[node_id] = {
                    "pass1_entities": "ENTITIES_DISCOVERED",
                    "pass2_fields": "FIELDS_DISCOVERED",
                }[phase]
                self._trace(
                    f"DECISION_APPLIED phase={phase} requirement={node_id} "
                    f"state={states[node_id]}"
                )
            self._trace(f"PASS_COMPLETED phase={phase} requirements={len(requirements)}")
            # Design diagnostics are accumulated across all three passes. Do
            # not stop after an invalid entity/field/relationship decision;
            # later passes may still recover usable schema information and the
            # controller will continue with the partial result.

        # Pass 3 is scheduled by ER entities, never by requirement nodes.
        entity_keys = sorted(state.entities)
        self._trace(f"PASS_START phase=pass3_relationships entities={len(entity_keys)}")
        for entity_key in entity_keys:
            decision = self._run_unit(
                phase="pass3_relationships", schema_name="arc_database_relationships",
                output_schema=RELATIONSHIP_DECISION_SCHEMA, instructions=RELATIONSHIP_INSTRUCTIONS,
                node_id=entity_key,
                context=self._relationship_context(entity_key, nodes, state, effective_dependencies),
                errors=errors, warnings=state.warnings,
            )
            if decision is None:
                continue
            # Attribute relationship ownership to its real requirement evidence.
            for relationship in decision["relationships"]:
                for requirement_id in sorted(set(relationship["evidence_requirement_ids"])):
                    state.apply_relationships(requirement_id, {"relationships": [relationship]}, errors)
            self._trace(f"DECISION_APPLIED phase=pass3_relationships entity={entity_key}")
        self._trace(f"PASS_COMPLETED phase=pass3_relationships entities={len(entity_keys)}")

        # Physical relationship mapping follows completion of the ER passes.
        er_model = state.to_er_model()
        er_model["status"] = "RESOLVED" if not errors else "PROPOSED"
        refined = refine_er_model(er_model, requirement_ids=set(requirements))
        errors.extend(refined.errors)
        state.warnings.extend(refined.warnings)
        schema = refined.schema
        if errors:
            schema["status"] = "PROPOSED"
            er_model["status"] = "PROPOSED"
        schema["er_model"] = er_model
        states.update({node_id: "SCHEMA_ANALYZED" if not errors else "FAILED" for node_id in requirements})
        return DatabasePassResult(schema, states, errors, state.warnings)

    def _context_for(
        self,
        phase: str,
        node_id: str,
        node: dict[str, Any],
        nodes: dict[str, Any],
        state: DatabaseSchemaState,
        effective_dependencies: dict[str, Any],
    ) -> dict[str, Any]:
        requirement = _node_context(
            node,
            nodes,
            effective_dependencies.get(node_id, node.get("dependencies", [])),
        )
        if phase == "pass1_entities":
            return {"requirement": requirement, "existing_entities": state.entity_headers()}
        if phase == "pass2_fields":
            return {"requirement": requirement, "related_entities": state.schema_slice(node_id)["entities"],
                    "own_field_contributions": {key: copy.deepcopy(rows[node_id])
                                                for key, rows in state.contributions["fields"].items() if node_id in rows}}
        raise ValueError(f"Unknown database design phase: {phase}")

    def _relationship_context(
        self, entity_key: str, nodes: dict[str, Any], state: DatabaseSchemaState,
        effective_dependencies: dict[str, Any],
    ) -> dict[str, Any]:
        excerpt_limit = _bounded_env_int("ARC_DATABASE_RELATIONSHIP_EXCERPT_CHARS", 500, 100, 4000)
        associated = set(state.entities[entity_key]["requirement_ids"])
        related_ids = set(associated)
        for rid in associated:
            related_ids.update(effective_dependencies.get(rid, nodes.get(rid, {}).get("dependencies", [])))
        return {
            "focus_entity": state._public_entity(state.entities[entity_key]),
            "related_requirements": [
                _node_context(nodes[rid], nodes, effective_dependencies.get(rid, nodes[rid].get("dependencies", [])))
                for rid in sorted(related_ids) if isinstance(nodes.get(rid), dict)
            ],
            "er_graph": {
                "entities": [
                    {"key": item["key"], "description": item["description"],
                     "requirement_ids": list(item["requirement_ids"]),
                     "attributes": [{"name": field["name"], "type": field["type"],
                                     "description": field["description"]}
                                    for field in sorted(item["fields"].values(), key=lambda field: field["name"])]}
                    for item in state._ordered_entities()
                ],
                "relationships": [copy.deepcopy(item) for item in sorted(
                    state.relationships.values(), key=lambda item: item["id"])],
            },
            "requirement_catalog": [
                {"id": rid, "name": nodes[rid].get("name", ""),
                 "description_excerpt": str(nodes[rid].get("description", ""))[:excerpt_limit],
                 "description_truncated": len(str(nodes[rid].get("description", ""))) > excerpt_limit}
                for rid in sorted(state.requirement_entity_links) if isinstance(nodes.get(rid), dict)
            ],
        }

    def _run_unit(
        self,
        *,
        phase: str,
        schema_name: str,
        output_schema: dict[str, Any],
        instructions: str,
        node_id: str,
        context: dict[str, Any],
        errors: list[str],
        warnings: list[str],
    ) -> dict[str, Any] | None:
        feedback: list[str] = []
        for attempt in range(self._retry_count + 1):
            payload = copy.deepcopy(context)
            if feedback:
                payload["previous_validation_errors"] = feedback
            started_at = time.monotonic()
            self._trace(
                f"MODEL_REQUEST phase={phase} requirement={node_id} "
                f"attempt={attempt + 1}/{self._retry_count + 1} schema={schema_name}"
            )
            request_payload = {
                "schema_name": schema_name,
                "instructions": instructions,
                "input_payload": payload,
                "output_schema": output_schema,
            }
            self._trace(
                _context_audit(
                    phase=phase,
                    requirement_id=node_id,
                    attempt=attempt + 1,
                    request_payload=request_payload,
                )
            )
            self._trace(
                f"MODEL_INPUT phase={phase} requirement={node_id} attempt={attempt + 1}\n"
                + format_payload_trace(request_payload)
            )
            try:
                decision = self._model.generate_json(
                    schema_name=schema_name,
                    instructions=instructions,
                    input_payload=payload,
                    output_schema=output_schema,
                )
            except Exception as exc:
                duration_ms = round((time.monotonic() - started_at) * 1000)
                feedback = [f"Model call failed: {describe_model_error(exc)}"]
                self._trace(
                    f"MODEL_ERROR phase={phase} requirement={node_id} "
                    f"attempt={attempt + 1} duration_ms={duration_ms} error={feedback[0]}"
                )
            else:
                duration_ms = round((time.monotonic() - started_at) * 1000)
                self._trace(
                    f"MODEL_OUTPUT phase={phase} requirement={node_id} "
                    f"attempt={attempt + 1} duration_ms={duration_ms}\n"
                    + format_payload_trace(decision)
                )
                decision, normalization_notes = _normalize_decision_context(
                    phase, decision, context
                )
                for note in normalization_notes:
                    self._trace(
                        f"MODEL_NORMALIZED phase={phase} requirement={node_id} "
                        f"action={note}"
                    )
                feedback = _validate_decision_shape(phase, node_id, decision)
                feedback.extend(_validate_decision_context(phase, decision, context))
                if not feedback:
                    self._trace(
                        f"MODEL_ACCEPTED phase={phase} requirement={node_id} "
                        f"attempt={attempt + 1} duration_ms={duration_ms}"
                    )
                    return decision
            self._trace(f"MODEL_REJECTED phase={phase} requirement={node_id} errors={'; '.join(feedback)}")
        errors.append(_format_error("ARC2103", f"{phase} failed: {'; '.join(feedback)}", node_id=node_id))
        return None


    def _trace(self, message: str) -> None:
        if self._trace_enabled:
            self._log.info(message)


def _node_context(
    node: dict[str, Any],
    nodes: dict[str, Any],
    effective_dependencies: Any,
) -> dict[str, Any]:
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
        "requirement_id": str(node.get("id", "")),
        "name": str(node.get("name", "")),
        "description": str(node.get("description", "")),
        "scenarios": copy.deepcopy(node.get("scenarios", [])),
        "dependencies": copy.deepcopy(effective_dependencies),
        "ancestors": ancestors,
    }


def _ordered_waves(atomic_ids: Any, dependency_graph: dict[str, Any]) -> list[list[str]]:
    declared = {str(item) for item in atomic_ids if str(item)} if isinstance(atomic_ids, list) else set()
    result: list[list[str]] = []
    seen: set[str] = set()
    raw_waves = dependency_graph.get("atomic_implementation_waves")
    if not isinstance(raw_waves, list) or not raw_waves:
        raw_waves = dependency_graph.get("implementation_waves", [])
    for raw_wave in raw_waves:
        if not isinstance(raw_wave, list):
            continue
        wave = sorted(({str(item) for item in raw_wave} & declared) - seen)
        if wave:
            result.append(wave)
            seen.update(wave)
    if declared - seen:
        result.append(sorted(declared - seen))
    return result


def _context_audit(
    *,
    phase: str,
    requirement_id: str,
    attempt: int,
    request_payload: dict[str, Any],
) -> str:
    def size(value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

    input_payload = request_payload.get("input_payload", {})
    section_sizes = sorted(
        (
            (str(key), size(value))
            for key, value in input_payload.items()
        )
        if isinstance(input_payload, dict)
        else [],
        key=lambda item: item[1],
        reverse=True,
    )
    model_context = {
        "instructions": request_payload.get("instructions", ""),
        "input_payload": input_payload,
        "output_schema": request_payload.get("output_schema", {}),
    }
    return (
        f"CONTEXT_AUDIT phase={phase} requirement={requirement_id} attempt={attempt} "
        f"context_total_chars={size(model_context)} "
        f"instructions_chars={size(request_payload.get('instructions', ''))} "
        f"input_payload_chars={size(input_payload)} "
        f"output_schema_chars={size(request_payload.get('output_schema', {}))} "
        f"section_chars="
        + ",".join(f"{key}:{value}" for key, value in section_sizes)
    )


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
