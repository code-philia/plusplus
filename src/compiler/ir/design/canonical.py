from __future__ import annotations

from typing import Any, Iterable

from ..common import IREdge, IRNode, NodeKind
from .contract import CallEdge, ContainEdge, DataFlowEdge, InvokeEdge, ReadEdge, RequirementTrace, WriteEdge
from .module import DesignModule


_TABLE_KINDS = {
    "pages": NodeKind.PAGE,
    "components": NodeKind.COMPONENT,
    "ui": NodeKind.UI,
    "data": NodeKind.FIELD,
    "properties": NodeKind.FIELD,
    "events": NodeKind.EVENT,
    "handlers": NodeKind.HANDLER,
    "effects": NodeKind.EFFECT,
}


def _row_id(row: dict[str, Any]) -> str | None:
    value = row.get("id")
    return str(value) if value else None


def _add_node(graph: DesignModule, row: dict[str, Any], kind: str, *, name: str | None = None) -> None:
    node_id = _row_id(row)
    if not node_id or node_id in graph.nodes:
        return
    graph.add_node(IRNode(
        id=node_id,
        kind=kind,
        name=name if name is not None else row.get("name"),
        requirement_ids=list(row.get("requirement_ids", []) or []),
        attributes={key: value for key, value in row.items()
                    if key not in {"id", "kind", "name", "requirement_ids"}},
    ))


def _add_requirement_edges(graph: DesignModule, node_id: str, requirement_ids: Iterable[str]) -> None:
    for requirement_id in dict.fromkeys(str(item) for item in requirement_ids if item):
        if requirement_id in graph.nodes:
            graph.add_edge(RequirementTrace(requirement_id, node_id))


def _add_containment(graph: DesignModule, parent_id: Any, child_id: str) -> None:
    if parent_id and str(parent_id) in graph.nodes:
        graph.add_edge(ContainEdge(str(parent_id), child_id))


def build_design_graph(
    database_schema: dict[str, Any] | None = None,
    backend_ir: dict[str, Any] | None = None,
    frontend_ir: dict[str, Any] | None = None,
) -> DesignModule:
    """Build the canonical cross-layer Design IR without changing legacy views."""
    graph = DesignModule(metadata={"schema_version": 1, "source_views": ["database", "backend", "frontend"]})

    database_schema = database_schema or {}
    for entity in database_schema.get("entities", []) or []:
        if not isinstance(entity, dict):
            continue
        entity_id = str(entity.get("key", ""))
        if not entity_id:
            continue
        _add_node(graph, {**entity, "id": entity_id}, NodeKind.ENTITY, name=entity.get("key"))
        _add_requirement_edges(graph, entity_id, entity.get("requirement_ids", []))
        fields = entity.get("fields", {}) or {}
        if isinstance(fields, dict):
            fields = fields.values()
        for field in fields:
            if not isinstance(field, dict) or not field.get("name"):
                continue
            field_id = f"{entity_id}.{field['name']}"
            field_row = {**field, "id": field_id, "name": field["name"]}
            _add_node(graph, field_row, NodeKind.FIELD)
            _add_containment(graph, entity_id, field_id)
            _add_requirement_edges(graph, field_id, field.get("requirement_ids", []))
    for relation in database_schema.get("relationships", []) or []:
        if not isinstance(relation, dict):
            continue
        relation_id = _row_id(relation)
        parent, child = relation.get("parent"), relation.get("child")
        if not relation_id:
            relation_id = f"relation:{parent}:{child}"
        _add_node(graph, {**relation, "id": relation_id}, NodeKind.RELATION)
        if parent and str(parent) in graph.nodes:
            graph.add_edge(DataFlowEdge(str(parent), relation_id, {"role": "source"}))
        if child and str(child) in graph.nodes:
            graph.add_edge(DataFlowEdge(relation_id, str(child), {"role": "target"}))

    backend_ir = backend_ir or {}
    for requirement in backend_ir.get("requirements", []) or []:
        if isinstance(requirement, dict) and requirement.get("id"):
            _add_node(graph, requirement, NodeKind.REQUIREMENT)
    for module in backend_ir.get("modules", []) or []:
        if not isinstance(module, dict) or not module.get("id"):
            continue
        module_id = str(module["id"])
        kind = {"API": NodeKind.API, "FUNC": NodeKind.FUNCTION, "DB": NodeKind.DATABASE_OPERATION}.get(
            str(module.get("kind", "")).upper(), str(module.get("kind", "module")).lower()
        )
        _add_node(graph, module, kind)
        _add_containment(graph, module.get("parent_id"), module_id)
        _add_requirement_edges(graph, module_id, [module.get("owner_requirement")])
        for callee in module.get("callees", []) or []:
            if str(callee) in graph.nodes:
                graph.add_edge(CallEdge(module_id, str(callee)))
        for effect in module.get("effects", []) or []:
            if not isinstance(effect, dict):
                continue
            target = effect.get("target") or effect.get("entity")
            if target and str(target) in graph.nodes:
                edge_type = str(effect.get("operation", "")).upper()
                edge = WriteEdge if edge_type in {"CREATE", "UPDATE", "DELETE", "WRITE"} else ReadEdge
                graph.add_edge(edge(module_id, str(target)))

    frontend_ir = frontend_ir or {}
    tables = frontend_ir.get("tables", frontend_ir)
    if isinstance(tables, dict):
        for table, rows in tables.items():
            if table in {"schema_version", "root_component_id", "visual_references"}:
                continue
            iterable = rows.values() if isinstance(rows, dict) else rows if isinstance(rows, list) else []
            kind = _TABLE_KINDS.get(table, table.rstrip("s"))
            for row in iterable:
                if not isinstance(row, dict) or not row.get("id"):
                    continue
                node_id = str(row["id"])
                _add_node(graph, row, kind)
                _add_requirement_edges(graph, node_id, row.get("requirement_ids", []))
                _add_containment(graph, row.get("parent_id"), node_id)
                for key, edge_type in (("children", ContainEdge), ("components", ContainEdge),
                                       ("invokes", InvokeEdge), ("targets", DataFlowEdge),
                                       ("binds", DataFlowEdge)):
                    for target in row.get(key, []) or []:
                        target_id = target.get("id") if isinstance(target, dict) else target
                        if target_id and str(target_id) in graph.nodes:
                            graph.add_edge(edge_type(node_id, str(target_id)))
                handler = row.get("handler")
                if isinstance(handler, dict):
                    handler = handler.get("id")
                if handler and str(handler) in graph.nodes:
                    graph.add_edge(InvokeEdge(node_id, str(handler)))
    return graph


__all__ = ["build_design_graph"]
