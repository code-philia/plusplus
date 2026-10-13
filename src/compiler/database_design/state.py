"""Shared ER design state and deterministic relational mapping."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from ..ir.database import ERModelIR
from .common import (
    _append_unique,
    _compact_properties,
    _format_error,
    _merge_properties,
    _normalize_identifier,
    _relationship_id,
)
from .contracts import FIELD_TYPES, IDENTIFIER_PATTERN, RELATIONSHIP_TYPES
from .merging import merge_field_contributions


class DatabaseSchemaState:
    """Compiler-owned state; semantic passes can mutate it only through apply methods."""

    def __init__(self) -> None:
        self.entities: dict[str, dict[str, Any]] = {}
        self.relationships: dict[str, dict[str, Any]] = {}
        self.constraints: dict[str, dict[str, Any]] = {}
        self.requirement_entity_links: dict[str, set[str]] = {}
        self.requirement_field_links: dict[str, set[str]] = {}
        self.requirement_relationship_links: dict[str, set[str]] = {}
        self.requirement_constraint_links: dict[str, set[str]] = {}
        self.warnings: list[str] = []
        self.contributions: dict[str, Any] = {"fields": {}, "relationships": {}}
        self.conflicts: list[dict[str, Any]] = []
        self.requirement_access: dict[str, dict[str, set[str]]] = {}

    def entity_headers(self) -> list[dict[str, Any]]:
        return [{"key": item["key"], "description": item["description"],
                 "requirement_ids": list(item['requirement_ids']),
                 "read": list(item.get('read', [])), "write": list(item.get('write', []))}
                for item in self._ordered_entities()]

    def ensure_requirement(self, requirement_id: str) -> None:
        """Retain traceability even when a requirement needs no persistent data."""

        self.requirement_entity_links.setdefault(requirement_id, set())
        self.requirement_access.setdefault(requirement_id, {'read': set(), 'write': set()})

    def related_entity_keys(self, requirement_id: str) -> list[str]:
        return sorted(self.requirement_entity_links.get(requirement_id, set()))

    def schema_slice(self, requirement_id: str) -> dict[str, Any]:
        keys = set(self.related_entity_keys(requirement_id))
        return {
            "entities": [self._public_entity(self.entities[key]) for key in sorted(keys)],
            "relationships": [copy.deepcopy(item) for item in self.relationships.values() if item["parent"] in keys and item["child"] in keys],
        }

    def apply_entities(self, requirement_id: str, decision: dict[str, Any], errors: list[str]) -> None:
        for raw_key in decision.get("reuse_entities", []):
            key = _normalize_identifier(str(raw_key))
            if key not in self.entities:
                errors.append(_format_error("ARC2211", f"Pass 1 reuses an unknown entity: {raw_key}.", node_id=requirement_id))
            else:
                self._link_entity(requirement_id, key)
        for raw in decision.get("new_entities", []):
            key = _normalize_identifier(str(raw.get("key", "")))
            if not IDENTIFIER_PATTERN.fullmatch(key):
                errors.append(_format_error("ARC2212", f"Pass 1 emitted an invalid entity key: {raw.get('key')}.", node_id=requirement_id))
                continue
            if key not in self.entities:
                self.entities[key] = {
                    "key": key, "description": str(raw.get("description", "")).strip(),
                    "requirement_ids": [], "fields": {}, "read": [], "write": [],
                }
                self._add_field(key, {
                    "name": "id", "type": "uuid", "nullable": False,
                    "description": "Compiler-provided stable primary key.", "properties": {},
                    "origin": "SYSTEM", "references": None,
                }, requirement_id, errors, trace=False)
            self._link_entity(requirement_id, key)
        self._apply_access(requirement_id, decision, errors)

    def apply_fields(self, requirement_id: str, decision: dict[str, Any], errors: list[str]) -> None:
        for item in decision.get("entities", []):
            key = _normalize_identifier(str(item.get("entity", "")))
            if key not in self.entities:
                errors.append(f'Unknown entity in field discovery: {key}')
                continue
            self._link_entity(requirement_id, key)
            for raw_field in [*item.get("new_fields", []), *item.get("update_fields", [])]:
                self._apply_field_contribution(key, raw_field, requirement_id, errors)
            for name in item.get('reuse_fields', []):
                if name not in self.entities[key]['fields']:
                    errors.append(f'Unknown reused field: {key}.{name}')
                else:
                    self._link_field(requirement_id, key, name)
        self._apply_access(requirement_id, decision, errors)

    def _apply_access(self, rid: str, decision: dict[str, Any], errors: list[str]) -> None:
        self.ensure_requirement(rid)
        for operation in ('read', 'write'):
            for reference in decision.get(operation, []):
                entity, separator, name = reference.partition('.')
                if entity not in self.entities or separator and name not in self.entities[entity]['fields']:
                    errors.append(f'Unknown {operation} target: {reference}')
                    continue
                self._link_entity(rid, entity)
                if separator:
                    self._link_field(rid, entity, name)
                record = self.entities[entity]['fields'][name] if separator else self.entities[entity]
                _append_unique(record.setdefault(operation, []), rid)
                self.requirement_access[rid][operation].add(reference)

    def _apply_field_contribution(self, key: str, raw: dict[str, Any], rid: str, errors: list[str]) -> None:
        reference = key + "." + raw["name"]
        candidates = copy.deepcopy(self.contributions["fields"].get(reference, {}))
        candidates[rid] = copy.deepcopy(raw)
        try:
            merged = merge_field_contributions(key, candidates)
        except ValueError as exc:
            self._record_conflict(reference, rid, raw, str(exc), errors)
            return
        existing = self.entities[key]["fields"].get(raw["name"])
        if existing is None:
            self._add_field(key, {**merged, "origin": "REQUIREMENT", "references": None}, rid, errors)
        else:
            existing.update(merged)
            self._link_field(rid, key, raw["name"])
        self.contributions["fields"][reference] = candidates

    def _record_conflict(self, scope: str, rid: str, candidate: dict[str, Any], reason: str, errors: list[str]) -> None:
        self.conflicts.append({"scope": scope, "requirement_id": rid,
                               "candidate": copy.deepcopy(candidate), "reason": reason})
        errors.append(_format_error("ARC2228", f"Conflicting contribution {scope}: {reason}", node_id=rid))

    def apply_relationships(self, requirement_id: str, decision: dict[str, Any], errors: list[str]) -> None:
        for raw in decision.get("relationships", []):
            relationship_key = _normalize_identifier(str(raw.get("key", "")))
            parent = _normalize_identifier(str(raw.get("parent", "")))
            child = _normalize_identifier(str(raw.get("child", "")))
            relationship_type = str(raw.get("type", ""))
            if parent not in self.entities or child not in self.entities:
                errors.append(_format_error("ARC2231", f"Relationship references unknown entities: {parent} -> {child}.", node_id=requirement_id))
                continue
            if relationship_type not in RELATIONSHIP_TYPES:
                errors.append(_format_error("ARC2232", f"Pass 3 emitted invalid cardinality: {relationship_type}.", node_id=requirement_id))
                continue
            relationship_id = _relationship_id(relationship_key)
            relationship = self.relationships.get(relationship_id)
            if relationship is None:
                relationship = {
                    "id": relationship_id, "key": relationship_key, "parent": parent, "child": child, "type": relationship_type,
                    "parent_min": int(raw["parent_min"]), "parent_max": raw["parent_max"],
                    "child_min": int(raw["child_min"]), "child_max": raw["child_max"],
                    "child_required": int(raw["child_min"]) == 1, "description": str(raw.get("description", "")).strip(),
                    "fk_entity": None, "fk_field": None, "association_entity": None, "requirement_ids": [],
                    "evidence_requirement_ids": [],
                }
                self.relationships[relationship_id] = relationship
            else:
                for name in ("parent", "child", "type", "parent_max", "child_max"):
                    relationship[name] = raw[name]
                relationship["parent_min"] = max(relationship["parent_min"], int(raw["parent_min"]))
                relationship["child_min"] = max(relationship["child_min"], int(raw["child_min"]))
                relationship["child_required"] = relationship["child_min"] == 1
                descriptions = set(relationship["description"].split("\n")) | {raw["description"].strip()}
                relationship["description"] = "\n".join(sorted(descriptions - {""}))
            ledger = self.contributions["relationships"].setdefault(relationship_id, {})
            # Entity-focused discovery can repeat the same evidence. Keep all distinct proposals.
            proposals = ledger.setdefault(requirement_id, [])
            if raw not in proposals:
                proposals.append(copy.deepcopy(raw))
                proposals.sort(key=lambda value: json.dumps(value, sort_keys=True))
            _append_unique(relationship["requirement_ids"], requirement_id)
            for evidence_id in raw["evidence_requirement_ids"]:
                _append_unique(relationship["evidence_requirement_ids"], evidence_id)
            self._link_entity(requirement_id, parent)
            self._link_entity(requirement_id, child)
            self.requirement_relationship_links.setdefault(requirement_id, set()).add(relationship_id)

    def lower_relationships(self, errors: list[str]) -> None:
        """Map the completed logical ER relationships only after discovery ends."""
        for relationship in self.relationships.values():
            for requirement_id in relationship["requirement_ids"]:
                self._lower_relationship(relationship, requirement_id, errors)

    def add_static_constraints(self) -> None:
        """Lower structural facts to constraints without an LLM call."""

        for entity in self._ordered_entities():
            for field_item in entity["fields"].values():
                ref = f"{entity['key']}.{field_item['name']}"
                for source in field_item["requirement_ids"] or ["SYSTEM"]:
                    if field_item.get("primary_key"):
                        self._add_constraint("PRIMARY_KEY", [ref], "Entity primary key.", source)
                    if not field_item["nullable"]:
                        self._add_constraint("NOT_NULL", [ref], "Field is required.", source)
                    if field_item.get("properties", {}).get("unique") is True:
                        self._add_constraint("UNIQUE", [ref], "Attribute business key.", source)
                    if field_item.get("references"):
                        self._add_constraint(
                            "FOREIGN_KEY", [ref, field_item["references"]], "Relationship foreign key.", source,
                        )
        for relationship in self.relationships.values():
            if relationship["type"] == "ONE_TO_ONE" and relationship["fk_entity"] and relationship["fk_field"]:
                for source in relationship["requirement_ids"] or ["SYSTEM"]:
                    self._add_constraint(
                        "UNIQUE", [f"{relationship['fk_entity']}.{relationship['fk_field']}"],
                        "One-to-one relationship.", source,
                    )
            if relationship["type"] == "MANY_TO_MANY" and relationship["association_entity"]:
                association = relationship["association_entity"]
                fields = [f"{association}.{name}" for name in relationship["association_fields"]]
                for source in relationship["requirement_ids"] or ["SYSTEM"]:
                    self._add_constraint(
                        "COMPOSITE_UNIQUE", fields, "Association pair must be unique.", source,
                    )

    def to_schema(self, *, status: str) -> dict[str, Any]:
        requirement_ids = (
            set(self.requirement_entity_links)
            | set(self.requirement_field_links)
            | set(self.requirement_relationship_links)
            | set(self.requirement_constraint_links)
        )
        traceability = {
            requirement_id: {
                "entities": sorted(self.requirement_entity_links.get(requirement_id, set())),
                "fields": sorted(self.requirement_field_links.get(requirement_id, set())),
                "relationships": sorted(self.requirement_relationship_links.get(requirement_id, set())),
                "constraints": sorted(self.requirement_constraint_links.get(requirement_id, set())),
                **{operation: sorted(self.requirement_access.get(requirement_id, {}).get(operation, set()))
                   for operation in ('read', 'write')},
            }
            for requirement_id in sorted(requirement_ids)
        }
        return {
            "status": status,
            "entities": [self._public_entity(item) for item in self._ordered_entities()],
            "relationships": [copy.deepcopy(self.relationships[key]) for key in sorted(self.relationships)],
            "constraints": [copy.deepcopy(self.constraints[key]) for key in sorted(self.constraints)],
            "traceability": {"requirements": traceability},
        }

    def to_er_model(self) -> ERModelIR:
        """Return the logical model without generated foreign keys or join entities."""
        return {
            "ir_kind": "er_model",
            "status": "PROPOSED",
            "contributions": copy.deepcopy(self.contributions),
            "conflicts": copy.deepcopy(self.conflicts),
            "access": {rid: {operation: sorted(targets) for operation, targets in access.items()}
                       for rid, access in sorted(self.requirement_access.items())},
            "entities": [self._public_entity(item) for item in self._ordered_entities()],
            "relationships": [
                {name: copy.deepcopy(item[name]) for name in (
                    "id", "key", "parent", "child", "type", "parent_min", "parent_max",
                    "child_min", "child_max", "description", "requirement_ids", "evidence_requirement_ids",
                )}
                for item in sorted(self.relationships.values(), key=lambda item: item["id"])
            ],
        }

    def _ordered_entities(self) -> list[dict[str, Any]]:
        return [self.entities[key] for key in sorted(self.entities)]

    def _public_entity(self, entity: dict[str, Any]) -> dict[str, Any]:
        fields = [copy.deepcopy(entity["fields"][name]) for name in sorted(entity["fields"])]
        return {
            "key": entity["key"],
            "description": entity["description"],
            "requirement_ids": list(entity["requirement_ids"]),
            "fields": fields,
            "read": list(entity.get('read', [])), "write": list(entity.get('write', [])),
        }

    def _link_entity(self, requirement_id: str, key: str) -> None:
        _append_unique(self.entities[key]["requirement_ids"], requirement_id)
        self.requirement_entity_links.setdefault(requirement_id, set()).add(key)

    def _link_field(self, requirement_id: str, entity_key: str, field_name: str) -> None:
        field_item = self.entities[entity_key]["fields"][field_name]
        _append_unique(field_item["requirement_ids"], requirement_id)
        self.requirement_field_links.setdefault(requirement_id, set()).add(f"{entity_key}.{field_name}")

    def _add_field(
        self,
        entity_key: str,
        raw: dict[str, Any],
        requirement_id: str,
        errors: list[str],
        *,
        trace: bool = True,
    ) -> None:
        name = _normalize_identifier(str(raw.get("name", "")))
        field_type = str(raw.get("type", ""))
        if not IDENTIFIER_PATTERN.fullmatch(name) or field_type not in FIELD_TYPES or not isinstance(raw.get("nullable"), bool):
            errors.append(_format_error("ARC2226", f"Invalid field declaration: {entity_key}.{name}.", node_id=requirement_id))
            return
        existing = self.entities[entity_key]["fields"].get(name)
        if existing is not None:
            existing["type"] = field_type
            existing["references"] = raw.get("references")
            existing["nullable"] = existing["nullable"] and bool(raw["nullable"])
            existing["properties"].update(copy.deepcopy(raw.get("properties", {})))
        else:
            existing = {
                "name": name,
                "type": field_type,
                "nullable": bool(raw["nullable"]),
                "primary_key": name == "id",
                "description": str(raw.get("description", "")).strip(),
                "properties": copy.deepcopy(raw.get("properties", {})),
                "origin": str(raw.get("origin", "REQUIREMENT")),
                "references": raw.get("references"),
                "requirement_ids": [],
                "read": [], "write": [],
            }
            self.entities[entity_key]["fields"][name] = existing
        if requirement_id != "SYSTEM":
            _append_unique(existing["requirement_ids"], requirement_id)
            if trace:
                self.requirement_field_links.setdefault(requirement_id, set()).add(f"{entity_key}.{name}")

    def _lower_relationship(self, relationship: dict[str, Any], requirement_id: str, errors: list[str]) -> None:
        parent, child = relationship["parent"], relationship["child"]
        if relationship["type"] in {"ONE_TO_MANY", "ONE_TO_ONE"}:
            # Preserve conventional names for a single nonrecursive edge;
            # separate semantic roles when several edges share a child/parent.
            multiple = sum(item["parent"] == parent and item["child"] == child
                           for item in self.relationships.values()) > 1
            field_name = f"{relationship['key']}_id" if multiple or parent == child else f"{parent}_id"
            relationship.update({"fk_entity": child, "fk_field": field_name})
            self._add_field(child, {
                "name": field_name, "type": "foreign_key", "nullable": not relationship["child_required"],
                "description": f"Reference to {parent}.", "properties": {}, "origin": "RELATIONSHIP",
                "references": f"{parent}.id",
            }, requirement_id, errors)
            return

        association = f"{relationship['key']}_association"
        relationship["association_entity"] = association
        endpoint_fields = [(parent, f"{parent}_id"), (child, f"{child}_id")]
        if parent == child:
            endpoint_fields = [(parent, "parent_id"), (child, "child_id")]
        relationship["association_fields"] = [name for _, name in endpoint_fields]
        if association not in self.entities:
            self.entities[association] = {
                "key": association, "description": f"Association between {parent} and {child}.",
                "requirement_ids": [], "fields": {},
            }
            self._add_field(association, {
                "name": "id", "type": "uuid", "nullable": False,
                "description": "Compiler-provided stable primary key.", "properties": {},
                "origin": "SYSTEM", "references": None,
            }, requirement_id, errors, trace=False)
        self._link_entity(requirement_id, association)
        for target, name in endpoint_fields:
            self._add_field(association, {
                "name": name, "type": "foreign_key", "nullable": False, "description": f"Reference to {target}.",
                "properties": {}, "origin": "RELATIONSHIP", "references": f"{target}.id",
            }, requirement_id, errors)
        self._add_constraint(
            "COMPOSITE_UNIQUE", [f"{association}.{name}" for _, name in endpoint_fields],
            "Association pair must be unique.", requirement_id,
        )

    def _add_constraint(
        self,
        constraint_type: str,
        fields: list[str],
        description: str,
        requirement_id: str,
    ) -> None:
        identity = {"type": constraint_type, "fields": fields, "description": description}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:12]
        constraint_id = f"constraint_{constraint_type.lower()}_{digest}"
        constraint = self.constraints.setdefault(constraint_id, {
            "id": constraint_id, "type": constraint_type, "fields": list(fields), "description": description,
            "requirement_ids": [],
        })
        if requirement_id != "SYSTEM":
            _append_unique(constraint["requirement_ids"], requirement_id)
            self.requirement_constraint_links.setdefault(requirement_id, set()).add(constraint_id)
