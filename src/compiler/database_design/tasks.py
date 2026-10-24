"""ER discovery lifecycles and isolated merge candidates."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from .common import format_error
from .contracts import RELATIONSHIP_DECISION_SCHEMA, RELATIONSHIP_INSTRUCTIONS
from .refinement import refine_er_model
from .state import DatabaseSchemaState


@dataclass(frozen=True, slots=True)
class DatabaseTaskInput:
    requirements: list[str]
    nodes: dict[str, Any]
    state: DatabaseSchemaState
    dependencies: dict[str, Any]
    node_states: dict[str, str]
    errors: list[str]


@dataclass(frozen=True, slots=True)
class DatabaseTaskOutput:
    state: DatabaseSchemaState
    node_states: dict[str, str]
    errors: list[str]


class RequirementDiscoveryTask:
    def __init__(self, *, phase, schema_name, schema, instructions, apply_method,
                 context, decide, validate, trace):
        self.phase, self.schema_name, self.schema = phase, schema_name, schema
        self.instructions, self.apply_method = instructions, apply_method
        self.context, self.decide, self.validate, self.trace = context, decide, validate, trace

    def run(self, inputs: DatabaseTaskInput) -> DatabaseTaskOutput:
        state, states, errors = copy.deepcopy((inputs.state, inputs.node_states, inputs.errors))
        self.trace(f'PASS_START phase={self.phase} requirements={len(inputs.requirements)}')
        for node_id in inputs.requirements:
            node = inputs.nodes.get(node_id)
            if not isinstance(node, dict):
                errors.append(format_error('ARC2101', 'Requirement is missing from Requirement IR.', node_id=node_id))
                states[node_id] = 'FAILED'
                continue
            decision = self.decide(phase=self.phase, schema_name=self.schema_name,
                output_schema=self.schema, instructions=self.instructions, node_id=node_id,
                context=self.context(self.phase, node_id, node, inputs.nodes, state, inputs.dependencies),
                errors=errors, warnings=state.warnings,
                validate=lambda value: self.validate(state, self.phase, node_id, value))
            if decision is None:
                states[node_id] = 'FAILED'
                continue
            candidate = copy.deepcopy(state)
            before = len(errors)
            getattr(candidate, self.apply_method)(node_id, decision, errors)
            if len(errors) != before:
                states[node_id] = 'FAILED'
                continue
            state = candidate
            states[node_id] = {'pass1_entities': 'ENTITIES_DISCOVERED', 'pass2_fields': 'FIELDS_DISCOVERED'}[self.phase]
            self.trace(f'DECISION_APPLIED phase={self.phase} requirement={node_id} state={states[node_id]}')
        self.trace(f'PASS_COMPLETED phase={self.phase} requirements={len(inputs.requirements)}')
        return DatabaseTaskOutput(state, states, errors)


class RelationshipDiscoveryTask:
    def __init__(self, context, decide, trace):
        self.context, self.decide, self.trace = context, decide, trace

    def run(self, inputs: DatabaseTaskInput) -> DatabaseTaskOutput:
        state, states, errors = copy.deepcopy((inputs.state, inputs.node_states, inputs.errors))
        entity_keys = list(state.entities)
        self.trace(f'PASS_START phase=pass3_relationships entities={len(entity_keys)}')
        for entity_key in entity_keys:
            decision = self.decide(phase='pass3_relationships', schema_name='arc_database_relationships',
                output_schema=RELATIONSHIP_DECISION_SCHEMA, instructions=RELATIONSHIP_INSTRUCTIONS,
                node_id=entity_key, context=self.context(entity_key, inputs.nodes, state, inputs.dependencies),
                errors=errors, warnings=state.warnings)
            if decision is None:
                continue
            candidate = copy.deepcopy(state)
            before = len(errors)
            for relationship in decision['relationships']:
                for requirement_id in sorted(set(relationship['evidence_requirement_ids'])):
                    candidate.apply_relationships(requirement_id, {'relationships': [relationship]}, errors)
            if len(errors) == before:
                state = candidate
            self.trace(f'DECISION_APPLIED phase=pass3_relationships entity={entity_key}')
        self.trace(f'PASS_COMPLETED phase=pass3_relationships entities={len(entity_keys)}')
        return DatabaseTaskOutput(state, states, errors)


@dataclass(frozen=True, slots=True)
class DatabaseRefinementOutput:
    schema: dict[str, Any]
    er_model: dict[str, Any]
    node_states: dict[str, str]
    errors: list[str]
    warnings: list[str]


class DatabaseRefinementTask:
    def run(self, inputs: DatabaseTaskInput) -> DatabaseRefinementOutput:
        errors = list(inputs.errors)
        warnings = list(inputs.state.warnings)
        er_model = inputs.state.to_er_model()
        er_model['status'] = 'RESOLVED' if not errors else 'PROPOSED'
        refined = refine_er_model(er_model, requirement_ids=set(inputs.requirements))
        errors.extend(refined.errors)
        warnings.extend(refined.warnings)
        if errors:
            refined.schema['status'] = er_model['status'] = 'PROPOSED'
        states = {node_id: 'SCHEMA_ANALYZED' if not errors else 'FAILED' for node_id in inputs.requirements}
        return DatabaseRefinementOutput(refined.schema, er_model, states, errors, warnings)
