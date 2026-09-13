from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import Diagnostic


SUPPORTED_NODE_TYPES = {"FOLDER", "ATOMIC"}
IMAGE_REFERENCE_PATTERN = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


@dataclass(slots=True)
class FrontendResult:
    requirement_ir: dict[str, Any]
    dependency_graph: dict[str, Any]
    normalized_tree: dict[str, Any]
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(item.severity == "error" for item in self.diagnostics)


class RequirementFrontend:
    """Parse, normalize, validate, and schedule an ARC requirement document."""

    def compile(self, requirement_path: Path) -> FrontendResult:
        source_path = requirement_path.expanduser().resolve()
        diagnostics: list[Diagnostic] = []
        try:
            source_bytes = source_path.read_bytes()
            payload = yaml.safe_load(source_bytes.decode("utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            diagnostics.append(Diagnostic("ARC1001", f"Cannot parse requirement document: {exc}", source=str(source_path)))
            return FrontendResult({}, {}, {}, diagnostics)

        if isinstance(payload, dict) and isinstance(payload.get("root"), dict):
            payload = payload["root"]
        elif isinstance(payload, dict) and "id" not in payload and isinstance(payload.get("requirement"), dict):
            payload = payload["requirement"]
        if not isinstance(payload, dict):
            diagnostics.append(Diagnostic("ARC1002", "Requirement document root must be a mapping.", source=str(source_path)))
            return FrontendResult({}, {}, {}, diagnostics)

        nodes: dict[str, dict[str, Any]] = {}
        node_order: list[str] = []
        normalized_tree = self._normalize_node(
            payload,
            parent_id=None,
            pointer="/",
            source_name=source_path.name,
            nodes=nodes,
            node_order=node_order,
            diagnostics=diagnostics,
        )
        root_id = str(normalized_tree.get("id") or "")
        self._validate_dependencies(nodes, diagnostics)

        atomic_ids = sorted(node_id for node_id, node in nodes.items() if node["type"] == "ATOMIC")
        folder_ids = sorted(node_id for node_id, node in nodes.items() if node["type"] == "FOLDER")
        effective_dependencies = self._effective_atomic_dependencies(nodes, atomic_ids, diagnostics)
        waves = self._topological_waves(atomic_ids, effective_dependencies, diagnostics)

        requirement_ir = {
            "schema_version": 1,
            "source": {
                "document": source_path.name,
                "sha256": hashlib.sha256(source_bytes).hexdigest(),
            },
            "root_id": root_id or None,
            "node_order": node_order,
            "atomic_units": atomic_ids,
            "folder_nodes": folder_ids,
            "nodes": dict(sorted(nodes.items())),
        }
        dependency_graph = {
            "schema_version": 1,
            "root_id": root_id or None,
            "requirements": {
                node_id: sorted(node["dependencies"])
                for node_id, node in sorted(nodes.items())
            },
            "atomic_dependencies": dict(sorted(effective_dependencies.items())),
            "implementation_waves": waves,
        }
        if not root_id:
            diagnostics.append(Diagnostic("ARC1003", "Requirement root id is missing.", source=str(source_path)))
        return FrontendResult(requirement_ir, dependency_graph, normalized_tree, diagnostics)

    def _normalize_node(
        self,
        raw: dict[str, Any],
        *,
        parent_id: str | None,
        pointer: str,
        source_name: str,
        nodes: dict[str, dict[str, Any]],
        node_order: list[str],
        diagnostics: list[Diagnostic],
    ) -> dict[str, Any]:
        node_id = str(raw.get("id") or raw.get("req_id") or "").strip()
        source = f"{source_name}#{pointer}"
        if not node_id:
            node_id = f"<missing:{pointer}>"
            diagnostics.append(Diagnostic("ARC1101", "Requirement node id is missing.", source=source))
        elif node_id in nodes:
            diagnostics.append(Diagnostic("ARC1102", f"Duplicate requirement id: {node_id}", node_id=node_id, source=source))

        node_type = str(raw.get("type") or ("FOLDER" if raw.get("children") else "ATOMIC")).strip().upper()
        if node_type not in SUPPORTED_NODE_TYPES:
            diagnostics.append(Diagnostic("ARC1103", f"Unsupported requirement node type: {node_type}", node_id=node_id, source=source))
            node_type = "ATOMIC"

        raw_dependencies = raw.get("dependencies") or []
        if not isinstance(raw_dependencies, list):
            diagnostics.append(Diagnostic("ARC1104", "dependencies must be a list.", node_id=node_id, source=source))
            raw_dependencies = []
        dependencies = sorted({str(item).strip() for item in raw_dependencies if str(item).strip()})

        raw_children = raw.get("children") or []
        if not isinstance(raw_children, list):
            diagnostics.append(Diagnostic("ARC1105", "children must be a list.", node_id=node_id, source=source))
            raw_children = []
        if node_type == "ATOMIC" and raw_children:
            diagnostics.append(Diagnostic("ARC1106", "ATOMIC requirement cannot contain children.", node_id=node_id, source=source))

        description = str(raw.get("description") or "").strip()
        visual_references = self._visual_references(raw.get("visual_reference"), description)
        scenarios = self._normalize_scenarios(raw.get("scenarios"), node_id, source, diagnostics)

        node = {
            "id": node_id,
            "name": str(raw.get("name") or "").strip(),
            "type": node_type,
            "description": description,
            "parent_id": parent_id,
            "children_ids": [],
            "dependencies": dependencies,
            "visual_references": visual_references,
            "scenarios": scenarios,
            "source": {"document": source_name, "pointer": pointer},
        }
        if node_id not in nodes:
            nodes[node_id] = node
            node_order.append(node_id)

        normalized_children: list[dict[str, Any]] = []
        for index, child in enumerate(raw_children):
            if not isinstance(child, dict):
                diagnostics.append(Diagnostic("ARC1107", "Requirement child must be a mapping.", node_id=node_id, source=f"{source}/{index}"))
                continue
            normalized_child = self._normalize_node(
                child,
                parent_id=node_id,
                pointer=f"{pointer.rstrip('/')}/children/{index}",
                source_name=source_name,
                nodes=nodes,
                node_order=node_order,
                diagnostics=diagnostics,
            )
            normalized_children.append(normalized_child)
        node["children_ids"] = [child["id"] for child in normalized_children]
        return {
            "id": node_id,
            "name": node["name"],
            "type": node_type,
            "description": description,
            "dependencies": dependencies,
            "visual_reference": visual_references,
            "scenarios": scenarios,
            "source": node["source"],
            "children": normalized_children,
        }

    @staticmethod
    def _normalize_scenarios(value: Any, node_id: str, source: str, diagnostics: list[Diagnostic]) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            diagnostics.append(Diagnostic("ARC1201", "scenarios must be a list.", node_id=node_id, source=source))
            return []
        scenarios: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw in enumerate(value, start=1):
            if not isinstance(raw, dict):
                diagnostics.append(Diagnostic("ARC1202", "Scenario must be a mapping.", node_id=node_id, source=source))
                continue
            scenario_id = str(raw.get("id") or raw.get("scenario_id") or f"{node_id}:scenario:{index}").strip()
            if scenario_id in seen:
                diagnostics.append(Diagnostic("ARC1203", f"Duplicate scenario id: {scenario_id}", node_id=node_id, source=source))
                continue
            seen.add(scenario_id)
            steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
            scenarios.append({
                "id": scenario_id,
                "scenario_id": scenario_id,
                "name": str(raw.get("name") or scenario_id).strip(),
                "steps": [
                    {
                        "keyword": str(step.get("keyword") or "").strip().upper(),
                        "content": str(step.get("content") or "").strip(),
                    }
                    for step in steps
                    if isinstance(step, dict)
                ],
            })
        return scenarios

    @staticmethod
    def _visual_references(explicit: Any, description: str) -> list[str]:
        values = explicit if isinstance(explicit, list) else ([explicit] if isinstance(explicit, str) else [])
        values = [str(item).strip() for item in values if str(item).strip()]
        values.extend(match.strip() for match in IMAGE_REFERENCE_PATTERN.findall(description) if match.strip())
        return sorted(set(values))

    @staticmethod
    def _validate_dependencies(nodes: dict[str, dict[str, Any]], diagnostics: list[Diagnostic]) -> None:
        for node_id, node in sorted(nodes.items()):
            for dependency in node["dependencies"]:
                if dependency == node_id:
                    diagnostics.append(Diagnostic("ARC1301", "Requirement cannot depend on itself.", node_id=node_id))
                elif dependency not in nodes:
                    diagnostics.append(Diagnostic("ARC1302", f"Unresolved requirement dependency: {dependency}", node_id=node_id))

    def _effective_atomic_dependencies(
        self,
        nodes: dict[str, dict[str, Any]],
        atomic_ids: list[str],
        diagnostics: list[Diagnostic],
    ) -> dict[str, list[str]]:
        atomic_set = set(atomic_ids)
        descendants: dict[str, set[str]] = {}

        def atomic_descendants(node_id: str, visiting: set[str]) -> set[str]:
            if node_id in descendants:
                return descendants[node_id]
            if node_id in visiting:
                return set()
            node = nodes.get(node_id)
            if node is None:
                return set()
            if node["type"] == "ATOMIC":
                return {node_id}
            result: set[str] = set()
            for child_id in node["children_ids"]:
                result.update(atomic_descendants(child_id, visiting | {node_id}))
            descendants[node_id] = result
            return result

        graph: dict[str, list[str]] = {}
        for atomic_id in atomic_ids:
            references: set[str] = set(nodes[atomic_id]["dependencies"])
            parent_id = nodes[atomic_id]["parent_id"]
            while parent_id:
                parent = nodes.get(parent_id)
                if parent is None:
                    break
                references.update(parent["dependencies"])
                parent_id = parent["parent_id"]
            resolved: set[str] = set()
            for reference in references:
                if reference not in nodes:
                    continue
                resolved.update(atomic_descendants(reference, set()))
            resolved.discard(atomic_id)
            graph[atomic_id] = sorted(resolved & atomic_set)
        return graph

    @staticmethod
    def _topological_waves(
        atomic_ids: list[str],
        dependencies: dict[str, list[str]],
        diagnostics: list[Diagnostic],
    ) -> list[list[str]]:
        remaining = set(atomic_ids)
        completed: set[str] = set()
        waves: list[list[str]] = []
        while remaining:
            wave = sorted(node_id for node_id in remaining if set(dependencies[node_id]) <= completed)
            if not wave:
                cycle_nodes = sorted(remaining)
                diagnostics.append(Diagnostic("ARC1303", f"Requirement dependency cycle detected: {', '.join(cycle_nodes)}"))
                break
            waves.append(wave)
            completed.update(wave)
            remaining.difference_update(wave)
        return waves
