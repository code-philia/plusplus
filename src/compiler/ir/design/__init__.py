"""Formal typed Design IR graph."""

from .backend import ApiIR, BackendDesignIR, BackendModuleIR, DatabaseOperationIR, FunctionIR
from .canonical import build_design_graph
from .contract import CallEdge, ContainEdge, DataFlowEdge, InvokeEdge, ReadEdge, RequirementTrace, WriteEdge
from .database import EntityIR, FieldIR, RelationIR
from .frontend import ComponentIR, EffectIR, EventIR, FrontendDesignIR, HandlerIR, PageIR, UIIR
from .module import DesignIR, DesignModule, DesignNode

__all__ = [
    "ApiIR", "BackendDesignIR", "BackendModuleIR", "CallEdge", "ComponentIR", "ContainEdge", "DataFlowEdge", "DatabaseOperationIR",
    "DesignIR", "DesignModule", "DesignNode", "EffectIR", "FrontendDesignIR", "EntityIR", "EventIR", "FieldIR", "FunctionIR",
    "HandlerIR", "InvokeEdge", "PageIR", "ReadEdge", "RelationIR", "RequirementTrace", "UIIR", "WriteEdge",
    "build_design_graph",
]
