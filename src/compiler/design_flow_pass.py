from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from arcbench_agent_runtime.jsonio import read_json, write_json_atomic
from core.logging import SynchronousLog

from .design_validator import DesignValidator, assign_module_files
from .model_client import StructuredModel, describe_model_error


CONTRACT_PROMPT_VERSION = "requirement-contract-v1"
MODULE_TREE_PROMPT_VERSION = "module-call-tree-v1"
CALL_BINDING_PROMPT_VERSION = "call-binding-v3"
RETURN_BINDING_PROMPT_VERSION = "return-binding-v2"
MODULE_KINDS = {"PAGE", "API", "FUNCTION", "REPOSITORY"}
FIELD_TYPES = {"string", "integer", "number", "boolean", "date", "datetime", "json"}
SYMBOL_PATTERN_TEXT = r"^[a-z][a-z0-9_]*$"
MODULE_PATTERN_TEXT = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
SYMBOL_PATTERN = re.compile(SYMBOL_PATTERN_TEXT)
MODULE_PATTERN = re.compile(MODULE_PATTERN_TEXT)


FIELD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "type", "required", "meaning", "sensitive"],
    "properties": {
        "name": {"type": "string", "pattern": SYMBOL_PATTERN_TEXT},
        "type": {"type": "string", "enum": sorted(FIELD_TYPES)},
        "required": {"type": "boolean"},
        "meaning": {"type": "string"},
        "sensitive": {"type": "boolean"},
    },
}

ACCESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "entity", "fields", "meaning"],
    "properties": {
        "id": {"type": "string", "pattern": SYMBOL_PATTERN_TEXT},
        "entity": {"type": "string", "pattern": SYMBOL_PATTERN_TEXT},
        "fields": {"type": "array", "items": {"type": "string", "pattern": SYMBOL_PATTERN_TEXT}},
        "meaning": {"type": "string"},
    },
}

EFFECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "operation", "entity", "fields", "condition", "meaning"],
    "properties": {
        "id": {"type": "string", "pattern": SYMBOL_PATTERN_TEXT},
        "operation": {"type": "string", "enum": ["CREATE", "UPDATE", "DELETE"]},
        "entity": {"type": "string", "pattern": SYMBOL_PATTERN_TEXT},
        "fields": {"type": "array", "items": {"type": "string", "pattern": SYMBOL_PATTERN_TEXT}},
        "condition": {"type": "string"},
        "meaning": {"type": "string"},
    },
}

CONTRACT_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["requirement_id", "inputs", "outputs", "accesses", "effects", "forbidden_effects"],
    "properties": {
        "requirement_id": {"type": "string"},
        "inputs": {"type": "array", "items": FIELD_SCHEMA},
        "outputs": {"type": "array", "items": FIELD_SCHEMA},
        "accesses": {"type": "array", "items": ACCESS_SCHEMA},
        "effects": {"type": "array", "items": EFFECT_SCHEMA},
        "forbidden_effects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["condition", "effect_ids"],
                "properties": {
                    "condition": {"type": "string"},
                    "effect_ids": {"type": "array", "items": {"type": "string", "pattern": SYMBOL_PATTERN_TEXT}},
                },
            },
        },
    },
}


def _items_schema(item_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {"items": {"type": "array", "minItems": 1, "maxItems": 1, "items": item_schema}},
    }


REQUIREMENT_CONTRACT_SCHEMA = _items_schema(CONTRACT_ITEM_SCHEMA)

MODULE_HEADER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "kind", "route", "purpose", "access_ids", "effect_ids"],
    "properties": {
        "id": {"type": "string", "pattern": MODULE_PATTERN_TEXT},
        "kind": {"type": "string", "enum": sorted(MODULE_KINDS)},
        "route": {"type": "string"},
        "purpose": {"type": "string"},
        "access_ids": {"type": "array", "items": {"type": "string", "pattern": SYMBOL_PATTERN_TEXT}},
        "effect_ids": {"type": "array", "items": {"type": "string", "pattern": SYMBOL_PATTERN_TEXT}},
    },
}

INVOCATION_HEADER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "parent_id", "order", "caller", "callee", "condition"],
    "properties": {
        "id": {"type": "string", "pattern": SYMBOL_PATTERN_TEXT},
        "parent_id": {"type": ["string", "null"]},
        "order": {"type": "integer", "minimum": 0},
        "caller": {"type": "string", "pattern": MODULE_PATTERN_TEXT},
        "callee": {"type": "string", "pattern": MODULE_PATTERN_TEXT},
        "condition": {"type": "string"},
    },
}

MODULE_CALL_TREE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["requirement_id", "modules", "invocations", "entrypoint", "outcomes"],
    "properties": {
        "requirement_id": {"type": "string"},
        "modules": {"type": "array", "minItems": 1, "items": MODULE_HEADER_SCHEMA},
        "invocations": {"type": "array", "minItems": 1, "items": INVOCATION_HEADER_SCHEMA},
        "entrypoint": {"type": "string", "pattern": MODULE_PATTERN_TEXT},
        "outcomes": {"type": "array", "minItems": 1, "items": {"type": "string", "pattern": MODULE_PATTERN_TEXT}},
    },
}

MODULE_CALL_TREE_SCHEMA = _items_schema(MODULE_CALL_TREE_ITEM_SCHEMA)

LOCAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "type", "meaning", "sensitive", "operation", "inputs"],
    "properties": {
        "name": {"type": "string", "pattern": SYMBOL_PATTERN_TEXT},
        "type": {"type": "string", "enum": sorted(FIELD_TYPES)},
        "meaning": {"type": "string"},
        "sensitive": {"type": "boolean"},
        "operation": {"type": "string"},
        "inputs": {"type": "array", "items": {"type": "string"}},
    },
}

CALL_ARGUMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source", "target"],
    "properties": {"source": {"type": "string"}, "target": FIELD_SCHEMA},
}

DB_MAPPING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source", "target"],
    "properties": {"source": {"type": "string"}, "target": {"type": "string"}},
}

DB_OPERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["responsibility_id", "type", "entity", "mapping", "produces"],
    "properties": {
        "responsibility_id": {"type": "string", "pattern": SYMBOL_PATTERN_TEXT},
        "type": {"type": "string", "enum": ["READ_DB", "WRITE_DB"]},
        "entity": {"type": "string", "pattern": SYMBOL_PATTERN_TEXT},
        "mapping": {"type": "array", "items": DB_MAPPING_SCHEMA},
        "produces": {"type": "array", "items": FIELD_SCHEMA},
    },
}

CALL_BINDING_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "requirement_id",
        "invocation_id",
        "caller_locals",
        "arguments",
        "locals",
        "database_operations",
    ],
    "properties": {
        "requirement_id": {"type": "string"},
        "invocation_id": {"type": "string", "pattern": SYMBOL_PATTERN_TEXT},
        "caller_locals": {"type": "array", "items": LOCAL_SCHEMA},
        "arguments": {"type": "array", "items": CALL_ARGUMENT_SCHEMA},
        "locals": {"type": "array", "items": LOCAL_SCHEMA},
        "database_operations": {"type": "array", "items": DB_OPERATION_SCHEMA},
    },
}

CALL_BINDING_SCHEMA = _items_schema(CALL_BINDING_ITEM_SCHEMA)

RETURN_VALUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source", "output", "target"],
    "properties": {"source": {"type": "string"}, "output": FIELD_SCHEMA, "target": {"type": "string"}},
}

RETURN_BINDING_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["requirement_id", "invocation_id", "locals", "values"],
    "properties": {
        "requirement_id": {"type": "string"},
        "invocation_id": {"type": "string", "pattern": SYMBOL_PATTERN_TEXT},
        "locals": {"type": "array", "items": LOCAL_SCHEMA},
        "values": {"type": "array", "items": RETURN_VALUE_SCHEMA},
    },
}

RETURN_BINDING_SCHEMA = _items_schema(RETURN_BINDING_ITEM_SCHEMA)


REQUIREMENT_CONTRACT_INSTRUCTIONS = """You are the REQUIREMENT_CONTRACT pass of a requirement compiler.
Analyze exactly one atomic requirement as a black box. Return its boundary inputs and outputs, required database read
accesses, database write effects, and effects forbidden on failure. Do not design modules, calls, local variables,
algorithms, or internal dataflow. Use only entities and fields in database. Each field needs a concise meaning.
Plaintext secrets are sensitive and must not be described as persisted outputs. Return exactly one item.
"""

MODULE_CALL_TREE_INSTRUCTIONS = """You are the MODULE_CALL_TREE pass of a requirement compiler.
For exactly one requirement contract, jointly design a small module responsibility plan and a compact ordered
invocation tree. Use only PAGE, API, FUNCTION, and REPOSITORY. PAGE calls API; API calls FUNCTION; FUNCTION calls
FUNCTION or REPOSITORY. Assign every contract access_id and effect_id to exactly one newly owned REPOSITORY module.
Known modules may be reused in invocations but must not be redefined. Module ids contain at least two snake_case
segments. PAGE/API routes must be concrete; FUNCTION/REPOSITORY routes are empty. Each invocation contains only
caller, callee, parent_id, sibling order, and condition. Do not emit interfaces, fields, mappings, locals, database
operations, CALL events, RETURN events, files, tests, or implementation details.
"""

CALL_BINDING_INSTRUCTIONS = """You are the CALL_BINDING pass of a requirement compiler.
Bind exactly one invocation CALL. First declare only the caller_locals computed immediately before this call; they may
use caller_available or earlier caller_locals. Then bind arguments from that resulting caller state. Each target
declares the minimum callee input field needed by its responsibility. Same meaning must keep the same scalar type.
locals are callee entry locals computed from callee inputs or earlier callee locals. Every local must list all input
references. Only a REPOSITORY may emit database_operations. Emit exactly one database operation for each responsibility
assigned to this invocation's callee, identify it by responsibility_id, and stay within database_contract. READ_DB mappings flow
from db.<entity>.<field> to <callee>.local.<field>; WRITE_DB mappings flow from <callee>.input/local.<field> to
db.<entity>.<field>. Database produces become callee locals. Do not emit RETURN or another invocation.
"""

RETURN_BINDING_INSTRUCTIONS = """You are the RETURN_BINDING pass of a requirement compiler.
Bind exactly one invocation RETURN after all callee children have completed. First declare only final callee locals
computed at this point from callee_available or earlier final locals. Then select RETURN sources from that resulting
callee state. Each selected value declares one callee output field. Targets must be caller.local fields. Prefer values
needed by later sibling calls or the requirement outputs. Module output returns upstream and is never a downstream
argument channel. Do not emit CALL, database operations, or another invocation.
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


class _FlowState:
    """Accumulate interfaces from accepted flow facts; never ask a second model for them."""

    def __init__(self, database_schema: dict[str, Any]) -> None:
        self.modules: dict[str, dict[str, Any]] = {}
        self.links: list[dict[str, Any]] = []
        self._database = {
            str(entity.get("key")): entity
            for entity in database_schema.get("entities", [])
            if isinstance(entity, dict) and entity.get("key")
        }

    def headers(self) -> list[dict[str, Any]]:
        return [
            {key: module[key] for key in ("id", "kind", "route", "purpose", "owner")}
            for module in sorted(self.modules.values(), key=lambda item: item["id"])
        ]

    def register_tree(self, node_id: str, tree: dict[str, Any], contract: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        accesses = {item["id"]: item for item in contract.get("accesses", [])}
        effects = {item["id"]: item for item in contract.get("effects", [])}
        for header in tree.get("modules", []):
            module_id = str(header.get("id", ""))
            if module_id in self.modules:
                errors.append(f"{node_id}: module call tree repeats known module: {module_id}")
                continue
            reads = sorted({accesses[key]["entity"] for key in header.get("access_ids", []) if key in accesses})
            writes = sorted({effects[key]["entity"] for key in header.get("effect_ids", []) if key in effects})
            self.modules[module_id] = {
                **copy.deepcopy(header),
                "owner": node_id,
                "input": [],
                "output": [],
                "local": [],
                "errors": [],
                "reads": reads,
                "writes": writes,
                "generates": [],
                "realized_access_ids": [],
                "realized_effect_ids": [],
                "_access_contracts": {
                    key: copy.deepcopy(accesses[key])
                    for key in header.get("access_ids", [])
                    if key in accesses
                },
                "_effect_contracts": {
                    key: copy.deepcopy(effects[key])
                    for key in header.get("effect_ids", [])
                    if key in effects
                },
            }
        return errors

    def seed_entrypoint(self, entrypoint: str, contract: dict[str, Any]) -> list[str]:
        module = self.modules.get(entrypoint)
        if module is None:
            return [f"Unknown entrypoint module: {entrypoint}"]
        errors: list[str] = []
        for field in contract.get("inputs", []):
            _merge_field(module, "input", field, errors)
        return errors

    def available(self, module_id: str) -> list[dict[str, Any]]:
        module = self.modules.get(module_id, {})
        result: list[dict[str, Any]] = []
        for scope in ("input", "local"):
            for field in module.get(scope, []):
                result.append({"ref": f"{module_id}.{scope}.{field['name']}", "scope": scope, **field})
        return result

    def validate_call(self, node_id: str, invocation: dict[str, Any], item: dict[str, Any]) -> list[str]:
        errors = _matching_binding(node_id, invocation, item)
        caller = str(invocation.get("caller", ""))
        callee = str(invocation.get("callee", ""))
        if caller not in self.modules or callee not in self.modules:
            errors.append(f"{node_id}: unresolved CALL endpoint: {caller} -> {callee}")
            return errors
        caller_refs = {field["ref"]: field for field in self.available(caller)}
        errors.extend(
            _validate_local_declarations(node_id, caller, item.get("caller_locals", []), caller_refs)
        )
        target_names: set[str] = set()
        for argument in item.get("arguments", []):
            source = str(argument.get("source", ""))
            target = argument.get("target", {})
            name = str(target.get("name", "")) if isinstance(target, dict) else ""
            if source not in caller_refs:
                errors.append(f"{node_id}: CALL source is not available: {source}")
            elif isinstance(target, dict) and caller_refs[source].get("type") != target.get("type"):
                errors.append(f"{node_id}: CALL type mismatch: {source} -> {callee}.input.{name}")
            if name in target_names:
                errors.append(f"{node_id}: duplicate CALL target: {callee}.input.{name}")
            target_names.add(name)
        callee_refs = {
            f"{callee}.input.{argument['target']['name']}": argument["target"]
            for argument in item.get("arguments", [])
        }
        errors.extend(_validate_local_declarations(node_id, callee, item.get("locals", []), callee_refs))
        callee_kind = self.modules[callee].get("kind")
        operations = item.get("database_operations", [])
        if callee_kind != "REPOSITORY" and operations:
            errors.append(f"{node_id}: only REPOSITORY may emit database operations: {callee}")
        allowed_reads = set(self.modules[callee].get("reads", []))
        allowed_writes = set(self.modules[callee].get("writes", []))
        seen_responsibilities: set[str] = set()
        for operation in operations:
            entity = str(operation.get("entity", ""))
            op_type = operation.get("type")
            responsibility_id = str(operation.get("responsibility_id", ""))
            contracts_key = "_access_contracts" if op_type == "READ_DB" else "_effect_contracts"
            realized_key = "realized_access_ids" if op_type == "READ_DB" else "realized_effect_ids"
            responsibility = self.modules[callee].get(contracts_key, {}).get(responsibility_id)
            allowed = allowed_reads if op_type == "READ_DB" else allowed_writes
            if entity not in self._database or entity not in allowed:
                errors.append(f"{node_id}: unauthorized {op_type}: {callee} -> {entity}")
            if responsibility is None:
                errors.append(
                    f"{node_id}: {op_type} does not match an assigned responsibility: "
                    f"{callee} -> {responsibility_id}"
                )
                continue
            if responsibility.get("entity") != entity:
                errors.append(
                    f"{node_id}: database responsibility entity mismatch: {responsibility_id} -> {entity}"
                )
            if responsibility_id in seen_responsibilities or responsibility_id in self.modules[callee].get(realized_key, []):
                errors.append(f"{node_id}: duplicate database responsibility: {responsibility_id}")
            seen_responsibilities.add(responsibility_id)
            errors.extend(
                self._validate_database_mapping(
                    node_id,
                    callee,
                    operation,
                    responsibility,
                    callee_refs,
                )
            )
            for produced in operation.get("produces", []):
                callee_refs[f"{callee}.local.{produced.get('name', '')}"] = produced
        return errors

    def _validate_database_mapping(
        self,
        node_id: str,
        module_id: str,
        operation: dict[str, Any],
        responsibility: dict[str, Any],
        available: dict[str, dict[str, Any]],
    ) -> list[str]:
        errors: list[str] = []
        entity_name = str(operation.get("entity", ""))
        entity = self._database.get(entity_name, {})
        database_fields = {
            str(field.get("name")): field
            for field in entity.get("fields", [])
            if field.get("name")
        }
        database_fields.update({
            str(relation.get("name")): {"type": "string"}
            for relation in entity.get("relations", [])
            if relation.get("name")
        })
        allowed_fields = set(responsibility.get("fields", []))
        produced_names = {
            str(field.get("name"))
            for field in operation.get("produces", [])
            if isinstance(field, dict)
        }
        for mapping in operation.get("mapping", []):
            source = str(mapping.get("source", ""))
            target = str(mapping.get("target", ""))
            if operation.get("type") == "READ_DB":
                database_name = _database_field_name(source, entity_name)
                target_name = _qualified_local_name(target, module_id)
                if database_name not in database_fields or database_name not in allowed_fields:
                    errors.append(f"{node_id}: READ_DB source is outside its contract: {source}")
                if target_name is None or target_name not in produced_names:
                    errors.append(f"{node_id}: READ_DB target is not a produced local: {target}")
            else:
                database_name = _database_field_name(target, entity_name)
                if source not in available:
                    errors.append(f"{node_id}: WRITE_DB source is not available: {source}")
                if database_name not in database_fields or database_name not in allowed_fields:
                    errors.append(f"{node_id}: WRITE_DB target is outside its contract: {target}")
                elif source in available and not _types_assignable(
                    str(available[source].get("type", "")),
                    str(database_fields[database_name].get("type", "")),
                ):
                    errors.append(f"{node_id}: WRITE_DB type mismatch: {source} -> {target}")
        return errors

    def apply_call(self, node_id: str, invocation: dict[str, Any], item: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        caller = invocation["caller"]
        callee = invocation["callee"]
        callee_module = self.modules[callee]
        _apply_local_declarations(self.modules[caller], item.get("caller_locals", []), errors)
        mapping: list[dict[str, str]] = []
        for argument in item.get("arguments", []):
            target = argument["target"]
            _merge_field(callee_module, "input", target, errors)
            mapping.append({"source": argument["source"], "target": f"{callee}.input.{target['name']}"})
        self.links.append({
            "type": "CALL",
            "from": caller,
            "to": callee,
            "mapping": mapping,
            "sources": [node_id],
            "invocation_id": invocation["id"],
            "condition": invocation.get("condition", ""),
        })

        _apply_local_declarations(callee_module, item.get("locals", []), errors)

        for operation in item.get("database_operations", []):
            op_type = operation["type"]
            entity = operation["entity"]
            realized_key = "realized_access_ids" if op_type == "READ_DB" else "realized_effect_ids"
            callee_module[realized_key].append(operation["responsibility_id"])
            for produced in operation.get("produces", []):
                _merge_field(callee_module, "local", produced, errors, extra={"operation": op_type, "inputs": []})
            if op_type == "WRITE_DB":
                for produced in operation.get("produces", []):
                    generated = f"{entity}.{produced['name']}"
                    if generated not in callee_module["generates"]:
                        callee_module["generates"].append(generated)
            direction = (f"db.{entity}", callee) if op_type == "READ_DB" else (callee, f"db.{entity}")
            self.links.append({
                "type": op_type,
                "from": direction[0],
                "to": direction[1],
                "mapping": copy.deepcopy(operation.get("mapping", [])),
                "sources": [node_id],
                "invocation_id": invocation["id"],
                "responsibility_id": operation["responsibility_id"],
            })
        return errors

    def validate_return(self, node_id: str, invocation: dict[str, Any], item: dict[str, Any]) -> list[str]:
        errors = _matching_binding(node_id, invocation, item)
        caller = str(invocation.get("caller", ""))
        callee = str(invocation.get("callee", ""))
        available = {field["ref"]: field for field in self.available(callee)}
        errors.extend(_validate_local_declarations(node_id, callee, item.get("locals", []), available))
        targets: set[str] = set()
        for value in item.get("values", []):
            source = str(value.get("source", ""))
            output = value.get("output", {})
            target = str(value.get("target", ""))
            if source not in available:
                errors.append(f"{node_id}: RETURN source is not available: {source}")
            elif isinstance(output, dict) and available[source].get("type") != output.get("type"):
                errors.append(f"{node_id}: RETURN type mismatch: {source}")
            if not _local_ref(target, caller):
                errors.append(f"{node_id}: RETURN target must be caller.local: {target}")
            if target in targets:
                errors.append(f"{node_id}: duplicate RETURN target: {target}")
            targets.add(target)
        return errors

    def apply_return(self, node_id: str, invocation: dict[str, Any], item: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        caller = invocation["caller"]
        callee = invocation["callee"]
        _apply_local_declarations(self.modules[callee], item.get("locals", []), errors)
        mapping: list[dict[str, str]] = []
        for value in item.get("values", []):
            output = value["output"]
            _merge_field(self.modules[callee], "output", output, errors)
            target_name = value["target"].removeprefix(f"{caller}.local.")
            _merge_field(
                self.modules[caller],
                "local",
                output,
                errors,
                name=target_name,
                extra={"operation": "CHILD_RETURN", "inputs": [f"{callee}.output.{output['name']}"]},
            )
            mapping.append({"source": f"{callee}.output.{output['name']}", "target": value["target"]})
        self.links.append({
            "type": "RETURN",
            "from": callee,
            "to": caller,
            "mapping": mapping,
            "sources": [node_id],
            "invocation_id": invocation["id"],
        })
        return errors

    def finalize_entrypoint(self, node_id: str, entrypoint: str, contract: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        module = self.modules[entrypoint]
        locals_by_name = {field["name"]: field for field in module.get("local", [])}
        for output in contract.get("outputs", []):
            local = locals_by_name.get(output["name"])
            if local is None:
                errors.append(f"{node_id}: requirement output is not available at entrypoint: {output['name']}")
                continue
            if local.get("type") != output.get("type"):
                errors.append(f"{node_id}: requirement output type mismatch: {output['name']}")
            _merge_field(module, "output", output, errors)
        return errors


class DesignPass:
    """Compile contracts, responsibility trees, and flow-derived module interfaces."""

    def __init__(self, model: StructuredModel, artifact_root: Path) -> None:
        self._model = model
        self._artifact_root = artifact_root.expanduser().resolve()
        self._log = SynchronousLog("DesignPass", workspace_root=self._artifact_root.parents[1])
        self._retry_count = _bounded_env_int("ARC_STRUCTURED_OUTPUT_RETRY_COUNT", 2, 0, 10)
        self._retry_backoff = _bounded_env_float("ARC_MODEL_RETRY_BACKOFF_SECONDS", 1.0, 0.0, 30.0)
        self._trace_enabled = _enabled_env_flag("ARC_DESIGN_TRACE", default=True)
        self._trace_payloads = _enabled_env_flag("ARC_DESIGN_TRACE_PAYLOADS", default=False)
        self._model_trace_files = _enabled_env_flag("ARC_MODEL_TRACE_FILES", default=True)
        self._model_trace_run_id = str(time.time_ns())

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
        contracts: dict[str, dict[str, Any]] = {}
        trees: dict[str, dict[str, Any]] = {}
        flow = _FlowState(database_schema)

        for wave_index, wave in enumerate(waves, start=1):
            self._trace(f"REQUIREMENT_CONTRACT_WAVE wave={wave_index} requirements={','.join(wave)}")
            for node_id in wave:
                dependency_contracts = [
                    _contract_summary(contracts[dependency])
                    for dependency in sorted(_dependency_closure(node_id, dependencies))
                    if dependency in contracts
                ]
                items = self._run_unit(
                    node_id=node_id,
                    phase="requirement_contracts",
                    prompt_version=CONTRACT_PROMPT_VERSION,
                    schema_name="arc_requirement_contract",
                    instructions=REQUIREMENT_CONTRACT_INSTRUCTIONS,
                    output_schema=REQUIREMENT_CONTRACT_SCHEMA,
                    context={
                        "requirement": _requirement_context(nodes, node_id, dependencies),
                        "database": _database_slice(database_schema, [node_id], dependencies),
                        "dependency_contracts": dependency_contracts,
                    },
                    validate_item=_contract_validator(database_schema),
                    resume=resume,
                    errors=errors,
                    cache_paths=cache_paths,
                )
                if items is None:
                    states[node_id] = "FAILED"
                    return DesignPassResult(_empty_design(flow, contracts, trees), states, errors, cache_paths)
                contracts[node_id] = items[0]
                states[node_id] = "CONTRACTED"

        for wave_index, wave in enumerate(waves, start=1):
            self._trace(f"MODULE_CALL_TREE_WAVE wave={wave_index} requirements={','.join(wave)}")
            for node_id in wave:
                related_known = {
                    module["id"]
                    for module in flow.headers()
                    if module["owner"] in _dependency_closure(node_id, dependencies)
                }
                items = self._run_unit(
                    node_id=node_id,
                    phase="module_call_trees",
                    prompt_version=MODULE_TREE_PROMPT_VERSION,
                    schema_name="arc_module_call_tree",
                    instructions=MODULE_CALL_TREE_INSTRUCTIONS,
                    output_schema=MODULE_CALL_TREE_SCHEMA,
                    context={
                        "contract": contracts[node_id],
                        "known_modules": flow.headers(),
                        "allowed_dependency_modules": sorted(related_known),
                        "database": _database_slice(database_schema, [node_id], dependencies),
                        "requirement": _requirement_context(nodes, node_id, dependencies),
                    },
                    validate_item=_tree_validator(flow.headers(), contracts[node_id], dependencies, node_id),
                    resume=resume,
                    errors=errors,
                    cache_paths=cache_paths,
                )
                if items is None:
                    states[node_id] = "FAILED"
                    return DesignPassResult(_empty_design(flow, contracts, trees), states, errors, cache_paths)
                tree = items[0]
                tree["modules"] = sorted(tree["modules"], key=_module_sort_key)
                tree["invocations"] = _sorted_invocations(tree["invocations"])
                errors.extend(flow.register_tree(node_id, tree, contracts[node_id]))
                if errors:
                    states[node_id] = "FAILED"
                    return DesignPassResult(_empty_design(flow, contracts, trees), states, errors, cache_paths)
                trees[node_id] = tree
                states[node_id] = "TREE_PLANNED"

        for wave_index, wave in enumerate(waves, start=1):
            self._trace(f"FLOW_BINDING_WAVE wave={wave_index} requirements={','.join(wave)}")
            for node_id in wave:
                tree = trees[node_id]
                errors.extend(flow.seed_entrypoint(tree["entrypoint"], contracts[node_id]))
                if errors:
                    states[node_id] = "FAILED"
                    break
                if not self._bind_tree(
                    node_id,
                    tree,
                    contracts[node_id],
                    database_schema,
                    flow,
                    resume,
                    errors,
                    cache_paths,
                ):
                    states[node_id] = "FAILED"
                    break
                errors.extend(flow.finalize_entrypoint(node_id, tree["entrypoint"], contracts[node_id]))
                if errors:
                    states[node_id] = "FAILED"
                    break
                states[node_id] = "FLOW_BOUND"
            if errors:
                break

        if errors:
            return DesignPassResult(_empty_design(flow, contracts, trees), states, errors, cache_paths)

        design = _assemble_design(flow, contracts, trees, waves, database_schema, dependencies)
        validation_errors = DesignValidator().validate(design, requirement_ir, database_schema, dependencies)
        errors.extend(validation_errors)
        design["status"] = "RESOLVED" if not errors else "PROPOSED"
        if errors:
            self._trace("DESIGN_VALIDATION_ERROR " + "; ".join(errors))
            for node_id in requirement_ir.get("atomic_units", []):
                states[node_id] = "FAILED"
        else:
            states.update({node_id: "DESIGNED" for node_id in requirement_ir.get("atomic_units", [])})
            self._trace(f"DESIGN_VALIDATED modules={len(design['modules'])} links={len(design['links'])}")
        return DesignPassResult(design, states, errors, cache_paths)

    def _bind_tree(
        self,
        node_id: str,
        tree: dict[str, Any],
        contract: dict[str, Any],
        database_schema: dict[str, Any],
        flow: _FlowState,
        resume: bool,
        errors: list[str],
        cache_paths: dict[str, str],
    ) -> bool:
        by_parent: dict[str | None, list[dict[str, Any]]] = {}
        for invocation in tree["invocations"]:
            by_parent.setdefault(invocation.get("parent_id"), []).append(invocation)
        for siblings in by_parent.values():
            siblings.sort(key=lambda item: (item["order"], item["id"]))

        def visit(invocation: dict[str, Any]) -> bool:
            caller = invocation["caller"]
            callee = invocation["callee"]
            call_items = self._run_unit(
                node_id=node_id,
                phase="call_bindings",
                prompt_version=CALL_BINDING_PROMPT_VERSION,
                schema_name="arc_call_binding",
                instructions=CALL_BINDING_INSTRUCTIONS,
                output_schema=CALL_BINDING_SCHEMA,
                context={
                    "contract": contract,
                    "invocation": invocation,
                    "caller": _module_header(flow.modules[caller]),
                    "caller_available": flow.available(caller),
                    "callee": _module_header(flow.modules[callee]),
                    "database_contract": _module_database_contract(flow.modules[callee], database_schema),
                    "policy_operations": ["normalize", "hash", "secure_random", "clock_now", "compare", "validate"],
                },
                validate_item=lambda target, item: flow.validate_call(target, invocation, item),
                resume=resume,
                errors=errors,
                cache_paths=cache_paths,
                cache_discriminator=invocation["id"],
            )
            if call_items is None:
                return False
            errors.extend(flow.apply_call(node_id, invocation, call_items[0]))
            if errors:
                return False

            for child in by_parent.get(invocation["id"], []):
                if not visit(child):
                    return False

            next_siblings = [
                _invocation_summary(item)
                for item in by_parent.get(invocation.get("parent_id"), [])
                if (item["order"], item["id"]) > (invocation["order"], invocation["id"])
            ]
            return_items = self._run_unit(
                node_id=node_id,
                phase="return_bindings",
                prompt_version=RETURN_BINDING_PROMPT_VERSION,
                schema_name="arc_return_binding",
                instructions=RETURN_BINDING_INSTRUCTIONS,
                output_schema=RETURN_BINDING_SCHEMA,
                context={
                    "contract_outputs": contract.get("outputs", []),
                    "invocation": invocation,
                    "callee": _module_header(flow.modules[callee]),
                    "callee_available": flow.available(callee),
                    "caller": _module_header(flow.modules[caller]),
                    "caller_available": flow.available(caller),
                    "later_siblings": next_siblings,
                    "policy_operations": ["normalize", "hash", "secure_random", "clock_now", "compare", "validate"],
                },
                validate_item=lambda target, item: flow.validate_return(target, invocation, item),
                resume=resume,
                errors=errors,
                cache_paths=cache_paths,
                cache_discriminator=invocation["id"],
            )
            if return_items is None:
                return False
            errors.extend(flow.apply_return(node_id, invocation, return_items[0]))
            return not errors

        for root in by_parent.get(None, []):
            if not visit(root):
                return False
        return True

    def _run_unit(
        self,
        *,
        node_id: str,
        phase: str,
        prompt_version: str,
        schema_name: str,
        instructions: str,
        output_schema: dict[str, Any],
        context: dict[str, Any],
        validate_item: Callable[[str, dict[str, Any]], list[str]],
        resume: bool,
        errors: list[str],
        cache_paths: dict[str, str],
        cache_discriminator: str | None = None,
    ) -> list[dict[str, Any]] | None:
        return self._run_batch(
            phase=phase,
            prompt_version=prompt_version,
            schema_name=schema_name,
            instructions=instructions,
            output_schema=output_schema,
            requirement_ids=[node_id],
            context=context,
            validate_item=validate_item,
            resume=resume,
            errors=errors,
            cache_paths=cache_paths,
            cache_discriminator=cache_discriminator,
        )

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
        cache_discriminator: str | None = None,
    ) -> list[dict[str, Any]] | None:
        input_hash = _stable_hash({"prompt_version": prompt_version, "context": context})
        cache_ids = [*requirement_ids, *([cache_discriminator] if cache_discriminator else [])]
        cache_path = self._cache_path(phase, cache_ids)
        for node_id in requirement_ids:
            key = f"{phase}:{node_id}" + (f":{cache_discriminator}" if cache_discriminator else "")
            cache_paths[key] = str(cache_path)
        if resume:
            cached = read_json(cache_path, {})
            payload = {"items": cached.get("items")}
            if cached.get("input_sha256") == input_hash:
                cache_errors = _validate_batch(requirement_ids, payload, validate_item)
                if not cache_errors:
                    self._trace(f"CACHE_HIT phase={phase} batch={','.join(requirement_ids)} path={cache_path.name}")
                    return payload["items"]

        feedback: list[str] = []
        for attempt in range(self._retry_count + 1):
            request_payload = copy.deepcopy(context)
            if feedback:
                request_payload["previous_errors"] = feedback
            request_chars = len(json.dumps(request_payload, ensure_ascii=False, separators=(",", ":")))
            trace_path = self._model_trace_path(phase, cache_path, attempt + 1)
            trace: dict[str, Any] = {
                "schema_version": 1,
                "status": "REQUESTED",
                "phase": phase,
                "prompt_version": prompt_version,
                "schema_name": schema_name,
                "requirement_ids": requirement_ids,
                "cache_discriminator": cache_discriminator,
                "attempt": attempt + 1,
                "max_attempts": self._retry_count + 1,
                "instructions": instructions,
                "input_payload": request_payload,
                "output_schema": output_schema,
                "request_chars": request_chars,
            }
            self._write_model_trace(trace_path, trace)
            self._trace(
                f"MODEL_CALL phase={phase} attempt={attempt + 1}/{self._retry_count + 1} "
                f"batch={','.join(requirement_ids)} request_chars={request_chars}"
            )
            started = time.perf_counter()
            transport_failed = False
            try:
                payload = self._model.generate_json(
                    schema_name=schema_name,
                    instructions=instructions,
                    input_payload=request_payload,
                    output_schema=output_schema,
                )
            except Exception as exc:
                transport_failed = True
                feedback = [f"Model call failed: {describe_model_error(exc)}"]
                elapsed = time.perf_counter() - started
                trace.update({"status": "TRANSPORT_ERROR", "seconds": round(elapsed, 3), "transport_error": feedback[0]})
                self._write_model_trace(trace_path, trace)
                self._trace(f"MODEL_ERROR phase={phase} seconds={elapsed:.1f} {feedback[0]}")
            else:
                elapsed = time.perf_counter() - started
                raw_payload = copy.deepcopy(payload)
                payload, events = _normalize_payload(payload)
                feedback = _validate_batch(requirement_ids, payload, validate_item)
                trace.update({
                    "status": "VALIDATION_ERROR" if feedback else "ACCEPTED",
                    "seconds": round(elapsed, 3),
                    "raw_output": raw_payload,
                    "normalized_output": payload,
                    "normalization_events": events,
                    "validation_errors": feedback,
                })
                self._write_model_trace(trace_path, trace)
                compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                self._trace(
                    f"MODEL_RESULT phase={phase} attempt={attempt + 1} batch={','.join(requirement_ids)} "
                    f"seconds={elapsed:.1f} items={len(payload.get('items', []))} response_chars={len(compact)}"
                )
                if not feedback:
                    write_json_atomic(cache_path, {
                        "schema_version": 1,
                        "prompt_version": prompt_version,
                        "input_sha256": input_hash,
                        "items": payload["items"],
                    })
                    return payload["items"]
                self._trace(f"MODEL_VALIDATION_ERROR phase={phase} {'; '.join(feedback)}")
            if attempt >= self._retry_count:
                errors.append(f"{phase} failed for {', '.join(requirement_ids)}: {'; '.join(feedback)}")
            elif transport_failed and self._retry_backoff:
                time.sleep(min(self._retry_backoff * (2 ** attempt), 30.0))
        return None

    def _trace(self, message: str) -> None:
        if self._trace_enabled:
            self._log.info(message)

    def _model_trace_path(self, phase: str, cache_path: Path, attempt: int) -> Path:
        return self._artifact_root / "model_traces" / phase / f"{cache_path.stem}-run-{self._model_trace_run_id}-attempt-{attempt}.json"

    def _write_model_trace(self, path: Path, payload: dict[str, Any]) -> None:
        if not self._model_trace_files:
            return
        try:
            write_json_atomic(path, payload)
        except OSError as exc:
            self._trace(f"MODEL_TRACE_ERROR path={path} error={exc}")
            return
        self._trace(
            f"MODEL_TRACE phase={payload.get('phase')} attempt={payload.get('attempt')} "
            f"status={payload.get('status')} path={path}"
        )

    def _cache_path(self, phase: str, ids: list[str]) -> Path:
        label = "-".join(ids)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("._")[:64] or "unit"
        suffix = hashlib.sha256(label.encode("utf-8")).hexdigest()[:10]
        return self._artifact_root / phase / f"{safe}-{suffix}.json"


def _validate_local_declarations(
    node_id: str,
    module_id: str,
    declarations: list[dict[str, Any]],
    available: dict[str, dict[str, Any]],
) -> list[str]:
    """Validate sequential local computations and extend a virtual symbol table."""

    errors: list[str] = []
    declared: set[str] = set()
    for local in declarations:
        name = str(local.get("name", ""))
        reference = f"{module_id}.local.{name}"
        if name in declared or reference in available:
            errors.append(f"{node_id}: duplicate local declaration: {reference}")
        missing = sorted(set(local.get("inputs", [])) - set(available))
        if missing:
            errors.append(
                f"{node_id}: local inputs are unavailable: {reference} <- {', '.join(missing)}"
            )
        declared.add(name)
        available[reference] = local
    return errors


def _apply_local_declarations(
    module: dict[str, Any],
    declarations: list[dict[str, Any]],
    errors: list[str],
) -> None:
    for local in declarations:
        field = {key: local[key] for key in ("name", "type", "meaning", "sensitive")}
        field.update({
            "required": True,
            "operation": local.get("operation", ""),
            "inputs": copy.deepcopy(local.get("inputs", [])),
        })
        _merge_field(module, "local", field, errors)


def _merge_field(
    module: dict[str, Any],
    scope: str,
    field: dict[str, Any],
    errors: list[str],
    *,
    name: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    candidate = copy.deepcopy(field)
    candidate["name"] = name or str(candidate.get("name", ""))
    candidate.setdefault("required", True)
    candidate.setdefault("meaning", candidate["name"])
    candidate.setdefault("sensitive", False)
    if extra:
        candidate.update(copy.deepcopy(extra))
    fields = module.setdefault(scope, [])
    existing = next((item for item in fields if item.get("name") == candidate["name"]), None)
    if existing is None:
        fields.append(candidate)
        fields.sort(key=lambda item: item["name"])
        return
    if existing.get("type") != candidate.get("type"):
        errors.append(
            f"Conflicting {scope} field type: {module.get('id')}.{candidate['name']} "
            f"({existing.get('type')} vs {candidate.get('type')})"
        )
    else:
        # Natural-language meanings often vary slightly between adjacent binding
        # calls.  The stable identity of a flow value is its qualified name and
        # scalar type; keep the first description instead of rejecting an
        # otherwise continuous flow.
        existing["required"] = bool(existing.get("required") or candidate.get("required"))
        existing["sensitive"] = bool(existing.get("sensitive") or candidate.get("sensitive"))


def _matching_binding(node_id: str, invocation: dict[str, Any], item: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if item.get("requirement_id") != node_id:
        errors.append(f"{node_id}: binding requirement_id does not match")
    if item.get("invocation_id") != invocation.get("id"):
        errors.append(f"{node_id}: binding invocation_id does not match: {invocation.get('id')}")
    return errors


def _local_ref(reference: str, module_id: str) -> bool:
    prefix = f"{module_id}.local."
    return reference.startswith(prefix) and bool(SYMBOL_PATTERN.fullmatch(reference[len(prefix):]))


def _qualified_local_name(reference: str, module_id: str) -> str | None:
    prefix = f"{module_id}.local."
    if not reference.startswith(prefix):
        return None
    name = reference[len(prefix):]
    return name if SYMBOL_PATTERN.fullmatch(name) else None


def _database_field_name(reference: str, entity: str) -> str | None:
    prefix = f"db.{entity}."
    if not reference.startswith(prefix):
        return None
    name = reference[len(prefix):]
    return name if SYMBOL_PATTERN.fullmatch(name) else None


def _types_assignable(source: str, target: str) -> bool:
    return source == target or (source == "integer" and target == "number")


def _module_header(module: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(module.get(key))
        for key in ("id", "kind", "route", "purpose", "owner", "access_ids", "effect_ids", "reads", "writes")
    }


def _module_database_contract(module: dict[str, Any], schema: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = set(module.get("reads", [])) | set(module.get("writes", []))
    return [
        {
            "entity": entity.get("key"),
            "read": entity.get("key") in set(module.get("reads", [])),
            "write": entity.get("key") in set(module.get("writes", [])),
            "access_responsibilities": [
                copy.deepcopy(contract)
                for contract in module.get("_access_contracts", {}).values()
                if contract.get("entity") == entity.get("key")
            ],
            "effect_responsibilities": [
                copy.deepcopy(contract)
                for contract in module.get("_effect_contracts", {}).values()
                if contract.get("entity") == entity.get("key")
            ],
            "fields": [
                {
                    "name": field.get("name"),
                    "type": field.get("type"),
                    "required": bool(field.get("required")),
                    "primary_key": bool(field.get("primary_key")),
                }
                for field in entity.get("fields", [])
            ],
            "relations": [
                {
                    "name": relation.get("name"),
                    "target_entity": relation.get("target_entity"),
                    "required": bool(relation.get("required")),
                }
                for relation in entity.get("relations", [])
            ],
        }
        for entity in schema.get("entities", [])
        if entity.get("key") in allowed
    ]


def _contract_validator(database_schema: dict[str, Any]) -> Callable[[str, dict[str, Any]], list[str]]:
    entities = {
        str(entity.get("key")): entity
        for entity in database_schema.get("entities", [])
        if isinstance(entity, dict) and entity.get("key")
    }

    def validate(node_id: str, item: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if item.get("requirement_id") != node_id:
            errors.append(f"{node_id}: contract requirement_id does not match")
        for scope in ("inputs", "outputs"):
            names: set[str] = set()
            for field in item.get(scope, []):
                name = str(field.get("name", "")) if isinstance(field, dict) else ""
                if name in names:
                    errors.append(f"{node_id}: duplicate contract {scope} field: {name}")
                names.add(name)
        access_ids: set[str] = set()
        for access in item.get("accesses", []):
            access_id = str(access.get("id", ""))
            if access_id in access_ids:
                errors.append(f"{node_id}: duplicate access id: {access_id}")
            access_ids.add(access_id)
            errors.extend(_database_reference_errors(node_id, access, entities))
        effect_ids: set[str] = set()
        for effect in item.get("effects", []):
            effect_id = str(effect.get("id", ""))
            if effect_id in effect_ids:
                errors.append(f"{node_id}: duplicate effect id: {effect_id}")
            effect_ids.add(effect_id)
            errors.extend(_database_reference_errors(node_id, effect, entities))
        for forbidden in item.get("forbidden_effects", []):
            unknown = sorted(set(forbidden.get("effect_ids", [])) - effect_ids)
            if unknown:
                errors.append(f"{node_id}: forbidden_effects references unknown effects: {', '.join(unknown)}")
        return errors

    return validate


def _database_reference_errors(
    node_id: str,
    item: dict[str, Any],
    entities: dict[str, dict[str, Any]],
) -> list[str]:
    entity_name = str(item.get("entity", ""))
    entity = entities.get(entity_name)
    if entity is None:
        return [f"{node_id}: unknown database entity in contract: {entity_name}"]
    valid = {
        str(field.get("name")) for field in entity.get("fields", []) if field.get("name")
    } | {
        str(relation.get("name")) for relation in entity.get("relations", []) if relation.get("name")
    }
    unknown = sorted(set(item.get("fields", [])) - valid)
    return [f"{node_id}: unknown fields for {entity_name}: {', '.join(unknown)}"] if unknown else []


def _tree_validator(
    known_modules: list[dict[str, Any]],
    contract: dict[str, Any],
    dependencies: dict[str, Any],
    node_id: str,
) -> Callable[[str, dict[str, Any]], list[str]]:
    known = {module["id"]: module for module in known_modules}
    allowed_known = {
        module_id
        for module_id, module in known.items()
        if module.get("owner") in _dependency_closure(node_id, dependencies)
    }
    access_ids = {item["id"] for item in contract.get("accesses", [])}
    effect_ids = {item["id"] for item in contract.get("effects", [])}

    def validate(target: str, item: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if item.get("requirement_id") != target:
            errors.append(f"{target}: call tree requirement_id does not match")
        modules: dict[str, dict[str, Any]] = {}
        routes = {str(module.get("route")) for module in known.values() if module.get("route")}
        claimed_accesses: list[str] = []
        claimed_effects: list[str] = []
        for module in item.get("modules", []):
            module_id = str(module.get("id", ""))
            if module_id in modules or module_id in known:
                errors.append(f"{target}: duplicate or known module definition: {module_id}")
            modules[module_id] = module
            kind = module.get("kind")
            route = str(module.get("route", ""))
            if kind == "PAGE" and (not route.startswith("/") or " " in route):
                errors.append(f"{target}: invalid PAGE route: {module_id}")
            elif kind == "API" and not re.fullmatch(r"(GET|POST|PUT|PATCH|DELETE) /[^ ]*", route):
                errors.append(f"{target}: invalid API route: {module_id}")
            elif kind not in {"PAGE", "API"} and route:
                errors.append(f"{target}: only PAGE/API may have routes: {module_id}")
            if route and route in routes:
                errors.append(f"{target}: duplicate route: {route}")
            routes.add(route)
            claimed_accesses.extend(module.get("access_ids", []))
            claimed_effects.extend(module.get("effect_ids", []))
            if kind != "REPOSITORY" and (module.get("access_ids") or module.get("effect_ids")):
                errors.append(f"{target}: only REPOSITORY may own database responsibilities: {module_id}")
        if sorted(claimed_accesses) != sorted(access_ids):
            errors.append(f"{target}: every access must have exactly one owner")
        if sorted(claimed_effects) != sorted(effect_ids):
            errors.append(f"{target}: every effect must have exactly one owner")

        available = set(modules) | allowed_known
        by_id: dict[str, dict[str, Any]] = {}
        orders: set[tuple[str | None, int]] = set()
        for invocation in item.get("invocations", []):
            invocation_id = str(invocation.get("id", ""))
            if invocation_id in by_id:
                errors.append(f"{target}: duplicate invocation: {invocation_id}")
            by_id[invocation_id] = invocation
            pair = (invocation.get("parent_id"), invocation.get("order"))
            if pair in orders:
                errors.append(f"{target}: duplicate sibling order: {pair}")
            orders.add(pair)
            if invocation.get("caller") not in available or invocation.get("callee") not in available:
                errors.append(f"{target}: unresolved invocation endpoint: {invocation_id}")
        for invocation_id, invocation in by_id.items():
            parent_id = invocation.get("parent_id")
            if parent_id is None:
                if invocation.get("caller") != item.get("entrypoint"):
                    errors.append(f"{target}: root invocation caller must be entrypoint: {invocation_id}")
                continue
            parent = by_id.get(str(parent_id))
            if parent is None:
                errors.append(f"{target}: unknown invocation parent: {invocation_id}")
            elif invocation.get("caller") != parent.get("callee"):
                errors.append(f"{target}: nested invocation caller mismatch: {invocation_id}")
        if item.get("entrypoint") not in modules and item.get("entrypoint") not in allowed_known:
            errors.append(f"{target}: unknown entrypoint")
        if any(outcome not in available for outcome in item.get("outcomes", [])):
            errors.append(f"{target}: unknown outcome module")
        return errors

    return validate


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


def _normalize_payload(payload: Any) -> tuple[Any, list[str]]:
    """Normalize recoverable CamelCase field and module names without inventing semantics."""

    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return payload, []
    result = copy.deepcopy(payload)
    events: list[str] = []

    def normalize_field(field: Any, label: str) -> None:
        if not isinstance(field, dict):
            return
        original = field.get("name")
        normalized = _canonical_symbol(original)
        if normalized != original:
            field["name"] = normalized
            events.append(f"DESIGN_NAME_NORMALIZED {label} {original}->{normalized}")

    for item in result["items"]:
        if not isinstance(item, dict):
            continue
        for key in ("inputs", "outputs"):
            for field in item.get(key, []):
                normalize_field(field, key)
        for module in item.get("modules", []):
            if not isinstance(module, dict):
                continue
            original = module.get("id")
            normalized = _canonical_module_id(original, module.get("kind"))
            if normalized != original:
                module["id"] = normalized
                events.append(f"DESIGN_NAME_NORMALIZED module {original}->{normalized}")
        for argument in item.get("arguments", []):
            normalize_field(argument.get("target") if isinstance(argument, dict) else None, "call target")
        for local in item.get("locals", []):
            normalize_field(local, "local")
        for operation in item.get("database_operations", []):
            for field in operation.get("produces", []) if isinstance(operation, dict) else []:
                normalize_field(field, "database result")
        for value in item.get("values", []):
            normalize_field(value.get("output") if isinstance(value, dict) else None, "return output")
    return result, events


def _canonical_symbol(value: Any) -> Any:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
        return value
    first = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    result = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first).lower()
    return result if SYMBOL_PATTERN.fullmatch(result) else value


def _canonical_module_id(value: Any, kind: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    parts = [_canonical_symbol(part) for part in value.split(".")]
    if any(not isinstance(part, str) or not SYMBOL_PATTERN.fullmatch(part) for part in parts):
        return value
    if len(parts) == 1 and kind in MODULE_KINDS:
        parts.insert(0, str(kind).lower())
    return ".".join(parts)


def _module_sort_key(module: dict[str, Any]) -> tuple[int, str]:
    return ({"PAGE": 0, "API": 1, "FUNCTION": 2, "REPOSITORY": 3}.get(str(module.get("kind")), 99), str(module.get("id", "")))


def _sorted_invocations(invocations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(invocations, key=lambda item: (str(item.get("parent_id") or ""), int(item.get("order", 0)), str(item.get("id", ""))))


def _invocation_summary(invocation: dict[str, Any]) -> dict[str, Any]:
    return {key: invocation.get(key) for key in ("id", "parent_id", "order", "caller", "callee", "condition")}


def _contract_summary(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "requirement_id": contract.get("requirement_id"),
        "outputs": contract.get("outputs", []),
        "accesses": contract.get("accesses", []),
        "effects": contract.get("effects", []),
    }


def _assemble_design(
    flow: _FlowState,
    contracts: dict[str, dict[str, Any]],
    trees: dict[str, dict[str, Any]],
    waves: list[list[str]],
    database_schema: dict[str, Any],
    dependencies: dict[str, Any],
) -> dict[str, Any]:
    requirements: list[dict[str, Any]] = []
    for wave in waves:
        for node_id in wave:
            tree = trees[node_id]
            requirements.append({
                "id": node_id,
                "contract_id": node_id,
                "data": _requirement_domains(database_schema, node_id, dependencies),
                "entrypoints": [tree["entrypoint"]],
                "outcomes": sorted(set(tree["outcomes"])),
            })
    modules = []
    for module in flow.modules.values():
        normalized = {
            key: copy.deepcopy(value)
            for key, value in module.items()
            if not key.startswith("_")
        }
        for key in ("input", "output", "local"):
            normalized[key] = sorted(normalized.get(key, []), key=lambda field: field["name"])
        for key in (
            "errors",
            "reads",
            "writes",
            "generates",
            "access_ids",
            "effect_ids",
            "realized_access_ids",
            "realized_effect_ids",
        ):
            normalized[key] = sorted(set(normalized.get(key, [])))
        modules.append(normalized)
    return {
        "schema_version": 4,
        "status": "PROPOSED",
        "contracts": [copy.deepcopy(contracts[key]) for key in sorted(contracts)],
        "call_trees": [copy.deepcopy(trees[key]) for key in sorted(trees)],
        "modules": assign_module_files(sorted(modules, key=lambda item: item["id"])),
        "links": copy.deepcopy(flow.links),
        "requirements": sorted(requirements, key=lambda item: item["id"]),
    }


def _empty_design(
    flow: _FlowState,
    contracts: dict[str, dict[str, Any]],
    trees: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    modules = assign_module_files(sorted(
        (
            {key: copy.deepcopy(value) for key, value in module.items() if not key.startswith("_")}
            for module in flow.modules.values()
        ),
        key=lambda item: item["id"],
    ))
    return {
        "schema_version": 4,
        "status": "PROPOSED",
        "contracts": [copy.deepcopy(contracts[key]) for key in sorted(contracts)],
        "call_trees": [copy.deepcopy(trees[key]) for key in sorted(trees)],
        "modules": modules,
        "links": copy.deepcopy(flow.links),
        "requirements": [],
    }


def _requirement_context(nodes: dict[str, Any], node_id: str, dependencies: dict[str, Any]) -> dict[str, Any]:
    node = nodes.get(node_id, {})
    ancestors: list[dict[str, str]] = []
    parent_id = node.get("parent_id")
    while isinstance(parent_id, str) and parent_id:
        parent = nodes.get(parent_id)
        if not isinstance(parent, dict):
            break
        ancestors.append({"id": parent_id, "name": str(parent.get("name", "")), "description": str(parent.get("description", ""))})
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
    return [
        copy.deepcopy(entity)
        for entity in schema.get("entities", [])
        if related.intersection(entity.get("sources", []))
    ]


def _requirement_domains(schema: dict[str, Any], node_id: str, dependencies: dict[str, Any]) -> list[str]:
    related = {node_id} | _dependency_closure(node_id, dependencies)
    return sorted(
        str(entity.get("key"))
        for entity in schema.get("entities", [])
        if entity.get("key") and related.intersection(entity.get("sources", []))
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
        wave = sorted(({str(item) for item in raw_wave} & declared) - seen)
        if wave:
            result.append(wave)
            seen.update(wave)
    if declared - seen:
        result.append(sorted(declared - seen))
    return result


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def design_hash(design_ir: dict[str, Any]) -> str:
    return _stable_hash(design_ir)


def database_hash(database_schema: dict[str, Any]) -> str:
    return _stable_hash(database_schema)


def verify_design_manifest(design_ir: dict[str, Any], database_schema: dict[str, Any], manifest: dict[str, Any]) -> bool:
    return manifest.get("design_sha256") == _stable_hash(design_ir) and manifest.get("database_sha256") == _stable_hash(database_schema)


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(os.environ.get(name, str(default))), maximum))
    except ValueError:
        return default


def _bounded_env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(float(os.environ.get(name, str(default))), maximum))
    except ValueError:
        return default


def _enabled_env_flag(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


__all__ = [
    "CALL_BINDING_INSTRUCTIONS",
    "CALL_BINDING_SCHEMA",
    "DesignPass",
    "DesignPassResult",
    "MODULE_CALL_TREE_INSTRUCTIONS",
    "MODULE_CALL_TREE_SCHEMA",
    "REQUIREMENT_CONTRACT_INSTRUCTIONS",
    "REQUIREMENT_CONTRACT_SCHEMA",
    "RETURN_BINDING_INSTRUCTIONS",
    "RETURN_BINDING_SCHEMA",
    "database_hash",
    "design_hash",
    "verify_design_manifest",
]
