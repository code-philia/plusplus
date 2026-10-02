from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..common import IREdge


@dataclass(frozen=True, slots=True)
class CallEdge(IREdge):
    def __init__(self, source_id: str, target_id: str, attributes: dict[str, Any] | None = None) -> None:
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "target_id", target_id)
        object.__setattr__(self, "kind", "calls")
        object.__setattr__(self, "attributes", attributes or {})


@dataclass(frozen=True, slots=True)
class DataFlowEdge(IREdge):
    def __init__(self, source_id: str, target_id: str, attributes: dict[str, Any] | None = None) -> None:
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "target_id", target_id)
        object.__setattr__(self, "kind", "data_flow")
        object.__setattr__(self, "attributes", attributes or {})


@dataclass(frozen=True, slots=True)
class ReadEdge(IREdge):
    def __init__(self, source_id: str, target_id: str, attributes: dict[str, Any] | None = None) -> None:
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "target_id", target_id)
        object.__setattr__(self, "kind", "reads")
        object.__setattr__(self, "attributes", attributes or {})


@dataclass(frozen=True, slots=True)
class WriteEdge(IREdge):
    def __init__(self, source_id: str, target_id: str, attributes: dict[str, Any] | None = None) -> None:
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "target_id", target_id)
        object.__setattr__(self, "kind", "writes")
        object.__setattr__(self, "attributes", attributes or {})


@dataclass(frozen=True, slots=True)
class InvokeEdge(IREdge):
    def __init__(self, source_id: str, target_id: str, attributes: dict[str, Any] | None = None) -> None:
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "target_id", target_id)
        object.__setattr__(self, "kind", "invokes")
        object.__setattr__(self, "attributes", attributes or {})


@dataclass(frozen=True, slots=True)
class ContainEdge(IREdge):
    def __init__(self, source_id: str, target_id: str, attributes: dict[str, Any] | None = None) -> None:
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "target_id", target_id)
        object.__setattr__(self, "kind", "contains")
        object.__setattr__(self, "attributes", attributes or {})


@dataclass(frozen=True, slots=True)
class RequirementTrace(IREdge):
    def __init__(self, source_id: str, target_id: str, attributes: dict[str, Any] | None = None) -> None:
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "target_id", target_id)
        object.__setattr__(self, "kind", "traces_to")
        object.__setattr__(self, "attributes", attributes or {})


__all__ = ["CallEdge", "ContainEdge", "DataFlowEdge", "InvokeEdge", "ReadEdge", "RequirementTrace", "WriteEdge"]