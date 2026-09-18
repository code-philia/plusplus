"""Design IR compiler with top-down module decomposition."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from core.logging import SynchronousLog

from .model_client import StructuredModel, describe_model_error


PRIMITIVE_TYPES = {"string", "integer", "number", "boolean", "date", "datetime", "uuid", "json"}
DATABASE_EFFECT_OPERATIONS = {"READ", "CREATE", "UPDATE", "DELETE"}
EFFECT_OPERATIONS = DATABASE_EFFECT_OPERATIONS | {"SESSION_WRITE", "COOKIE_WRITE", "EXTERNAL_IO"}
REPAIR_CURRENT = "REPAIR_CURRENT"
REOPEN_PARENT = "REOPEN_PARENT"
UNRESOLVED = "UNRESOLVED"


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


FIELD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["semantic_id", "name", "type", "description", "required"],
    "properties": {
        "semantic_id": {"type": "string", "maxLength": 120, "pattern": r"^[a-z][a-z0-9_.]*$"},
        "name": {"type": "string", "maxLength": 64, "pattern": r"^[a-z][a-z0-9_]*$"},
        "type": {"type": "string", "enum": sorted(PRIMITIVE_TYPES)},
        "description": {"type": "string", "maxLength": 300},
        "required": {"type": "boolean"},
    },
}

EFFECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "operation", "target", "fields"],
    "properties": {
        "id": {"type": "string", "maxLength": 64, "pattern": r"^[a-z][a-z0-9_]*$"},
        "operation": {"type": "string", "enum": sorted(EFFECT_OPERATIONS)},
        "target": _nullable({"type": "string", "maxLength": 64}),
        "fields": {"type": "array", "items": {"type": "string", "maxLength": 64}},
    },
}

REQUIREMENT_CONTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["requirement_id", "spec", "inputs", "outputs", "effects"],
    "properties": {
        "requirement_id": {"type": "string"},
        "spec": {"type": "string", "maxLength": 800},
        "inputs": {"type": "array", "items": FIELD_SCHEMA},
        "outputs": {"type": "array", "items": FIELD_SCHEMA},
        "effects": {"type": "array", "items": EFFECT_SCHEMA},
    },
}

MODULE_INTERFACE_FIELD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["semantic_id", "name", "type", "required"],
    "properties": {
        "semantic_id": {"type": "string", "maxLength": 120, "pattern": r"^[a-z][a-z0-9_.]*$"},
        "name": {"type": "string", "maxLength": 64, "pattern": r"^[a-z][a-z0-9_]*$"},
        "type": {"type": "string", "enum": sorted(PRIMITIVE_TYPES)},
        "required": {"type": "boolean"},
    },
}

MODULE_EFFECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "operation", "target", "fields"],
    "properties": {
        "id": {"type": "string", "maxLength": 120, "pattern": r"^[a-z][a-z0-9_]*$"},
        "operation": {
            "type": "string",
            "enum": sorted(EFFECT_OPERATIONS),
        },
        "target": _nullable({"type": "string", "maxLength": 64}),
        "fields": {"type": "array", "items": {"type": "string", "maxLength": 64}},
    },
}

DECOMPOSED_MODULE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "name", "spec", "inputs", "outputs", "effects"],
    "properties": {
        "kind": {"type": "string", "enum": ["FUNC", "DB"]},
        "name": {"type": "string", "maxLength": 64, "pattern": r"^[A-Za-z][A-Za-z0-9]*$"},
        "spec": {"type": "string"},
        "inputs": {"type": "array", "items": MODULE_INTERFACE_FIELD_SCHEMA},
        "outputs": {"type": "array", "items": MODULE_INTERFACE_FIELD_SCHEMA},
        "effects": {"type": "array", "items": MODULE_EFFECT_SCHEMA},
    },
}

MODULE_DECOMPOSITION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["modules"],
    "properties": {
        "modules": {"type": "array", "minItems": 0, "maxItems": 8, "items": DECOMPOSED_MODULE_SCHEMA},
    },
}

API_MODULE_SCHEMA = copy.deepcopy(DECOMPOSED_MODULE_SCHEMA)
API_MODULE_SCHEMA["properties"]["kind"]["enum"] = ["API"]

API_DECOMPOSITION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["modules"],
    "properties": {
        "modules": {"type": "array", "minItems": 0, "maxItems": 4, "items": API_MODULE_SCHEMA},
    },
}


REQUIREMENT_CONTRACT_INSTRUCTIONS = """Analyze one atomic requirement as a black box and fill the supplied fixed
template. Keep every required key and use [] when a section is empty. Describe only the requirement spec, external
inputs, observable outputs, and required effects. Represent database reads as READ effects and state changes as their
corresponding effect operation. Use only supplied database entities and fields. Constraints in the context must be
reflected in the spec when they affect behavior; do not return constraint ids or allocate constraints to modules.
Every database access required to satisfy observable behavior must appear as a READ, CREATE, UPDATE, or DELETE effect.
This includes persisted session records: when `session` is a supplied database entity, inserting a session row is
CREATE on `session`, not SESSION_WRITE. Reserve SESSION_WRITE for a non-database runtime session store. If the
requirement writes a browser cookie, include COOKIE_WRITE. In particular, checking whether values already exist for
UNIQUE or COMPOSITE_UNIQUE constraints requires a READ effect even when the database also enforces the constraint. Do
not hide required effects inside the prose spec.
Do not design modules, calls, steps, bindings, outcomes, guards, or algorithms. Keep every id concise (64 characters
or fewer). Return only the structured object.

Example shape:
{"requirement_id":"REQ-1.1","spec":"Register one traveler.","inputs":[{"semantic_id":"registration.username","name":"username","type":"string","description":"Requested username.","required":true}],"outputs":[{"semantic_id":"traveler.id","name":"traveler_id","type":"uuid","description":"Created traveler id.","required":true}],"effects":[{"id":"create_traveler","operation":"CREATE","target":"traveler","fields":["username"]}]}
"""

API_DECOMPOSITION_INSTRUCTIONS = """Turn one Requirement Contract into API modules. Keep one user action in one API
unless the requirement explicitly defines multiple operations. Every API contains exactly kind, name, spec, inputs,
outputs, and effects. Set kind to API. Copy interface fields and effects from the supplied contract without changing
their semantic identifiers, types, or required flags. Field names are local parameter labels and may be made clearer
without changing the represented data. A module whose spec describes a database read or write must own the
corresponding effect from the supplied contract; never describe hidden database access on a module with an empty
effects list.
Do not design child functions or implementation steps. Return only `{\"modules\": [...]}`.
"""

MODULE_DECOMPOSITION_INSTRUCTIONS = """Read the layered Markdown context and decompose the current module from the top down.
Silently plan how the parent responsibility is completed, then return only its direct child modules in execution order.

Every child contains exactly six top-level fields: kind, name, spec, inputs, outputs, and effects. Each interface field
contains semantic_id, name, type, and required. Preserve required exactly when reusing a parent field; mark newly
introduced values required only when the child cannot complete without them. An API may call FUNC only. A
FUNC may contain its own logic and may call FUNC or DB modules. A DB module is always a leaf. The list order is the call
order. A module input must come from the parent inputs or an earlier child output. Copy the exact interface field and
effect identifiers supplied in the Markdown. Keep child responsibilities cohesive and smaller than the parent. Return
an empty modules list when a FUNC can complete its remaining pure logic itself. Database effects are exactly READ,
CREATE, UPDATE, and DELETE; they must be delegated to DB modules and may not remain inside a terminal FUNC.
SESSION_WRITE, COOKIE_WRITE, and EXTERNAL_IO are application effects and must never be assigned to a DB module. Every
DB child must own at least one database effect. Never invent an effect: when the parent effect table is empty, all
child effects must be [] and no DB child may be introduced. A child whose spec describes database access must own one
of the exact effects listed in the parent table.

Treat semantic_id as the stable identity of a data value and type as its canonical data type. The name is only a local
parameter label and may differ between modules. A child output may introduce new data or pass through/refine data that
is already available, such as returning account.id after verifying its password. When reusing a semantic_id, preserve
its type exactly. Do not invent a new semantic_id merely to represent that an existing value passed validation.

Do not output any fields beyond the six listed above. The compiler derives symbol IDs and graph relationships. Return
only the structured `{\"modules\": [...]}` object.
"""


@dataclass(slots=True)
class DesignIssue:
    code: str
    message: str
    owner_phase: str
    blame_symbol: str
    repair_action: str = REPAIR_CURRENT
    severity: str = "ERROR"
    context: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "owner_phase": self.owner_phase,
            "blame_symbol": self.blame_symbol,
            "repair_action": self.repair_action,
            "message": self.message,
            "context": copy.deepcopy(self.context),
        }

    def feedback(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass(slots=True)
class DesignPassResult:
    design_ir: dict[str, Any]
    node_states: dict[str, str]
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(slots=True)
class DecisionResult:
    value: dict[str, Any] | None
    issues: list[DesignIssue]


class DesignState:
    """Transactional symbol registry and authoritative design graph state."""

    def __init__(self) -> None:
        self.requirement_contracts: dict[str, dict[str, Any]] = {}
        self.modules: dict[str, dict[str, Any]] = {}
        self.invocations: list[dict[str, Any]] = []
        self.requirement_modules: dict[str, set[str]] = {}

    def clone(self) -> "DesignState":
        return copy.deepcopy(self)

    def to_ir(self) -> dict[str, Any]:
        callers_by_module: dict[str, set[str]] = {}
        callees_by_module: dict[str, list[str]] = {}
        for invocation in self.invocations:
            caller_id = str(invocation["caller"])
            callee_id = str(invocation["callee"])
            callers_by_module.setdefault(callee_id, set()).add(caller_id)
            callees = callees_by_module.setdefault(caller_id, [])
            if callee_id not in callees:
                callees.append(callee_id)
        modules: list[dict[str, Any]] = []
        for module_id in sorted(self.modules):
            module = copy.deepcopy(self.modules[module_id])
            module["callers"] = sorted(callers_by_module.get(module_id, set()))
            module["callees"] = copy.deepcopy(callees_by_module.get(module_id, []))
            modules.append(module)
        return {
            "requirements": [{
                "id": requirement_id,
                "contract": copy.deepcopy(self.requirement_contracts[requirement_id]),
                "api_ids": sorted(
                    module_id for module_id in self.requirement_modules.get(requirement_id, set())
                    if self.modules[module_id]["kind"] == "API"
                ),
            } for requirement_id in sorted(self.requirement_contracts)],
            "modules": modules,
        }


class DesignPass:
    """Compile Design IR through top-down module decomposition."""

    def __init__(self, model: StructuredModel, artifact_root: Path) -> None:
        self._model = model
        arc_root = artifact_root.expanduser().resolve()
        self._log = SynchronousLog("DesignPass", workspace_root=arc_root.parent)
        self._local_retries = _env_int("ARC_STRUCTURED_OUTPUT_RETRY_COUNT", 2, 0, 10)
        self._reopen_budget = _env_int("ARC_DESIGN_REOPEN_COUNT", 2, 0, 6)
        self._trace_enabled = _env_flag("ARC_DESIGN_TRACE", True)
        self._max_modules = _env_int("ARC_DESIGN_MAX_MODULES_PER_REQUIREMENT", 64, 4, 256)

    def compile(
        self,
        requirement_ir: dict[str, Any],
        dependency_graph: dict[str, Any],
        database_schema: dict[str, Any],
    ) -> DesignPassResult:
        nodes = requirement_ir.get("nodes", {})
        dependencies = dependency_graph.get("atomic_dependencies", {})
        order = [node for wave in _waves(requirement_ir, dependency_graph) for node in wave]
        state = DesignState()
        all_issues: list[DesignIssue] = []
        states: dict[str, str] = {}

        for requirement_id in order:
            requirement = _requirement_context(nodes, requirement_id)
            design_context = project_design_context(database_schema, requirement_id, dependencies)
            model_design_context = _model_design_context(design_context)
            contract_result = self._decision(
                phase="requirement_contract",
                unit_id=requirement_id,
                schema_name="arc_requirement_contract",
                instructions=REQUIREMENT_CONTRACT_INSTRUCTIONS,
                output_schema=REQUIREMENT_CONTRACT_SCHEMA,
                context={
                    "requirement": requirement,
                    "design_context": model_design_context,
                    "fixed_template": _requirement_contract_template(requirement_id),
                },
                validator=lambda value: _requirement_contract_issues(value, requirement_id, design_context),
            )
            if contract_result.value is None:
                states[requirement_id] = "FAILED"
                all_issues.extend(contract_result.issues)
                break

            base = state.clone()
            contract = _normalize_requirement_contract(contract_result.value)
            contract, normalized_effect_ids = _normalize_database_backed_session_effects(
                contract,
                design_context=design_context,
            )
            if normalized_effect_ids:
                self._trace(
                    "CONTRACT_EFFECTS_NORMALIZED "
                    f"requirement={requirement_id} effects={normalized_effect_ids} "
                    "from=SESSION_WRITE to=CREATE source=database_entities"
                )
            contract, completed_effect_ids = _complete_constraint_read_effects(
                contract,
                requirement_id=requirement_id,
                design_context=design_context,
            )
            if completed_effect_ids:
                self._trace(
                    "CONTRACT_EFFECTS_COMPLETED "
                    f"requirement={requirement_id} effects={completed_effect_ids} "
                    "source=database_unique_constraints"
                )
            base.requirement_contracts[requirement_id] = contract
            compiled, issues = self._compile_requirement(
                base,
                requirement_id,
                requirement,
                contract,
                design_context,
            )
            if compiled is None:
                states[requirement_id] = "FAILED"
                all_issues.extend(issues)
                break
            state = compiled
            states[requirement_id] = "DESIGN_VALIDATED"

        final_issues = all_issues
        design = state.to_ir()
        errors = [f"{issue.code}: {issue.message}" for issue in final_issues]
        return DesignPassResult(design, states, errors)

    def _compile_requirement(
        self,
        base: DesignState,
        requirement_id: str,
        requirement: dict[str, Any],
        contract: dict[str, Any],
        design_context: dict[str, Any],
    ) -> tuple[DesignState | None, list[DesignIssue]]:
        upstream_feedback: list[str] = []
        seen_failures: set[str] = set()
        last_issues: list[DesignIssue] = []
        for reopen_attempt in range(self._reopen_budget + 1):
            context = {
                "requirement": requirement,
                "fixed_requirement_contract": contract,
                "fixed_template": _api_template(),
            }
            if upstream_feedback:
                context["upstream_validation_feedback"] = upstream_feedback
            result = self._decision(
                phase="requirement_api",
                unit_id=requirement_id,
                schema_name="arc_requirement_api",
                instructions=API_DECOMPOSITION_INSTRUCTIONS,
                output_schema=API_DECOMPOSITION_SCHEMA,
                context=context,
                validator=lambda value: _api_plan_issues(value, requirement_id, contract),
            )
            if result.value is None:
                return None, result.issues
            trial = base.clone()
            api_ids, issues = _materialize_apis(trial, requirement_id, contract, result.value)
            if issues:
                return None, issues
            failure: list[DesignIssue] = []
            for api_id in api_ids:
                expanded, child_issues = self._expand_module(
                    trial,
                    requirement_id,
                    requirement,
                    api_id,
                    contract,
                    design_context,
                    depth=0,
                )
                if expanded is None:
                    failure = child_issues
                    break
                trial = expanded
            if not failure:
                return trial, []
            last_issues = failure
            if not any(issue.repair_action == REOPEN_PARENT for issue in failure):
                return None, failure
            fingerprint = _hash({
                "decision": result.value,
                "issues": [issue.as_dict() for issue in failure],
            })
            if fingerprint in seen_failures:
                return None, [DesignIssue("UNRESOLVED_DESIGN_DECISION", f"Repeated upstream repair produced the same conflict for {requirement_id}", "REQUIREMENT_API", requirement_id, UNRESOLVED, context={"issues": [item.as_dict() for item in failure]})]
            seen_failures.add(fingerprint)
            upstream_feedback = [issue.feedback() for issue in failure]
            self._trace(f"UPSTREAM_REOPEN phase=requirement_api requirement={requirement_id} attempt={reopen_attempt + 1}/{self._reopen_budget + 1} feedback={'; '.join(upstream_feedback)}")
        return None, [DesignIssue(
            "UNRESOLVED_DESIGN_DECISION",
            f"Upstream repair budget was exhausted for {requirement_id}",
            "REQUIREMENT_API",
            requirement_id,
            UNRESOLVED,
            context={"issues": [item.as_dict() for item in last_issues]},
        )]

    def _decision(
        self,
        *,
        phase: str,
        unit_id: str,
        schema_name: str,
        instructions: str,
        output_schema: dict[str, Any],
        context: dict[str, Any],
        validator: Callable[[dict[str, Any]], list[DesignIssue]],
    ) -> DecisionResult:
        feedback: list[str] = []
        last_issues: list[DesignIssue] = []
        for attempt in range(self._local_retries + 1):
            payload = copy.deepcopy(context)
            if feedback:
                if phase == "module_decomposition" and isinstance(payload.get("context_markdown"), str):
                    payload["context_markdown"] += _feedback_markdown(feedback)
                else:
                    payload["validation_feedback"] = feedback
            self._trace(f"MODEL_REQUEST phase={phase} unit={unit_id} attempt={attempt + 1}/{self._local_retries + 1}")
            provider_schema = _provider_output_schema(output_schema)
            self._trace_json("MODEL_INPUT", phase, unit_id, {
                "instructions": instructions,
                "input_payload": payload,
                "output_schema": provider_schema,
                "local_validation_schema": output_schema,
                "schema_name": schema_name,
            })
            started = time.perf_counter()
            try:
                decision = self._model.generate_json(
                    schema_name=schema_name,
                    instructions=instructions,
                    input_payload=payload,
                    output_schema=provider_schema,
                )
            except Exception as exc:
                issue = DesignIssue("MODEL_CALL_FAILED", describe_model_error(exc), phase.upper(), unit_id)
                last_issues = [issue]
                feedback = [issue.feedback()]
                self._trace(f"MODEL_ERROR phase={phase} unit={unit_id} errors={issue.feedback()}")
                continue
            duration = int((time.perf_counter() - started) * 1000)
            self._trace_json("MODEL_OUTPUT", phase, unit_id, decision, duration)
            issues = _shape_issues(decision, output_schema, phase, unit_id)
            if not issues:
                issues = validator(decision)
            if not issues:
                self._trace(f"MODEL_ACCEPTED phase={phase} unit={unit_id} attempt={attempt + 1} duration_ms={duration}")
                return DecisionResult(decision, [])
            last_issues = issues
            feedback = [issue.feedback() for issue in issues]
            self._trace(f"MODEL_REJECTED phase={phase} unit={unit_id} errors={'; '.join(feedback)}")
        return DecisionResult(None, last_issues)

    def _trace(self, message: str) -> None:
        if self._trace_enabled:
            self._log.info(message)

    def _trace_json(self, marker: str, phase: str, unit_id: str, payload: Any, duration: int | None = None) -> None:
        suffix = f" duration_ms={duration}" if duration is not None else ""
        self._trace(f"{marker} phase={phase} unit={unit_id}{suffix}\n{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}")

    def _expand_module(
        self,
        state: DesignState,
        requirement_id: str,
        requirement: dict[str, Any],
        module_id: str,
        requirement_contract: dict[str, Any],
        design_context: dict[str, Any],
        *,
        depth: int,
    ) -> tuple[DesignState | None, list[DesignIssue]]:
        if len(state.requirement_modules.get(requirement_id, set())) > self._max_modules:
            return None, [DesignIssue("DESIGN_LIMIT_EXCEEDED", f"Requirement exceeds {self._max_modules} modules", "MODULE_DECOMPOSITION", module_id, UNRESOLVED)]
        module = state.modules[module_id]
        if module["kind"] == "DB":
            return state, []

        child_feedback: list[str] = []
        seen_failures: set[str] = set()
        last_issues: list[DesignIssue] = []
        for reopen_attempt in range(self._reopen_budget + 1):
            decomposition_schema = _module_decomposition_output_schema(module)
            context_markdown = _module_decomposition_markdown(
                requirement,
                design_context,
                state,
                module_id,
                decomposition_schema,
            )
            if child_feedback:
                context_markdown += _feedback_markdown(child_feedback, heading="上层重新拆解反馈")
            context = {"context_markdown": context_markdown}
            result = self._decision(
                phase="module_decomposition",
                unit_id=f"{requirement_id}__{_safe(module_id)}",
                schema_name="arc_module_decomposition",
                instructions=MODULE_DECOMPOSITION_INSTRUCTIONS,
                output_schema=decomposition_schema,
                context=context,
                validator=lambda value: _simple_decomposition_issues(value, module),
            )
            if result.value is None:
                issues = result.issues
                if any(issue.code in {"FLOW_SOURCE_MISSING", "PARENT_CONTRACT_MISSING", "PARENT_OUTPUT_UNREALIZED"} for issue in issues):
                    for issue in issues:
                        issue.repair_action = REOPEN_PARENT
                        issue.owner_phase = "PARENT_MODULE_DECOMPOSITION"
                return None, issues
            trial = state.clone()
            child_ids, issues = _materialize_simple_decomposition(
                trial,
                requirement_id,
                module_id,
                result.value,
            )
            if issues:
                return None, issues
            failure: list[DesignIssue] = []
            for child_id in child_ids:
                child = trial.modules[child_id]
                if child["kind"] != "FUNC" or child["owner_requirement"] != requirement_id:
                    continue
                expanded, child_issues = self._expand_module(
                    trial,
                    requirement_id,
                    requirement,
                    child_id,
                    requirement_contract,
                    design_context,
                    depth=depth + 1,
                )
                if expanded is None:
                    failure = child_issues
                    break
                trial = expanded
            if not failure:
                return trial, []
            last_issues = failure
            if not any(issue.repair_action == REOPEN_PARENT for issue in failure):
                return None, failure
            fingerprint = _hash({
                "module_id": module_id,
                "decision": result.value,
                "issues": [issue.as_dict() for issue in failure],
            })
            if fingerprint in seen_failures:
                return None, [DesignIssue("UNRESOLVED_DESIGN_DECISION", f"Repeated module decomposition produced the same child conflict for {module_id}", "MODULE_DECOMPOSITION", module_id, UNRESOLVED, context={"issues": [item.as_dict() for item in failure]})]
            seen_failures.add(fingerprint)
            child_feedback = [issue.feedback() for issue in failure]
            self._trace(f"UPSTREAM_REOPEN phase=module_decomposition unit={module_id} depth={depth} attempt={reopen_attempt + 1}/{self._reopen_budget + 1} feedback={'; '.join(child_feedback)}")
        return None, [DesignIssue(
            "UNRESOLVED_DESIGN_DECISION",
            f"Upstream repair budget was exhausted for {module_id}",
            "MODULE_DECOMPOSITION",
            module_id,
            UNRESOLVED,
            context={"issues": [item.as_dict() for item in last_issues]},
        )]


def project_design_context(schema: dict[str, Any], requirement_id: str, dependencies: dict[str, Any]) -> dict[str, Any]:
    """Project the one authoritative Stage 1 view used by prompts and validators."""

    allowed_requirements = {requirement_id} | _dependency_closure(requirement_id, dependencies)
    entities: list[dict[str, Any]] = []
    allowed_entities: set[str] = set()
    for entity in schema.get("entities", []):
        requirement_ids = set(entity.get("requirement_ids", []))
        fields = [
            copy.deepcopy(item)
            for item in entity.get("fields", [])
            if not item.get("requirement_ids")
            or set(item.get("requirement_ids", [])) & allowed_requirements
        ]
        if requirement_ids & allowed_requirements or fields:
            key = str(entity.get("key", "")).lower()
            allowed_entities.add(key)
            entities.append({
                "key": key,
                "description": entity.get("description", ""),
                "fields": [{
                    "name": item.get("name"),
                    "type": item.get("type"),
                    "nullable": item.get("nullable"),
                    "references": item.get("references"),
                    "properties": copy.deepcopy(item.get("properties", {})),
                    "origin": item.get("origin"),
                    "primary_key": bool(item.get("primary_key")),
                } for item in fields],
            })
    relationships = [
        copy.deepcopy(item)
        for item in schema.get("relationships", [])
        if str(item.get("parent", "")).lower() in allowed_entities
        and str(item.get("child", "")).lower() in allowed_entities
    ]
    constraints: list[dict[str, Any]] = []
    for raw in schema.get("constraints", []):
        fields = [str(value) for value in raw.get("fields", [])]
        referenced = {value.partition(".")[0].lower() for value in fields if "." in value}
        requirement_ids = set(raw.get("requirement_ids", []))
        if referenced and not referenced <= allowed_entities:
            continue
        if requirement_ids and not requirement_ids & allowed_requirements:
            continue
        item = copy.deepcopy(raw)
        item["id"] = str(raw.get("id") or f"constraint_{_hash(raw)[:12]}")
        constraints.append(item)
    return {
        "requirement_id": requirement_id,
        "allowed_requirement_ids": sorted(allowed_requirements),
        "entities": sorted(entities, key=lambda item: item["key"]),
        "relationships": relationships,
        "constraints": constraints,
        "allowed_entities": sorted(allowed_entities),
    }


def _model_design_context(context: dict[str, Any]) -> dict[str, Any]:
    """Project database facts that can affect the requirement contract."""

    return {
        "requirement_id": context.get("requirement_id"),
        "allowed_requirement_ids": copy.deepcopy(context.get("allowed_requirement_ids", [])),
        "allowed_entities": copy.deepcopy(context.get("allowed_entities", [])),
        "entities": copy.deepcopy(context.get("entities", [])),
        "relationships": copy.deepcopy(context.get("relationships", [])),
        "constraints": copy.deepcopy(context.get("constraints", [])),
    }


def _requirement_contract_issues(value: dict[str, Any], requirement_id: str, context: dict[str, Any]) -> list[DesignIssue]:
    issues: list[DesignIssue] = []
    if value.get("requirement_id") != requirement_id:
        issues.append(_issue("CONTRACT_ID_MISMATCH", "requirement_id must match the fixed requirement", "REQUIREMENT_CONTRACT", requirement_id))
    if not str(value.get("spec", "")).strip():
        issues.append(_issue("CONTRACT_SPEC_EMPTY", "spec must not be empty", "REQUIREMENT_CONTRACT", requirement_id))
    issues.extend(_field_issues(value.get("inputs", []), "requirement inputs", requirement_id))
    issues.extend(_field_issues(value.get("outputs", []), "requirement outputs", requirement_id))
    input_fields = _simple_field_catalog(value.get("inputs", []))
    output_fields = _simple_field_catalog(value.get("outputs", []))
    for semantic_id in set(input_fields) & set(output_fields):
        if _simple_field_type(input_fields[semantic_id]) != _simple_field_type(output_fields[semantic_id]):
            issues.append(_issue(
                "FIELD_SEMANTIC_ID_CONFLICT",
                f"Requirement output changes the type of input {semantic_id}",
                "REQUIREMENT_CONTRACT",
                requirement_id,
            ))
    database_fields = _database_field_catalog(context)
    allowed_entities = set(context["allowed_entities"])
    effect_ids: set[str] = set()
    for effect in value.get("effects", []):
        effect_id = str(effect.get("id", ""))
        if effect_id in effect_ids:
            issues.append(_issue("DUPLICATE_EFFECT", f"Duplicate effect id: {effect_id}", "REQUIREMENT_CONTRACT", requirement_id))
        effect_ids.add(effect_id)
        operation = str(effect.get("operation", ""))
        target = str(effect.get("target") or "").lower()
        if operation in DATABASE_EFFECT_OPERATIONS and target not in allowed_entities:
            issues.append(_issue("DATA_DOMAIN_OUT_OF_SCOPE", f"Effect {effect_id} references unavailable entity: {target}", "REQUIREMENT_CONTRACT", requirement_id))
        unknown = set(effect.get("fields", [])) - database_fields.get(target, set())
        database_backed = operation in DATABASE_EFFECT_OPERATIONS or (
            operation == "SESSION_WRITE" and target in allowed_entities
        )
        if database_backed and unknown:
            issues.append(_issue("UNKNOWN_DATABASE_FIELD", f"Effect {effect_id} references unknown fields: {sorted(unknown)}", "REQUIREMENT_CONTRACT", requirement_id))
    return issues


def _api_plan_issues(value: dict[str, Any], requirement_id: str, contract: dict[str, Any]) -> list[DesignIssue]:
    issues: list[DesignIssue] = []
    apis = value.get("modules", [])
    names = [str(item.get("name", "")) for item in apis]
    if len(names) != len(set(names)):
        issues.append(_issue("DUPLICATE_API", "API names must be unique within the requirement", "REQUIREMENT_API", requirement_id))
    contract_inputs = _simple_field_catalog(contract.get("inputs", []))
    contract_outputs = _simple_field_catalog(contract.get("outputs", []))
    expected_effects = {item["id"]: item for item in contract.get("effects", [])}
    exposed_inputs: set[str] = set()
    exposed_outputs: set[str] = set()
    allocated_effects: list[str] = []
    for api in apis:
        name = str(api.get("name", ""))
        issues.extend(_simple_interface_field_issues(api.get("inputs", []), f"API.{name} inputs", requirement_id))
        issues.extend(_simple_interface_field_issues(api.get("outputs", []), f"API.{name} outputs", requirement_id))
        if not str(api.get("spec", "")).strip():
            issues.append(_issue("API_SPEC_EMPTY", f"API.{name} spec must not be empty", "REQUIREMENT_API", name))
        for field_item in api.get("inputs", []):
            semantic_id = str(field_item.get("semantic_id", ""))
            expected = contract_inputs.get(semantic_id)
            if expected is None or _simple_field_type(expected) != _simple_field_type(field_item):
                issues.append(_issue("API_FIELD_OUT_OF_CONTRACT", f"API.{name} changes or invents input {semantic_id}", "REQUIREMENT_API", name))
            exposed_inputs.add(semantic_id)
        for field_item in api.get("outputs", []):
            semantic_id = str(field_item.get("semantic_id", ""))
            expected = contract_outputs.get(semantic_id)
            if expected is None or _simple_field_type(expected) != _simple_field_type(field_item):
                issues.append(_issue("API_FIELD_OUT_OF_CONTRACT", f"API.{name} changes or invents output {semantic_id}", "REQUIREMENT_API", name))
            exposed_outputs.add(semantic_id)
        for effect in api.get("effects", []):
            effect_id = str(effect.get("id", ""))
            expected = expected_effects.get(effect_id)
            allocated_effects.append(effect_id)
            if expected is None or _effect_signature(expected) != _effect_signature(effect):
                issues.append(_issue("API_EFFECT_OUT_OF_CONTRACT", f"API.{name} changes or invents effect {effect_id}", "REQUIREMENT_API", name))
    missing_inputs = set(contract_inputs) - exposed_inputs
    missing_outputs = set(contract_outputs) - exposed_outputs
    if missing_inputs:
        issues.append(_issue("API_INPUT_COVERAGE", f"Contract inputs are not exposed: {sorted(missing_inputs)}", "REQUIREMENT_API", requirement_id))
    if missing_outputs:
        issues.append(_issue("API_OUTPUT_COVERAGE", f"Contract outputs are not exposed: {sorted(missing_outputs)}", "REQUIREMENT_API", requirement_id))
    issues.extend(_allocation_issues(allocated_effects, set(expected_effects), "effect", "REQUIREMENT_API", requirement_id))
    return issues


def _simple_decomposition_issues(
    value: dict[str, Any],
    parent: dict[str, Any],
) -> list[DesignIssue]:
    """Validate only the small public module contract returned by the model."""

    issues: list[DesignIssue] = []
    parent_id = str(parent["id"])
    available = _simple_field_catalog(parent.get("inputs", []))
    parent_outputs = _simple_field_catalog(parent.get("outputs", []))
    expected_effects = {item["id"]: item for item in _module_visible_effects(parent)}
    allocated_effects: list[str] = []
    names = [str(module.get("name", "")) for module in value.get("modules", [])]
    if len(names) != len(set(names)):
        issues.append(_issue(
            "DUPLICATE_MODULE",
            "Direct child module names must be unique",
            "MODULE_DECOMPOSITION",
            parent_id,
        ))

    for index, module in enumerate(value.get("modules", []), start=1):
        label = str(module.get("name") or f"module {index}")
        kind = str(module.get("kind", ""))
        if not str(module.get("spec", "")).strip():
            issues.append(_issue(
                "MODULE_SPEC_EMPTY",
                f"Module {label} spec must not be empty",
                "MODULE_DECOMPOSITION",
                parent_id,
            ))
        inputs = module.get("inputs", [])
        outputs = module.get("outputs", [])
        issues.extend(_simple_interface_field_issues(inputs, f"{label} inputs", parent_id))
        issues.extend(_simple_interface_field_issues(outputs, f"{label} outputs", parent_id))

        for field_item in inputs:
            semantic_id = str(field_item.get("semantic_id", ""))
            source = available.get(semantic_id)
            if source is None:
                issues.append(_issue(
                    "FLOW_SOURCE_MISSING",
                    f"Module {label} input has no parent or earlier-child source: {semantic_id}",
                    "MODULE_DECOMPOSITION",
                    parent_id,
                ))
            elif _simple_field_type(source) != _simple_field_type(field_item):
                issues.append(_issue(
                    "FIELD_SEMANTIC_ID_CONFLICT",
                    f"Module {label} changes the type of {semantic_id}: "
                    f"expected {_simple_field_type(source)}, got {_simple_field_type(field_item)}",
                    "MODULE_DECOMPOSITION",
                    parent_id,
                ))

        for field_item in outputs:
            semantic_id = str(field_item.get("semantic_id", ""))
            source = available.get(semantic_id)
            if source is not None and _simple_field_type(source) != _simple_field_type(field_item):
                issues.append(_issue(
                    "FIELD_SEMANTIC_ID_CONFLICT",
                    f"Module {label} reuses {semantic_id} with a different type: "
                    f"expected {_simple_field_type(source)}, got {_simple_field_type(field_item)}",
                    "MODULE_DECOMPOSITION",
                    parent_id,
                ))
            elif source is None:
                available[semantic_id] = copy.deepcopy(field_item)

        module_effects = module.get("effects", [])
        if kind == "DB" and not module_effects:
            issues.append(_issue(
                "DB_EFFECT_REQUIRED",
                f"DB module {label} must own at least one READ, CREATE, UPDATE, or DELETE effect",
                "MODULE_DECOMPOSITION",
                parent_id,
            ))
        for effect in module_effects:
            effect_id = str(effect.get("id", ""))
            allocated_effects.append(effect_id)
            operation = str(effect.get("operation", ""))
            if kind == "DB" and operation not in DATABASE_EFFECT_OPERATIONS:
                issues.append(_issue(
                    "DB_EFFECT_INVALID",
                    f"DB module {label} cannot own {operation or 'an empty operation'}; "
                    "DB modules accept only READ, CREATE, UPDATE, or DELETE",
                    "MODULE_DECOMPOSITION",
                    parent_id,
                ))
            expected = expected_effects.get(effect_id)
            if expected is None:
                issues.append(_issue(
                    "EFFECT_OUT_OF_CONTRACT",
                    f"Module {label} declares an unknown effect: {effect_id}",
                    "MODULE_DECOMPOSITION",
                    parent_id,
                ))
                continue
            expected_signature = (
                expected.get("operation"),
                expected.get("target"),
                tuple(sorted(expected.get("fields", []))),
            )
            actual_signature = (
                effect.get("operation"),
                effect.get("target"),
                tuple(sorted(effect.get("fields", []))),
            )
            if actual_signature != expected_signature:
                issues.append(_issue(
                    "EFFECT_CONTRACT_MISMATCH",
                    f"Module {label} changes effect {effect_id}; copy it exactly from the parent",
                    "MODULE_DECOMPOSITION",
                    parent_id,
                ))

    missing_outputs = set(parent_outputs) - set(available)
    if parent.get("kind") == "API" and missing_outputs:
        issues.append(_issue(
            "PARENT_OUTPUT_UNREALIZED",
            f"Child modules do not produce parent outputs: {sorted(missing_outputs)}",
            "MODULE_DECOMPOSITION",
            parent_id,
        ))
    required_effects = (
        set(expected_effects)
        if parent.get("kind") == "API"
        else {
            effect_id
            for effect_id, effect in expected_effects.items()
            if effect.get("operation") in DATABASE_EFFECT_OPERATIONS
        }
    )
    allocated_required = [effect_id for effect_id in allocated_effects if effect_id in required_effects]
    issues.extend(_allocation_issues(
        allocated_required,
        required_effects,
        "effect",
        "MODULE_DECOMPOSITION",
        parent_id,
    ))
    repeated_effects = sorted({effect_id for effect_id in allocated_effects if allocated_effects.count(effect_id) > 1})
    if repeated_effects and not any(issue.code == "EFFECT_ALLOCATED_TWICE" for issue in issues):
        issues.append(_issue(
            "EFFECT_ALLOCATED_TWICE",
            f"effects are allocated more than once: {repeated_effects}",
            "MODULE_DECOMPOSITION",
            parent_id,
        ))
    return issues


def _simple_interface_field_issues(
    fields: list[dict[str, Any]],
    label: str,
    blame: str,
) -> list[DesignIssue]:
    issues: list[DesignIssue] = []
    seen: set[str] = set()
    for item in fields:
        semantic_id = str(item.get("semantic_id", ""))
        if semantic_id in seen:
            issues.append(_issue(
                "DUPLICATE_FIELD",
                f"{label} repeats semantic id: {semantic_id}",
                "MODULE_DECOMPOSITION",
                blame,
            ))
        seen.add(semantic_id)
    return issues


def _materialize_apis(state: DesignState, requirement_id: str, contract: dict[str, Any], decision: dict[str, Any]) -> tuple[list[str], list[DesignIssue]]:
    fields = _field_catalog([*contract.get("inputs", []), *contract.get("outputs", [])])
    effects = {item["id"]: item for item in contract.get("effects", [])}
    api_ids: list[str] = []
    for item in decision.get("modules", []):
        module_id = _qualified_module_id(requirement_id, "API", item["name"])
        if module_id in state.modules:
            return [], [_issue("MODULE_ID_CONFLICT", f"Module id already exists: {module_id}", "REQUIREMENT_API", module_id)]
        module = {
            "id": module_id,
            "kind": "API",
            "owner_requirement": requirement_id,
            "spec": item["spec"],
            "inputs": [_expand_interface_field(field_item, fields) for field_item in item.get("inputs", [])],
            "outputs": [_expand_interface_field(field_item, fields) for field_item in item.get("outputs", [])],
            "effects": [_compact_module_effect(effects[item_effect["id"]]) for item_effect in item.get("effects", [])],
            "parent_id": None,
        }
        state.modules[module_id] = module
        state.requirement_modules.setdefault(requirement_id, set()).add(module_id)
        api_ids.append(module_id)
    return api_ids, []


def _materialize_simple_decomposition(
    state: DesignState,
    requirement_id: str,
    parent_id: str,
    decision: dict[str, Any],
) -> tuple[list[str], list[DesignIssue]]:
    """Create child symbols and calls from the six-field module format."""

    parent = state.modules[parent_id]
    authoritative_effects = {item["id"]: item for item in _module_visible_effects(parent)}
    field_catalog = _field_catalog([*parent.get("inputs", []), *parent.get("outputs", [])])
    child_ids: list[str] = []

    for item in decision.get("modules", []):
        kind = str(item["kind"])
        child_inputs = [_expand_interface_field(field_item, field_catalog) for field_item in item.get("inputs", [])]
        child_outputs = [_expand_interface_field(field_item, field_catalog) for field_item in item.get("outputs", [])]
        for field_item in [*child_inputs, *child_outputs]:
            field_catalog.setdefault(field_item["semantic_id"], copy.deepcopy(field_item))
        selected_effects = [
            copy.deepcopy(authoritative_effects[effect["id"]])
            for effect in item.get("effects", [])
        ]
        child_contract = {
            "kind": kind,
            "owner_requirement": requirement_id,
            "spec": str(item.get("spec", "")).strip(),
            "inputs": child_inputs,
            "outputs": child_outputs,
            "effects": [_compact_module_effect(effect) for effect in selected_effects],
            "parent_id": parent_id,
        }
        child_id = _allocate_local_module_id(
            state,
            requirement_id,
            kind,
            str(item["name"]),
        )
        child = {
            **child_contract,
            "id": child_id,
        }
        state.modules[child_id] = child
        state.requirement_modules.setdefault(requirement_id, set()).add(child_id)

        state.invocations.append({
            "caller": parent_id,
            "callee": child_id,
        })
        child_ids.append(child_id)

    return child_ids, []


def _normalize_requirement_contract(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result["inputs"] = _normalize_fields(result.get("inputs", []))
    result["outputs"] = _normalize_fields(result.get("outputs", []))
    for effect in result.get("effects", []):
        if effect.get("target"):
            effect["target"] = str(effect["target"]).lower()
        effect["fields"] = sorted(set(effect.get("fields", [])))
    return result


def _normalize_database_backed_session_effects(
    contract: dict[str, Any],
    *,
    design_context: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Represent writes to a persisted session entity as database CREATE effects.

    SESSION_WRITE is reserved for runtime session stores that are not represented
    in Database IR. A model may still use that operation for an insertion into a
    supplied `session` entity; normalize that ambiguity before the contract is
    frozen so every persisted DB module uses the canonical CRUD vocabulary.
    """

    result = copy.deepcopy(contract)
    database_entities = {
        str(value).lower()
        for value in design_context.get("allowed_entities", [])
        if str(value).strip()
    }
    normalized: list[str] = []
    for effect in result.get("effects", []):
        operation = str(effect.get("operation", "")).upper()
        target = str(effect.get("target") or "").lower()
        if operation != "SESSION_WRITE" or target not in database_entities:
            continue
        effect["operation"] = "CREATE"
        normalized.append(str(effect.get("id", "")))
    return result, normalized


def _complete_constraint_read_effects(
    contract: dict[str, Any],
    *,
    requirement_id: str,
    design_context: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Complete READ effects implied by this requirement's uniqueness rules.

    UNIQUE constraints are enforced by the database, but requirements commonly
    need a preflight lookup to return a domain error instead of surfacing a raw
    constraint failure. The Database IR is authoritative for the entity/field
    identity, so this completion is deterministic and keeps all later module
    decomposition inside the frozen Requirement Contract.
    """

    result = copy.deepcopy(contract)
    effects = result.setdefault("effects", [])
    existing_ids = {str(effect.get("id", "")) for effect in effects}
    existing_read_fields: dict[str, set[str]] = {}
    for effect in effects:
        if str(effect.get("operation", "")).upper() != "READ":
            continue
        target = str(effect.get("target") or "").lower()
        existing_read_fields.setdefault(target, set()).update(
            str(value) for value in effect.get("fields", []) if str(value).strip()
        )
    fields_by_target: dict[str, set[str]] = {}

    for constraint in design_context.get("constraints", []):
        if str(constraint.get("type", "")).upper() not in {
            "UNIQUE",
            "COMPOSITE_UNIQUE",
        }:
            continue
        owner_ids = {
            str(value)
            for value in constraint.get("requirement_ids", [])
            if str(value).strip()
        }
        if owner_ids and requirement_id not in owner_ids:
            continue
        qualified_fields = [
            str(value).strip()
            for value in constraint.get("fields", [])
            if str(value).strip()
        ]
        targets = {value.partition(".")[0].lower() for value in qualified_fields if "." in value}
        if len(targets) != 1:
            continue
        target = next(iter(targets))
        fields = {
            value.partition(".")[2]
            for value in qualified_fields
            if value.partition(".")[0].lower() == target and value.partition(".")[2]
        }
        if fields:
            fields_by_target.setdefault(target, set()).update(fields)

    completed: list[str] = []
    for target, fields in sorted(fields_by_target.items()):
        ordered_fields = sorted(fields - existing_read_fields.get(target, set()))
        if not ordered_fields:
            continue
        base_id = _stable_effect_id("read_existing", target, ordered_fields)
        effect_id = base_id
        suffix = 2
        while effect_id in existing_ids:
            effect_id = f"{base_id[:61]}_{suffix}"
            suffix += 1
        effects.append({
            "id": effect_id,
            "operation": "READ",
            "target": target,
            "fields": ordered_fields,
        })
        existing_ids.add(effect_id)
        existing_read_fields.setdefault(target, set()).update(ordered_fields)
        completed.append(effect_id)

    effects.sort(key=lambda effect: str(effect.get("id", "")))
    return result, completed


def _stable_effect_id(prefix: str, target: str, fields: list[str]) -> str:
    parts = [
        re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
        for value in (prefix, target, *fields)
    ]
    readable = "_".join(value for value in parts if value)
    if len(readable) <= 64:
        return readable
    digest = _hash({"prefix": prefix, "target": target, "fields": fields})[:10]
    return f"{readable[:53].rstrip('_')}_{digest}"


def _field_issues(fields: Any, label: str, blame: str) -> list[DesignIssue]:
    issues: list[DesignIssue] = []
    seen: dict[str, tuple[str, str]] = {}
    for item in fields if isinstance(fields, list) else []:
        semantic_id = str(item.get("semantic_id", ""))
        signature = (str(item.get("name", "")), str(item.get("type", "")))
        if semantic_id in seen and seen[semantic_id] != signature:
            issues.append(_issue("FIELD_SEMANTIC_ID_CONFLICT", f"{label} reuses {semantic_id} with an incompatible shape", "CONTRACT", blame))
        elif semantic_id in seen:
            issues.append(_issue("DUPLICATE_FIELD", f"{label} repeats semantic id: {semantic_id}", "CONTRACT", blame))
        seen[semantic_id] = signature
    return issues


def _allocation_issues(values: list[str], expected: set[str], label: str, phase: str, blame: str) -> list[DesignIssue]:
    actual = set(values)
    issues: list[DesignIssue] = []
    if actual != expected:
        issues.append(_issue(f"{label.upper()}_ALLOCATION_MISMATCH", f"{label} allocation must cover the parent exactly: missing={sorted(expected - actual)} extra={sorted(actual - expected)}", phase, blame))
    repeated = sorted({value for value in values if values.count(value) > 1})
    if repeated:
        issues.append(_issue(f"{label.upper()}_ALLOCATED_TWICE", f"{label} obligations are allocated more than once: {repeated}", phase, blame))
    return issues


def _shape_issues(value: Any, schema: dict[str, Any], phase: str, unit_id: str) -> list[DesignIssue]:
    errors = _shape_errors(value, schema, "$")
    return [_issue("STRUCTURED_OUTPUT_INVALID", error, phase.upper(), unit_id) for error in errors]


def _provider_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return the strict-schema subset supported by OpenAI-compatible providers.

    Length and uniqueness constraints remain in the authoritative local schema and
    are checked after every response. Several compatible endpoints reject those
    JSON Schema annotation keywords before invoking the model.
    """

    unsupported = {"maxLength", "uniqueItems"}
    return {
        key: _provider_output_schema(value) if isinstance(value, dict) else [
            _provider_output_schema(item) if isinstance(item, dict) else item
            for item in value
        ] if isinstance(value, list) else value
        for key, value in schema.items()
        if key not in unsupported
    }


def _module_decomposition_output_schema(parent: dict[str, Any]) -> dict[str, Any]:
    """Specialize the decomposition form so illegal layer choices are unavailable."""

    schema = copy.deepcopy(MODULE_DECOMPOSITION_SCHEMA)
    step_properties = schema["properties"]["modules"]["items"]["properties"]
    allowed = {"FUNC"} if parent.get("kind") == "API" else {"FUNC", "DB"}
    step_properties["kind"]["enum"] = sorted(allowed)
    schema["properties"]["modules"]["minItems"] = 0
    return schema


def _shape_errors(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    if "anyOf" in schema:
        if any(not _shape_errors(value, option, path) for option in schema["anyOf"]):
            return []
        return [f"{path} does not match any allowed shape"]
    expected = schema.get("type")
    type_ok = {
        "object": isinstance(value, dict), "array": isinstance(value, list), "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool), "boolean": isinstance(value, bool),
        "null": value is None,
    }
    if expected and not type_ok.get(expected, True):
        return [f"{path} must be {expected}"]
    errors: list[str] = []
    if isinstance(value, dict) and expected == "object":
        properties = schema.get("properties", {})
        missing = set(schema.get("required", [])) - set(value)
        errors.extend(f"{path}.{key} is required" for key in sorted(missing))
        if schema.get("additionalProperties") is False:
            errors.extend(f"{path}.{key} is not allowed" for key in sorted(set(value) - set(properties)))
        for key in set(value) & set(properties):
            errors.extend(_shape_errors(value[key], properties[key], f"{path}.{key}"))
    if isinstance(value, list) and expected == "array":
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path} has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path} has too many items")
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            errors.append(f"{path} must contain unique items")
        for index, item in enumerate(value):
            errors.extend(_shape_errors(item, schema.get("items", {}), f"{path}[{index}]"))
    if isinstance(value, str):
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path} exceeds maximum length {schema['maxLength']}")
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path} must be one of {schema['enum']}")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            errors.append(f"{path} does not match {schema['pattern']}")
    return errors


def _issue(code: str, message: str, phase: str, blame: str, repair: str = REPAIR_CURRENT) -> DesignIssue:
    return DesignIssue(code, message, phase, blame, repair)


def _field_catalog(fields: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in fields:
        key = str(item.get("semantic_id", ""))
        if key and (key not in result or _field_signature(result[key]) == _field_signature(item)):
            result[key] = copy.deepcopy(item)
    return result


def _field_signature(item: dict[str, Any]) -> tuple[str, bool]:
    return str(item.get("type", "")), bool(item.get("required"))


def _normalize_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((copy.deepcopy(item) for item in fields), key=lambda item: item["semantic_id"])


def _database_field_catalog(context: dict[str, Any]) -> dict[str, set[str]]:
    return {str(entity.get("key", "")).lower(): {str(item.get("name", "")) for item in entity.get("fields", [])} for entity in context.get("entities", [])}


def _compact_module_effect(effect: dict[str, Any]) -> dict[str, Any]:
    """Project the semantic effect fields consumed by downstream passes."""

    return {
        key: copy.deepcopy(effect[key])
        for key in ("id", "operation", "target", "fields")
        if effect.get(key) is not None
    }


def _module_visible_effects(module: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the compact effects exposed by a module contract."""

    return [
        {
            "id": effect.get("id"),
            "operation": effect.get("operation"),
            "target": effect.get("target"),
            "fields": copy.deepcopy(effect.get("fields", [])),
        }
        for effect in module.get("effects", [])
        if effect.get("id")
    ]


def _simple_field_catalog(fields: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("semantic_id", "")): {
            "semantic_id": item.get("semantic_id"),
            "name": item.get("name"),
            "type": item.get("type"),
        }
        for item in fields
        if item.get("semantic_id")
    }


def _simple_field_type(field_item: dict[str, Any]) -> str:
    """Return the cross-module compatibility key for one semantic value."""

    return str(field_item.get("type", ""))


def _effect_signature(effect: dict[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    return (
        str(effect.get("operation", "")),
        str(effect.get("target") or ""),
        tuple(sorted(str(value) for value in effect.get("fields", []))),
    )


def _expand_interface_field(
    field_item: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    semantic_id = str(field_item["semantic_id"])
    existing = catalog.get(semantic_id)
    if existing is not None:
        return {
            "semantic_id": semantic_id,
            "name": str(existing["name"]),
            "type": str(existing["type"]),
            "required": bool(existing.get("required", True)),
        }
    return {
        "semantic_id": semantic_id,
        "name": str(field_item["name"]),
        "type": str(field_item["type"]),
        "required": bool(field_item.get("required", True)),
    }


def _allocate_local_module_id(state: DesignState, requirement_id: str, kind: str, name: str) -> str:
    base = _qualified_module_id(requirement_id, kind, name)
    if base not in state.modules:
        return base
    index = 2
    while f"{base}{index}" in state.modules:
        index += 1
    return f"{base}{index}"


def _qualified_module_id(requirement_id: str, kind: str, name: str) -> str:
    return f"{requirement_id}::{kind}.{name}"


def _module_decomposition_markdown(
    requirement: dict[str, Any],
    design_context: dict[str, Any],
    state: DesignState,
    module_id: str,
    output_schema: dict[str, Any],
) -> str:
    """Render only the decision-local context not already encoded by the schema."""

    module = state.modules[module_id]
    allowed_kinds = output_schema["properties"]["modules"]["items"]["properties"]["kind"]["enum"]
    lines = [
        "# Current decomposition decision",
        "",
        "## Requirement",
        "",
        f"- ID: `{_md(requirement.get('requirement_id', ''))}`",
        f"- Name: {_md(requirement.get('name', ''))}",
        f"- Summary: {_md(requirement.get('description', ''))}",
        "",
        "## Parent module",
        "",
        f"- ID: `{_md(module.get('id'))}`",
        f"- Kind: `{_md(module.get('kind'))}`",
        f"- Responsibility: {_md(module.get('spec'))}",
        f"- Allowed direct child kinds: {_inline_list(allowed_kinds)}",
        "",
        "### Available inputs",
        "",
        *_field_table(module.get("inputs", [])),
        "",
        "### Required outputs",
        "",
        *_field_table(module.get("outputs", [])),
        "",
        "### Effects that children may own",
        "",
        *_effect_table(_module_visible_effects(module)),
        "",
        "## Relevant database slice",
        "",
    ]
    entities = design_context.get("entities", [])
    if not entities:
        lines.append("- None")
    for entity in entities:
        fields = ", ".join(
            f"{field.get('name')}:{field.get('type')}"
            for field in entity.get("fields", [])
        )
        lines.append(f"- `{_md(entity.get('key'))}`: {_md(fields or 'no fields')}")
    return "\n".join(lines).strip()


def _field_table(fields: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Semantic ID | Name | Type | Required | Description |",
        "|---|---|---|---:|---|",
    ]
    if not fields:
        lines.append("| — | — | — | — | — |")
    for item in fields:
        lines.append(
            f"| {_md(item.get('semantic_id'))} | {_md(item.get('name'))} | {_md(item.get('type'))} "
            f"| {_yes_no(item.get('required'))} | {_md(item.get('description'))} |"
        )
    return lines


def _effect_table(effects: list[dict[str, Any]]) -> list[str]:
    lines = ["| ID | Operation | Target | Fields |", "|---|---|---|---|"]
    if not effects:
        lines.append("| — | — | — | — |")
    for item in effects:
        lines.append(
            f"| {_md(item.get('id'))} | {_md(item.get('operation'))} | {_md(item.get('target'))} "
            f"| {_inline_list(item.get('fields', []))} |"
        )
    return lines


def _feedback_markdown(feedback: list[str], *, heading: str = "校验反馈（请修复后重新输出）") -> str:
    return "\n\n## " + heading + "\n\n" + "\n".join(f"- {_md(item)}" for item in feedback)


def _inline_list(values: Iterable[Any]) -> str:
    rendered = [f"`{_md(value)}`" for value in values]
    return ", ".join(rendered) if rendered else "—"


def _yes_no(value: Any) -> str:
    return "yes" if bool(value) else "no"


def _md(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _requirement_contract_template(requirement_id: str) -> dict[str, Any]:
    """Return the compact fixed shape shown to the contract model."""

    return {
        "requirement_id": requirement_id,
        "spec": "",
        "inputs": [],
        "outputs": [],
        "effects": [],
    }


def _api_template() -> dict[str, Any]:
    """Return a minimal API decision example without embedding a large schema."""

    return {
        "modules": [{
            "kind": "API",
            "name": "OperationName",
            "spec": "Expose one requirement operation.",
            "inputs": [],
            "outputs": [],
            "effects": [],
        }],
    }


def _requirement_context(nodes: dict[str, Any], requirement_id: str) -> dict[str, Any]:
    raw = copy.deepcopy(nodes.get(requirement_id, {}))
    return {**raw, "requirement_id": requirement_id}


def _waves(requirement_ir: dict[str, Any], dependency_graph: dict[str, Any]) -> list[list[str]]:
    atomic_ids = {
        str(item)
        for item in requirement_ir.get("atomic_units", [])
        if str(item).strip()
    }
    waves = dependency_graph.get("atomic_implementation_waves")
    if not isinstance(waves, list) or not waves:
        waves = dependency_graph.get("implementation_waves")
    if isinstance(waves, list) and waves:
        return [
            [str(item) for item in wave if str(item) in atomic_ids]
            for wave in waves
            if isinstance(wave, list)
            and any(str(item) in atomic_ids for item in wave)
        ]
    return [[item] for item in sorted(atomic_ids)]


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


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() not in {"0", "false", "no", "off"}


def design_traceability(design_ir: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    """Project compact requirement-to-symbol links from the authoritative Design IR."""

    modules_by_requirement: dict[str, list[dict[str, Any]]] = {}
    for module in design_ir.get("modules", []):
        if not isinstance(module, dict):
            continue
        requirement_id = str(module.get("owner_requirement") or "").strip()
        module_id = str(module.get("id") or "").strip()
        if requirement_id and module_id:
            modules_by_requirement.setdefault(requirement_id, []).append(module)
    result: dict[str, dict[str, list[str]]] = {}
    for requirement in design_ir.get("requirements", []):
        if not isinstance(requirement, dict):
            continue
        requirement_id = str(requirement.get("id") or "").strip()
        if not requirement_id:
            continue
        modules = modules_by_requirement.get(requirement_id, [])
        result[requirement_id] = {
            "api_ids": sorted({str(value) for value in requirement.get("api_ids", []) if str(value).strip()}),
            "module_ids": sorted({str(module["id"]) for module in modules}),
        }
    return result


__all__ = [
    "API_DECOMPOSITION_INSTRUCTIONS", "API_DECOMPOSITION_SCHEMA", "MODULE_DECOMPOSITION_INSTRUCTIONS",
    "MODULE_DECOMPOSITION_SCHEMA", "REQUIREMENT_CONTRACT_INSTRUCTIONS", "REQUIREMENT_CONTRACT_SCHEMA",
    "DesignIssue", "DesignPass", "DesignPassResult", "DesignState", "design_traceability",
    "project_design_context",
]
