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

from compiler.preprocessing.queries import leaf_ids

from ..ir.database import ERModelIR, SchemaIR
from model.contracts import StructuredModel
from utils.trace import format_payload_trace
from .common import format_error
from .contracts import (
    ENTITY_DECISION_SCHEMA,
    ENTITY_INSTRUCTIONS,
    FIELD_DECISION_SCHEMA,
    FIELD_INSTRUCTIONS,
)
from .state import DatabaseSchemaState
from .decisions import DatabaseDecisionInput, DatabaseDecisionTask
from .tasks import (DatabaseTaskInput, RequirementDiscoveryTask, RelationshipDiscoveryTask,
                    DatabaseRefinementTask)


@dataclass(slots=True)
class DatabasePassResult:
    schema: SchemaIR
    node_states: dict[str, str]
    er_model: ERModelIR
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
        self._attempts = bounded_env_int("MODEL_ATTEMPT", 5, 1, 20)
        self._trace_enabled = enabled_env_flag("ARC_DATABASE_TRACE", default=True)

    def compile(
        self,
        requirement_ir: dict[str, Any],
        dependency_graph: dict[str, Any] | None = None,
    ) -> DatabasePassResult:
        # Stage: Database design - collect requirements in postorder ===========================
        nodes = requirement_ir.get("nodes", {})
        requirements = leaf_ids(requirement_ir)
        effective_dependencies = (dependency_graph or {}).get("requirement_dependencies", {})
        self.trace(f"DESIGN_ORDER traversal=depth_first_postorder requirements={requirements}")
        states = {node_id: "DISCOVERED" for node_id in requirements}
        errors: list[str] = []
        state = DatabaseSchemaState()
        for node_id in requirements:
            state.ensure_requirement(node_id)
        passes = [
            ('pass1_entities', 'arc_database_entities', ENTITY_DECISION_SCHEMA, ENTITY_INSTRUCTIONS, 'apply_entities'),
            ('pass2_fields', 'arc_database_fields', FIELD_DECISION_SCHEMA, FIELD_INSTRUCTIONS, 'apply_fields'),
        ]
        for phase, schema_name, schema, instructions, apply_method in passes:
            task = RequirementDiscoveryTask(phase=phase, schema_name=schema_name, schema=schema,
                instructions=instructions, apply_method=apply_method, context=self.context_for,
                decide=self.run_unit, validate=validate_access_decision, trace=self.trace)
            output = task.run(DatabaseTaskInput(requirements, nodes, state, effective_dependencies, states, errors))
            state, states, errors = output.state, output.node_states, output.errors
        output = RelationshipDiscoveryTask(self.relationship_context, self.run_unit, self.trace).run(
            DatabaseTaskInput(requirements, nodes, state, effective_dependencies, states, errors))
        refined = DatabaseRefinementTask().run(DatabaseTaskInput(requirements, nodes, output.state,
            effective_dependencies, output.node_states, output.errors))
        return DatabasePassResult(refined.schema, refined.node_states, refined.er_model,
                                  refined.errors, refined.warnings)

    def context_for(
        self,
        phase: str,
        node_id: str,
        node: dict[str, Any],
        nodes: dict[str, Any],
        state: DatabaseSchemaState,
        effective_dependencies: dict[str, Any],
    ) -> dict[str, Any]:
        requirement = node_context(
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

    def relationship_context(
        self, entity_key: str, nodes: dict[str, Any], state: DatabaseSchemaState,
        effective_dependencies: dict[str, Any],
    ) -> dict[str, Any]:
        excerpt_limit = bounded_env_int("ARC_DATABASE_RELATIONSHIP_EXCERPT_CHARS", 500, 100, 4000)
        associated = set(state.entities[entity_key]["requirement_ids"])
        related_ids = set(associated)
        for rid in associated:
            related_ids.update(effective_dependencies.get(rid, nodes.get(rid, {}).get("dependencies", [])))
        return {
            "focus_entity": state.public_entity(state.entities[entity_key]),
            "related_requirements": [
                node_context(nodes[rid], nodes, effective_dependencies.get(rid, nodes[rid].get("dependencies", [])))
                for rid in sorted(related_ids) if isinstance(nodes.get(rid), dict)
            ],
            "er_graph": {
                "entities": [
                    {"key": item["key"], "description": item["description"],
                     "requirement_ids": list(item["requirement_ids"]),
                     "attributes": [{"name": field["name"], "type": field["type"],
                                     "description": field["description"]}
                                    for field in sorted(item["fields"].values(), key=lambda field: field["name"])]}
                    for item in state.ordered_entities()
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

    def run_unit(
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
        validate: Callable[[dict[str, Any]], list[str]] | None = None,
    ) -> dict[str, Any] | None:
        inputs = DatabaseDecisionInput(phase, schema_name, output_schema, instructions, node_id, context)
        return DatabaseDecisionTask(inputs, model=self._model, attempts=self._attempts,
            prepare=self.prepare_decision_request, trace=self.trace, errors=errors, validate=validate).run()

    def prepare_decision_request(
        self, *, phase: str, schema_name: str, output_schema: dict[str, Any],
        instructions: str, node_id: str, context: dict[str, Any], feedback: list[str],
        attempt: int,
    ) -> tuple[dict[str, Any], float]:
        # Copy each attempt independently; normalization still uses the original context.
        payload = copy.deepcopy(context)
        if feedback:
            payload["previous_validation_errors"] = feedback
        started_at = time.monotonic()
        self.trace(
            f"MODEL_REQUEST phase={phase} requirement={node_id} "
            f"attempt={attempt + 1}/{self._attempts} schema={schema_name}"
        )
        request_payload = {
            "schema_name": schema_name,
            "instructions": instructions,
            "input_payload": payload,
            "output_schema": output_schema,
        }
        self.trace(
            context_audit(
                phase=phase,
                requirement_id=node_id,
                attempt=attempt + 1,
                request_payload=request_payload,
            )
        )
        self.trace(
            f"MODEL_INPUT phase={phase} requirement={node_id} attempt={attempt + 1}\n"
            + format_payload_trace(request_payload)
        )
        return payload, started_at

    def trace(self, message: str) -> None:
        if self._trace_enabled:
            self._log.info(message)


def validate_access_decision(state: DatabaseSchemaState, phase: str, rid: str,
                              value: Any) -> list[str]:
    """Check metadata and references on an isolated trial before committing a pass."""
    if not isinstance(value, dict) or value.get('requirement_id') != rid:
        return ['Return an object with the exact current requirement_id.']
    errors = []
    for operation in ('read', 'write'):
        references = value.get(operation)
        if not isinstance(references, list) or any(not isinstance(ref, str) for ref in references):
            errors.append(f'{operation} must be an array of entity or entity.field references.')
        elif len(references) != len(set(references)):
            errors.append(f'{operation} contains duplicate targets.')
        elif phase == 'pass1_entities' and any('.' in ref for ref in references):
            errors.append('Entity discovery read/write must contain entity keys only; report fields in pass2.')
    if errors:
        return errors
    trial = copy.deepcopy(state)
    try:
        if phase == 'pass1_entities':
            for row in value.get('new_entities', []):
                if not isinstance(row.get('description'), str) or not row['description'].strip():
                    errors.append('Each new entity needs a concise meaning/purpose description.')
            trial.apply_entities(rid, value, errors)
        else:
            for item in value.get('entities', []):
                reused = item.get('reuse_fields')
                if not isinstance(reused, list) or any(not isinstance(name, str) for name in reused):
                    errors.append('Each entity entry needs reuse_fields as an array of existing field names.')
                    continue
                for row in [*item.get('new_fields', []), *item.get('update_fields', [])]:
                    if not isinstance(row.get('description'), str) or not row['description'].strip():
                        errors.append('Each declared field needs a concise meaning/purpose description.')
            if not errors:
                trial.apply_fields(rid, value, errors)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        errors.append(f'Invalid database design declaration: {exc}')
    return errors


def node_context(
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
        "pages": copy.deepcopy(node.get("pages", [])),
        "dependencies": copy.deepcopy(effective_dependencies),
        "ancestors": ancestors,
    }


def context_audit(
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


def bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(os.environ.get(name, str(default))), maximum))
    except ValueError:
        return default


def enabled_env_flag(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}
