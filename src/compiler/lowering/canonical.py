from __future__ import annotations

import re

from ..ir.common import IRLevel, IRNode, NodeKind, Reference
from ..ir.design.module import DesignModule
from ..ir.implementation.file import FileIR
from ..ir.implementation.project import ImplementationIR
from ..ir.implementation.symbol import SymbolIR


def _safe(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").lower()
    return value or "generated"


def _kind(value: object) -> str:
    return str(value).split(".")[-1].lower()


def _path(node: IRNode) -> tuple[str, str]:
    kind = _kind(node.kind)
    name = _safe(node.name or node.id)
    if kind in {"entity", "field", "relation", "database_operation"}:
        return f"backend/src/db/{name}.py", "python"
    if kind in {"api", "function"}:
        return f"backend/src/{kind}/{name}.py", "python"
    if kind in {"page", "component", "ui", "event", "handler", "effect"}:
        return f"frontend/src/{kind}/{name}.tsx", "typescript"
    return f"shared/src/{name}.ts", "typescript"


class CanonicalDesignLowering:
    """Lower canonical Design IR to a deterministic Implementation IR plan."""

    def lower(self, design: DesignModule) -> ImplementationIR:
        implementation = ImplementationIR(metadata={
            "source_level": str(IRLevel.DESIGN),
            "lowering": "canonical",
            "schema_version": 1,
        })
        for node in sorted(design.nodes.values(), key=lambda item: item.id):
            path, language = _path(node)
            file_id = f"file:{path}"
            symbol_id = f"symbol:{node.id}"
            implementation.files.setdefault(file_id, FileIR(
                id=file_id,
                name=path.rsplit("/", 1)[-1],
                path=path,
                language=language,
                requirement_ids=list(node.requirement_ids),
                attributes={"design_node_id": node.id},
            ))
            implementation.symbols[symbol_id] = SymbolIR(
                id=symbol_id,
                name=node.name or node.id,
                symbol_kind=_kind(node.kind),
                exported=True,
                requirement_ids=list(node.requirement_ids),
                attributes={"design_node_id": node.id, "file_id": file_id},
            )
            implementation.files[file_id].symbols.append(Reference(symbol_id))
            implementation.edges.extend(
                edge for edge in design.outgoing(node.id)
                if edge.target_id in design.nodes
            )
        return implementation


__all__ = ["CanonicalDesignLowering"]
