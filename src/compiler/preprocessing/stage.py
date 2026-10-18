from __future__ import annotations

import hashlib
import copy
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

SUPPORTED_NODE_TYPES = {"FOLDER", "ATOMIC"}


@dataclass(slots=True)
class PreprocessingResult:
    requirement_ir: dict[str, Any]
    dependency_graph: dict[str, Any]
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class RequirementPreprocessor:
    """Parse, normalize, validate, and schedule an ARC requirement document."""

    def compile(self, requirement_path: Path) -> PreprocessingResult:
        source_path = requirement_path.expanduser().resolve()
        errors: list[str] = []
        try:
            source_bytes = source_path.read_bytes()
            payload = yaml.safe_load(source_bytes.decode("utf-8-sig")) or {}
        except UnicodeError as exc:
            errors.append(_format_error("ARC1001", f"Requirement document must use UTF-8 encoding (Chinese is supported): {exc}", source=str(source_path)))
            return PreprocessingResult({}, {}, errors)
        except (OSError, yaml.YAMLError) as exc:
            errors.append(_format_error("ARC1001", f"Cannot parse requirement document: {exc}", source=str(source_path)))
            return PreprocessingResult({}, {}, errors)

        if isinstance(payload, dict) and isinstance(payload.get("root"), dict):
            payload = payload["root"]
        elif isinstance(payload, dict) and "id" not in payload and isinstance(payload.get("requirement"), dict):
            payload = payload["requirement"]
        if not isinstance(payload, dict):
            errors.append(_format_error("ARC1002", "Requirement document root must be a mapping.", source=str(source_path)))
            return PreprocessingResult({}, {}, errors)

        nodes: dict[str, dict[str, Any]] = {}
        node_order: list[str] = []
        root_id = self._normalize_node(
            payload,
            parent_id=None,
            pointer="/",
            source_name=source_path.name,
            nodes=nodes,
            node_order=node_order,
            errors=errors,
        )
        self._validate_dependencies(nodes, errors)

        atomic_ids = sorted(node_id for node_id, node in nodes.items() if node["type"] == "ATOMIC")
        folder_ids = sorted(node_id for node_id, node in nodes.items() if node["type"] == "FOLDER")
        effective_dependencies = self._effective_atomic_dependencies(nodes, atomic_ids, errors)
        requirement_ids = sorted(nodes)
        requirement_dependencies = self._effective_requirement_dependencies(nodes)
        waves = self._topological_waves(requirement_ids, requirement_dependencies, errors)
        atomic_wave_errors: list[str] = []
        atomic_waves = self._topological_waves(
            atomic_ids,
            effective_dependencies,
            atomic_wave_errors,
        )
        errors.extend(error for error in atomic_wave_errors if error not in errors)

        requirement_ir = {
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
        # External validation setup is preserved as metadata, not expanded into
        # requirement descriptions or the internal TDD task scope.
        for key in ("environment", "data"):
            if key in payload:
                requirement_ir[key] = copy.deepcopy(payload[key])
        dependency_graph = {
            "requirement_dependencies": dict(sorted(requirement_dependencies.items())),
            "atomic_dependencies": dict(sorted(effective_dependencies.items())),
            "implementation_waves": waves,
            "atomic_implementation_waves": atomic_waves,
        }
        if not root_id:
            errors.append(_format_error("ARC1003", "Requirement root id is missing.", source=str(source_path)))
        return PreprocessingResult(requirement_ir, dependency_graph, errors)

    def _normalize_node(
        self,
        raw: dict[str, Any],
        *,
        parent_id: str | None,
        pointer: str,
        source_name: str,
        nodes: dict[str, dict[str, Any]],
        node_order: list[str],
        errors: list[str],
    ) -> str:
        node_id = str(raw.get("id") or raw.get("req_id") or "").strip()
        source = f"{source_name}#{pointer}"
        if not node_id:
            node_id = f"<missing:{pointer}>"
            errors.append(_format_error("ARC1101", "Requirement node id is missing.", source=source))
        elif node_id in nodes:
            errors.append(_format_error("ARC1102", f"Duplicate requirement id: {node_id}", node_id=node_id, source=source))

        node_type = str(raw.get("type") or ("FOLDER" if raw.get("children") else "ATOMIC")).strip().upper()
        if node_type not in SUPPORTED_NODE_TYPES:
            errors.append(_format_error("ARC1103", f"Unsupported requirement node type: {node_type}", node_id=node_id, source=source))
            node_type = "ATOMIC"

        raw_dependencies = raw.get("dependencies") or []
        if not isinstance(raw_dependencies, list):
            errors.append(_format_error("ARC1104", "dependencies must be a list.", node_id=node_id, source=source))
            raw_dependencies = []
        dependencies = sorted({str(item).strip() for item in raw_dependencies if str(item).strip()})

        raw_children = raw.get("children") or []
        if not isinstance(raw_children, list):
            errors.append(_format_error("ARC1105", "children must be a list.", node_id=node_id, source=source))
            raw_children = []
        if node_type == "ATOMIC" and raw_children:
            errors.append(_format_error("ARC1106", "ATOMIC requirement cannot contain children.", node_id=node_id, source=source))

        description = str(raw.get("description") or "").strip()
        pages = self._normalize_pages(raw.get("pages", []), node_id, source, errors)
        visual_references = sorted({page["reference"] for page in pages if page.get("reference")})
        scenarios = self._normalize_scenarios(raw.get("scenarios"), node_id, source, errors)

        node = {
            "id": node_id,
            "name": str(raw.get("name") or "").strip(),
            "type": node_type,
            "description": description,
            "parent_id": parent_id,
            "children_ids": [],
            "dependencies": dependencies,
            "pages": pages,
            "visual_references": visual_references,
            "scenarios": scenarios,
            "source": {"document": source_name, "pointer": pointer},
        }
        for key in ("environment", "data"):
            if key in raw:
                node[key] = copy.deepcopy(raw[key])
        if node_id not in nodes:
            nodes[node_id] = node
            node_order.append(node_id)

        child_ids: list[str] = []
        for index, child in enumerate(raw_children):
            if not isinstance(child, dict):
                errors.append(_format_error("ARC1107", "Requirement child must be a mapping.", node_id=node_id, source=f"{source}/{index}"))
                continue
            child_id = self._normalize_node(
                child,
                parent_id=node_id,
                pointer=f"{pointer.rstrip('/')}/children/{index}",
                source_name=source_name,
                nodes=nodes,
                node_order=node_order,
                errors=errors,
            )
            child_ids.append(child_id)
        node["children_ids"] = child_ids
        return node_id

    @staticmethod
    def _normalize_pages(value: Any, node_id: str, source: str, errors: list[str]) -> list[dict[str, str]]:
        if not isinstance(value, list):
            errors.append(_format_error("ARC1108", "pages must be a list.", node_id=node_id, source=source))
            return []
        pages = []
        for page in value:
            if not isinstance(page, dict) or set(page) - {"name", "reference"} or not isinstance(page.get("name"), str) or not page["name"].strip():
                errors.append(_format_error("ARC1108", "Each page needs a nonempty name and optional reference.", node_id=node_id, source=source))
                continue
            name = page["name"].strip()
            row = {"name": name}
            reference = page.get("reference")
            if reference is not None:
                if not isinstance(reference, str) or not reference.strip() or PurePosixPath(reference.replace(chr(92), "/")).is_absolute() or ":" in reference:
                    errors.append(_format_error("ARC1108", "Page reference must be a relative image path.", node_id=node_id, source=source))
                    continue
                row["reference"] = PurePosixPath(reference.strip().replace('\\', '/')).as_posix()
            pages.append(row)
        return pages

    @staticmethod
    def _normalize_scenarios(value: Any, node_id: str, source: str, errors: list[str]) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            errors.append(_format_error("ARC1201", "scenarios must be a list.", node_id=node_id, source=source))
            return []
        scenarios: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw in enumerate(value, start=1):
            if not isinstance(raw, dict):
                errors.append(_format_error("ARC1202", "Scenario must be a mapping.", node_id=node_id, source=source))
                continue
            scenario_id = str(raw.get("id") or raw.get("scenario_id") or f"{node_id}:scenario:{index}").strip()
            if scenario_id in seen:
                errors.append(_format_error("ARC1203", f"Duplicate scenario id: {scenario_id}", node_id=node_id, source=source))
                continue
            seen.add(scenario_id)
            steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
            scenarios.append({
                "id": scenario_id,
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
    def _validate_dependencies(nodes: dict[str, dict[str, Any]], errors: list[str]) -> None:
        for node_id, node in sorted(nodes.items()):
            for dependency in node["dependencies"]:
                if dependency == node_id:
                    errors.append(_format_error("ARC1301", "Requirement cannot depend on itself.", node_id=node_id))
                elif dependency not in nodes:
                    errors.append(_format_error("ARC1302", f"Unresolved requirement dependency: {dependency}", node_id=node_id))

    def _effective_atomic_dependencies(
        self,
        nodes: dict[str, dict[str, Any]],
        atomic_ids: list[str],
        errors: list[str],
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
    def _effective_requirement_dependencies(
        nodes: dict[str, dict[str, Any]],
    ) -> dict[str, list[str]]:
        """Build one bottom-up DAG containing both folder and atomic nodes.

        Folder nodes depend on their direct children so detailed leaf design is
        available before aggregate UI planning. Every node also inherits the
        explicit dependencies declared by its ancestors. Backend-only passes
        continue to consume ``atomic_dependencies`` and skip folder nodes.
        """

        graph: dict[str, list[str]] = {}
        for node_id, node in sorted(nodes.items()):
            references = set(node["dependencies"])
            parent_id = node.get("parent_id")
            while isinstance(parent_id, str) and parent_id:
                parent = nodes.get(parent_id)
                if parent is None:
                    break
                references.update(parent["dependencies"])
                parent_id = parent.get("parent_id")
            if node["type"] == "FOLDER":
                references.update(node["children_ids"])
            references.discard(node_id)
            graph[node_id] = sorted(reference for reference in references if reference in nodes)
        return graph

    @staticmethod
    def _topological_waves(
        requirement_ids: list[str],
        dependencies: dict[str, list[str]],
        errors: list[str],
    ) -> list[list[str]]:
        remaining = set(requirement_ids)
        completed: set[str] = set()
        waves: list[list[str]] = []
        while remaining:
            wave = sorted(node_id for node_id in remaining if set(dependencies[node_id]) <= completed)
            if not wave:
                cycle_nodes = sorted(remaining)
                errors.append(_format_error("ARC1303", f"Requirement dependency cycle detected: {', '.join(cycle_nodes)}"))
                break
            waves.append(wave)
            completed.update(wave)
            remaining.difference_update(wave)
        return waves


def _format_error(code: str, message: str, *, node_id: str | None = None, source: str | None = None) -> str:
    context = ", ".join(value for value in (node_id, source) if value)
    return f"{code}: {message}" + (f" ({context})" if context else "")
